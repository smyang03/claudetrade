from __future__ import annotations

from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping

from execution.execution_advisor import (
    ExecutionAdvisorAction,
    ExecutionAdvisorConfig,
    ExecutionAdvisorDecision,
    evaluate_existing_position,
    evaluate_filled_pathb_position,
    evaluate_open_sell_order,
    evaluate_pending_buy_order,
    should_call_claude,
)
from lifecycle.event_store import EventStore
from lifecycle.models import DataQuality, LifecycleEvent, LifecycleEventType
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime_paths import get_runtime_path


ACTIVE_PATHB_STATUSES = (
    "WAITING",
    "HIT",
    "ORDER_SENT",
    "ORDER_ACKED",
    "PARTIAL_FILLED",
    "FILLED",
    "SELL_SENT",
    "SELL_ACKED",
    "SELL_PARTIAL_FILLED",
    "ORDER_UNKNOWN",
)

FILLED_POSITION_STATUSES = {"FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED", "ORDER_UNKNOWN"}
PENDING_BUY_STATUSES = {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}
OPEN_SELL_STATUSES = {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}


class ExecutionAdvisorRuntime:
    def __init__(
        self,
        bot: Any | None = None,
        *,
        is_paper: bool | None = None,
        runtime_mode: str | None = None,
        event_store: EventStore | None = None,
        broker_truth: BrokerTruthSnapshot | None = None,
        state_path: str | Path | None = None,
        claude_client: Callable[[dict[str, Any]], Any] | None = None,
        append_events: bool = True,
        enabled: bool | None = None,
    ):
        self.bot = bot
        self.runtime_mode = runtime_mode or str(getattr(bot, "_mode", "") or ("paper" if is_paper else "live"))
        self.store = event_store or EventStore()
        self.broker_truth = broker_truth or BrokerTruthSnapshot(runtime_mode=self.runtime_mode)
        self.state_path = Path(state_path) if state_path else get_runtime_path("state", f"{self.runtime_mode}_execution_advisor_state.json")
        self.claude_client = claude_client
        self.append_events = bool(append_events)
        self.config = ExecutionAdvisorConfig.from_env()
        self.enabled = _env_bool("EXEC_ADVISOR_ENABLED", False) if enabled is None else bool(enabled)
        self.shadow_only = _env_bool("EXEC_ADVISOR_SHADOW_ONLY", True)
        self.check_interval_sec = max(1, _env_int("EXEC_ADVISOR_CHECK_INTERVAL_SEC", 60))
        self.broker_truth_ttl_sec = max(1, _env_int("EXEC_ADVISOR_BROKER_TRUTH_TTL_SEC", 120))
        self.event_cooldown_minutes = max(0, _env_int("EXEC_ADVISOR_EVENT_COOLDOWN_MINUTES", 15))
        self._last_scan_at: dict[str, float] = {}

    def scan_market(self, market: str, *, force: bool = False, include_existing_noop: bool = False) -> dict[str, Any]:
        market_key = _market_key(market)
        if not self.enabled and not force:
            return {"ok": True, "market": market_key, "skipped": True, "reason": "execution_advisor_disabled"}
        now_ts = time.time()
        if not force:
            last = float(self._last_scan_at.get(market_key, 0.0) or 0.0)
            if last and (now_ts - last) < self.check_interval_sec:
                return {"ok": True, "market": market_key, "skipped": True, "reason": "execution_advisor_interval_gate"}
        self._last_scan_at[market_key] = now_ts

        market_data = self._market_snapshot(market_key)
        broker_truth_fresh = self._broker_truth_fresh(market_data)
        positions_by_ticker = self._positions_by_ticker(market_key, market_data.get("positions") or [])
        local_positions_by_ticker = self._positions_by_ticker(market_key, self._local_positions(market_key))
        open_orders = list(market_data.get("open_orders") or [])
        today_fills = list(market_data.get("today_fills") or [])
        decisions: list[ExecutionAdvisorDecision] = []
        seen_position_tickers: set[str] = set()

        for run in self._active_pathb_runs(market_key):
            ticker = _ticker_key(market_key, str(run.get("ticker") or ""))
            if not ticker:
                continue
            status = str(run.get("status") or "").strip().upper()
            plan = dict(run.get("plan") or {})
            if status in OPEN_SELL_STATUSES:
                order = self._match_order(open_orders, market_key, ticker, side="sell", plan=plan)
                position = positions_by_ticker.get(ticker)
                if order:
                    decisions.append(
                        evaluate_open_sell_order(
                            market=market_key,
                            order=order,
                            broker_position=position,
                            config=self.config,
                            broker_truth_fresh=broker_truth_fresh,
                        )
                    )
                    seen_position_tickers.add(ticker)
                continue
            if status in FILLED_POSITION_STATUSES:
                decisions.append(
                    evaluate_filled_pathb_position(
                        market=market_key,
                        ticker=ticker,
                        path_run=run,
                        broker_position=positions_by_ticker.get(ticker),
                        local_position=local_positions_by_ticker.get(ticker),
                        broker_fills=today_fills,
                        config=self.config,
                        broker_truth_fresh=broker_truth_fresh,
                    )
                )
                seen_position_tickers.add(ticker)
                continue
            if status in PENDING_BUY_STATUSES:
                order = self._match_order(open_orders, market_key, ticker, side="buy", plan=plan)
                if order:
                    decisions.append(
                        evaluate_pending_buy_order(
                            market=market_key,
                            order=order,
                            plan=plan,
                            current_price=order.get("current_price") or plan.get("current_price"),
                            config=self.config,
                            broker_truth_fresh=broker_truth_fresh,
                        )
                    )

        for order in open_orders:
            if str(order.get("side") or "").strip().lower() != "sell":
                continue
            ticker = _ticker_key(market_key, str(order.get("ticker") or ""))
            if not ticker or ticker in seen_position_tickers:
                continue
            decisions.append(
                evaluate_open_sell_order(
                    market=market_key,
                    order=order,
                    broker_position=positions_by_ticker.get(ticker),
                    config=self.config,
                    broker_truth_fresh=broker_truth_fresh,
                )
            )
            seen_position_tickers.add(ticker)

        if include_existing_noop:
            for ticker, position in positions_by_ticker.items():
                if ticker in seen_position_tickers:
                    continue
                decisions.append(evaluate_existing_position(market=market_key, broker_position=position, broker_truth_fresh=broker_truth_fresh))

        state = self._load_state()
        day_key = self._session_date(market_key)
        if state.get("date") != day_key:
            state = {"date": day_key, "claude_calls": 0, "cooldowns": {}, "last_events": {}}
        state.setdefault("last_events", {})
        appended = 0
        for decision in decisions:
            gate, response = self._maybe_run_claude(decision, state)
            if self._should_append(decision, state):
                self._append_event(decision, market_data=market_data, claude_gate=gate, claude_response=response)
                self._mark_appended(decision, state)
                appended += 1
        self._write_state(state)
        return {
            "ok": True,
            "market": market_key,
            "runtime_mode": self.runtime_mode,
            "broker_truth_fresh": broker_truth_fresh,
            "decisions": [decision.to_payload() for decision in decisions],
            "events_appended": appended if self.append_events else 0,
            "shadow_only": self.shadow_only,
            "claude_calls": int(state.get("claude_calls") or 0),
        }

    def _market_snapshot(self, market: str) -> dict[str, Any]:
        try:
            snapshot = self.broker_truth.market_snapshot(market, ttl_sec=self.broker_truth_ttl_sec)
            if not bool(snapshot.get("stale")) and not bool(snapshot.get("missing")):
                return snapshot
            refreshed = self.broker_truth.refresh_market(market, force=False, ttl_sec=self.broker_truth_ttl_sec)
            return dict((refreshed.get("markets") or {}).get(market) or snapshot)
        except Exception as exc:
            return {
                "missing": True,
                "stale": True,
                "error": f"broker_truth_snapshot_error:{type(exc).__name__}:{exc}",
                "positions": [],
                "open_orders": [],
                "today_fills": [],
            }

    def _active_pathb_runs(self, market: str) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        seen: set[str] = set()
        for status in ACTIVE_PATHB_STATUSES:
            try:
                rows = self.store.path_runs_for_session(
                    market=market,
                    runtime_mode=self.runtime_mode,
                    status=status,
                    path_type="claude_price",
                )
            except Exception:
                rows = []
            for row in rows:
                path_run_id = str(row.get("path_run_id") or "")
                if not path_run_id or path_run_id in seen:
                    continue
                seen.add(path_run_id)
                runs.append(dict(row))
        return runs

    def _maybe_run_claude(self, decision: ExecutionAdvisorDecision, state: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        cooldowns = state.setdefault("cooldowns", {})
        gate = should_call_claude(
            decision,
            config=self.config,
            cooldown_state=cooldowns,
            daily_call_count=int(state.get("claude_calls") or 0),
        )
        gate_payload = {"allowed": gate.allowed, "reason_code": gate.reason_code, "cooldown_key": gate.cooldown_key}
        if not gate.allowed:
            return gate_payload, None
        if self.claude_client is None:
            gate_payload["allowed"] = False
            gate_payload["reason_code"] = "claude_client_unconfigured"
            return gate_payload, None
        try:
            response = self.claude_client(decision.to_payload())
        except Exception as exc:
            gate_payload["allowed"] = False
            gate_payload["reason_code"] = f"claude_client_error:{type(exc).__name__}"
            return gate_payload, None
        cooldowns[gate.cooldown_key] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["claude_calls"] = int(state.get("claude_calls") or 0) + 1
        return gate_payload, response

    def _append_event(
        self,
        decision: ExecutionAdvisorDecision,
        *,
        market_data: Mapping[str, Any],
        claude_gate: Mapping[str, Any],
        claude_response: Any,
    ) -> None:
        if not self.append_events:
            return
        session_date = self._event_session_date(decision)
        decision_id = self._decision_id_for_event(decision, session_date)
        payload = decision.to_payload()
        payload.update(
            {
                "advisor_profile": self.config.profile,
                "shadow_only": self.shadow_only,
                "broker_truth": {
                    "fresh": decision.broker_truth_fresh,
                    "stale": bool(market_data.get("stale")),
                    "missing": bool(market_data.get("missing")),
                    "last_success_at": market_data.get("last_success_at", ""),
                    "error": market_data.get("error", ""),
                },
                "claude_gate": dict(claude_gate),
                "claude_response": claude_response,
            }
        )
        self.store.append(
            LifecycleEvent(
                event_type=LifecycleEventType.EXECUTION_ADVISOR_DECISION,
                market=decision.market,
                runtime_mode=self.runtime_mode,
                session_date=session_date,
                ticker=decision.ticker,
                decision_id=decision_id,
                prompt_version="execution_advisor_v1",
                brain_snapshot_id="execution_advisor",
                execution_id=decision.order_no or None,
                position_id=decision.path_run_id or None,
                reason_code=decision.reason_code,
                data_quality=DataQuality.CLEAN if decision.broker_truth_fresh else DataQuality.SUSPECT,
                payload=payload,
            )
        )

    def _should_append(self, decision: ExecutionAdvisorDecision, state: Mapping[str, Any]) -> bool:
        if decision.action == ExecutionAdvisorAction.NO_EXECUTION_ADVISOR_ACTION and decision.source_flow == "existing_position":
            return False
        last_events = state.get("last_events") if isinstance(state, Mapping) else {}
        if not isinstance(last_events, Mapping):
            return True
        key = self._event_state_key(decision)
        last = last_events.get(key)
        if not isinstance(last, Mapping):
            return True
        if str(last.get("signature") or "") != self._event_signature(decision):
            return True
        return not self._event_cooldown_active(last.get("at"))

    def _mark_appended(self, decision: ExecutionAdvisorDecision, state: dict[str, Any]) -> None:
        last_events = state.setdefault("last_events", {})
        if not isinstance(last_events, dict):
            last_events = {}
            state["last_events"] = last_events
        last_events[self._event_state_key(decision)] = {
            "signature": self._event_signature(decision),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _event_state_key(self, decision: ExecutionAdvisorDecision) -> str:
        identity = decision.path_run_id or decision.order_no or decision.ticker
        return f"{decision.market}:{identity}:{decision.source_flow}"

    def _event_signature(self, decision: ExecutionAdvisorDecision) -> str:
        return "|".join(
            [
                decision.action.value,
                decision.reason_code,
                str(decision.manual_or_mismatch),
                str(decision.broker_truth_fresh),
                str(decision.claude_candidate),
            ]
        )

    def _event_cooldown_active(self, value: Any) -> bool:
        if self.event_cooldown_minutes <= 0:
            return False
        if value in (None, ""):
            return False
        try:
            last = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return False
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - last.astimezone(timezone.utc)).total_seconds()
        return elapsed < self.event_cooldown_minutes * 60
        return True

    def _decision_id_for_event(self, decision: ExecutionAdvisorDecision, session_date: str) -> str:
        if decision.path_run_id:
            try:
                run = self.store.find_path_run(decision.path_run_id)
                if run and run.get("decision_id"):
                    return str(run.get("decision_id"))
            except Exception:
                pass
        ticker = decision.ticker.upper() if decision.market == "US" else decision.ticker
        return f"exec_advisor_{self.runtime_mode}_{session_date}_{decision.market}_{ticker}"

    def _event_session_date(self, decision: ExecutionAdvisorDecision) -> str:
        if decision.path_run_id:
            try:
                run = self.store.find_path_run(decision.path_run_id)
                if run and run.get("session_date"):
                    return str(run.get("session_date"))
            except Exception:
                pass
        return self._session_date(decision.market)

    def _session_date(self, market: str) -> str:
        provider = getattr(self.bot, "_current_session_date_str", None)
        if callable(provider):
            try:
                return str(provider(market))
            except Exception:
                pass
        return date.today().isoformat()

    def _local_positions(self, market: str) -> list[dict[str, Any]]:
        positions_for_market = getattr(self.bot, "_positions_for_market", None)
        if callable(positions_for_market):
            try:
                return list(positions_for_market(market) or [])
            except Exception:
                pass
        risk = getattr(self.bot, "risk", None)
        return [pos for pos in list(getattr(risk, "positions", []) or []) if _ticker_key(market, str(pos.get("ticker") or ""))]

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"date": "", "claude_calls": 0, "cooldowns": {}}
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {"date": "", "claude_calls": 0, "cooldowns": {}}
        if not isinstance(data, dict):
            return {"date": "", "claude_calls": 0, "cooldowns": {}}
        data.setdefault("cooldowns", {})
        data.setdefault("claude_calls", 0)
        data.setdefault("date", "")
        return data

    def _write_state(self, state: Mapping[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_name(f"{self.state_path.name}.tmp")
        tmp.write_text(json.dumps(dict(state), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _positions_by_ticker(market: str, rows: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            ticker = _ticker_key(market, str(row.get("ticker") or ""))
            if ticker:
                out[ticker] = dict(row)
        return out

    @staticmethod
    def _match_order(
        rows: list[Mapping[str, Any]],
        market: str,
        ticker: str,
        *,
        side: str,
        plan: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        ticker_key = _ticker_key(market, ticker)
        planned_order_no = str(plan.get("entry_order_no") or plan.get("order_no") or plan.get("sell_order_no") or "").strip()
        candidates: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if _ticker_key(market, str(row.get("ticker") or "")) != ticker_key:
                continue
            if str(row.get("side") or "").strip().lower() != side:
                continue
            if _safe_int(row.get("remaining_qty")) <= 0:
                continue
            candidates.append(dict(row))
        if planned_order_no:
            for row in candidates:
                if str(row.get("order_no") or "").strip() == planned_order_no:
                    return row
        return candidates[0] if candidates else None

    @staticmethod
    def _broker_truth_fresh(market_data: Mapping[str, Any]) -> bool:
        return not bool(market_data.get("missing")) and not bool(market_data.get("stale")) and not bool(market_data.get("error"))


def _market_key(value: str) -> str:
    return "US" if str(value or "").strip().upper() == "US" else "KR"


def _ticker_key(market: str, ticker: str) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if _market_key(market) == "US" else raw


def _env_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or str(value).strip() == "":
        return int(default)
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return int(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return int(default)
        return int(float(str(value).replace(",", "")))
    except Exception:
        return int(default)
