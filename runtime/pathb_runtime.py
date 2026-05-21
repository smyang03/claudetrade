from __future__ import annotations

import json
import math
import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from decision.claude_price_plan import PricePlan, parse_plan_from_claude
from execution.claude_price_adapter import (
    ClaudePriceAdapter,
    EntrySignal,
    round_down_to_cent,
    round_down_to_kr_tick,
    round_up_to_cent,
    round_up_to_kr_tick,
)
from execution.claude_price_sell_manager import ClaudePriceSellManager, ExitSignal
from execution.order_failure import is_permanent_order_failure
from execution.path_arbiter import SameDayReentryGuard
from execution.safety_gate import PathBSafetyGate, SafetyContext
from kis_api import cancel_order, get_balance, get_price, place_order, precheck_order
from lifecycle.event_store import EventStore
from logger import get_trading_logger
from runtime.broker_side import broker_row_side_matches
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.market_resolver import infer_ticker_market
from runtime.pathb_reasons import (
    ORDER_UNKNOWN_HARD_TIMEOUT_SEC_DEFAULT,
    ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS_DEFAULT,
    ORDER_UNKNOWN_SOFT_TIMEOUT_SEC_DEFAULT,
    normalize_pathb_decision_exit_reason,
)
from runtime.sizing_contract import calculate_order_quantity
from runtime_paths import get_runtime_path
from telegram_reporter import buy_order_alert, send as tg_send


KST = ZoneInfo("Asia/Seoul")
log = get_trading_logger()


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(str(raw).replace(",", "").strip())
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return int(default)
    try:
        return int(float(str(raw).replace(",", "").strip()))
    except Exception:
        return int(default)


def _bot_token(bot: Any, market: str, *, force_refresh: bool = False) -> str:
    getter = getattr(bot, "_token_for_market", None)
    if callable(getter):
        return str(getter(str(market or "KR").upper(), force_refresh=force_refresh) or "")
    return str(getattr(bot, "token", "") or "")


@dataclass(frozen=True)
class PathBControlState:
    enabled: bool = True
    emergency_disabled: bool = False
    updated_at: str = ""
    updated_by: str = "default"
    reason: str = ""


class PathBControlStore:
    def __init__(self, mode: str):
        self.path = get_runtime_path("state", f"{mode}_pathb_control.json")

    def load(self) -> PathBControlState:
        if not self.path.exists():
            return PathBControlState(enabled=True, emergency_disabled=False)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return PathBControlState(enabled=True, emergency_disabled=False)
        return PathBControlState(
            enabled=bool(data.get("enabled", True)),
            emergency_disabled=bool(data.get("emergency_disabled", False)),
            updated_at=str(data.get("updated_at", "") or ""),
            updated_by=str(data.get("updated_by", "") or ""),
            reason=str(data.get("reason", "") or ""),
        )

    def save(
        self,
        *,
        enabled: bool,
        emergency_disabled: bool = False,
        updated_by: str = "operator",
        reason: str = "",
    ) -> PathBControlState:
        state = PathBControlState(
            enabled=bool(enabled),
            emergency_disabled=bool(emergency_disabled),
            updated_at=datetime.now(KST).isoformat(timespec="seconds"),
            updated_by=str(updated_by or "operator"),
            reason=str(reason or ""),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state.__dict__, ensure_ascii=False, indent=2), encoding="utf-8")
        return state


class PathBRuntime:
    """
    Production facade for Claude Price Path.

    It owns Path B registration, live buy monitoring, live sell monitoring, and
    operator control. trading_bot.py only calls this facade at stable lifecycle
    points so the production bot is not filled with Path B internals.
    """

    ORDER_UNKNOWN_OPEN_RETRY_RESOLUTIONS = {
        "",
        "ambiguous_broker_truth",
        "broker_no_evidence",
        "broker_truth_unavailable",
        "session_end_unresolved",
    }
    ORDER_UNKNOWN_OPEN_LOOKBACK_SESSIONS = 5
    ORDER_UNKNOWN_SOFT_TIMEOUT_SEC = ORDER_UNKNOWN_SOFT_TIMEOUT_SEC_DEFAULT
    ORDER_UNKNOWN_HARD_TIMEOUT_SEC = ORDER_UNKNOWN_HARD_TIMEOUT_SEC_DEFAULT
    ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS = ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS_DEFAULT
    SELL_PENDING_LOOKBACK_SESSIONS = 5
    PRE_CLOSE_CARRY_REVIEW_MINUTES = 15.0
    HOLD_POLICY_MIN_VALID_MINUTES = 3
    HOLD_POLICY_MAX_VALID_MINUTES = 30
    HOLD_POLICY_DEFAULT_VALID_MINUTES = 10
    HOLD_POLICY_HARD_GAP_CAP = {"US": 0.015, "KR": 0.01}

    def __init__(
        self,
        bot: Any,
        *,
        is_paper: bool,
        config: V2Config = DEFAULT_V2_CONFIG,
        store: EventStore | None = None,
    ):
        self.bot = bot
        self.is_paper = bool(is_paper)
        self.mode = "paper" if self.is_paper else "live"
        self.config = config
        self.store = store or EventStore()
        self.adapter = ClaudePriceAdapter(self.store, self.config)
        self.sell_manager = ClaudePriceSellManager(self.adapter, self.config)
        self.safety_gate = PathBSafetyGate(self.config)
        self.reentry_guard = SameDayReentryGuard(self.store, self.config)
        self.control_store = PathBControlStore(self.mode)
        self._last_entry_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_exit_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_unknown_reconcile_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._profit_review_last_attempt_at: dict[str, float] = {}
        self._profit_review_calls_this_scan = 0
        self._pathb_sell_attempt_locks: dict[str, float] = {}
        self._entry_block_log_state: dict[str, dict[str, Any]] = {}
        self.broker_truth = BrokerTruthSnapshot(
            runtime_mode=self.mode,
            token_provider=lambda market="KR": _bot_token(self.bot, market),
            balance_provider=self._balance_for_snapshot,
            date_provider=lambda market: self._session_date(str(market or "").upper()),
        )

    def status(self) -> dict[str, Any]:
        control = self.control_store.load()
        return {
            "enabled": self.is_enabled(),
            "configured_enabled": bool(self.config.pathb_enabled),
            "market_live_enabled": {
                "KR": self._market_live_enabled("KR"),
                "US": self._market_live_enabled("US"),
            },
            "market_live_gate_source": {
                "KR": self._market_live_gate_detail("KR"),
                "US": self._market_live_gate_detail("US"),
            },
            "market_shadow_plan_enabled": {
                "KR": self._market_shadow_plan_enabled("KR"),
                "US": self._market_shadow_plan_enabled("US"),
            },
            "mode": self.config.pathb_mode,
            "runtime_mode": self.mode,
            "operator_enabled": control.enabled,
            "emergency_disabled": control.emergency_disabled or bool(self.config.pathb_emergency_disable),
            "fixed_order_krw": int(self.config.pathb_fixed_order_krw),
            "allow_one_share_over_budget": bool(self.config.pathb_allow_one_share_over_budget),
            "one_share_over_budget_max_krw": int(self.config.pathb_one_share_over_budget_max_krw),
            "one_share_over_budget_max_account_pct": float(
                self.config.pathb_one_share_over_budget_max_account_pct
            ),
            "max_positions": int(self.config.pathb_max_positions),
            "max_daily_entries": int(self.config.pathb_max_daily_entries),
            "min_confidence": float(self.config.pathb_min_confidence),
            "updated_at": control.updated_at,
            "updated_by": control.updated_by,
            "reason": control.reason,
            "consistency_health": {
                market: self.consistency_health(market)
                for market in ("KR", "US")
            },
        }

    def consistency_health(self, market: str) -> dict[str, Any]:
        market_key = str(market or "").upper()
        session_date = self._session_date(market_key)
        issues: list[dict[str, Any]] = []
        try:
            runs = self.store.path_runs_for_session(
                market=market_key,
                runtime_mode=self.mode,
                session_date=session_date,
                path_type="claude_price",
            )
        except Exception as exc:
            return {
                "market": market_key,
                "session_date": session_date,
                "ok": False,
                "issue_count": 1,
                "checked_runs": 0,
                "checked_events": 0,
                "issues": [{"code": "pathb_runs_unreadable", "error": str(exc)}],
            }
        try:
            events = self.store.events_for_session(
                market=market_key,
                runtime_mode=self.mode,
                session_date=session_date,
            )
        except Exception as exc:
            events = []
            issues.append({"code": "lifecycle_events_unreadable", "error": str(exc)})

        runs_by_id = {str(run.get("path_run_id", "") or ""): run for run in runs}
        pathb_ids = {path_run_id for path_run_id in runs_by_id if path_run_id}
        pathb_keys = {
            (
                str(run.get("decision_id", "") or ""),
                self._ticker_key(market_key, str(run.get("ticker", "") or "")),
            )
            for run in runs
        }
        closed_lifecycle: dict[str, dict[str, Any]] = {}
        lifecycle_status_types = {
            "CLAUDE_PRICE_PLAN_CREATED",
            "CLAUDE_PRICE_WAITING",
            "CLAUDE_PRICE_HIT",
            "CLAUDE_PRICE_CANCELLED",
            "CLAUDE_PRICE_EXPIRED",
            "ORDER_SENT",
            "ORDER_ACKED",
            "PARTIAL_FILLED",
            "FILLED",
            "SELL_SENT",
            "SELL_ACKED",
            "SELL_PARTIAL_FILLED",
            "CLOSED",
            "ORDER_UNKNOWN",
        }
        execution_required_types = {
            "ORDER_SENT",
            "ORDER_ACKED",
            "PARTIAL_FILLED",
            "FILLED",
            "SELL_SENT",
            "SELL_ACKED",
            "SELL_PARTIAL_FILLED",
            "CLOSED",
        }
        pathb_event_keys_with_path_run = set()
        for event in events:
            event_type = str(event.get("event_type", "") or "")
            if event_type not in lifecycle_status_types:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            payload_path_run_id = str(payload.get("path_run_id", "") or "")
            if not payload_path_run_id:
                continue
            pathb_event_keys_with_path_run.add(
                (
                    event_type,
                    str(event.get("decision_id", "") or ""),
                    self._ticker_key(market_key, str(event.get("ticker", "") or "")),
                    str(event.get("occurred_at", "") or ""),
                )
            )
        for event in events:
            event_type = str(event.get("event_type", "") or "")
            if event_type not in lifecycle_status_types:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            payload_path_run_id = str(payload.get("path_run_id", "") or "")
            payload_path_type = str(payload.get("path_type", "") or "")
            event_decision_id = str(event.get("decision_id", "") or "")
            event_ticker = self._ticker_key(market_key, str(event.get("ticker", "") or ""))
            decision_match = (event_decision_id, event_ticker) in pathb_keys
            pathb_related = (
                payload_path_type == "claude_price"
                or payload_path_run_id in pathb_ids
                or decision_match
            )
            if not pathb_related:
                continue
            if not payload_path_run_id and (
                event_type,
                event_decision_id,
                event_ticker,
                str(event.get("occurred_at", "") or ""),
            ) in pathb_event_keys_with_path_run:
                continue
            if not event_decision_id:
                issues.append(
                    {
                        "code": "pathb_lifecycle_missing_decision_id",
                        "event_id": event.get("event_id", 0),
                        "event_type": event_type,
                        "path_run_id": payload_path_run_id,
                        "ticker": event_ticker,
                    }
                )
            if payload_path_run_id:
                if event_type == "CLOSED":
                    closed_lifecycle[payload_path_run_id] = event
                if payload_path_run_id not in pathb_ids:
                    issues.append(
                        {
                            "code": "lifecycle_path_run_missing",
                            "event_id": event.get("event_id", 0),
                            "event_type": event_type,
                            "path_run_id": payload_path_run_id,
                            "ticker": event_ticker,
                        }
                    )
            else:
                issues.append(
                    {
                        "code": "pathb_lifecycle_missing_path_run_id",
                        "event_id": event.get("event_id", 0),
                        "event_type": event_type,
                        "decision_id": event_decision_id,
                        "ticker": event_ticker,
                    }
                )
            if payload_path_run_id in pathb_ids and payload_path_type != "claude_price":
                issues.append(
                    {
                        "code": "pathb_lifecycle_missing_path_type",
                        "event_id": event.get("event_id", 0),
                        "event_type": event_type,
                        "path_run_id": payload_path_run_id,
                        "ticker": event_ticker,
                    }
                )
            if event_type in execution_required_types and not str(event.get("execution_id", "") or ""):
                issues.append(
                    {
                        "code": "pathb_lifecycle_missing_execution_id",
                        "event_id": event.get("event_id", 0),
                        "event_type": event_type,
                        "path_run_id": payload_path_run_id,
                        "ticker": event_ticker,
                    }
                )

        market_data = self.broker_truth.market_snapshot(market_key)
        broker_available = not (
            bool(market_data.get("missing"))
            or bool(market_data.get("stale"))
            or str(market_data.get("error", "") or "")
        )
        open_statuses = {"PARTIAL_FILLED", "FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}
        for run in runs:
            path_run_id = str(run.get("path_run_id", "") or "")
            status = str(run.get("status", "") or "")
            plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
            ticker = self._ticker_key(market_key, str(run.get("ticker", "") or ""))
            if status == "ORDER_UNKNOWN":
                issues.append(
                    {
                        "code": "active_order_unknown",
                        "path_run_id": path_run_id,
                        "ticker": ticker,
                        "resolution": str(plan.get("order_unknown_resolution", "") or ""),
                        "broker_position_evidence": bool(plan.get("broker_position_evidence", False)),
                        "broker_open_order_evidence": bool(plan.get("broker_open_order_evidence", False)),
                        "broker_today_fill_evidence": bool(plan.get("broker_today_fill_evidence", False)),
                    }
                )
            closed_event = closed_lifecycle.get(path_run_id)
            if closed_event and status != "CLOSED":
                issues.append(
                    {
                        "code": "raw_status_lags_closed_lifecycle",
                        "path_run_id": path_run_id,
                        "ticker": ticker,
                        "stored_status": status,
                        "closed_event_id": closed_event.get("event_id", 0),
                    }
                )
            if status in open_statuses:
                local_pos = self._find_position(market_key, ticker, path_run_id=path_run_id)
                broker_positions = (
                    self._broker_rows_for_ticker(market_data.get("positions", []), market_key, ticker)
                    if broker_available
                    else []
                )
                if not broker_available:
                    issues.append(
                        {
                            "code": "broker_truth_unavailable_for_open_pathb",
                            "path_run_id": path_run_id,
                            "ticker": ticker,
                            "broker_truth_stale": bool(market_data.get("stale")),
                            "broker_truth_error": str(market_data.get("error", "") or ""),
                        }
                    )
                elif local_pos is None and not broker_positions and not closed_event:
                    issues.append(
                        {
                            "code": "open_pathb_missing_position_evidence",
                            "path_run_id": path_run_id,
                            "ticker": ticker,
                            "stored_status": status,
                        }
                    )
        return {
            "market": market_key,
            "session_date": session_date,
            "ok": len(issues) == 0,
            "issue_count": len(issues),
            "checked_runs": len(runs),
            "checked_events": len(events),
            "issues": issues[:20],
        }

    def is_enabled(self) -> bool:
        control = self.control_store.load()
        mode = str(self.config.pathb_mode or "").strip().lower()
        if mode in {"", "disabled", "off"}:
            return False
        if bool(self.config.pathb_emergency_disable):
            return False
        if not bool(self.config.pathb_enabled):
            return False
        if control.emergency_disabled:
            return False
        return bool(control.enabled)

    def _market_live_enabled(self, market: str) -> bool:
        return bool(self._market_live_gate_detail(market).get("effective", True))

    def _market_live_gate_detail(self, market: str) -> dict[str, Any]:
        if self.is_paper:
            return {
                "effective": True,
                "source_key": "paper_runtime",
                "source_value": "",
                "legacy_key": "",
                "legacy_value": "",
                "legacy_shadowed": False,
            }
        market_key = str(market or "").upper()
        if not market_key:
            return {
                "effective": True,
                "source_key": "default",
                "source_value": "",
                "legacy_key": "",
                "legacy_value": "",
                "legacy_shadowed": False,
            }
        primary = f"PATHB_{market_key}_LIVE_ENABLED"
        legacy = f"{market_key}_CLAUDE_PRICE_LIVE_ENABLED"
        if os.getenv(primary) is not None:
            return {
                "effective": _env_bool(primary, True),
                "source_key": primary,
                "source_value": str(os.getenv(primary, "")),
                "legacy_key": legacy,
                "legacy_value": str(os.getenv(legacy, "")),
                "legacy_shadowed": os.getenv(legacy) is not None,
            }
        return {
            "effective": _env_bool(legacy, True),
            "source_key": legacy,
            "source_value": str(os.getenv(legacy, "")),
            "legacy_key": legacy,
            "legacy_value": str(os.getenv(legacy, "")),
            "legacy_shadowed": False,
        }

    def _market_shadow_plan_enabled(self, market: str) -> bool:
        if self.is_paper:
            return False
        market_key = str(market or "").upper()
        if not market_key:
            return False
        primary = f"PATHB_{market_key}_SHADOW_PLAN_ENABLED"
        legacy = f"{market_key}_CLAUDE_PRICE_SHADOW_PLAN_ENABLED"
        if os.getenv(primary) is not None:
            return _env_bool(primary, False)
        return _env_bool(legacy, False)

    def _audit_entry_scan_blocked(self, market: str, entry_gate: dict[str, Any]) -> None:
        bot = getattr(self, "bot", None)
        emit_signal = getattr(bot, "_audit_emit_signal", None)
        active_episode = getattr(bot, "_audit_active_episode", None)
        link_episode = getattr(bot, "_audit_link_signal_episode", None)
        if not callable(emit_signal):
            return
        market_key = str(market or "").upper()
        reason = str((entry_gate or {}).get("reason") or "NEW_BUY_BLOCKED")
        scope = str((entry_gate or {}).get("scope") or "market")
        now_dt = datetime.now(KST)
        now_iso = now_dt.isoformat(timespec="seconds")
        episode_id = ""
        if reason == "ORDER_UNKNOWN_UNRESOLVED" and callable(active_episode):
            episode_id = active_episode(
                market_key,
                episode_type="ORDER_UNKNOWN_PAUSE",
                scope=scope,
                reason=reason,
                payload={"stage": "pathb_entry_scan", "entry_gate": entry_gate},
            )
        try:
            runs = self.adapter.get_waiting_runs(market_key, self.mode, self._session_date(market_key))
        except Exception:
            runs = []
        for run in runs:
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            try:
                signal_id = emit_signal(
                    market_key,
                    plan.ticker,
                    strategy="claude_price",
                    signal_at=now_iso,
                    signal_price=0.0,
                    score=float(plan.confidence or 0.0),
                    decision="BLOCKED",
                    block_reason=reason,
                    source="path_b_entry_scan_blocked",
                    path_type="claude_price",
                    path_run_id=plan.path_run_id,
                    decision_id=plan.decision_id,
                    payload={
                        "entry_gate": entry_gate,
                        "buy_zone_low": plan.buy_zone_low,
                        "buy_zone_high": plan.buy_zone_high,
                        "sell_target": plan.sell_target,
                        "stop_loss": plan.stop_loss,
                    },
                )
                if episode_id and callable(link_episode):
                    link_episode(signal_id, episode_id, reason="ORDER_UNKNOWN_PATHB_BLOCKED")
            except Exception:
                pass

    def _log_entry_scan_blocked(self, market: str, entry_gate: dict[str, Any]) -> None:
        market_key = str(market or "").upper()
        reason = str((entry_gate or {}).get("reason") or "NEW_BUY_BLOCKED")
        scope = str((entry_gate or {}).get("scope") or "market")
        if reason != "BROKER_SYNC_QUARANTINE":
            log.warning(f"[PathB entry scan blocked] {market_key} {reason} scope={scope}")
            return
        state_key = f"{market_key}:{reason}:{scope}"
        states = getattr(self, "_entry_block_log_state", None)
        if not isinstance(states, dict):
            states = {}
            self._entry_block_log_state = states
        now = time.time()
        state = states.setdefault(state_key, {"count": 0, "last_emit": 0.0})
        state["count"] = int(state.get("count", 0) or 0) + 1
        try:
            interval = max(5, _env_int("PATHB_ENTRY_BLOCK_SUMMARY_SEC", 60))
        except Exception:
            interval = 60
        last_emit = float(state.get("last_emit", 0.0) or 0.0)
        if last_emit and now - last_emit < interval:
            log.info(
                f"[PathB entry scan blocked] {market_key} {reason} scope={scope} "
                f"suppressed_count={state['count']}"
            )
            return
        details = (entry_gate or {}).get("details") if isinstance((entry_gate or {}).get("details"), dict) else {}
        broker_state = getattr(self.bot, "_broker_state", {}).get(market_key, {}) if getattr(self, "bot", None) is not None else {}
        broker_trust = str(details.get("broker_trust_level") or broker_state.get("trust_level") or "")
        last_ok_at = str(broker_state.get("last_ok_at") or "")
        last_error = str(broker_state.get("last_error") or broker_state.get("error") or "")
        recheck_after = details.get("recheck_after_seconds", "")
        log.warning(
            f"[PathB entry scan blocked] {market_key} {reason} scope={scope} "
            f"repeat={state['count']} broker_trust={broker_trust} last_ok_at={last_ok_at} "
            f"last_error={last_error[:120]} recheck_after_seconds={recheck_after}"
        )
        state["count"] = 0
        state["last_emit"] = now

    def _audit_pathb_price_seen(self, plan: PricePlan, current: float, *, source: str) -> None:
        bot = getattr(self, "bot", None)
        price_sample = getattr(bot, "_audit_emit_price_sample", None)
        if not callable(price_sample):
            return
        try:
            price_sample(
                plan.market,
                plan.ticker,
                price=float(current or 0.0),
                source=source,
                decision_id=plan.decision_id,
                path_run_id=plan.path_run_id,
                payload={
                    "buy_zone_low": plan.buy_zone_low,
                    "buy_zone_high": plan.buy_zone_high,
                    "sell_target": plan.sell_target,
                    "stop_loss": plan.stop_loss,
                },
            )
        except Exception:
            pass

    def _audit_pathb_zone_hit(self, plan: PricePlan, signal: EntrySignal) -> str:
        bot = getattr(self, "bot", None)
        emit_signal = getattr(bot, "_audit_emit_signal", None)
        if not callable(emit_signal):
            return ""
        try:
            return str(
                emit_signal(
                    plan.market,
                    plan.ticker,
                    strategy="claude_price",
                    signal_at=datetime.now(KST).isoformat(timespec="seconds"),
                    signal_price=float(signal.price or signal.limit_price or 0.0),
                    risk_price_krw=self._price_to_krw(float(signal.limit_price or signal.price or 0.0), plan.market),
                    score=float(plan.confidence or 0.0),
                    decision="pathb_zone_hit",
                    source="path_b",
                    path_type="claude_price",
                    path_run_id=plan.path_run_id,
                    decision_id=plan.decision_id,
                    payload={
                        "reason": signal.reason,
                        "limit_price": signal.limit_price,
                        "buy_zone_low": plan.buy_zone_low,
                        "buy_zone_high": plan.buy_zone_high,
                    },
                )
            )
        except Exception:
            return ""

    def _audit_pathb_exit_signal(self, plan: PricePlan, pos: dict[str, Any], signal: ExitSignal) -> str:
        bot = getattr(self, "bot", None)
        emit_signal = getattr(bot, "_audit_emit_signal", None)
        if not callable(emit_signal):
            return ""
        try:
            return str(
                emit_signal(
                    plan.market,
                    plan.ticker,
                    strategy="claude_price_exit",
                    signal_at=datetime.now(KST).isoformat(timespec="seconds"),
                    signal_price=float(signal.price or pos.get("display_current_price", 0) or 0.0),
                    risk_price_krw=self._price_to_krw(float(signal.price or 0.0), plan.market),
                    score=float(plan.confidence or 0.0),
                    decision="pathb_exit_signal",
                    source="path_b_exit",
                    path_type="claude_price",
                    path_run_id=plan.path_run_id,
                    decision_id=plan.decision_id,
                    payload={
                        "reason": signal.reason,
                        "close_reason": signal.close_reason,
                        "qty": int(pos.get("qty", 0) or 0),
                        "entry": pos.get("entry"),
                        "display_avg_price": pos.get("display_avg_price"),
                    },
                )
            )
        except Exception:
            return ""

    def _audit_pathb_buy_sent(
        self,
        plan: PricePlan,
        signal: EntrySignal,
        *,
        qty: int,
        order_no: str,
        risk_price_krw: float,
        order_cost_krw: float,
    ) -> str:
        bot = getattr(self, "bot", None)
        emit_signal = getattr(bot, "_audit_emit_signal", None)
        audit_emit = getattr(bot, "_audit_try_emit", None)
        if not callable(emit_signal):
            return ""
        try:
            signal_id = str(
                emit_signal(
                    plan.market,
                    plan.ticker,
                    strategy="claude_price",
                    signal_at=datetime.now(KST).isoformat(timespec="seconds"),
                    signal_price=float(signal.price or signal.limit_price or 0.0),
                    risk_price_krw=float(risk_price_krw or 0.0),
                    score=float(plan.confidence or 0.0),
                    decision="BUY_SIGNAL",
                    source="path_b",
                    path_type="claude_price",
                    path_run_id=plan.path_run_id,
                    decision_id=plan.decision_id,
                    payload={
                        "reason": signal.reason,
                        "limit_price": signal.limit_price,
                        "qty": int(qty or 0),
                        "order_no": order_no,
                        "order_cost_krw": order_cost_krw,
                    },
                )
            )
            if signal_id and callable(audit_emit):
                audit_emit(
                    {
                        "kind": "trade_link",
                        "signal_id": signal_id,
                        "decision_id": plan.decision_id,
                        "path_run_id": plan.path_run_id,
                        "order_no": order_no,
                        "entry_price": float(signal.limit_price or signal.price or 0.0),
                        "payload": {"side": "buy", "path_type": "claude_price", "qty": int(qty or 0)},
                    }
                )
            return signal_id
        except Exception:
            return ""

    def _audit_pathb_buy_fill(
        self,
        run: dict[str, Any],
        order: dict[str, Any],
        *,
        price: float,
        qty: int,
        partial: bool,
    ) -> None:
        bot = getattr(self, "bot", None)
        price_sample = getattr(bot, "_audit_emit_price_sample", None)
        audit_emit = getattr(bot, "_audit_try_emit", None)
        if not callable(price_sample) and not callable(audit_emit):
            return
        try:
            plan_json = run.get("plan") if isinstance(run.get("plan"), dict) else run.get("plan_json") or {}
            market = str(order.get("market", "") or run.get("market", "") or plan_json.get("market", "") or "").upper()
            ticker = str(order.get("ticker", "") or run.get("ticker", "") or plan_json.get("ticker", "") or "")
            decision_id = str(order.get("v2_decision_id", "") or run.get("decision_id", "") or plan_json.get("decision_id", "") or "")
            path_run_id = str(run.get("path_run_id", "") or order.get("pathb_path_run_id", "") or "")
            order_no = str(order.get("order_no", "") or order.get("v2_execution_id", "") or "")
            if callable(price_sample):
                price_sample(
                    market,
                    ticker,
                    price=float(price or 0.0),
                    source="pathb:buy_fill_partial" if partial else "pathb:buy_fill",
                    decision_id=decision_id,
                    path_run_id=path_run_id,
                    payload={"qty": int(qty or 0), "order_no": order_no, "partial": bool(partial)},
                )
            if callable(audit_emit):
                audit_emit(
                    {
                        "kind": "trade_link",
                        "decision_id": decision_id,
                        "path_run_id": path_run_id,
                        "order_no": order_no,
                        "entry_price": float(price or 0.0),
                        "payload": {"side": "buy_fill", "qty": int(qty or 0), "partial": bool(partial)},
                    }
                )
        except Exception:
            pass

    def _audit_pathb_sell_sent(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        signal: ExitSignal,
        *,
        signal_id: str,
        qty: int,
        order_no: str,
        order_price: float,
    ) -> None:
        bot = getattr(self, "bot", None)
        mark_decision = getattr(bot, "_audit_mark_signal_decision", None)
        price_sample = getattr(bot, "_audit_emit_price_sample", None)
        audit_emit = getattr(bot, "_audit_try_emit", None)
        try:
            if signal_id and callable(mark_decision):
                mark_decision(
                    plan.market,
                    plan.ticker,
                    signal_id=signal_id,
                    decision="SELL_SIGNAL",
                    signal_price=float(order_price or signal.price or 0.0),
                    risk_price_krw=self._price_to_krw(float(order_price or signal.price or 0.0), plan.market),
                    strategy="claude_price_exit",
                    score=float(plan.confidence or 0.0),
                    source="path_b_exit",
                    path_type="claude_price",
                    path_run_id=plan.path_run_id,
                    decision_id=plan.decision_id,
                    payload={"reason": signal.reason, "close_reason": signal.close_reason, "order_no": order_no, "qty": int(qty or 0)},
                )
            if callable(price_sample):
                price_sample(
                    plan.market,
                    plan.ticker,
                    price=float(order_price or signal.price or 0.0),
                    source="pathb:sell_sent",
                    decision_id=plan.decision_id,
                    path_run_id=plan.path_run_id,
                    signal_id=signal_id,
                    payload={"reason": signal.reason, "close_reason": signal.close_reason, "order_no": order_no, "qty": int(qty or 0)},
                )
            entry_native = float(pos.get("display_avg_price", 0) or pos.get("avg_price", 0) or pos.get("entry_price", 0) or 0)
            if entry_native <= 0 and str(plan.market or "").upper() == "KR":
                entry_native = float(pos.get("entry", 0) or 0)
            pnl_pct = ((float(order_price or 0.0) / entry_native - 1.0) * 100.0) if entry_native > 0 and float(order_price or 0.0) > 0 else None
            if callable(audit_emit):
                audit_emit(
                    {
                        "kind": "trade_link",
                        "signal_id": signal_id,
                        "decision_id": plan.decision_id,
                        "path_run_id": plan.path_run_id,
                        "order_no": order_no,
                        "exit_price": float(order_price or 0.0),
                        "pnl_pct": pnl_pct,
                        "exit_reason": signal.close_reason,
                        "payload": {"side": "sell", "reason": signal.reason, "qty": int(qty or 0)},
                    }
                )
        except Exception:
            pass

    def set_enabled(self, enabled: bool, *, updated_by: str = "telegram", reason: str = "") -> PathBControlState:
        state = self.control_store.save(
            enabled=bool(enabled),
            emergency_disabled=False,
            updated_by=updated_by,
            reason=reason,
        )
        log.warning(f"[PathB control] enabled={state.enabled} by={state.updated_by} reason={state.reason}")
        return state

    def emergency_disable(self, *, updated_by: str = "telegram", reason: str = "operator_kill") -> PathBControlState:
        state = self.control_store.save(
            enabled=False,
            emergency_disabled=True,
            updated_by=updated_by,
            reason=reason,
        )
        log.error(f"[PathB KILL] emergency disable by={updated_by} reason={reason}")
        for market in ("KR", "US"):
            try:
                self.cancel_waiting(market, reason="pathb_kill")
            except Exception as exc:
                log.warning(f"[PathB KILL] cancel waiting failed {market}: {exc}")
            try:
                self.close_all_open(market, reason="pathb_kill")
            except Exception as exc:
                log.warning(f"[PathB KILL] close open failed {market}: {exc}")
        return state

    def _pathb_registration_inputs(
        self,
        market: str,
        meta: dict[str, Any],
        *,
        shadow_only: bool = False,
    ) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
        if str(meta.get("_pathb_registration_scope") or "") == "candidate_actions_wait_only":
            # Policy note: PULLBACK_WAIT is not PathA trade_ready, but the current
            # PathB live policy treats Claude's buy-zone plan as executable when
            # price enters the zone. Keep this behavior for now; revisit if PathB
            # live entries should require explicit BUY_READY/PROBE_READY instead.
            trade_ready = list(meta.get("_pathb_wait_tickers") or [])
            price_targets = dict(meta.get("_pathb_price_targets") or {})
        else:
            trade_ready = list(meta.get("trade_ready") or [])
            price_targets = dict(meta.get("price_targets") or {})

        origin_map = dict(meta.get("_pathb_wait_origins") or {}) if isinstance(meta.get("_pathb_wait_origins"), dict) else {}
        if shadow_only:
            shadow_tickers = list(meta.get("_pathb_shadow_tickers") or [])
            shadow_targets = dict(meta.get("_pathb_shadow_price_targets") or {})
            shadow_origins = (
                dict(meta.get("_pathb_shadow_origins") or {})
                if isinstance(meta.get("_pathb_shadow_origins"), dict)
                else {}
            )
            trade_ready = list(dict.fromkeys(list(trade_ready or []) + shadow_tickers))
            price_targets = {**price_targets, **shadow_targets}
            origin_map = {**origin_map, **shadow_origins}
            if str(meta.get("_pathb_registration_scope") or "") == "candidate_actions_wait_only":
                fallback_targets = dict(meta.get("price_targets") or {})
                for ticker in list(meta.get("trade_ready") or []):
                    key = self._ticker_key(market, ticker)
                    trade_ready.append(key)
                    raw = fallback_targets.get(ticker) or fallback_targets.get(key)
                    if raw and key not in price_targets:
                        price_targets[key] = raw
                trade_ready = list(dict.fromkeys(trade_ready))

        return trade_ready, price_targets, origin_map

    def _pathb_shadow_registration_inputs(
        self,
        market: str,
        meta: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
        shadow_tickers = [
            self._ticker_key(market, ticker)
            for ticker in list(meta.get("_pathb_shadow_tickers") or [])
        ]
        shadow_targets = dict(meta.get("_pathb_shadow_price_targets") or {})
        shadow_origins = (
            dict(meta.get("_pathb_shadow_origins") or {})
            if isinstance(meta.get("_pathb_shadow_origins"), dict)
            else {}
        )
        return list(dict.fromkeys(shadow_tickers)), shadow_targets, shadow_origins

    def _shadow_path_for_ticker(self, market: str, ticker: str) -> dict[str, Any] | None:
        key = self._ticker_key(market, ticker)
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
            path_type="claude_price",
        ):
            if self._ticker_key(market, str(run.get("ticker", "") or "")) != key:
                continue
            if str(run.get("status", "") or "") in {"SHADOW_WAITING", "SHADOW_HIT"}:
                return run
            plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
            if bool(plan.get("shadow_only")):
                return run
        return None

    def _plan_shadow_only(self, plan: PricePlan) -> bool:
        try:
            run = self.store.find_path_run(plan.path_run_id) or {}
        except Exception as exc:
            log.warning(
                f"[PathB plan truth unavailable] {plan.market} {plan.ticker} "
                f"path_run_id={plan.path_run_id} err={exc}"
            )
            return True
        if not run:
            log.warning(
                f"[PathB plan truth missing] {plan.market} {plan.ticker} "
                f"path_run_id={plan.path_run_id}"
            )
            return True
        status = str(run.get("status", "") or "")
        if status.startswith("SHADOW_"):
            return True
        raw_plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        return bool(raw_plan.get("shadow_only"))

    def register_from_selection_meta(self, market: str, meta: dict[str, Any]) -> list[str]:
        if not self.is_enabled():
            return []
        market = str(market or "").upper()
        live_enabled = self._market_live_enabled(market)
        shadow_only = (not live_enabled) and self._market_shadow_plan_enabled(market)
        if not live_enabled and not shadow_only:
            log.warning(f"[PathB paper-only] {market} live Claude Price registration skipped")
            return []
        if shadow_only:
            log.info(f"[PathB shadow plan] {market} live off; registering shadow-only Claude Price plans")
        decision_ids = meta.get("v2_decision_ids") or {}
        session_date = self._session_date(market)
        registered: list[str] = []
        missing_price_targets: list[str] = []
        entry_gate = self._new_buy_block_state(market, strategy="path_b_plan_registration")

        def _decision_id_for(
            ticker: str,
            key: str,
            *,
            shadow_registration: bool = False,
            isolated_shadow: bool = False,
        ) -> str:
            if shadow_registration and isolated_shadow:
                return f"shadow:{self.mode}:{market}:{session_date}:{key}"
            decision_id = str(decision_ids.get(ticker) or decision_ids.get(key) or "")
            if not decision_id:
                try:
                    decision_id = str(self.bot._v2_decision_id_for_ticker(market, key) or "")
                except Exception:
                    decision_id = ""
            if not decision_id and shadow_registration:
                return f"shadow:{self.mode}:{market}:{session_date}:{key}"
            return decision_id

        def _shadow_registration_isolated(
            ticker: str,
            key: str,
            origin_map: dict[str, Any],
            batch_shadow_reason: str,
        ) -> bool:
            origin = origin_map.get(key) or origin_map.get(ticker) or origin_map.get(str(ticker).upper()) or {}
            if isinstance(origin, dict):
                if str(origin.get("registration_scope") or "") == "candidate_actions_shadow_only":
                    return True
                if str(origin.get("origin_route") or "") == "pathb_shadow_only":
                    return True
            return str(batch_shadow_reason or "") != "market_live_disabled"

        def _shadow_overrides_for(origin: dict[str, Any], reason: str) -> dict[str, Any]:
            overrides: dict[str, Any] = {
                "shadow_only": True,
                "live_order_enabled": False,
                "execution_allowed": False,
                "shadow_reason": reason,
            }
            if isinstance(origin, dict):
                for field in (
                    "origin_reason",
                    "demoted_from",
                    "demotion_reason",
                    "microstructure_data_quality",
                    "pathb_shadow_reason",
                ):
                    value = origin.get(field)
                    if value not in (None, ""):
                        overrides[field] = value
            return overrides

        def _register_batch(
            trade_ready: list[str],
            price_targets: dict[str, Any],
            origin_map: dict[str, Any],
            *,
            shadow_registration: bool = False,
            shadow_reason: str = "",
        ) -> None:
            if not trade_ready:
                return
            if not bool(entry_gate.get("allowed", True)):
                reason = str(entry_gate.get("reason") or "ORDER_UNKNOWN_UNRESOLVED")
                for ticker in trade_ready:
                    key = self._ticker_key(market, ticker)
                    decision_id = _decision_id_for(
                        ticker,
                        key,
                        shadow_registration=shadow_registration,
                        isolated_shadow=(
                            shadow_registration
                            and _shadow_registration_isolated(ticker, key, origin_map, shadow_reason)
                        ),
                    )
                    if not decision_id:
                        continue
                    try:
                        self.bot._v2_record_lifecycle_event(
                            "CLAUDE_PRICE_PLAN_GATE_WARNING",
                            market,
                            key,
                            decision_id=decision_id,
                            reason_code=reason,
                            payload={
                                **(entry_gate.get("details") or {}),
                                "stage": "pathb_plan_registration",
                                "scope": entry_gate.get("scope", ""),
                                "path_type": "claude_price",
                                "shadow_registration": bool(shadow_registration),
                            },
                        )
                    except Exception as exc:
                        log.warning(f"[PathB plan gate warning record failed] {market} {key} {reason}: {exc}")
                log.warning(
                    f"[PathB plan registration execution-gate warning] {market} {reason} "
                    f"scope={entry_gate.get('scope', '')} trade_ready={trade_ready}"
                )
            for ticker in trade_ready:
                key = self._ticker_key(market, ticker)
                if shadow_registration:
                    if self._active_path_for_ticker(market, key) or self._shadow_path_for_ticker(market, key):
                        continue
                else:
                    if self._active_path_for_ticker(market, key):
                        continue
                    shadow_run = self._shadow_path_for_ticker(market, key)
                    if shadow_run and str(shadow_run.get("status") or "") == "SHADOW_WAITING":
                        self.adapter.mark_shadow_cancelled(
                            str(shadow_run.get("path_run_id") or ""),
                            runtime_mode=self.mode,
                            brain_snapshot_id=self._brain_snapshot_id(market),
                            reason="live_candidate_supersedes_shadow",
                        )
                isolated_shadow = (
                    shadow_registration
                    and _shadow_registration_isolated(ticker, key, origin_map, shadow_reason)
                )
                decision_id = _decision_id_for(
                    ticker,
                    key,
                    shadow_registration=shadow_registration,
                    isolated_shadow=isolated_shadow,
                )
                if not decision_id:
                    continue
                raw_plan = price_targets.get(ticker) or price_targets.get(key)
                if not raw_plan:
                    missing_price_targets.append(key)
                    self._record_blocked(
                        market,
                        key,
                        decision_id,
                        "CLAUDE_PRICE_MISSING",
                        {
                            "trade_ready": list(trade_ready),
                            "price_target_keys": list(price_targets.keys()) if isinstance(price_targets, dict) else [],
                            "shadow_registration": bool(shadow_registration),
                        },
                    )
                    continue
                origin = origin_map.get(key) or origin_map.get(ticker) or origin_map.get(str(ticker).upper()) or {}
                if isinstance(origin, dict) and origin:
                    raw_plan = {
                        **dict(raw_plan),
                        "_origin_action": str(origin.get("origin_action") or ""),
                        "_origin_route": str(origin.get("origin_route") or ""),
                        "_registration_scope": str(origin.get("registration_scope") or ""),
                        "_not_patha_trade_ready": bool(origin.get("not_patha_trade_ready", False)),
                        "_origin_reason": str(origin.get("reason") or origin.get("origin_reason") or ""),
                    }
                plan, errors = parse_plan_from_claude(
                    decision_id=decision_id,
                    ticker=key,
                    market=market,
                    session_date=session_date,
                    raw=raw_plan,
                    prompt_stage="PRE_SESSION",
                    prompt_version="pathb_price_v1.0",
                    min_confidence=float(self.config.pathb_min_confidence),
                )
                if plan is None:
                    self._record_blocked(
                        market,
                        key,
                        decision_id,
                        "CLAUDE_PRICE_INVALID",
                        {"errors": errors, "raw_plan": raw_plan, "shadow_registration": bool(shadow_registration)},
                    )
                    continue
                origin_dict = origin if isinstance(origin, dict) else {}
                resolved_shadow_reason = shadow_reason
                if shadow_registration and shadow_reason != "market_live_disabled":
                    resolved_shadow_reason = (
                        str(origin_dict.get("pathb_shadow_reason") or "")
                        or str(origin_dict.get("origin_reason") or "")
                        or str(origin_dict.get("reason") or "")
                        or shadow_reason
                        or "candidate_action_shadow_validation"
                    )
                path_run_id = self.adapter.register_plan(
                    plan,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    initial_status="SHADOW_WAITING" if shadow_registration else "WAITING",
                    plan_overrides=(
                        _shadow_overrides_for(
                            origin_dict,
                            resolved_shadow_reason or "candidate_action_shadow_validation",
                        )
                        if shadow_registration
                        else None
                    ),
                )
                registered.append(path_run_id)
                log.info(
                    f"[PathB {'shadow ' if shadow_registration else ''}plan] {market} {key} "
                    f"zone={plan.buy_zone_low:g}-{plan.buy_zone_high:g} "
                    f"target={plan.sell_target:g} stop={plan.stop_loss:g} conf={plan.confidence:.2f}"
                )

        if shadow_only:
            trade_ready, price_targets, origin_map = self._pathb_registration_inputs(
                market,
                meta,
                shadow_only=True,
            )
            _register_batch(
                trade_ready,
                price_targets,
                origin_map,
                shadow_registration=True,
                shadow_reason="market_live_disabled",
            )
        else:
            trade_ready, price_targets, origin_map = self._pathb_registration_inputs(
                market,
                meta,
                shadow_only=False,
            )
            _register_batch(trade_ready, price_targets, origin_map)
            if self._market_shadow_plan_enabled(market):
                shadow_tickers, shadow_targets, shadow_origins = self._pathb_shadow_registration_inputs(market, meta)
                live_keys = {self._ticker_key(market, ticker) for ticker in trade_ready}
                if live_keys:
                    shadow_tickers = [
                        ticker
                        for ticker in shadow_tickers
                        if self._ticker_key(market, ticker) not in live_keys
                    ]
                _register_batch(
                    shadow_tickers,
                    shadow_targets,
                    shadow_origins,
                    shadow_registration=True,
                )
        if missing_price_targets:
            log.warning(
                f"[PathB plan missing] {market} trade_ready without price_targets: "
                f"{missing_price_targets}"
            )
        return registered

    def _scan_shadow_waiting_entries(self, market: str) -> int:
        market_key = str(market or "").upper()
        hit_count = 0
        for run in self.store.path_runs_for_session(
            market=market_key,
            runtime_mode=self.mode,
            session_date=self._session_date(market_key),
            status="SHADOW_WAITING",
            path_type="claude_price",
        ):
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            current = self._current_native_price(market_key, plan.ticker)
            if current <= 0:
                continue
            self._audit_pathb_price_seen(plan, current, source="pathb:shadow_waiting_scan")
            cancel_above = float(plan.cancel_if_open_above or 0)
            if cancel_above > 0 and current > cancel_above:
                self.store.update_path_run(
                    plan.path_run_id,
                    plan={
                        "shadow_cancel_reason": "cancel_if_open_above",
                        "shadow_cancel_trigger_price": float(current),
                        "shadow_order_submitted": False,
                    },
                    merge_plan=True,
                )
                self.adapter.mark_shadow_cancelled(
                    plan.path_run_id,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market_key),
                    reason="shadow_cancel_if_open_above",
                )
                continue
            if not (float(plan.buy_zone_low or 0) <= current <= float(plan.buy_zone_high or 0)):
                continue
            self.adapter.mark_shadow_hit(
                plan.path_run_id,
                price=current,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market_key),
            )
            hit_count += 1
            log.info(
                f"[PathB shadow hit] {market_key} {plan.ticker} "
                f"price={current:g} zone={plan.buy_zone_low:g}-{plan.buy_zone_high:g}"
            )
        return hit_count

    def scan_waiting_entries(self, market: str, *, force: bool = False) -> None:
        market = str(market or "").upper()
        if not self.is_enabled():
            return
        if not force and not self._scan_due(self._last_entry_scan_at, market, 10):
            return
        self._last_entry_scan_at[market] = time.time()
        self.reconcile_order_unknowns(market, force=False)
        self.reconcile_buy_pending_cancel_above(market, force=False)
        self.process_miss_quality_followups(market)
        if not self._market_live_enabled(market):
            self.cancel_unsent_waiting(market, reason="PATHB_MANUALLY_DISABLED", include_shadow=False)
            if self._market_shadow_plan_enabled(market):
                self._scan_shadow_waiting_entries(market)
                return
            self.cancel_unsent_waiting(market, reason="PATHB_MANUALLY_DISABLED", include_shadow=True)
            return
        kr_new_entry_blocked = (
            market == "KR"
            and str(os.getenv("KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK", "false")).strip().lower()
            in {"1", "true", "yes", "y", "on"}
        )
        if self._market_shadow_plan_enabled(market):
            self._scan_shadow_waiting_entries(market)
        entry_gate = self._new_buy_block_state(market, strategy="path_b")
        if not bool(entry_gate.get("allowed", True)):
            self._audit_entry_scan_blocked(market, entry_gate)
            self._log_entry_scan_blocked(market, entry_gate)
            return
        kr_blocked_tickers: list[str] = []
        for run in self.adapter.get_waiting_runs(market, self.mode, self._session_date(market)):
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            current = self._current_native_price(market, plan.ticker)
            if current <= 0:
                continue
            self._audit_pathb_price_seen(plan, current, source="pathb:waiting_scan")
            signal = self.adapter.check_entry(plan.path_run_id, current)
            if signal.reason == "cancel_if_open_above":
                self.store.update_path_run(
                    plan.path_run_id,
                    plan={
                        "cancel_trigger_price": float(current),
                        "cancel_trigger_source": "waiting_scan",
                        "cancel_trigger_at": datetime.now(KST).isoformat(timespec="seconds"),
                        "market_close_at": self._market_close_at(market),
                    },
                    merge_plan=True,
                )
                self.adapter.cancel_plan(
                    plan.path_run_id,
                    reason="cancel_if_open_above",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
                continue
            if not signal.signal:
                continue
            if kr_new_entry_blocked:
                kr_blocked_tickers.append(plan.ticker)
                self._record_blocked(
                    market,
                    plan.ticker,
                    plan.decision_id,
                    "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK",
                    {
                        "stage": "pathb_waiting_scan",
                        "scope": "market",
                        "reason": "kr_claude_price_new_entry_block",
                        "price": float(current or 0.0),
                        "limit_price": float(signal.limit_price or 0.0),
                        "signal_reason": str(signal.reason or ""),
                    },
                    plan.path_run_id,
                )
                continue
            # See register_from_selection_meta policy note: a waiting Claude price
            # plan may originate from PULLBACK_WAIT rather than PathA trade_ready.
            self._audit_pathb_zone_hit(plan, signal)
            self._submit_buy(plan, signal)
        if kr_blocked_tickers:
            sample = ",".join(kr_blocked_tickers[:8])
            log.warning(
                f"[PathB entry scan blocked] KR KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK "
                f"count={len(kr_blocked_tickers)} tickers={sample}"
            )

    def reconcile_buy_pending_cancel_above(self, market: str, *, force: bool = False) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "checked": 0,
            "cancel_requested": 0,
            "cancel_confirmed": 0,
            "filled": 0,
            "still_open": 0,
            "order_unknown": 0,
            "skipped": 0,
            "errors": [],
        }
        for run in self._pending_buy_runs(market_key):
            try:
                result = self._reconcile_buy_pending_cancel_above_run(run, market_key)
                summary["checked"] += 1
                summary[result] = int(summary.get(result, 0) or 0) + 1
            except Exception as exc:
                summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        if summary["checked"] or summary["errors"]:
            log.info(f"[PathB BUY cancel_above reconcile] {summary}")
        return summary

    def process_miss_quality_followups(self, market: str = "", *, limit: int = 20) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {"checked": 0, "filled": 0, "insufficient_quotes": 0, "market_closed": 0, "quote_error": 0}
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            rows = self.store.pending_pathb_miss_quality(now_iso=now_iso, limit=int(limit or 20))
        except Exception as exc:
            log.debug(f"[PathB miss-quality] pending query failed: {exc}")
            return summary
        for row in rows:
            if market_key and str(row.get("market", "") or "").upper() != market_key:
                continue
            summary["checked"] += 1
            try:
                status = self._fill_miss_quality_followup(row)
            except Exception as exc:
                status = "quote_error"
                try:
                    self.store.update_pathb_miss_quality_followup(
                        int(row.get("id") or 0),
                        followup_status=status,
                        payload={**(row.get("payload") or {}), "followup_error": str(exc)},
                    )
                except Exception:
                    pass
            summary[status] = int(summary.get(status, 0) or 0) + 1
        if summary["checked"]:
            log.info(f"[PathB miss-quality followup] {summary}")
        return summary

    def _fill_miss_quality_followup(self, row: dict[str, Any]) -> str:
        row_id = int(row.get("id") or 0)
        market = str(row.get("market", "") or "").upper()
        ticker = str(row.get("ticker", "") or "").strip()
        baseline = float(row.get("baseline_price") or 0)
        buy_zone_high = float(row.get("buy_zone_high") or 0)
        if row_id <= 0 or not market or not ticker or baseline <= 0:
            self.store.update_pathb_miss_quality_followup(
                row_id,
                followup_status="insufficient_quotes",
                payload={**(row.get("payload") or {}), "reason": "missing_baseline_or_identity"},
            )
            return "insufficient_quotes"

        due_at = self._parse_followup_time(row.get("followup_due_at"))
        close_at = self._parse_followup_time(row.get("market_close_at"))
        if close_at is not None and due_at is not None and due_at > close_at:
            self.store.update_pathb_miss_quality_followup(
                row_id,
                followup_status="market_closed",
                payload={**(row.get("payload") or {}), "reason": "followup_due_after_market_close"},
            )
            return "market_closed"

        samples = self._miss_quality_price_samples(market, ticker, row)
        sample_source = "post_open_history"
        if not samples:
            current = self._current_native_price(market, ticker)
            if current > 0:
                samples = [current]
                sample_source = "current_quote_only"
        if not samples:
            self.store.update_pathb_miss_quality_followup(
                row_id,
                followup_status="insufficient_quotes",
                payload={**(row.get("payload") or {}), "reason": "no_price_sample"},
            )
            return "insufficient_quotes"

        max_price = max(samples)
        min_price = min(samples)
        observed = samples[-1]
        mfe_pct = (max_price / baseline - 1.0) * 100.0
        mae_pct = (min_price / baseline - 1.0) * 100.0
        zone_reentered = None if buy_zone_high <= 0 else min_price <= buy_zone_high
        self.store.update_pathb_miss_quality_followup(
            row_id,
            followup_status="filled",
            zone_reentered_after_cancel=zone_reentered,
            mfe_30m_pct=float(mfe_pct),
            mae_30m_pct=float(mae_pct),
            observed_price_30m=float(observed),
            quote_sample_count=len(samples),
            payload={
                **(row.get("payload") or {}),
                "sample_source": sample_source,
                "baseline_price": baseline,
                "max_price_after_cancel": float(max_price),
                "min_price_after_cancel": float(min_price),
            },
        )
        return "filled"

    def _miss_quality_price_samples(self, market: str, ticker: str, row: dict[str, Any]) -> list[float]:
        key_func = getattr(self.bot, "_post_open_key", None)
        key = key_func(market, ticker) if callable(key_func) else f"{market}:{ticker.upper() if market == 'US' else ticker}"
        history = list((getattr(self.bot, "_post_open_price_history", {}) or {}).get(key, []) or [])
        if not history:
            return []
        start = self._parse_followup_time(row.get("cancelled_at"))
        end = self._parse_followup_time(row.get("followup_due_at"))
        samples: list[float] = []
        for item in history:
            try:
                ts = self._parse_followup_time((item or {}).get("ts"))
                price = float((item or {}).get("price") or 0)
            except Exception:
                continue
            if price <= 0 or ts is None:
                continue
            if start is not None and ts < start:
                continue
            if end is not None and ts > end:
                continue
            samples.append(price)
        return samples

    @staticmethod
    def _parse_followup_time(raw: Any) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except Exception:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST)

    def _market_close_at(self, market: str) -> str:
        market_key = str(market or "").upper()
        try:
            close_dt = self.bot._market_regular_close_dt(
                market_key,
                session_date=self._session_date(market_key),
            )
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=KST)
            return close_dt.astimezone(KST).isoformat(timespec="seconds")
        except Exception:
            return ""

    def scan_exits(self, market: str, *, force: bool = False) -> None:
        market = str(market or "").upper()
        if not self._exits_allowed():
            return
        if not force and not self._scan_due(self._last_exit_scan_at, market, 10):
            return
        self._last_exit_scan_at[market] = time.time()
        self._profit_review_calls_this_scan = 0
        self.reconcile_sell_pending(market, force=False)
        self.reconcile_filled_positions(market, force=False)
        minutes_to_close = self._minutes_to_close(market)
        for run in self._active_exit_runs_for_market(market):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id)
            if not pos:
                continue
            if str(run.get("status", "")) == "ORDER_UNKNOWN":
                recovered_run = self._recover_order_unknown_local_holding(run, plan, pos)
                if recovered_run is None:
                    continue
                run = recovered_run
            elif str(run.get("status", "")) in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}:
                recovered_run = self._recover_entry_pending_local_holding(run, plan, pos)
                if recovered_run is None:
                    continue
                run = recovered_run
            if str(run.get("status", "")) not in {"FILLED", "PARTIAL_FILLED"}:
                continue
            self._clear_stale_pathb_closing_lock(pos, market, plan.path_run_id)
            if self._pathb_sell_in_flight(run, pos):
                continue
            current = self._current_native_price(market, plan.ticker)
            if current <= 0:
                continue
            self._audit_pathb_price_seen(plan, current, source="pathb:exit_scan")
            hard_stop_price = self._native_hard_stop(pos, market)
            loss_cap_price = self._native_loss_cap_stop(pos, market)
            mfe_signal = self._pathb_mfe_breakeven_signal(
                plan,
                pos,
                current,
                hard_stop_price=hard_stop_price,
                loss_cap_price=loss_cap_price,
            )
            if mfe_signal is not None:
                exit_signal = mfe_signal
            elif (
                loss_cap_price is not None
                and loss_cap_price > 0
                and (hard_stop_price is None or loss_cap_price >= hard_stop_price)
                and current <= loss_cap_price
            ):
                exit_signal = ExitSignal(True, "loss_cap", "CLOSED_LOSS_CAP", current, plan.path_run_id)
            elif hard_stop_price is not None and hard_stop_price > 0 and current <= hard_stop_price:
                exit_signal = ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", current, plan.path_run_id)
            else:
                ladder_signal = self._pathb_profit_ladder_signal(
                    plan,
                    pos,
                    current,
                    market,
                    hard_stop_price=hard_stop_price,
                    loss_cap_price=loss_cap_price,
                )
                if ladder_signal is not None:
                    exit_signal = ladder_signal
                else:
                    policy_eval = self._evaluate_pathb_auto_sell_policy(plan, pos, current)
                    policy_action = str(policy_eval.get("action", "proceed") or "proceed")
                    if policy_action == "skip":
                        continue
                    if policy_action in {"sell", "recheck"} and isinstance(policy_eval.get("signal"), ExitSignal):
                        exit_signal = policy_eval["signal"]
                    else:
                        exit_signal = self.sell_manager.check_exit(plan.path_run_id, current, hard_stop_price=hard_stop_price)
            if not exit_signal.signal:
                self._maybe_trigger_profit_protection_review(plan, pos, current, market)
            if not exit_signal.signal:
                self._maybe_run_pre_close_carry_review(plan, pos, current, minutes_to_close)
            if not exit_signal.signal and self._pre_close_force_exit(plan.path_run_id, minutes_to_close):
                exit_signal = ExitSignal(
                    True,
                    "pre_close",
                    "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
                    current,
                    plan.path_run_id,
                )
            if exit_signal.signal:
                self._submit_sell(plan, pos, exit_signal)

    def on_buy_fill(self, order: dict[str, Any], *, position: dict[str, Any] | None = None, partial: bool = False) -> None:
        path_run_id = str(order.get("pathb_path_run_id", "") or "")
        if not path_run_id:
            return
        run = self.store.find_path_run(path_run_id)
        if not run:
            return
        qty = int(order.get("qty", 0) or 0)
        price = float(order.get("filled_price_native", 0) or order.get("raw_price", 0) or 0)
        if qty <= 0:
            return
        if partial:
            self.adapter.mark_partial_filled(
                path_run_id,
                price=price,
                qty=qty,
                execution_id=str(order.get("v2_execution_id", "") or order.get("order_no", "") or ""),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(str(order.get("market", run.get("market", "KR")) or "KR")),
            )
            self._audit_pathb_buy_fill(run, order, price=price, qty=qty, partial=True)
            self._record_pathb_buy_decision_event(run, order, price=price, qty=qty, partial=True)
            return
        self.adapter.mark_filled(
            path_run_id,
            price=price,
            qty=qty,
            execution_id=str(order.get("v2_execution_id", "") or order.get("order_no", "") or ""),
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(str(order.get("market", run.get("market", "KR")) or "KR")),
        )
        self._audit_pathb_buy_fill(run, order, price=price, qty=qty, partial=False)
        self._record_pathb_buy_decision_event(run, order, price=price, qty=qty, partial=False)

    def _record_pathb_buy_decision_event(
        self,
        run: dict[str, Any],
        order: dict[str, Any],
        *,
        price: float,
        qty: int,
        partial: bool,
    ) -> None:
        recorder = getattr(self.bot, "_record_decision_event", None)
        if not callable(recorder):
            return
        plan = self._plan_from_run(run)
        plan_json = run.get("plan") or {}
        market = str(order.get("market") or run.get("market") or (plan.market if plan else "") or "KR").upper()
        ticker = str(order.get("ticker") or run.get("ticker") or (plan.ticker if plan else "") or "").strip()
        if not ticker or int(qty or 0) <= 0:
            return
        path_run_id = str(order.get("pathb_path_run_id", "") or run.get("path_run_id", "") or (plan.path_run_id if plan else "") or "")
        decision_id = str(order.get("v2_decision_id", "") or run.get("decision_id", "") or (plan.decision_id if plan else "") or "")
        execution_id = str(order.get("v2_execution_id", "") or order.get("order_no", "") or "")
        selected_reason = str(
            order.get("selected_reason")
            or plan_json.get("entry_rationale")
            or plan_json.get("rationale")
            or "claude_price"
        )
        try:
            recorder(
                market,
                "buy_order",
                ticker,
                strategy=str(order.get("strategy") or "claude_price"),
                source_strategy=str(order.get("source_strategy") or "claude_price"),
                path_type="claude_price",
                pathb_path_run_id=path_run_id,
                v2_decision_id=decision_id,
                v2_execution_id=execution_id,
                qty=int(qty or 0),
                price_native=float(price or 0),
                price_krw=float(self._price_to_krw(float(price or 0), market) or 0),
                selected_reason=selected_reason,
                detail=f"pathb_run={path_run_id} partial={bool(partial)}",
                order_no=str(order.get("order_no", "") or ""),
                actual_fill_price=float(price or 0),
                broker_fill_confirmed=True,
                broker_filled_qty=int(qty or 0),
                broker_fill_source="pathb_broker_truth",
            )
        except Exception as exc:
            log.warning(f"[PathB BUY fill] decision event record failed {market} {ticker}: {exc}")

    def on_external_close(
        self,
        closed_trade: dict[str, Any],
        *,
        market: str,
        execution_id: str = "",
        close_reason: str = "",
        price: float = 0.0,
    ) -> bool:
        path_run_id = str(
            closed_trade.get("pathb_path_run_id", "")
            or closed_trade.get("path_run_id", "")
            or ""
        )
        if not path_run_id:
            return False
        run = self.store.find_path_run(path_run_id)
        if not run or str(run.get("path_type", "")) != "claude_price":
            return False
        market_key = str(market or run.get("market", "") or "").upper()
        if not market_key:
            return False
        close_reason = str(close_reason or closed_trade.get("close_reason", "") or "CLOSED_USER_MANUAL")
        price_native = float(price or closed_trade.get("display_exit_price", 0) or closed_trade.get("actual_exit_price", 0) or 0)
        pnl_pct = float(closed_trade.get("pnl_pct", 0) or 0)
        execution_id = str(execution_id or closed_trade.get("exit_execution_id", "") or closed_trade.get("order_no", "") or "")
        position_id = str(closed_trade.get("position_id", "") or "")
        if str(run.get("status", "")) != "CLOSED":
            self.sell_manager.mark_closed(
                path_run_id,
                close_reason=close_reason,
                price=price_native,
                pnl_pct=pnl_pct,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market_key),
                execution_id=execution_id,
                position_id=position_id,
            )
        self.store.update_path_run(
            path_run_id,
            plan={
                "external_close_synced": True,
                "external_close_synced_at": datetime.now(KST).isoformat(timespec="seconds"),
                "external_close_source": "generic_sell",
                "exit_execution_id": execution_id,
                "close_reason": close_reason,
            },
            merge_plan=True,
        )
        return True

    def cancel_waiting(self, market: str, *, reason: str, include_shadow: bool = True) -> int:
        count = 0
        market = str(market or "").upper()
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            status = str(run.get("status", ""))
            if status in {"SHADOW_WAITING", "SHADOW_HIT"}:
                if not include_shadow:
                    continue
                if self.adapter.mark_shadow_cancelled(
                    str(run.get("path_run_id", "")),
                    reason=reason,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                ):
                    count += 1
                continue
            if status not in {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED"}:
                continue
            self.adapter.cancel_plan(
                str(run.get("path_run_id", "")),
                reason=reason,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            count += 1
        return count

    def cancel_unsent_waiting(self, market: str, *, reason: str, include_shadow: bool = False) -> int:
        count = 0
        market = str(market or "").upper()
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            status = str(run.get("status", ""))
            if status in {"SHADOW_WAITING", "SHADOW_HIT"}:
                if not include_shadow:
                    continue
                if self.adapter.mark_shadow_cancelled(
                    str(run.get("path_run_id", "")),
                    reason=reason,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                ):
                    count += 1
                continue
            if status not in {"WAITING", "HIT"}:
                continue
            self.adapter.cancel_plan(
                str(run.get("path_run_id", "")),
                reason=reason,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            count += 1
        return count

    def cancel_waiting_for_ticker(self, market: str, ticker: str, *, reason: str) -> int:
        count = 0
        market = str(market or "").upper()
        target = self._ticker_key(market, ticker)
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if self._ticker_key(market, str(run.get("ticker", "") or "")) != target:
                continue
            status = str(run.get("status", ""))
            if status in {"SHADOW_WAITING", "SHADOW_HIT"}:
                if self.adapter.mark_shadow_cancelled(
                    str(run.get("path_run_id", "")),
                    reason=reason,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                ):
                    count += 1
                continue
            if status not in {"WAITING", "HIT"}:
                continue
            self.adapter.cancel_plan(
                str(run.get("path_run_id", "")),
                reason=reason,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            count += 1
        return count

    def expire_waiting_at_session_close(self, market: str) -> int:
        market_key = str(market or "").upper()
        count = self.sell_manager.expire_all_waiting(
            market_key,
            self.mode,
            self._session_date(market_key),
            brain_snapshot_id=self._brain_snapshot_id(market_key),
        )
        for status in ("SHADOW_WAITING", "SHADOW_HIT"):
            for run in self.store.path_runs_for_session(
                market=market_key,
                runtime_mode=self.mode,
                session_date=self._session_date(market_key),
                status=status,
                path_type="claude_price",
            ):
                if self.adapter.mark_shadow_cancelled(
                    str(run.get("path_run_id", "")),
                    reason="SESSION_CLOSE_EXPIRED",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market_key),
                ):
                    count += 1
        return count

    def finalize_carried_positions_at_session_close(self, market: str) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "checked": 0,
            "carried": 0,
            "missing_position": 0,
            "errors": [],
        }
        for status in ("FILLED", "PARTIAL_FILLED"):
            for run in self.store.path_runs_for_session(
                market=market_key,
                runtime_mode=self.mode,
                session_date=self._session_date(market_key),
                status=status,
                path_type="claude_price",
            ):
                plan_json = run.get("plan") or {}
                if str(plan_json.get("carry_decision", "") or "").upper() != "CARRY":
                    continue
                summary["checked"] += 1
                try:
                    plan = self._plan_from_run(run)
                    if plan is None:
                        summary["errors"].append(f"{run.get('path_run_id', '?')}:invalid_plan")
                        continue
                    pos = self._find_position(market_key, plan.ticker, path_run_id=plan.path_run_id)
                    if not pos:
                        summary["missing_position"] += 1
                        continue
                    pos["carry_source"] = "pathb_preclose"
                    pos["origin_path_run_id"] = plan.path_run_id
                    pos["buy_path"] = "path_b"
                    pos.setdefault("path_type", "claude_price")
                    pos.setdefault("pathb_path_run_id", plan.path_run_id)
                    self.store.update_path_run(
                        plan.path_run_id,
                        status="CARRIED_OUT",
                        plan={
                            "carried_at_session_close": datetime.now(KST).isoformat(timespec="seconds"),
                            "carry_status": "carried_out",
                        },
                        merge_plan=True,
                    )
                    summary["carried"] += 1
                except Exception as exc:
                    summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        if summary["carried"]:
            self._save_positions_if_possible()
            log.warning(f"[PathB carry session_close] {summary}")
        return summary

    def close_all_open(self, market: str, *, reason: str = "pathb_closeall") -> int:
        count = 0
        market = str(market or "").upper()
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price" or str(run.get("status", "")) not in {"FILLED", "PARTIAL_FILLED"}:
                continue
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id)
            if not pos:
                continue
            current = self._current_native_price(market, plan.ticker) or float(pos.get("display_current_price", 0) or 0)
            signal = ExitSignal(True, reason, "CLOSED_CLAUDE_PRICE_PRE_CLOSE", current, plan.path_run_id)
            if self._submit_sell(plan, pos, signal):
                count += 1
        return count

    def recover_on_startup(self) -> dict[str, Any]:
        """
        Re-attach active Path B state after a process restart.

        This is intentionally conservative. If a Path B order was sent but the
        local pending order and local/broker-restored position are both missing,
        keep the system from placing duplicate entries by escalating that path
        run to ORDER_UNKNOWN.
        """
        summary: dict[str, Any] = {
            "recovered_waiting": 0,
            "recovered_pending": 0,
            "recovered_positions": 0,
            "order_unknown": 0,
            "missing_positions": 0,
            "errors": [],
        }
        active_statuses = {
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
        }
        for market in ("KR", "US"):
            try:
                runs = self.store.path_runs_for_session(
                    market=market,
                    runtime_mode=self.mode,
                    session_date=self._session_date(market),
                    path_type="claude_price",
                )
            except Exception as exc:
                summary["errors"].append(f"{market}:load:{exc}")
                continue
            for run in runs:
                status = str(run.get("status") or "")
                if status not in active_statuses:
                    continue
                path_run_id = str(run.get("path_run_id", "") or "")
                plan = self._plan_from_run(run)
                if not path_run_id or plan is None:
                    summary["errors"].append(f"{market}:invalid_run:{path_run_id or '?'}")
                    continue
                if status in {"WAITING", "HIT"}:
                    summary["recovered_waiting"] += 1
                    continue
                if status == "ORDER_UNKNOWN":
                    summary["order_unknown"] += 1
                    continue

                pending = self._find_pending_order(market, plan.ticker, path_run_id=path_run_id)
                pos = self._find_position(market, plan.ticker, path_run_id=path_run_id)
                if pending is not None:
                    self._attach_pathb_order_metadata(pending, plan)
                    summary["recovered_pending"] += 1
                if pos is None:
                    pos = self._find_position(market, plan.ticker)
                if pos is not None:
                    self._attach_pathb_position_metadata(pos, plan)
                    if status in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}:
                        self._recover_entry_pending_local_holding(run, plan, pos)
                    summary["recovered_positions"] += 1
                    continue

                if status in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"} and pending is None:
                    self.adapter.mark_order_unknown(
                        path_run_id,
                        detail="startup_recovery_missing_pending_and_position",
                        runtime_mode=self.mode,
                        brain_snapshot_id=self._brain_snapshot_id(market),
                    )
                    summary["order_unknown"] += 1
                elif status in {"FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}:
                    # Do not downgrade a filled/selling Path B run before broker ccld
                    # truth is checked. A completed sell may have removed the local
                    # position already, and the broker fill history should decide
                    # whether the run is CLOSED or still ambiguous.
                    summary["missing_positions"] += 1
        if summary["recovered_positions"]:
            try:
                self.bot._save_positions()
            except Exception:
                pass
        for market in ("KR", "US"):
            try:
                self.refresh_broker_truth(market, force=True)
                self.reconcile_sell_pending(market, force=True)
                self.reconcile_filled_positions(market, force=True)
                self.reconcile_order_unknowns(market, force=True)
            except Exception as exc:
                summary["errors"].append(f"{market}:broker_truth_reconcile:{exc}")
        log.info(f"[PathB startup recovery] {summary}")
        return summary

    def _submit_buy(self, plan: PricePlan, signal: EntrySignal) -> bool:
        market = plan.market
        if self._plan_shadow_only(plan):
            self._record_blocked(
                market,
                plan.ticker,
                plan.decision_id,
                "PATHB_SHADOW_ONLY",
                {"shadow_only": True, "order_submitted": False, "stage": "pathb_submit_buy"},
                plan.path_run_id,
            )
            log.warning(f"[PathB shadow blocked] {market} {plan.ticker} submit_buy ignored path_run_id={plan.path_run_id}")
            return False
        if not self._market_live_enabled(market):
            self._record_blocked(
                market,
                plan.ticker,
                plan.decision_id,
                "PATHB_MANUALLY_DISABLED",
                {"market_live_enabled": False, "paper_only": True},
                plan.path_run_id,
            )
            self.adapter.cancel_plan(
                plan.path_run_id,
                reason="PATHB_MANUALLY_DISABLED",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            return False
        entry_gate = self._new_buy_block_state(market, plan.ticker, strategy="path_b")
        if not bool(entry_gate.get("allowed", True)):
            reason = str(entry_gate.get("reason") or "MARKET_CLOSED")
            self._record_blocked(market, plan.ticker, plan.decision_id, reason, entry_gate, plan.path_run_id)
            return False
        reentry = self.reentry_guard.evaluate(
            market=market,
            runtime_mode=self.mode,
            session_date=plan.session_date,
            ticker=plan.ticker,
            now=datetime.now(KST),
        )
        if not reentry.allowed:
            self._record_blocked(
                market,
                plan.ticker,
                plan.decision_id,
                reentry.reason_code,
                {
                    **(reentry.details or {}),
                    "message": reentry.message,
                    "stage": "pathb_same_day_reentry",
                },
                plan.path_run_id,
            )
            self.adapter.cancel_plan(
                plan.path_run_id,
                reason=reentry.reason_code,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            return False
        risk_price_krw = self._price_to_krw(signal.limit_price, market)
        cash_krw = float(getattr(getattr(self.bot, "risk", None), "cash", 0) or 0)
        min_order_krw = self._pathb_min_order_krw(market)
        qty = self._pathb_qty(market, risk_price_krw, cash_krw=cash_krw)
        order_cost = float(qty) * float(risk_price_krw)
        realized_daily_pnl_pct = self._daily_pnl_pct(market)
        equity_daily_pnl_pct = self._equity_daily_pnl_pct(market)
        ctx = SafetyContext(
            market=market,
            runtime_mode=self.mode,
            ticker=plan.ticker,
            price_krw=risk_price_krw,
            qty=qty,
            order_cost_krw=order_cost,
            cash_krw=cash_krw,
            min_order_krw=float(min_order_krw),
            positions=list(getattr(getattr(self.bot, "risk", None), "positions", []) or []),
            pending_orders=list(getattr(self.bot, "pending_orders", []) or []),
            daily_entry_count=self._base_daily_entry_count(market),
            max_daily_entries=self._base_max_daily_entries(market),
            daily_pnl_pct=realized_daily_pnl_pct,
            daily_pnl_basis="realized",
            realized_daily_pnl_pct=realized_daily_pnl_pct,
            equity_daily_pnl_pct=equity_daily_pnl_pct,
            broker_trust_level=self._broker_trust_level(market),
            market_open=bool(getattr(self.bot, "session_active", False)),
            last_market_data_at=datetime.now(KST).isoformat(),
            stopped_tickers=set(getattr(self.bot, "_v2_same_day_stop_tickers", {}).get(market, set()) or set()),
            order_unknown_blocked=self._order_unknown_blocked(market),
        )
        decision = self.safety_gate.evaluate(
            ctx,
            plan=plan,
            patha_holding=self._patha_holding(market, plan.ticker),
            pathb_holding=bool(self._active_path_for_ticker(market, plan.ticker, exclude=plan.path_run_id)),
            pathb_open_positions=self._pathb_open_position_count(market),
            pathb_daily_count=self._pathb_daily_count(market),
            manually_disabled=not self.control_store.load().enabled,
            order_unknown_blocked=self._order_unknown_blocked(market),
        )
        if not decision.passed:
            self._record_blocked(market, plan.ticker, plan.decision_id, decision.reason_code, decision.details, plan.path_run_id)
            self.adapter.cancel_plan(
                plan.path_run_id,
                reason=decision.reason_code,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            return False
        submit_gate = self._kr_pathb_submit_gate(plan, signal)
        if submit_gate.get("allowed") is False:
            reason = str(submit_gate.get("reason") or "KR_PATHB_SUBMIT_GUARD_BLOCKED")
            self._record_blocked(
                market,
                plan.ticker,
                plan.decision_id,
                reason,
                submit_gate,
                plan.path_run_id,
            )
            if bool(submit_gate.get("cancel_plan", True)):
                self.adapter.cancel_plan(
                    plan.path_run_id,
                    reason=reason,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
            return False
        self.adapter.mark_hit(
            plan.path_run_id,
            price=signal.price,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        try:
            pre = precheck_order(plan.ticker, qty, signal.limit_price, "buy", _bot_token(self.bot, market), market=market)
        except Exception as exc:
            self._record_blocked(market, plan.ticker, plan.decision_id, "BROKER_UNTRUSTED", {"precheck_exception": str(exc)}, plan.path_run_id)
            return False
        if not pre.get("ok"):
            self._record_blocked(market, plan.ticker, plan.decision_id, "BROKER_UNTRUSTED", {"precheck": pre}, plan.path_run_id)
            self.adapter.cancel_plan(
                plan.path_run_id,
                reason="precheck_failed",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            return False
        try:
            result = place_order(plan.ticker, qty, signal.limit_price, "buy", _bot_token(self.bot, market), market=market)
        except Exception as exc:
            self.adapter.mark_order_unknown(
                plan.path_run_id,
                detail=f"buy_order_exception:{exc}",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            self.reconcile_order_unknowns(market, force=True, path_run_id=plan.path_run_id)
            return False
        execution_id = str(result.get("order_no", "") or f"pathb_{market}_{plan.ticker}_{int(time.time())}")
        if not result.get("success"):
            self.adapter.mark_order_unknown(
                plan.path_run_id,
                detail=str(result.get("msg", "") or "buy_order_rejected"),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            self.reconcile_order_unknowns(market, force=True, path_run_id=plan.path_run_id)
            return False
        self.adapter.mark_order_sent(
            plan.path_run_id,
            execution_id=execution_id,
            price=signal.limit_price,
            qty=qty,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        self.adapter.mark_order_acked(
            plan.path_run_id,
            execution_id=execution_id,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        self.bot._add_pending_order({
            "order_no": execution_id,
            "ticker": plan.ticker,
            "name": self._ticker_name(plan.ticker, market),
            "market": market,
            "qty": int(qty),
            "raw_price": float(signal.limit_price),
            "risk_price_krw": float(risk_price_krw),
            "strategy": "claude_price",
            "source_strategy": "claude_price",
            "tp_pct": max(0.001, (plan.sell_target / signal.limit_price) - 1.0),
            "sl_pct": max(0.001, 1.0 - (plan.stop_loss / signal.limit_price)),
            "max_hold": 1 if bool(self.config.pathb_intraday_only) else int(plan.hold_days),
            "created_at": datetime.now(KST).isoformat(),
            "session_date": plan.session_date,
            "decision_id": -1,
            "v2_decision_id": plan.decision_id,
            "v2_execution_id": execution_id,
            "path_type": "claude_price",
            "pathb_path_run_id": plan.path_run_id,
            "pathb_plan": plan.to_dict(),
            "original_order_cost_krw": order_cost,
            "adjusted_order_cost_krw": order_cost,
        })
        self._audit_pathb_buy_sent(
            plan,
            signal,
            qty=qty,
            order_no=execution_id,
            risk_price_krw=risk_price_krw,
            order_cost_krw=order_cost,
        )
        try:
            self.bot._block_entry(plan.ticker, 3, "pathb_buy_placed")
        except Exception:
            pass
        try:
            buy_order_alert(
                market=market,
                ticker=plan.ticker,
                qty=qty,
                order_no=execution_id,
                detail=f"PathB Claude Price live buy @ {signal.limit_price:g}",
                name=self._ticker_name(plan.ticker, market),
                buy_path="path_b",
            )
        except Exception:
            pass
        log.warning(f"[PathB LIVE BUY] {market} {plan.ticker} qty={qty} limit={signal.limit_price:g} order={execution_id}")
        return True

    @staticmethod
    def _pathb_sell_review_required(signal: ExitSignal) -> bool:
        reason_key = str(signal.reason or "").strip().lower()
        final_policy_exit_reasons = {
            "policy_protective_stop",
            "policy_hard_stop",
            "policy_forced_sell",
        }
        if reason_key in final_policy_exit_reasons:
            return False
        if _env_bool("CLAUDE_REVIEW_ALL_AUTOMATED_SELLS", False):
            return reason_key not in {
                "pathb_kill",
                "pathb_closeall",
                "operator_kill",
            }
        return reason_key not in {
            "pathb_kill",
            "pathb_closeall",
            "operator_kill",
            "profit_ladder",
            "policy_protective_stop",
            "policy_hard_stop",
            "policy_forced_sell",
        }

    @staticmethod
    def _pathb_hold_policy_mode() -> str:
        raw = str(os.getenv("PATHB_HOLD_POLICY_MODE", "enforce") or "enforce").strip().lower()
        return raw if raw in {"off", "shadow", "enforce"} else "enforce"

    @staticmethod
    def _pathb_auto_sell_review_default_policy(signal: ExitSignal) -> str:
        reason_key = str(signal.reason or "").strip().lower()
        close_reason = str(signal.close_reason or "").strip().upper()
        if reason_key == "loss_cap" or close_reason == "CLOSED_LOSS_CAP":
            return (
                "This loss-cap alert is reviewable, but serious. SELL unless fresh evidence shows "
                "the thesis is intact, the loss remains controlled, and a concrete protective_stop, "
                "recover_above, invalid_if, and next_review_min are provided."
            )
        if reason_key in {"hard_stop", "policy_hard_stop"} or close_reason == "CLOSED_HARD_STOP":
            return (
                "This hard-stop alert is reviewable, but serious. SELL unless fresh evidence shows "
                "a transient stop check with bounded recovery risk. HOLD requires protective_stop, "
                "hard_stop, recover_above, invalid_if, and near next_review_min."
            )
        if reason_key in {"profit_ladder", "profit_floor", "trail_stop"} or close_reason == "CLOSED_PROFIT_LADDER":
            return (
                "This profit-protection sell is reviewable. SELL if giveback risk now outweighs "
                "remaining upside. HOLD only when upside remains attractive and the retained "
                "profit/loss floor is explicit."
            )
        return (
            "SELL only if this PathB automatic sell signal remains valid after fresh review. "
            "Return HOLD when evidence is stale, ambiguous, or the position should be rechecked later."
        )

    @staticmethod
    def _pathb_review_position_pnl_pct(pos: dict[str, Any], market: str, current_native: float = 0.0) -> float:
        try:
            if str(market or "").upper() == "US":
                entry = float(pos.get("display_avg_price") or pos.get("entry_native") or 0)
            else:
                entry = float(pos.get("entry") or 0)
            current = float(current_native or pos.get("display_current_price") or pos.get("current_price") or 0)
            if entry > 0 and current > 0:
                return (current / entry - 1.0) * 100.0
        except Exception:
            pass
        return 0.0

    @staticmethod
    def _pathb_auto_sell_review_force_sell_threshold_pct(market: str) -> float:
        market_key = str(market or "").upper()
        raw = os.getenv(
            f"{market_key}_AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT",
            os.getenv("AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT", "2.5"),
        )
        try:
            return max(0.0, float(raw or 0))
        except Exception:
            return 2.5

    def _pathb_auto_sell_review_force_sell_required(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        signal: ExitSignal,
        current_native: float,
    ) -> tuple[bool, str]:
        reason_key = str(signal.reason or "").strip().lower()
        close_reason = str(signal.close_reason or "").strip().upper()
        if reason_key in {"pathb_kill", "pathb_closeall", "operator_kill"}:
            return True, f"catastrophic_exit:{reason_key}"
        if reason_key not in {"loss_cap", "hard_stop", "policy_hard_stop"} and close_reason not in {
            "CLOSED_LOSS_CAP",
            "CLOSED_HARD_STOP",
            "CLOSED_CLAUDE_PRICE_STOP",
        }:
            return False, ""
        threshold = self._pathb_auto_sell_review_force_sell_threshold_pct(plan.market)
        if threshold <= 0:
            return False, ""
        pnl_pct = self._pathb_review_position_pnl_pct(pos, plan.market, current_native=current_native)
        if pnl_pct <= -threshold:
            return True, f"loss {pnl_pct:.2f}% <= force threshold -{threshold:.2f}%"
        return False, ""

    @staticmethod
    def _parse_kst_iso(raw: Any) -> datetime | None:
        text = str(raw or "").strip()
        if not text:
            return None
        try:
            value = datetime.fromisoformat(text)
        except Exception:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=KST)
        return value.astimezone(KST)

    @staticmethod
    def _policy_float(value: Any) -> float:
        try:
            if isinstance(value, str):
                value = value.replace(",", "").replace("$", "").strip()
            return float(value or 0)
        except Exception:
            return 0.0

    @staticmethod
    def _policy_int(value: Any) -> int:
        try:
            return int(float(value or 0))
        except Exception:
            return 0

    def _round_policy_price(self, price: Any, market: str, *, direction: str) -> float:
        value = self._policy_float(price)
        if value <= 0:
            return 0.0
        market_key = str(market or "").upper()
        if market_key == "KR":
            return round_up_to_kr_tick(value) if direction == "up" else round_down_to_kr_tick(value)
        return round_up_to_cent(value) if direction == "up" else round_down_to_cent(value)

    def _policy_valid_minutes(self, advice: dict[str, Any]) -> int:
        raw = self._policy_int((advice or {}).get("valid_for_min"))
        if raw <= 0:
            raw = self._policy_int((advice or {}).get("next_review_min"))
        if raw <= 0:
            raw = self.HOLD_POLICY_DEFAULT_VALID_MINUTES
        return max(self.HOLD_POLICY_MIN_VALID_MINUTES, min(self.HOLD_POLICY_MAX_VALID_MINUTES, raw))

    def _pathb_auto_sell_policy_from_advice(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        signal: ExitSignal,
        advice: dict[str, Any],
        current_native: float,
        *,
        now: datetime,
    ) -> tuple[dict[str, Any], str]:
        if not isinstance(advice, dict) or bool(advice.get("fallback", False)):
            return {}, "fallback_or_invalid_advice"
        action = str((advice or {}).get("action", "") or "").upper()
        if action not in {"HOLD", "SELL"}:
            return {}, "action_not_hold_or_sell"
        close_reason = str(signal.close_reason or "").strip().upper()
        signal_reason = str(signal.reason or "").strip().lower()
        current = float(current_native or 0)
        if current <= 0:
            return {}, "invalid_current_price"
        market = str(plan.market or "").upper()
        valid_for_min = self._policy_valid_minutes(advice)
        valid_until = now + timedelta(minutes=valid_for_min)
        reask_after_min = self._policy_int(advice.get("reask_after_min"))
        if reask_after_min <= 0:
            reask_after_min = valid_for_min
        reask_after_min = max(self.HOLD_POLICY_MIN_VALID_MINUTES, min(valid_for_min, reask_after_min))
        reask_after_at = now + timedelta(minutes=reask_after_min)
        base: dict[str, Any] = {
            "version": 1,
            "status": "active",
            "source": "hold_advisor",
            "created_at": now.isoformat(timespec="seconds"),
            "valid_until": valid_until.isoformat(timespec="seconds"),
            "valid_for_min": valid_for_min,
            "reask_after_at": reask_after_at.isoformat(timespec="seconds"),
            "reask_after_min": reask_after_min,
            "signal_reason": str(signal.reason or ""),
            "signal_close_reason": close_reason,
            "created_price": current,
            "peak_price": current,
            "original_sell_target": float(plan.sell_target or 0),
            "original_stop_loss": float(plan.stop_loss or 0),
            "confidence": self._policy_float(advice.get("confidence")),
            "reason": self._hold_advice_reason(advice),
            "invalid_if": str(advice.get("invalid_if", "") or "")[:240],
            "max_rechecks": max(0, min(8, self._policy_int(advice.get("max_rechecks")))),
        }
        if action == "SELL":
            forced_close_reason = str(advice.get("close_reason") or close_reason or "CLOSED_CLAUDE_SELL").strip().upper()
            if not forced_close_reason.startswith("CLOSED_"):
                forced_close_reason = "CLOSED_CLAUDE_SELL"
            return {
                **base,
                "mode": "forced_sell",
                "source": str(advice.get("source", "") or "hold_advisor_sell")[:80],
                "force_sell": True,
                "close_reason": forced_close_reason,
                "signal_close_reason": forced_close_reason,
            }, ""

        protective_hold_reasons = {
            "trail_stop",
            "profit_floor",
            "stop_loss",
            "soft_exit_floor_price",
        }
        stop_recovery_close_reasons = {
            "CLOSED_CLAUDE_PRICE_STOP",
            "CLOSED_LOSS_CAP",
            "CLOSED_HARD_STOP",
        }
        if close_reason not in {
            "CLOSED_CLAUDE_PRICE_TARGET",
            *stop_recovery_close_reasons,
            "CLOSED_PROFIT_FLOOR",
            "CLOSED_TRAILING_STOP",
        } and signal_reason not in protective_hold_reasons:
            return {}, "unsupported_close_reason"
        if close_reason == "CLOSED_CLAUDE_PRICE_TARGET":
            revised_target = self._round_policy_price(advice.get("revised_sell_target"), market, direction="up")
            if revised_target <= current:
                return {}, "revised_target_not_above_current"
            protective_stop = self._round_policy_price(advice.get("protective_stop"), market, direction="down")
            if protective_stop <= 0:
                return {}, "protective_stop_missing"
            if protective_stop >= current:
                return {}, "protective_stop_not_below_current"
            original_stop = float(plan.stop_loss or 0)
            if original_stop > 0 and protective_stop < original_stop:
                return {}, "protective_stop_looser_than_plan_stop"
            drawdown_trigger = self._policy_float(advice.get("reask_drawdown_from_peak_pct"))
            if drawdown_trigger <= 0:
                drawdown_trigger = 0.8
            reask_if_price_above = self._round_policy_price(
                advice.get("reask_if_price_above") or revised_target,
                market,
                direction="up",
            )
            return {
                **base,
                "mode": "target_extension",
                "revised_sell_target": revised_target,
                "protective_stop": protective_stop,
                "trail_pct": self._policy_float(advice.get("trail_pct")),
                "reask_drawdown_from_peak_pct": max(0.1, min(10.0, drawdown_trigger)),
                "reask_if_price_above": reask_if_price_above,
            }, ""

        if close_reason not in stop_recovery_close_reasons:
            protective_stop = self._round_policy_price(advice.get("protective_stop"), market, direction="down")
            if protective_stop <= 0:
                return {}, "protective_stop_missing"
            if protective_stop >= current:
                return {}, "protective_stop_not_below_current"
            plan_stop = float(plan.stop_loss or 0)
            if plan_stop > 0 and protective_stop <= plan_stop:
                return {}, "protective_stop_not_tighter_than_plan_stop"
            native_stop = self._native_hard_stop(pos, market)
            if native_stop is not None and native_stop > 0 and protective_stop <= native_stop:
                return {}, "trailing_already_tighter"
            hard_stop = self._round_policy_price(
                advice.get("hard_stop") or advice.get("protective_stop"),
                market,
                direction="down",
            )
            if hard_stop <= 0 or hard_stop > protective_stop:
                hard_stop = protective_stop
            return {
                **base,
                "mode": "protective_hold",
                "source": "pathb_exit_signal_review",
                "protective_stop": protective_stop,
                "hard_stop": hard_stop,
                "trail_release_threshold": protective_stop,
            }, ""

        hard_stop = self._round_policy_price(
            advice.get("hard_stop") or advice.get("protective_stop"),
            market,
            direction="down",
        )
        if hard_stop <= 0:
            return {}, "hard_stop_missing"
        if hard_stop >= current:
            return {}, "hard_stop_not_below_current"
        claude_stop = float(plan.stop_loss or 0)
        gap_cap = float(self.HOLD_POLICY_HARD_GAP_CAP.get(market, 0.02))
        if claude_stop > 0 and hard_stop < claude_stop * (1.0 - gap_cap):
            return {}, "hard_stop_gap_too_wide"
        recover_above = self._round_policy_price(advice.get("recover_above") or claude_stop, market, direction="up")
        if recover_above <= current:
            return {}, "recover_above_not_above_current"
        recovery_watch_min = self._policy_int(advice.get("recovery_watch_min"))
        if recovery_watch_min <= 0:
            recovery_watch_min = valid_for_min
        return {
            **base,
            "mode": "stop_recovery",
            "hard_stop": hard_stop,
            "recover_above": recover_above,
            "recovery_watch_min": max(1, min(valid_for_min, recovery_watch_min)),
            "stop_gap_pct": round(((claude_stop - hard_stop) / claude_stop) * 100.0, 4) if claude_stop > 0 else 0.0,
        }, ""

    def _mark_pathb_auto_sell_policy(self, path_run_id: str, **updates: Any) -> None:
        try:
            run = self.store.find_path_run(path_run_id) or {}
            plan_json = run.get("plan") or {}
            policy = dict(plan_json.get("auto_sell_policy") or {})
            if not policy:
                return
            policy.update(updates)
            self.store.update_path_run(path_run_id, plan={"auto_sell_policy": policy}, merge_plan=True)
        except Exception:
            pass

    def _set_pathb_auto_sell_policy(
        self,
        path_run_id: str,
        policy: dict[str, Any],
        *,
        merge: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(policy, dict) or not policy:
            return {"updated": False, "reason": "empty_policy"}
        try:
            run = self.store.find_path_run(path_run_id) or {}
            if not run:
                return {"updated": False, "reason": "path_run_not_found"}
            plan_json = run.get("plan") or {}
            existing = dict(plan_json.get("auto_sell_policy") or {}) if isinstance(plan_json, dict) else {}
            existing_status = str(existing.get("status", "") or "").lower()
            new_status = str(policy.get("status", "active") or "active").lower()
            existing_stop = self._policy_float(existing.get("protective_stop"))
            new_stop = self._policy_float(policy.get("protective_stop"))
            if existing_status == "active" and new_status == "active" and existing_stop > 0 and new_stop > 0:
                if existing_stop >= new_stop:
                    log.info(
                        f"[PathB protective_hold SKIP] reason=existing_policy_tighter "
                        f"run={path_run_id} existing_ps={existing_stop:g} new_ps={new_stop:g}"
                    )
                    return {"updated": False, "reason": "existing_policy_tighter", "policy": existing}
            next_policy = {**existing, **policy} if merge and existing else dict(policy)
            self.store.update_path_run(
                path_run_id,
                plan={
                    "auto_sell_policy": next_policy,
                    "auto_sell_policy_last_set_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "auto_sell_policy_last_set_mode": str(next_policy.get("mode", "") or ""),
                },
                merge_plan=True,
            )
            return {"updated": True, "reason": "policy_set", "policy": next_policy}
        except Exception as exc:
            log.warning(f"[PathB protective_hold SET failed] run={path_run_id} err={exc}")
            return {"updated": False, "reason": f"set_failed:{exc}"}

    def _protective_hold_valid_minutes(self, advice: dict[str, Any]) -> int:
        default_min = _env_int("PATHB_PROTECTIVE_HOLD_DEFAULT_VALID_MIN", 15)
        raw = self._policy_int((advice or {}).get("valid_for_min"))
        if raw <= 0:
            raw = self._policy_int((advice or {}).get("next_review_min"))
        if raw <= 0:
            raw = default_min
        min_default = self.HOLD_POLICY_MIN_VALID_MINUTES
        max_default = self.HOLD_POLICY_MAX_VALID_MINUTES
        min_valid = _env_int("PATHB_PROTECTIVE_HOLD_MIN_VALID_MIN", min_default)
        max_valid = _env_int("PATHB_PROTECTIVE_HOLD_MAX_VALID_MIN", max_default)
        if max_valid < min_valid:
            max_valid = min_valid
        return max(min_valid, min(max_valid, raw))

    def apply_general_hold_advice_policy(
        self,
        pos: dict[str, Any],
        market: str,
        advice: dict[str, Any],
        current_native: float,
    ) -> dict[str, Any]:
        try:
            if not isinstance(pos, dict):
                return {"updated": False, "reason": "invalid_position"}
            if not isinstance(advice, dict):
                return {"updated": False, "reason": "invalid_advice"}
            action = str(advice.get("action", "HOLD") or "HOLD").upper()
            if action not in {"HOLD", "SELL"}:
                return {"updated": False, "reason": "action_not_hold_or_sell"}
            path_run_id = str(pos.get("pathb_path_run_id") or pos.get("path_run_id") or "").strip()
            if not path_run_id:
                return {"updated": False, "reason": "not_pathb_position"}
            run = self.store.find_path_run(path_run_id) or {}
            if not run:
                return {"updated": False, "reason": "path_run_not_found"}
            if str(run.get("status", "") or "") not in {"FILLED", "PARTIAL_FILLED"}:
                return {"updated": False, "reason": "path_run_not_filled"}
            plan = self._plan_from_run(run)
            if plan is None:
                return {"updated": False, "reason": "invalid_plan"}

            market_key = str(market or plan.market or "").upper()
            current = float(current_native or 0)
            if current <= 0:
                current = self._current_native_price(market_key, plan.ticker)
            if current <= 0:
                return {"updated": False, "reason": "invalid_current_price"}
            if action == "SELL":
                sell_advice = {**advice, "source": str(advice.get("source", "") or "general_review_sell")}
                signal = ExitSignal(True, "policy_forced_sell", "CLOSED_CLAUDE_SELL", current, path_run_id)
                policy, reject_reason = self._pathb_auto_sell_policy_from_advice(
                    plan,
                    pos,
                    signal,
                    sell_advice,
                    current,
                    now=datetime.now(KST),
                )
                if not policy:
                    return {"updated": False, "reason": reject_reason or "forced_sell_policy_rejected"}
                result = self._set_pathb_auto_sell_policy(path_run_id, policy)
                if result.get("updated"):
                    log.warning(
                        f"[PathB forced_sell SET] {market_key} {plan.ticker} "
                        f"valid_until={policy.get('valid_until', '')} reason={policy.get('reason', '')}"
                    )
                else:
                    log.info(
                        f"[PathB forced_sell SKIP] {market_key} {plan.ticker} "
                        f"reason={result.get('reason')}"
                    )
                return result

            protective_stop = self._round_policy_price(advice.get("protective_stop"), market_key, direction="down")
            if protective_stop <= 0:
                return {"updated": False, "reason": "protective_stop_missing"}
            min_distance = _env_float(
                "PATHB_PROTECTIVE_HOLD_MIN_DISTANCE_US" if market_key == "US" else "PATHB_PROTECTIVE_HOLD_MIN_DISTANCE_KR",
                0.003 if market_key == "US" else 0.005,
            )
            if protective_stop >= current * (1.0 - max(0.0, min_distance)):
                return {"updated": False, "reason": "protective_stop_too_close_or_above_current"}

            plan_stop = float(plan.stop_loss or 0)
            if plan_stop > 0 and protective_stop <= plan_stop:
                return {"updated": False, "reason": "protective_stop_not_tighter_than_plan_stop"}
            native_trailing = self._native_hard_stop(pos, market_key)
            if native_trailing is not None and native_trailing > 0 and protective_stop <= native_trailing:
                return {"updated": False, "reason": "trailing_already_tighter"}

            hard_stop = self._round_policy_price(
                advice.get("hard_stop") or advice.get("protective_stop"),
                market_key,
                direction="down",
            )
            if hard_stop <= 0:
                hard_stop = protective_stop
            if hard_stop > protective_stop:
                hard_stop = protective_stop

            plan_json = run.get("plan") or {}
            existing = dict(plan_json.get("auto_sell_policy") or {}) if isinstance(plan_json, dict) else {}
            existing_stop = self._policy_float(existing.get("protective_stop"))
            if str(existing.get("status", "") or "").lower() == "active" and existing_stop >= protective_stop > 0:
                return {"updated": False, "reason": "existing_policy_tighter", "policy": existing}

            now = datetime.now(KST)
            valid_for_min = self._protective_hold_valid_minutes(advice)
            valid_until = now + timedelta(minutes=valid_for_min)
            policy = {
                "version": 1,
                "status": "active",
                "mode": "protective_hold",
                "source": str(advice.get("source", "") or "general_review"),
                "protective_stop": protective_stop,
                "hard_stop": hard_stop,
                "created_at": now.isoformat(timespec="seconds"),
                "valid_until": valid_until.isoformat(timespec="seconds"),
                "valid_for_min": valid_for_min,
                "created_price": current,
                "original_stop_loss": plan_stop,
                "trail_release_threshold": protective_stop,
                "reason": self._hold_advice_reason(advice),
                "confidence": self._policy_float(advice.get("confidence")),
                "hold_mode": str(advice.get("hold_mode", "") or ""),
            }
            result = self._set_pathb_auto_sell_policy(path_run_id, policy)
            if result.get("updated"):
                log.warning(
                    f"[PathB protective_hold SET] {market_key} {plan.ticker} "
                    f"ps={protective_stop:g} hs={hard_stop:g} valid_until={policy['valid_until']}"
                )
            else:
                log.info(
                    f"[PathB protective_hold SKIP] {market_key} {plan.ticker} "
                    f"reason={result.get('reason')} ps={protective_stop:g}"
                )
            return result
        except Exception as exc:
            log.warning(f"[PathB protective_hold bridge failed] {market} err={exc}")
            return {"updated": False, "reason": f"bridge_failed:{exc}"}

    def _policy_skip_or_shadow(
        self,
        plan: PricePlan,
        policy: dict[str, Any],
        *,
        current: float,
        reason: str,
    ) -> dict[str, Any]:
        mode = self._pathb_hold_policy_mode()
        if mode == "shadow":
            payload = {
                "auto_sell_policy_shadow": {
                    "at": datetime.now(KST).isoformat(timespec="seconds"),
                    "reason": reason,
                    "current": float(current or 0),
                    "mode": str(policy.get("mode", "") or ""),
                }
            }
            try:
                self.store.update_path_run(plan.path_run_id, plan=payload, merge_plan=True)
            except Exception:
                pass
            log.info(f"[PathB hold policy shadow] {plan.market} {plan.ticker} would_skip={reason} price={current:g}")
            return {"action": "proceed", "reason": f"shadow_{reason}", "policy": policy}
        log.debug(f"[PathB hold policy skip] {plan.market} {plan.ticker} reason={reason} price={current:g}")
        return {"action": "skip", "reason": reason, "policy": policy}

    def _evaluate_pathb_auto_sell_policy(self, plan: PricePlan, pos: dict[str, Any], current: float) -> dict[str, Any]:
        run = self.store.find_path_run(plan.path_run_id) or {}
        plan_json = run.get("plan") or {}
        policy = plan_json.get("auto_sell_policy") if isinstance(plan_json, dict) else {}
        if not isinstance(policy, dict) or str(policy.get("status", "") or "") != "active":
            return {"action": "proceed", "reason": "no_active_policy"}
        now = datetime.now(KST)
        valid_until = self._parse_kst_iso(policy.get("valid_until"))
        if valid_until is None or valid_until <= now:
            self._mark_pathb_auto_sell_policy(
                plan.path_run_id,
                status="expired",
                expired_at=now.isoformat(timespec="seconds"),
            )
            return {"action": "proceed", "reason": "policy_expired", "policy": policy}
        current_price = float(current or 0)
        if current_price <= 0:
            return {"action": "proceed", "reason": "invalid_current_price", "policy": policy}
        mode = str(policy.get("mode", "") or "")
        if mode == "forced_sell":
            close_reason = str(policy.get("close_reason") or policy.get("signal_close_reason") or "CLOSED_CLAUDE_SELL").strip().upper()
            if not close_reason.startswith("CLOSED_"):
                close_reason = "CLOSED_CLAUDE_SELL"
            return {
                "action": "sell",
                "reason": "policy_forced_sell",
                "signal": ExitSignal(True, "policy_forced_sell", close_reason, current_price, plan.path_run_id),
                "policy": policy,
            }
        if self._pathb_hold_policy_mode() == "off":
            return {"action": "proceed", "reason": "policy_mode_off", "policy": policy}
        reask_at = self._parse_kst_iso(policy.get("reask_after_at"))
        if reask_at is not None and reask_at <= now:
            close_reason = "CLOSED_CLAUDE_PRICE_STOP" if mode == "stop_recovery" else "CLOSED_CLAUDE_PRICE_TARGET"
            return {
                "action": "recheck",
                "reason": "policy_time_decay",
                "signal": ExitSignal(True, "policy_recheck", close_reason, current_price, plan.path_run_id),
                "policy": policy,
            }

        if mode == "protective_hold":
            protective_stop = self._policy_float(policy.get("protective_stop"))
            hard_stop = self._policy_float(policy.get("hard_stop") or protective_stop)
            if protective_stop <= 0:
                self._mark_pathb_auto_sell_policy(
                    plan.path_run_id,
                    status="invalid",
                    invalidated_at=now.isoformat(timespec="seconds"),
                    invalid_reason="protective_stop_missing",
                )
                return {"action": "proceed", "reason": "protective_hold_invalid", "policy": policy}
            if hard_stop <= 0 or hard_stop > protective_stop:
                hard_stop = protective_stop
            native_stop = self._native_hard_stop(pos, plan.market)
            if native_stop is not None and native_stop > 0 and native_stop >= protective_stop:
                self._mark_pathb_auto_sell_policy(
                    plan.path_run_id,
                    status="released",
                    released_at=now.isoformat(timespec="seconds"),
                    released_price=current_price,
                    release_reason="trailing_caught_up",
                    trailing_stop=native_stop,
                )
                log.info(
                    f"[PathB protective_hold RELEASED] {plan.market} {plan.ticker} "
                    f"reason=trailing_caught_up sl={native_stop:g} ps={protective_stop:g}"
                )
                return {"action": "proceed", "reason": "protective_hold_released", "policy": policy}
            if hard_stop < protective_stop and current_price <= hard_stop:
                return {
                    "action": "sell",
                    "reason": "policy_hard_stop",
                    "signal": ExitSignal(True, "policy_hard_stop", "CLOSED_HARD_STOP", current_price, plan.path_run_id),
                    "policy": policy,
                }
            if current_price <= protective_stop:
                return {
                    "action": "sell",
                    "reason": "policy_protective_stop",
                    "signal": ExitSignal(
                        True,
                        "policy_protective_stop",
                        "CLOSED_CLAUDE_PRICE_STOP",
                        current_price,
                        plan.path_run_id,
                    ),
                    "policy": policy,
                }
            return self._policy_skip_or_shadow(plan, policy, current=current_price, reason="inside_protective_hold_policy")

        if mode == "target_extension":
            protective_stop = self._policy_float(policy.get("protective_stop"))
            if protective_stop > 0 and current_price <= protective_stop:
                return {
                    "action": "sell",
                    "reason": "policy_protective_stop",
                    "signal": ExitSignal(
                        True,
                        "policy_protective_stop",
                        "CLOSED_CLAUDE_PRICE_STOP",
                        current_price,
                        plan.path_run_id,
                    ),
                    "policy": policy,
                }
            revised_target = self._policy_float(policy.get("revised_sell_target") or plan_json.get("sell_target"))
            if revised_target > 0 and current_price >= revised_target:
                return {"action": "proceed", "reason": "revised_target_reached", "policy": policy}
            reask_price = self._policy_float(policy.get("reask_if_price_above"))
            if reask_price > 0 and current_price >= reask_price:
                return {
                    "action": "recheck",
                    "reason": "policy_price_above_trigger",
                    "signal": ExitSignal(True, "policy_recheck", "CLOSED_CLAUDE_PRICE_TARGET", current_price, plan.path_run_id),
                    "policy": policy,
                }
            peak = max(self._policy_float(policy.get("peak_price")), current_price)
            if peak > self._policy_float(policy.get("peak_price")):
                self._mark_pathb_auto_sell_policy(plan.path_run_id, peak_price=peak)
            drawdown_trigger = self._policy_float(policy.get("reask_drawdown_from_peak_pct"))
            if peak > 0 and drawdown_trigger > 0:
                drawdown_pct = ((peak - current_price) / peak) * 100.0
                if drawdown_pct >= drawdown_trigger:
                    return {
                        "action": "recheck",
                        "reason": "policy_drawdown_trigger",
                        "signal": ExitSignal(True, "policy_recheck", "CLOSED_CLAUDE_PRICE_TARGET", current_price, plan.path_run_id),
                        "policy": policy,
                    }
            return self._policy_skip_or_shadow(plan, policy, current=current_price, reason="inside_target_policy")

        if mode == "stop_recovery":
            hard_stop = self._policy_float(policy.get("hard_stop"))
            if hard_stop > 0 and current_price <= hard_stop:
                return {
                    "action": "sell",
                    "reason": "policy_hard_stop",
                    "signal": ExitSignal(True, "policy_hard_stop", "CLOSED_HARD_STOP", current_price, plan.path_run_id),
                    "policy": policy,
                }
            recover_above = self._policy_float(policy.get("recover_above"))
            if recover_above > 0 and current_price >= recover_above:
                self._mark_pathb_auto_sell_policy(
                    plan.path_run_id,
                    status="recovered",
                    recovered_at=now.isoformat(timespec="seconds"),
                    recovered_price=current_price,
                )
                return {"action": "proceed", "reason": "stop_recovery_completed", "policy": policy}
            return self._policy_skip_or_shadow(plan, policy, current=current_price, reason="inside_stop_recovery_policy")

        return {"action": "proceed", "reason": "unknown_policy_mode", "policy": policy}

    def _pathb_mfe_breakeven_signal(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        *,
        hard_stop_price: float | None = None,
        loss_cap_price: float | None = None,
    ) -> ExitSignal | None:
        if not _env_bool("PATHB_MFE_BREAKEVEN_ENABLED", True):
            return None
        current_price = float(current or 0)
        if current_price <= 0:
            return None
        if hard_stop_price is not None and float(hard_stop_price or 0) > 0 and current_price <= float(hard_stop_price):
            return None
        if loss_cap_price is not None and float(loss_cap_price or 0) > 0 and current_price <= float(loss_cap_price):
            return None
        try:
            trigger_pct = float(os.getenv("PATHB_MFE_BREAKEVEN_TRIGGER_PCT", "2.5") or 2.5)
        except Exception:
            trigger_pct = 2.5
        try:
            buffer_pct = float(os.getenv("PATHB_MFE_BREAKEVEN_BUFFER_PCT", "0.001") or 0.001)
        except Exception:
            buffer_pct = 0.001
        if trigger_pct <= 0:
            return None
        entry = self._position_entry_native(pos, plan.market)
        if entry <= 0:
            store = getattr(self, "store", None)
            run = store.find_path_run(plan.path_run_id) if store is not None else {}
            entry = float((run or {}).get("plan", {}).get("actual_entry_price", 0) or 0)
        if entry <= 0:
            return None
        try:
            mfe_pct = float(pos.get("peak_pnl_pct") or pos.get("position_mfe_pct") or 0)
        except Exception:
            mfe_pct = 0.0
        if mfe_pct <= 0:
            peak_price = 0.0
            try:
                run = self.store.find_path_run(plan.path_run_id) or {}
                policy = (run.get("plan") or {}).get("auto_sell_policy") or {}
                peak_price = float(policy.get("peak_price") or 0)
            except Exception:
                peak_price = 0.0
            if peak_price > 0:
                mfe_pct = ((peak_price / entry) - 1.0) * 100.0
        if mfe_pct < trigger_pct:
            return None
        breakeven_stop = entry * (1.0 + max(0.0, buffer_pct))
        if current_price > breakeven_stop:
            return None
        return ExitSignal(
            True,
            "mfe_breakeven",
            "CLOSED_MFE_BREAKEVEN",
            current_price,
            plan.path_run_id,
        )

    def _pathb_position_age_sec(self, plan: PricePlan, pos: dict[str, Any]) -> float | None:
        raw_values = [
            pos.get("pathb_filled_at"),
            pos.get("filled_at"),
            pos.get("entry_at"),
            pos.get("created_at"),
        ]
        try:
            run = self.store.find_path_run(plan.path_run_id) or {}
            plan_json = run.get("plan") or {}
            raw_values.extend(
                [
                    plan_json.get("filled_at"),
                    plan_json.get("partial_filled_at"),
                    plan_json.get("entry_filled_at"),
                    plan_json.get("actual_entry_at"),
                ]
            )
        except Exception:
            pass
        now = datetime.now(KST)
        for raw in raw_values:
            parsed = self._parse_kst_iso(raw)
            if parsed is None:
                continue
            return max(0.0, (now - parsed).total_seconds())
        return None

    def _pathb_profit_ladder_floor(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        market: str,
    ) -> dict[str, Any]:
        current_price = float(current or 0)
        if current_price <= 0:
            return {}
        entry = self._position_entry_native(pos, plan.market)
        if entry <= 0:
            try:
                run = self.store.find_path_run(plan.path_run_id) or {}
                entry = float((run.get("plan") or {}).get("actual_entry_price", 0) or 0)
            except Exception:
                entry = 0.0
        if entry <= 0:
            return {}
        try:
            mfe_pct = float(pos.get("peak_pnl_pct") or pos.get("position_mfe_pct") or 0)
        except Exception:
            mfe_pct = 0.0
        peak_price = self._policy_float(
            pos.get("peak_price")
            or pos.get("position_peak_price")
            or pos.get("high_price")
        )
        if peak_price <= 0:
            try:
                run = self.store.find_path_run(plan.path_run_id) or {}
                policy = (run.get("plan") or {}).get("auto_sell_policy") or {}
                peak_price = self._policy_float(policy.get("peak_price"))
            except Exception:
                peak_price = 0.0
        if mfe_pct <= 0 and peak_price > 0:
            mfe_pct = ((peak_price / entry) - 1.0) * 100.0
        if peak_price <= 0 and mfe_pct > 0:
            peak_price = entry * (1.0 + mfe_pct / 100.0)
        if mfe_pct <= 0 or peak_price <= 0:
            return {}

        tier1 = _env_float("PATHB_LADDER_TIER1_PCT", 1.2)
        tier2 = _env_float("PATHB_LADDER_TIER2_PCT", 2.0)
        tier3 = _env_float("PATHB_LADDER_TIER3_PCT", 3.0)
        tier4 = _env_float("PATHB_LADDER_TIER4_PCT", 4.0)
        tier = ""
        floor = 0.0
        if mfe_pct >= tier4 > 0:
            tier = "tier4"
            floor = peak_price * (1.0 - max(0.0, _env_float("PATHB_LADDER_TIER4_PEAK_GIVEBACK_PCT", 0.012)))
        elif mfe_pct >= tier3 > 0:
            tier = "tier3"
            floor = peak_price * (1.0 - max(0.0, _env_float("PATHB_LADDER_TIER3_PEAK_GIVEBACK_PCT", 0.010)))
        elif mfe_pct >= tier2 > 0:
            tier = "tier2"
            floor = entry * (1.0 + max(0.0, _env_float("PATHB_LADDER_TIER2_FLOOR_BUFFER_PCT", 0.005)))
        elif mfe_pct >= tier1 > 0:
            tier = "tier1"
            floor = entry
        if floor <= 0:
            return {}
        market_key = str(market or plan.market or "").upper()
        floor = self._round_policy_price(floor, market_key, direction="down")
        return {
            "tier": tier,
            "floor": floor,
            "entry": entry,
            "peak_price": peak_price,
            "mfe_pct": mfe_pct,
        }

    def _pathb_profit_ladder_signal(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        market: str,
        *,
        hard_stop_price: float | None = None,
        loss_cap_price: float | None = None,
    ) -> ExitSignal | None:
        if not _env_bool("PATHB_PROFIT_LADDER_ENABLED", True):
            return None
        current_price = float(current or 0)
        if current_price <= 0:
            return None
        if hard_stop_price is not None and float(hard_stop_price or 0) > 0 and current_price <= float(hard_stop_price):
            return None
        if loss_cap_price is not None and float(loss_cap_price or 0) > 0 and current_price <= float(loss_cap_price):
            return None
        min_hold_sec = max(0, _env_int("PATHB_LADDER_MIN_HOLD_SEC", 180))
        age_sec = self._pathb_position_age_sec(plan, pos)
        if age_sec is not None and age_sec < min_hold_sec:
            return None
        floor_info = self._pathb_profit_ladder_floor(plan, pos, current_price, market)
        if not floor_info:
            return None
        floor = self._policy_float(floor_info.get("floor"))
        if floor <= 0:
            return None
        if float(plan.stop_loss or 0) > 0 and floor <= float(plan.stop_loss or 0):
            return None
        try:
            run = self.store.find_path_run(plan.path_run_id) or {}
            policy = (run.get("plan") or {}).get("auto_sell_policy") or {}
        except Exception:
            policy = {}
        if (
            isinstance(policy, dict)
            and str(policy.get("status", "") or "").lower() == "active"
            and str(policy.get("mode", "") or "") == "protective_hold"
            and self._policy_float(policy.get("protective_stop")) >= floor
        ):
            return None
        if current_price > floor:
            return None
        log.warning(
            f"[PathB profit ladder SELL] {plan.market} {plan.ticker} "
            f"mfe={float(floor_info.get('mfe_pct') or 0):+.2f}% floor={floor:g} current={current_price:g} "
            f"reason={floor_info.get('tier')}"
        )
        return ExitSignal(True, "profit_ladder", "CLOSED_PROFIT_LADDER", current_price, plan.path_run_id)

    def _maybe_trigger_profit_protection_review(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        market: str,
    ) -> dict[str, Any]:
        if not _env_bool("PATHB_PROFIT_REVIEW_ENABLED", True):
            return {"triggered": False, "reason": "disabled"}
        path_run_id = str(plan.path_run_id or "")
        if not path_run_id:
            return {"triggered": False, "reason": "missing_path_run_id"}
        current_price = float(current or 0)
        if current_price <= 0:
            return {"triggered": False, "reason": "invalid_current_price"}
        try:
            run = self.store.find_path_run(path_run_id) or {}
            policy = (run.get("plan") or {}).get("auto_sell_policy") or {}
        except Exception:
            run = {}
            policy = {}
        if (
            isinstance(policy, dict)
            and str(policy.get("status", "") or "").lower() == "active"
            and str(policy.get("mode", "") or "") == "protective_hold"
        ):
            return {"triggered": False, "reason": "protective_hold_active"}
        floor_info = self._pathb_profit_ladder_floor(plan, pos, current_price, market)
        if not floor_info:
            return {"triggered": False, "reason": "ladder_floor_missing"}
        peak_pnl = float(floor_info.get("mfe_pct") or 0)
        trigger_pct = _env_float("PATHB_PROFIT_REVIEW_TRIGGER_PCT", 1.5)
        if peak_pnl < trigger_pct:
            return {"triggered": False, "reason": "peak_pnl_below_trigger"}
        floor = self._policy_float(floor_info.get("floor"))
        if floor <= 0 or current_price <= floor * (1.0 + max(0.0, _env_float("PATHB_PROFIT_REVIEW_LADDER_BUFFER_PCT", 0.003))):
            return {"triggered": False, "reason": "too_close_to_ladder_floor"}
        cooldown_sec = max(0, _env_int("PATHB_PROFIT_REVIEW_COOLDOWN_SEC", 600))
        now_ts = time.time()
        last_attempt = float(self._profit_review_last_attempt_at.get(path_run_id, 0.0) or 0.0)
        if cooldown_sec > 0 and last_attempt > 0 and now_ts - last_attempt < cooldown_sec:
            return {"triggered": False, "reason": "cooldown"}
        max_per_scan = max(1, _env_int("PATHB_PROFIT_REVIEW_MAX_PER_SCAN", 1))
        if int(getattr(self, "_profit_review_calls_this_scan", 0) or 0) >= max_per_scan:
            return {"triggered": False, "reason": "per_scan_cap"}

        self._profit_review_last_attempt_at[path_run_id] = now_ts
        self._profit_review_calls_this_scan = int(getattr(self, "_profit_review_calls_this_scan", 0) or 0) + 1
        now_iso = datetime.now(KST).isoformat(timespec="seconds")
        review_pos = dict(pos)
        review_pos.update(
            {
                "ticker": plan.ticker,
                "market": plan.market,
                "decision_stage": "PATHB_PROFIT_PROTECTION_REVIEW",
                "pathb_plan": plan.to_dict(),
                "pathb_profit_ladder_floor": floor,
                "pathb_profit_ladder_tier": str(floor_info.get("tier") or ""),
                "pathb_peak_pnl_pct": peak_pnl,
            }
        )
        if plan.market == "US":
            review_pos.setdefault("display_current_price", current_price)
            fx = self._usd_krw()
            if fx > 0:
                review_pos["current_price"] = current_price * fx
            entry = self._position_entry_native(pos, plan.market)
            if entry > 0:
                review_pos.setdefault("display_avg_price", entry)
        else:
            review_pos["current_price"] = current_price
            review_pos["display_current_price"] = current_price
        default_policy = (
            "This PathB position has open profit. Prefer HOLD only with explicit protective_stop "
            "and hard_stop that protect profit or reduce loss. SELL only when the thesis is broken."
        )
        payload_base = {
            "profit_review_triggered_at": now_iso,
            "profit_review_peak_pnl_pct": peak_pnl,
            "profit_review_ladder_floor": floor,
        }
        try:
            from minority_report.hold_advisor import ask as advisor_ask

            digest = self._pre_close_carry_digest(plan.market)
            builder = getattr(self.bot, "_advisor_pos", None)
            if callable(builder):
                try:
                    review_pos = builder(review_pos, plan.market)
                except Exception:
                    pass

            def _ask() -> dict[str, Any]:
                return advisor_ask(
                    review_pos,
                    plan.market,
                    digest,
                    decision_stage="INTRADAY_REVIEW",
                    default_policy=default_policy,
                    minutes_to_close=self._minutes_to_close(plan.market),
                )

            timeout_sec = _env_float("PATHB_PROFIT_REVIEW_TIMEOUT_SEC", 10.0)
            if timeout_sec > 0:
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(_ask)
                try:
                    advice = future.result(timeout=timeout_sec)
                except FuturesTimeoutError:
                    future.cancel()
                    executor.shutdown(wait=False, cancel_futures=True)
                    log.warning(f"[PathB profit_review timeout] {plan.market} {plan.ticker} timeout={timeout_sec:g}s")
                    self.store.update_path_run(
                        path_run_id,
                        plan={**payload_base, "profit_review_action": "TIMEOUT"},
                        merge_plan=True,
                    )
                    return {"triggered": True, "reason": "timeout"}
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                advice = _ask()
            action = str((advice or {}).get("action", "HOLD") or "HOLD").upper()
            if action not in {"SELL", "HOLD"}:
                action = "HOLD"
            payload = {
                **payload_base,
                "profit_review_action": action,
                "profit_review_detail": self._hold_advice_reason(advice),
                "profit_review_confidence": self._policy_float((advice or {}).get("confidence") if isinstance(advice, dict) else 0),
            }
            self.store.update_path_run(path_run_id, plan=payload, merge_plan=True)
            if action == "HOLD" and isinstance(advice, dict) and self._policy_float(advice.get("protective_stop")) > 0:
                advice = {**advice, "source": "profit_protection_review"}
                bridge_result = self.apply_general_hold_advice_policy(pos, market, advice, current_price)
                log.warning(
                    f"[PathB profit_review TRIGGERED] {plan.market} {plan.ticker} "
                    f"peak_pnl={peak_pnl:+.2f}% current={current_price:g} bridge={bridge_result.get('reason')}"
                )
                return {"triggered": True, "reason": "hold_policy", "bridge": bridge_result, "advice": advice}
            if action == "SELL":
                advice = {**advice, "source": "profit_protection_review"} if isinstance(advice, dict) else {"action": "SELL"}
                bridge_result = self.apply_general_hold_advice_policy(pos, market, advice, current_price)
                log.warning(
                    f"[PathB profit_review SELL policy] {plan.market} {plan.ticker} "
                    f"current={current_price:g} bridge={bridge_result.get('reason')}"
                )
                return {"triggered": True, "reason": "forced_sell_policy", "bridge": bridge_result, "advice": advice}
            return {"triggered": True, "reason": "hold_without_protective_stop", "advice": advice}
        except Exception as exc:
            log.warning(f"[PathB profit_review failed] {plan.market} {plan.ticker}: {exc}")
            try:
                self.store.update_path_run(
                    path_run_id,
                    plan={**payload_base, "profit_review_action": "ERROR", "profit_review_error": str(exc)[:300]},
                    merge_plan=True,
                )
            except Exception:
                pass
            return {"triggered": True, "reason": f"error:{exc}"}

    def _run_pathb_sell_review_gate(self, plan: PricePlan, pos: dict[str, Any], signal: ExitSignal) -> dict[str, Any]:
        raw_close_reason = str(signal.close_reason or "").strip().upper()
        if not _env_bool("CLAUDE_REVIEW_ALL_AUTOMATED_SELLS", False) and raw_close_reason in {
            "CLOSED_HARD_STOP",
            "CLOSED_LOSS_CAP",
            "CLOSED_PROFIT_LADDER",
        }:
            return {"allowed": True, "bypassed": True, "reason": raw_close_reason.lower()}
        if not self._pathb_sell_review_required(signal):
            return {"allowed": True, "bypassed": True, "reason": "operator_close"}
        now_dt = datetime.now(KST)
        now_iso = now_dt.isoformat(timespec="seconds")
        current_native = float(signal.price or 0)
        review_pos = dict(pos)
        review_pos["ticker"] = plan.ticker
        review_pos["market"] = plan.market
        review_pos["decision_stage"] = "AUTO_SELL_REVIEW"
        review_pos["auto_sell_reason"] = str(signal.reason or "")
        review_pos["auto_sell_close_reason"] = str(signal.close_reason or "")
        review_pos["pathb_plan"] = plan.to_dict()
        review_pos["pathb_exit_signal"] = {
            "reason": signal.reason,
            "close_reason": signal.close_reason,
            "price": current_native,
            "path_run_id": signal.path_run_id,
        }
        if plan.market == "US":
            review_pos.setdefault("display_current_price", current_native)
            fx = self._usd_krw()
            if fx > 0:
                review_pos["current_price"] = current_native * fx
            if float(review_pos.get("display_avg_price", 0) or 0) <= 0:
                entry = self._position_entry_native(pos, plan.market)
                if entry > 0:
                    review_pos["display_avg_price"] = entry
        else:
            review_pos["current_price"] = current_native
            review_pos["display_current_price"] = current_native
        default_policy = self._pathb_auto_sell_review_default_policy(signal)
        try:
            from minority_report.hold_advisor import ask as advisor_ask

            digest = self._pre_close_carry_digest(plan.market)
            builder = getattr(self.bot, "_advisor_pos", None)
            if callable(builder):
                try:
                    review_pos = builder(review_pos, plan.market)
                except Exception:
                    pass
            advice = advisor_ask(
                review_pos,
                plan.market,
                digest,
                decision_stage="AUTO_SELL_REVIEW",
                default_policy=default_policy,
                minutes_to_close=self._minutes_to_close(plan.market),
            )
            action = str((advice or {}).get("action", "HOLD") or "HOLD").upper()
            if action not in {"SELL", "HOLD"}:
                action = "HOLD"
            detail = self._hold_advice_reason(advice) or str((advice or {}).get("reason", "") or "")[:500]
            confidence = float((advice or {}).get("confidence", 0.0) or 0.0)
            fallback = bool((advice or {}).get("fallback", False))
        except Exception as exc:
            advice = {
                "action": "HOLD",
                "confidence": 0.0,
                "reason": f"pathb_auto_sell_review_failed:{exc}",
                "fallback": True,
                "decision_stage": "AUTO_SELL_REVIEW",
            }
            action = "HOLD"
            detail = str(advice["reason"])[:500]
            confidence = 0.0
            fallback = True
            # fail-safe: reviewer 불가 시 수익/손실 보호 신호 원래대로 집행
            if raw_close_reason in {"CLOSED_PROFIT_LADDER", "CLOSED_HARD_STOP", "CLOSED_LOSS_CAP"}:
                action = "SELL"
                advice["action"] = "SELL"
                detail = detail + f" | review_unavailable_failsafe:{raw_close_reason.lower()}"
        force_sell, force_detail = self._pathb_auto_sell_review_force_sell_required(
            plan,
            pos,
            signal,
            current_native,
        )
        if action != "SELL" and force_sell:
            action = "SELL"
            detail = (detail + " | " if detail else "") + f"system_force_sell_after_review:{force_detail}"
        payload = {
            "auto_sell_reviewed_at": now_iso,
            "auto_sell_review_reason": str(signal.reason or ""),
            "auto_sell_review_close_reason": str(signal.close_reason or ""),
            "auto_sell_review_action": action,
            "auto_sell_review_detail": detail,
            "auto_sell_review_confidence": confidence,
            "auto_sell_review_fallback": fallback,
        }
        if force_sell:
            payload["auto_sell_review_system_force_sell"] = True
            payload["auto_sell_review_force_detail"] = force_detail
        if action == "HOLD":
            policy, reject_reason = self._pathb_auto_sell_policy_from_advice(
                plan,
                pos,
                signal,
                advice if isinstance(advice, dict) else {},
                current_native,
                now=now_dt,
            )
            if policy:
                payload["auto_sell_policy"] = policy
                payload["auto_sell_policy_reject_reason"] = ""
                if (
                    str(policy.get("mode", "") or "") == "target_extension"
                    and self._pathb_hold_policy_mode() == "enforce"
                ):
                    payload["sell_target"] = float(policy.get("revised_sell_target", 0) or 0)
            elif reject_reason:
                payload["auto_sell_policy_reject_reason"] = reject_reason
                payload["auto_sell_policy_rejected_at"] = now_iso
        pos.update(payload)
        try:
            self.store.update_path_run(plan.path_run_id, plan=payload, merge_plan=True)
        except Exception:
            pass
        try:
            self._save_positions_if_possible()
        except Exception:
            pass
        if action != "SELL":
            log.warning(
                f"[PathB auto sell review HOLD] {plan.market} {plan.ticker} "
                f"reason={signal.reason} detail={detail}"
            )
            return {"allowed": False, "advice": advice, **payload}
        log.info(
            f"[PathB auto sell review SELL] {plan.market} {plan.ticker} "
            f"reason={signal.reason} confidence={confidence:.2f}"
        )
        return {"allowed": True, "advice": advice, **payload}

    def _submit_sell(self, plan: PricePlan, pos: dict[str, Any], signal: ExitSignal) -> bool:
        market = plan.market
        if not self._acquire_pathb_sell_attempt_lock(market, plan.ticker, plan.path_run_id):
            log.info(f"[PathB sell skipped] {market} {plan.ticker} sell attempt lock active run={plan.path_run_id}")
            return False
        keep_lock = False
        try:
            run = self.store.find_path_run(plan.path_run_id) or {}
            if self._pathb_sell_in_flight(run, pos):
                log.info(
                    f"[PathB sell skipped] {market} {plan.ticker} sell already in flight "
                    f"run={plan.path_run_id}"
                )
                return False
            qty = int(pos.get("qty", 0) or 0)
            order_price = self._compute_sell_order_price(market, signal.price)
            if qty <= 0 or order_price < 0:
                return False
            review = self._run_pathb_sell_review_gate(plan, pos, signal)
            if not bool(review.get("allowed", False)):
                return False
            latest_run = self.store.find_path_run(plan.path_run_id) or {}
            latest_pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id) or pos
            if self._pathb_sell_in_flight(latest_run, latest_pos):
                log.info(
                    f"[PathB sell skipped] {market} {plan.ticker} sell became in-flight before precheck "
                    f"run={plan.path_run_id}"
                )
                return False
            pos["pathb_closing"] = datetime.now(KST).isoformat(timespec="seconds")
            audit_signal_id = self._audit_pathb_exit_signal(plan, pos, signal)

            try:
                pre = precheck_order(plan.ticker, qty, order_price, "sell", _bot_token(self.bot, market), market=market)
            except Exception as exc:
                pos.pop("pathb_closing", None)
                self._note_sell_failure(market, plan.ticker, signal.reason, f"pathb_precheck_exception:{exc}")
                log.error(f"[PathB SELL PRECHECK EXCEPTION] {market} {plan.ticker}: {exc}")
                return False
            if not pre.get("ok"):
                pos.pop("pathb_closing", None)
                if self._precheck_failed_zero_holding(pre):
                    try:
                        self.reconcile_sell_pending(market, force=True)
                    except Exception as exc:
                        log.debug(f"[PathB sell precheck reconcile failed] {market} {plan.ticker}: {exc}")
                    refreshed_run = self.store.find_path_run(plan.path_run_id) or {}
                    refreshed_pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id) or pos
                    if str(refreshed_run.get("status", "") or "") == "CLOSED":
                        log.info(
                            f"[PathB sell precheck skipped] {market} {plan.ticker} broker already closed "
                            f"run={plan.path_run_id}"
                        )
                        return False
                    if self._pathb_sell_in_flight(refreshed_run, refreshed_pos):
                        log.info(
                            f"[PathB sell precheck skipped] {market} {plan.ticker} sell in flight after reconcile "
                            f"run={plan.path_run_id}"
                        )
                        return False
                self._note_sell_failure(market, plan.ticker, signal.reason, str(pre.get("msg", "") or "precheck_failed"))
                log.error(f"[PathB SELL PRECHECK FAILED] {market} {plan.ticker}: {pre}")
                return False

            try:
                result = place_order(plan.ticker, qty, order_price, "sell", _bot_token(self.bot, market), market=market)
            except Exception as exc:
                self.adapter.mark_order_unknown(
                    plan.path_run_id,
                    detail=f"sell_order_exception:{exc}",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
                log.error(f"[PathB SELL UNKNOWN] {market} {plan.ticker} sell order exception: {exc}")
                return False

            if not result.get("success"):
                pos.pop("pathb_closing", None)
                self._note_sell_failure(market, plan.ticker, signal.reason, str(result.get("msg", "") or "sell_order_failed"))
                log.error(f"[PathB SELL FAILED] {market} {plan.ticker}: {result.get('msg', '')}")
                return False

            execution_id = str(result.get("order_no", "") or f"pathb_sell_{market}_{plan.ticker}_{int(time.time())}")
            self.sell_manager.mark_sell_order_sent(
                plan.path_run_id,
                price=order_price,
                qty=qty,
                execution_id=execution_id,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                close_reason=signal.close_reason,
            )
            pos["pathb_pending_sell_order_no"] = execution_id
            pos["pathb_pending_sell_qty"] = qty
            pos["pathb_pending_close_reason"] = signal.close_reason
            pos["pathb_pending_sell_price"] = order_price
            try:
                self.bot._save_positions()
            except Exception:
                pass
            self._audit_pathb_sell_sent(
                plan,
                pos,
                signal,
                signal_id=audit_signal_id,
                qty=qty,
                order_no=execution_id,
                order_price=order_price,
            )
            log.warning(
                f"[PathB SELL SENT] {market} {plan.ticker} qty={qty} order_price={order_price:g} "
                f"order={execution_id} reason={signal.close_reason}"
            )
            keep_lock = True
            return True
        finally:
            if not keep_lock:
                self._release_pathb_sell_attempt_lock(market, plan.ticker, plan.path_run_id)

    def _compute_sell_order_price(self, market: str, raw_price: float) -> float:
        calculator = getattr(self.bot, "_compute_order_price", None)
        if callable(calculator):
            return float(calculator("sell", market, float(raw_price or 0)))
        return float(raw_price or 0)

    def _note_sell_failure(self, market: str, ticker: str, reason: str, detail: str) -> None:
        marker = getattr(self.bot, "_note_sell_failure", None)
        if callable(marker):
            try:
                marker(market, ticker, reason, detail)
            except Exception:
                pass

    @staticmethod
    def _precheck_failed_zero_holding(precheck: dict[str, Any]) -> bool:
        if str((precheck or {}).get("reason", "") or "") != "insufficient_holding":
            return False
        try:
            return int(float((precheck or {}).get("allowed_qty", 0) or 0)) <= 0
        except Exception:
            return True

    def _broker_remaining_qty(self, market: str, ticker: str) -> int | None:
        if self.is_paper:
            return 0
        key = self._ticker_key(market, ticker)
        try:
            balance = get_balance(_bot_token(self.bot, market), market=market, force_refresh=True)
        except Exception as exc:
            log.warning(f"[PathB broker truth] {market} {key} balance refresh failed after sell: {exc}")
            return None
        for stock in balance.get("stocks", []) or []:
            stock_key = self._ticker_key(market, str(stock.get("ticker", "") or ""))
            if stock_key == key:
                return max(0, int(float(stock.get("qty", 0) or 0)))
        return 0

    def _balance_for_snapshot(self, market: str, force: bool) -> dict[str, Any]:
        getter = getattr(self.bot, "_get_balance_with_token_refresh", None)
        if callable(getter):
            return getter(str(market or "").upper(), force_refresh_balance=bool(force))
        market_key = str(market or "").upper()
        return get_balance(_bot_token(self.bot, market_key), market=market_key, force_refresh=bool(force))

    def refresh_broker_truth(self, market: str, *, force: bool = False, ttl_sec: int | None = None) -> dict[str, Any]:
        market_key = str(market or "").upper()
        ttl = int(ttl_sec if ttl_sec is not None else (30 if self._session_active_for_market(market_key) else 60))
        return self.broker_truth.refresh_market(market_key, force=bool(force), ttl_sec=ttl)

    def reconcile_sell_pending(self, market: str, *, force: bool = False, session_end: bool = False) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "session_end": bool(session_end),
            "checked": 0,
            "closed": 0,
            "partial": 0,
            "acked": 0,
            "order_unknown": 0,
            "broker_truth_unavailable": 0,
            "skipped": 0,
            "errors": [],
        }
        runs = self._pending_sell_runs(market_key)
        if not runs:
            return summary
        try:
            self.refresh_broker_truth(market_key, force=force)
        except Exception as exc:
            summary["errors"].append(f"snapshot_refresh:{exc}")
        for run in runs:
            try:
                result = self._reconcile_sell_pending_run(run, market_key, session_end=session_end)
                summary["checked"] += 1
                summary[result] = int(summary.get(result, 0) or 0) + 1
            except Exception as exc:
                summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        if summary["checked"] or summary["errors"]:
            log.info(f"[PathB SELL reconcile] {summary}")
        return summary

    def finalize_sell_pending_at_session_close(self, market: str) -> dict[str, Any]:
        return self.reconcile_sell_pending(str(market or "").upper(), force=True, session_end=True)

    def reconcile_filled_positions(self, market: str, *, force: bool = False) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "checked": 0,
            "kept_open": 0,
            "kept_open_local": 0,
            "closed": 0,
            "order_unknown": 0,
            "broker_truth_unavailable": 0,
            "errors": [],
        }
        runs: list[dict[str, Any]] = []
        for status in ("FILLED", "PARTIAL_FILLED"):
            runs.extend(
                self.store.path_runs_for_session(
                    market=market_key,
                    runtime_mode=self.mode,
                    session_date=self._session_date(market_key),
                    status=status,
                    path_type="claude_price",
                )
            )
        if not runs:
            return summary
        try:
            self.refresh_broker_truth(market_key, force=force)
        except Exception as exc:
            summary["errors"].append(f"snapshot_refresh:{exc}")
        market_data = self.broker_truth.market_snapshot(market_key)
        if bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
            summary["broker_truth_unavailable"] = len(runs)
            return summary
        for run in runs:
            try:
                result = self._reconcile_filled_position_run(run, market_key, market_data)
                summary["checked"] += 1
                summary[result] = int(summary.get(result, 0) or 0) + 1
            except Exception as exc:
                summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        if summary["checked"] or summary["errors"]:
            log.info(f"[PathB FILLED reconcile] {summary}")
        return summary

    def _reconcile_filled_position_run(self, run: dict[str, Any], market: str, market_data: dict[str, Any]) -> str:
        path_run_id = str(run.get("path_run_id", "") or "")
        plan = self._plan_from_run(run)
        if not path_run_id or plan is None:
            return "errors"
        ticker = self._ticker_key(market, plan.ticker)
        local_pos = self._find_position(market, plan.ticker, path_run_id=path_run_id)
        positions = self._broker_rows_for_ticker(market_data.get("positions", []), market, ticker)
        if positions:
            return "kept_open"
        fills = self._broker_rows_for_ticker(market_data.get("today_fills", []), market, ticker)
        sell_fills = self._matching_sell_fills(fills, execution_id="")
        filled_qty = sum(int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) for row in sell_fills)
        plan_json = run.get("plan") or {}
        requested_qty = int(plan_json.get("filled_qty", 0) or plan_json.get("partial_entry_qty", 0) or 0)
        if sell_fills and (requested_qty <= 0 or filled_qty >= requested_qty):
            evidence = {
                "broker_truth_last_success_at": str(market_data.get("last_success_at", "") or ""),
                "broker_sell_fill_qty": int(filled_qty),
                "broker_position_qty_after_sell": 0,
                "stale_filled_recovered": True,
            }
            self._finalize_pathb_sell_close(
                plan,
                price=self._weighted_fill_price(sell_fills),
                qty=int(filled_qty or requested_qty or 0),
                execution_id=str(sell_fills[0].get("order_no", "") or ""),
                close_reason=str(plan_json.get("pending_close_reason") or run.get("pending_close_reason") or "CLOSED_CLAUDE_PRICE_PRE_CLOSE"),
                evidence=evidence,
            )
            return "closed"
        if local_pos is not None and int(float(local_pos.get("qty", 0) or 0)) > 0:
            # KIS balance can lag fills by several seconds. A live local Path B
            # position is enough to keep exit monitoring active until broker
            # sell-fill evidence or a later fresh balance snapshot confirms close.
            return "kept_open_local"
        self.adapter.mark_order_unknown(
            path_run_id,
            detail="filled_position_missing_without_sell_ccld",
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        self._set_order_unknown_resolution(
            path_run_id,
            "ambiguous_broker_truth",
            {
                "broker_position_evidence": False,
                "broker_today_sell_fill_evidence": False,
                "filled_position_missing": True,
            },
            next_retry=True,
        )
        return "order_unknown"

    def _pending_buy_runs(self, market: str) -> list[dict[str, Any]]:
        runs: list[dict[str, Any]] = []
        for status in ("ORDER_SENT", "ORDER_ACKED"):
            runs.extend(
                self.store.path_runs_for_session(
                    market=market,
                    runtime_mode=self.mode,
                    session_date=self._session_date(market),
                    status=status,
                    path_type="claude_price",
                )
            )
        return runs

    def _reconcile_buy_pending_cancel_above_run(self, run: dict[str, Any], market: str) -> str:
        path_run_id = str(run.get("path_run_id", "") or "")
        plan = self._plan_from_run(run)
        if not path_run_id or plan is None:
            return "skipped"
        threshold = float(plan.cancel_if_open_above or 0)
        if threshold <= 0:
            return "skipped"
        current = self._current_native_price(market, plan.ticker)
        if current <= 0 or current <= threshold:
            return "skipped"

        plan_json = run.get("plan") or {}
        execution_id = str(plan_json.get("entry_execution_id", "") or "")
        qty = int(plan_json.get("entry_qty", 0) or 0)
        order_price = float(plan_json.get("entry_order_price", 0) or 0)
        if not execution_id or qty <= 0:
            self.adapter.mark_order_unknown(
                path_run_id,
                detail="cancel_above_after_ack_missing_order_identity",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                execution_id=execution_id,
            )
            return "order_unknown"

        cancel_requested_at = str(plan_json.get("cancel_requested_at", "") or "").strip()
        now_iso = datetime.now(KST).isoformat(timespec="seconds")
        if not cancel_requested_at:
            try:
                result = cancel_order(
                    plan.ticker,
                    execution_id,
                    qty,
                    _bot_token(self.bot, market),
                    market=market,
                    price=order_price,
                )
            except Exception as exc:
                self.store.update_path_run(
                    path_run_id,
                    plan={
                        "cancel_above_after_ack": True,
                        "cancel_requested_at": now_iso,
                        "cancel_request_error": str(exc),
                        "cancel_trigger_price": float(current),
                    },
                    merge_plan=True,
                )
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail=f"cancel_above_after_ack_request_failed:{exc}",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id,
                )
                return "order_unknown"
            self.store.update_path_run(
                path_run_id,
                plan={
                    "cancel_above_after_ack": True,
                    "cancel_requested_at": now_iso,
                    "cancel_trigger_price": float(current),
                    "cancel_if_open_above": threshold,
                    "cancel_order_result": result,
                },
                merge_plan=True,
            )
            try:
                unknown = getattr(self.bot, "v2_order_unknown", None)
                if unknown is not None and hasattr(unknown, "record_cancel_requested"):
                    unknown.record_cancel_requested(
                        market=market,
                        ticker=plan.ticker,
                        order_no=execution_id,
                        qty=qty,
                        reason="cancel_above_after_ack",
                    )
            except Exception as exc:
                log.debug(f"[PathB cancel registry] record request failed {market} {plan.ticker}: {exc}")
            cancel_requested_at = now_iso
            requested = True
        else:
            requested = False

        try:
            self.refresh_broker_truth(market, force=True)
        except Exception:
            pass
        market_data = self.broker_truth.market_snapshot(market)
        if bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
            if self._cancel_confirm_ttl_expired(cancel_requested_at):
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="cancel_above_after_ack_broker_truth_unavailable_ttl",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id,
                )
                return "order_unknown"
            return "cancel_requested" if requested else "still_open"

        ticker = self._ticker_key(market, plan.ticker)
        fills = self._broker_rows_for_ticker(market_data.get("today_fills", []), market, ticker)
        fill_match = self._match_pathb_fill(plan, fills)
        if fill_match.get("row"):
            row = dict(fill_match["row"])
            filled_qty = int(row.get("filled_qty", 0) or row.get("qty", 0) or qty)
            fill_price = float(row.get("avg_price", 0) or row.get("fill_price", 0) or row.get("price", 0) or order_price)
            fill_execution_id = str(row.get("order_no", "") or execution_id)
            self.adapter.mark_filled(
                path_run_id,
                price=fill_price,
                qty=filled_qty,
                execution_id=fill_execution_id,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            positions = self._broker_rows_for_ticker(market_data.get("positions", []), market, ticker)
            self._attach_recovered_broker_position(plan, positions, row, filled_qty, fill_price, fill_execution_id)
            self._save_positions_if_possible()
            return "filled"
        if fill_match.get("ambiguous"):
            self.adapter.mark_order_unknown(
                path_run_id,
                detail="cancel_above_after_ack_ambiguous_buy_fill",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                execution_id=execution_id,
            )
            return "order_unknown"

        open_orders = self._broker_rows_for_ticker(market_data.get("open_orders", []), market, ticker)
        open_match = self._match_pathb_open_order(plan, open_orders)
        if open_match.get("row") or open_match.get("ambiguous"):
            if self._cancel_confirm_ttl_expired(cancel_requested_at):
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="cancel_above_after_ack_open_after_ttl",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id,
                )
                return "order_unknown"
            self.store.update_path_run(
                path_run_id,
                plan={
                    "cancel_above_after_ack": True,
                    "cancel_still_open_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "cancel_open_order_evidence": True,
                },
                merge_plan=True,
            )
            return "cancel_requested" if requested else "still_open"

        self.adapter.cancel_plan(
            path_run_id,
            reason="cancel_above_after_ack_confirmed",
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        self.store.update_path_run(
            path_run_id,
            plan={
                "cancel_above_after_ack": True,
                "cancel_confirmed_by_broker": True,
                "cancel_confirmed_at": datetime.now(KST).isoformat(timespec="seconds"),
            },
            merge_plan=True,
        )
        try:
            unknown = getattr(self.bot, "v2_order_unknown", None)
            if unknown is not None and hasattr(unknown, "record_cancel_resolved"):
                unknown.record_cancel_resolved(
                    market=market,
                    ticker=plan.ticker,
                    order_no=execution_id,
                    resolution="CANCEL_CONFIRMED",
                )
        except Exception as exc:
            log.debug(f"[PathB cancel registry] record resolved failed {market} {plan.ticker}: {exc}")
        return "cancel_confirmed"

    @staticmethod
    def _cancel_confirm_ttl_expired(cancel_requested_at: str) -> bool:
        raw = str(cancel_requested_at or "").strip()
        if not raw:
            return False
        try:
            requested_at = datetime.fromisoformat(raw)
            if requested_at.tzinfo is None:
                requested_at = requested_at.replace(tzinfo=KST)
        except Exception:
            return False
        return (datetime.now(KST) - requested_at.astimezone(KST)).total_seconds() >= 120

    def _pending_sell_runs(self, market: str) -> list[dict[str, Any]]:
        market_key = str(market or "").upper()
        candidates: list[dict[str, Any]] = []
        for status in ("SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"):
            candidates.extend(
                self.store.path_runs_for_session(
                    market=market_key,
                    runtime_mode=self.mode,
                    status=status,
                    path_type="claude_price",
                )
            )
        lookback_sessions = max(
            1,
            _env_int("PATHB_SELL_PENDING_LOOKBACK_SESSIONS", self.SELL_PENDING_LOOKBACK_SESSIONS),
        )
        sessions: list[str] = []
        for run in sorted(candidates, key=lambda item: str(item.get("session_date", "") or ""), reverse=True):
            session_date = str(run.get("session_date", "") or "")
            if session_date and session_date not in sessions:
                sessions.append(session_date)
            if len(sessions) >= lookback_sessions:
                break
        current_session = self._session_date(market_key)
        if current_session and current_session not in sessions:
            sessions.append(current_session)
        allowed_sessions = set(sessions)
        ttl_days = max(1, _env_int("PATHB_SELL_PENDING_RECONCILE_TTL_DAYS", 3))
        cutoff = datetime.now(KST) - timedelta(days=ttl_days)
        runs: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(run: dict[str, Any] | None) -> None:
            if not run:
                return
            if str(run.get("path_type", "")) != "claude_price":
                return
            if str(run.get("market", "") or "").upper() != market_key:
                return
            if str(run.get("runtime_mode", "") or "") != self.mode:
                return
            if str(run.get("status", "") or "") not in {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}:
                return
            path_run_id = str(run.get("path_run_id", "") or "")
            if not path_run_id or path_run_id in seen:
                return
            session_date = str(run.get("session_date", "") or "")
            if session_date not in allowed_sessions and not self._pathb_pending_sell_recent_enough(run, cutoff):
                return
            seen.add(path_run_id)
            runs.append(run)

        for run in candidates:
            add(run)
        for pos in self._local_pathb_positions(market_key):
            add(self.store.find_path_run(str(pos.get("pathb_path_run_id", "") or "")))
        return runs

    def _pathb_pending_sell_recent_enough(self, run: dict[str, Any], cutoff: datetime) -> bool:
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        for key in ("sell_order_sent_at", "updated_at", "created_at"):
            raw = str(plan.get(key) or run.get(key) or "").strip()
            parsed = self._parse_kst_iso(raw.replace("Z", "+00:00"))
            if parsed is not None and parsed >= cutoff:
                return True
        market = str(run.get("market", "") or "").upper()
        return str(run.get("session_date", "") or "") == self._session_date(market)

    def _reconcile_sell_pending_run(self, run: dict[str, Any], market: str, *, session_end: bool = False) -> str:
        path_run_id = str(run.get("path_run_id", "") or "")
        plan = self._plan_from_run(run)
        if not path_run_id or plan is None:
            return "skipped"
        market_data = self.broker_truth.market_snapshot(market)
        if bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
            if session_end:
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="sell_session_end_broker_truth_unavailable",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=str((run.get("plan") or {}).get("exit_execution_id", "") or ""),
                )
                return "order_unknown"
            self._set_sell_pending_resolution(
                path_run_id,
                "broker_truth_unavailable",
                {"broker_truth_error": str(market_data.get("error", "") or ""), "broker_truth_stale": bool(market_data.get("stale"))},
            )
            return "broker_truth_unavailable"

        ticker = self._ticker_key(market, plan.ticker)
        positions = self._broker_rows_for_ticker(market_data.get("positions", []), market, ticker)
        open_orders = self._broker_rows_for_ticker(market_data.get("open_orders", []), market, ticker)
        fills = self._broker_rows_for_ticker(market_data.get("today_fills", []), market, ticker)
        plan_json = run.get("plan") or {}
        requested_qty = int(plan_json.get("exit_qty", 0) or 0)
        if requested_qty <= 0:
            pos = self._find_position(market, ticker, path_run_id=path_run_id) or self._find_position(market, ticker)
            requested_qty = int((pos or {}).get("qty", 0) or 0)
        execution_id = str(plan_json.get("exit_execution_id", "") or "")
        sell_fills = self._matching_sell_fills(
            fills,
            execution_id=execution_id,
            strict_execution=bool(execution_id),
        )
        filled_qty = sum(int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) for row in sell_fills)
        fill_price = self._weighted_fill_price(sell_fills)
        remaining_balance_qty = self._broker_position_qty(positions)
        evidence = {
            "broker_truth_last_success_at": str(market_data.get("last_success_at", "") or ""),
            "broker_sell_fill_qty": int(filled_qty),
            "broker_position_qty_after_sell": int(remaining_balance_qty),
            "broker_open_order_evidence": bool(self._matching_sell_open_orders(open_orders, execution_id=execution_id)),
            "exit_execution_id": execution_id,
        }

        if requested_qty > 0 and filled_qty >= requested_qty:
            self._finalize_pathb_sell_close(
                plan,
                price=fill_price or float(plan_json.get("exit_order_price", 0) or 0),
                qty=requested_qty,
                execution_id=execution_id or str((sell_fills[0] if sell_fills else {}).get("order_no", "") or ""),
                close_reason=str(plan_json.get("pending_close_reason") or run.get("pending_close_reason") or "CLOSED_CLAUDE_PRICE_PRE_CLOSE"),
                evidence=evidence,
            )
            return "closed"

        if filled_qty > 0:
            remaining = max(0, int(requested_qty or filled_qty) - int(filled_qty))
            self._update_local_pathb_remaining_qty(plan, remaining)
            self.sell_manager.mark_sell_partial(
                path_run_id,
                execution_id=execution_id,
                price=fill_price or float(plan_json.get("exit_order_price", 0) or 0),
                filled_qty=int(filled_qty),
                remaining_qty=int(remaining),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            self._set_sell_pending_resolution(path_run_id, "partial_sell_fill", evidence)
            if session_end:
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="sell_partial_session_end_unresolved",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id,
                )
                return "order_unknown"
            if self._sell_ttl_expired(run, partial=True):
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="sell_partial_ttl_expired",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id,
                )
                return "order_unknown"
            return "partial"

        open_matches = self._matching_sell_open_orders(
            open_orders,
            execution_id=execution_id,
            strict_execution=bool(execution_id),
        )
        if open_matches:
            if session_end:
                self.adapter.mark_order_unknown(
                    path_run_id,
                    detail="sell_session_end_open_order_unresolved",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                    execution_id=execution_id or str(open_matches[0].get("order_no", "") or ""),
                )
                return "order_unknown"
            self.sell_manager.mark_sell_acked(
                path_run_id,
                execution_id=execution_id or str(open_matches[0].get("order_no", "") or ""),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                detail="broker_open_order_evidence",
            )
            self._set_sell_pending_resolution(path_run_id, "broker_open_order_evidence", evidence)
            return "acked"

        detail = "ambiguous_broker_truth" if remaining_balance_qty <= 0 else "sell_fill_not_confirmed"
        if session_end:
            self.adapter.mark_order_unknown(
                path_run_id,
                detail=f"{detail}:session_end_unresolved",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                execution_id=execution_id,
            )
            return "order_unknown"
        if self._sell_ttl_expired(run, partial=False):
            self.adapter.mark_order_unknown(
                path_run_id,
                detail=f"{detail}:ttl_expired",
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
                execution_id=execution_id,
            )
            return "order_unknown"
        self.sell_manager.mark_sell_acked(
            path_run_id,
            execution_id=execution_id,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
            detail=detail,
        )
        self._set_sell_pending_resolution(path_run_id, detail, evidence)
        return "acked"

    def _set_sell_pending_resolution(self, path_run_id: str, resolution: str, extra: dict[str, Any] | None = None) -> None:
        self.store.update_path_run(
            path_run_id,
            plan={
                "sell_pending_resolution": str(resolution or ""),
                "sell_pending_resolution_at": datetime.now(KST).isoformat(timespec="seconds"),
                **(extra or {}),
            },
            merge_plan=True,
        )

    def _matching_sell_fills(
        self,
        fills: list[dict[str, Any]],
        *,
        execution_id: str = "",
        strict_execution: bool = False,
    ) -> list[dict[str, Any]]:
        rows = [row for row in fills if self._side_matches(row, "sell") and int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) > 0]
        if execution_id:
            matched = [row for row in rows if str(row.get("order_no", "") or "") == execution_id]
            if matched or strict_execution:
                return matched
        return rows

    def _matching_sell_open_orders(
        self,
        open_orders: list[dict[str, Any]],
        *,
        execution_id: str = "",
        strict_execution: bool = False,
    ) -> list[dict[str, Any]]:
        rows = [row for row in open_orders if self._side_matches(row, "sell") and int(row.get("remaining_qty", 0) or 0) > 0]
        if execution_id:
            matched = [row for row in rows if str(row.get("order_no", "") or "") == execution_id]
            if matched or strict_execution:
                return matched
        return rows

    @staticmethod
    def _weighted_fill_price(rows: list[dict[str, Any]]) -> float:
        total_qty = 0
        total_value = 0.0
        for row in rows:
            qty = int(row.get("filled_qty", 0) or row.get("qty", 0) or 0)
            price = float(row.get("avg_price", 0) or row.get("fill_price", 0) or row.get("price", 0) or 0)
            if qty > 0 and price > 0:
                total_qty += qty
                total_value += qty * price
        return total_value / total_qty if total_qty > 0 else 0.0

    @staticmethod
    def _broker_position_qty(positions: list[dict[str, Any]]) -> int:
        return sum(max(0, int(row.get("qty", 0) or 0)) for row in positions)

    @staticmethod
    def _pathb_decision_exit_reason(close_reason: str) -> str:
        return normalize_pathb_decision_exit_reason(close_reason)

    def _sell_ttl_expired(self, run: dict[str, Any], *, partial: bool) -> bool:
        plan = run.get("plan") or {}
        raw = str(plan.get("sell_order_sent_at", "") or "").strip()
        if not raw:
            return False
        try:
            sent_at = datetime.fromisoformat(raw)
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=KST)
        except Exception:
            return False
        ttl_min = int(self.config.pathb_sell_partial_ttl_minutes if partial else self.config.pathb_sell_pending_ttl_minutes)
        return (datetime.now(KST) - sent_at.astimezone(KST)).total_seconds() >= ttl_min * 60

    def _finalize_pathb_sell_close(
        self,
        plan: PricePlan,
        *,
        price: float,
        qty: int,
        execution_id: str,
        close_reason: str,
        evidence: dict[str, Any],
    ) -> None:
        market = plan.market
        price_native = float(price or 0)
        exit_price_krw = self._price_to_krw(price_native, market)
        pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id) or self._find_position(market, plan.ticker)
        ex: dict[str, Any] | None = None
        exit_meta: dict[str, Any] = {}
        if pos is not None:
            pos.pop("pathb_closing", None)
            exit_meta = self._pathb_exit_meta(pos, market, close_reason)
            try:
                ex = self.bot.risk.close_position(
                    plan.ticker,
                    exit_price_krw,
                    close_reason,
                    session_date=plan.session_date,
                    exit_meta=exit_meta,
                )
            except TypeError:
                ex = self.bot.risk.close_position(plan.ticker, exit_price_krw, close_reason)
            except Exception as exc:
                log.warning(f"[PathB SELL reconcile] local close failed {market} {plan.ticker}: {exc}")
            self._save_positions_if_possible()
        self._record_pathb_sell_decision_event(
            plan,
            price_native=price_native,
            exit_price_krw=exit_price_krw,
            qty=qty,
            execution_id=execution_id,
            close_reason=close_reason,
            ex=ex,
            pos=pos,
            exit_meta=exit_meta,
        )
        if close_reason in {"CLOSED_LOSS_CAP", "CLOSED_HARD_STOP", "CLOSED_CLAUDE_PRICE_STOP"}:
            try:
                key = plan.ticker.upper() if market == "US" else plan.ticker
                note_stop = getattr(self.bot, "_note_stop_loss_event", None)
                if callable(note_stop):
                    note_stop(
                        market,
                        key,
                        close_reason,
                        event_id=str(plan.path_run_id or ""),
                        qty=int((ex or pos or {}).get("qty", 0) or 0),
                        pnl_krw=float((ex or {}).get("pnl_krw", (ex or {}).get("pnl", 0)) or 0),
                        pnl_pct=float((ex or {}).get("pnl_pct", 0) or 0),
                        occurred_at=datetime.now(KST).isoformat(timespec="seconds"),
                        suppress_market_count=self._pathb_stop_ticker_only(ex or pos or {}, market),
                    )
                else:
                    self.bot._v2_same_day_stop_tickers.setdefault(market, set()).add(key)
                    self.bot._daily_sl_count[market] = int(self.bot._daily_sl_count.get(market, 0) or 0) + 1
            except Exception:
                pass
            try:
                mark_runtime = getattr(self.bot, "_selection_meta_mark_runtime_filtered", None)
                if callable(mark_runtime):
                    reason_map = {
                        "CLOSED_LOSS_CAP": "loss_cap_exited",
                        "CLOSED_HARD_STOP": "hard_stop_exited",
                        "CLOSED_CLAUDE_PRICE_STOP": "claude_price_stop_exited",
                    }
                    mark_runtime(
                        market,
                        plan.ticker,
                        reason_map.get(close_reason, str(close_reason).lower()),
                        remove_trade_ready=True,
                        persist=True,
                    )
            except Exception:
                pass
        pnl_pct = 0.0
        if ex is not None:
            pnl_pct = float(ex.get("pnl_pct", 0) or 0)
        else:
            entry = float((self.store.find_path_run(plan.path_run_id) or {}).get("plan", {}).get("actual_entry_price", 0) or 0)
            pnl_pct = ((price_native / entry) - 1.0) * 100.0 if entry > 0 and price_native > 0 else 0.0
        self.sell_manager.mark_closed(
            plan.path_run_id,
            close_reason=close_reason,
            price=price_native,
            pnl_pct=pnl_pct,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
            execution_id=execution_id,
        )
        self.store.update_path_run(
            plan.path_run_id,
            plan={
                "exit_fill_qty": int(qty or 0),
                "exit_fill_confirmed": True,
                "sell_pending_resolution": "broker_sell_fill_confirmed",
                **evidence,
            },
            merge_plan=True,
        )
        try:
            price_sample = getattr(self.bot, "_audit_emit_price_sample", None)
            audit_emit = getattr(self.bot, "_audit_try_emit", None)
            if callable(price_sample):
                price_sample(
                    market,
                    plan.ticker,
                    price=float(price_native or 0.0),
                    source="pathb:sell_fill_confirmed",
                    decision_id=plan.decision_id,
                    path_run_id=plan.path_run_id,
                    payload={
                        "close_reason": close_reason,
                        "order_no": execution_id,
                        "qty": int(qty or 0),
                        "pnl_pct": pnl_pct,
                    },
                )
            if callable(audit_emit):
                audit_emit(
                    {
                        "kind": "trade_link",
                        "decision_id": plan.decision_id,
                        "path_run_id": plan.path_run_id,
                        "order_no": execution_id,
                        "exit_price": float(price_native or 0.0),
                        "pnl_pct": pnl_pct,
                        "exit_reason": close_reason,
                        "payload": {"side": "sell_fill_confirmed", "qty": int(qty or 0), **evidence},
                    }
                )
        except Exception:
            pass
        log.warning(
            f"[PathB SELL CLOSED] {market} {plan.ticker} qty={qty} price={price_native:g} "
            f"reason={close_reason} order={execution_id}"
        )

    def _record_pathb_sell_decision_event(
        self,
        plan: PricePlan,
        *,
        price_native: float,
        exit_price_krw: float,
        qty: int,
        execution_id: str,
        close_reason: str,
        ex: dict[str, Any] | None,
        pos: dict[str, Any] | None,
        exit_meta: dict[str, Any],
    ) -> None:
        recorder = getattr(self.bot, "_record_decision_event", None)
        if not callable(recorder):
            return
        try:
            run = self.store.find_path_run(plan.path_run_id) or {}
            plan_json = run.get("plan") or {}
            record_qty = int((ex or {}).get("qty", qty) or qty or 0)
            entry_native = float(
                (ex or {}).get("display_entry_price", 0)
                or (pos or {}).get("display_avg_price", 0)
                or plan_json.get("actual_entry_price", 0)
                or (pos or {}).get("avg_price", 0)
                or (pos or {}).get("entry", 0)
                or 0
            )
            if plan.market == "US" and entry_native > 1000:
                try:
                    entry_native = entry_native / float(self._usd_krw() or 1)
                except Exception:
                    pass
            pnl_krw = float((ex or {}).get("pnl_krw", (ex or {}).get("pnl", 0)) or 0)
            pnl_pct = float((ex or {}).get("pnl_pct", 0) or 0)
            if ex is None and entry_native > 0 and price_native > 0 and record_qty > 0:
                entry_krw = self._price_to_krw(entry_native, plan.market)
                pnl_krw = (float(exit_price_krw or 0) - float(entry_krw or 0)) * record_qty
                pnl_pct = ((float(price_native or 0) / entry_native) - 1.0) * 100.0
            strategy = str((ex or {}).get("strategy") or (pos or {}).get("strategy") or "claude_price")
            source_strategy = str(
                (ex or {}).get("source_strategy") or (pos or {}).get("source_strategy") or "claude_price"
            )
            event_meta = {
                key: value
                for key, value in dict(exit_meta or {}).items()
                if key not in {"path_type", "pathb_path_run_id", "v2_decision_id", "v2_execution_id"}
            }
            recorder(
                plan.market,
                "sell_filled",
                plan.ticker,
                strategy=strategy,
                source_strategy=source_strategy,
                path_type="claude_price",
                pathb_path_run_id=plan.path_run_id,
                v2_decision_id=plan.decision_id,
                v2_execution_id=str(execution_id or ""),
                position_id=str((ex or pos or {}).get("position_id", "") or ""),
                qty=record_qty,
                price_native=float(price_native or 0),
                price_krw=float(exit_price_krw or 0),
                reason=self._pathb_decision_exit_reason(close_reason),
                detail=f"pathb_run={plan.path_run_id} close_reason={close_reason}",
                order_no=str(execution_id or ""),
                pnl_krw=pnl_krw,
                pnl_pct=pnl_pct,
                actual_fill_price=float(price_native or 0),
                broker_fill_confirmed=True,
                broker_filled_qty=int(qty or record_qty or 0),
                broker_fill_source="pathb_broker_truth",
                pathb_reference_target=float((pos or {}).get("pathb_reference_target", 0) or 0),
                selection_reference_target=float((pos or {}).get("selection_reference_target", 0) or 0),
                **event_meta,
            )
        except Exception as exc:
            log.warning(f"[PathB SELL reconcile] decision event record failed {plan.market} {plan.ticker}: {exc}")

    def _update_local_pathb_remaining_qty(self, plan: PricePlan, remaining_qty: int) -> None:
        pos = self._find_position(plan.market, plan.ticker, path_run_id=plan.path_run_id)
        if pos is None:
            return
        pos["qty"] = max(0, int(remaining_qty or 0))
        pos.pop("pathb_closing", None)
        try:
            self._save_positions_if_possible()
        except Exception:
            pass

    def _save_positions_if_possible(self) -> None:
        saver = getattr(self.bot, "_save_positions", None)
        if callable(saver):
            saver()

    def _save_pending_orders_if_possible(self) -> None:
        saver = getattr(self.bot, "_save_pending_orders", None)
        if callable(saver):
            saver()

    def reconcile_order_unknowns(
        self,
        market: str,
        *,
        force: bool = False,
        path_run_id: str = "",
        session_end: bool = False,
        include_cross_session: bool = False,
        auto_clear_no_evidence: bool = False,
        refresh_snapshot: bool = True,
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "checked": 0,
            "recovered_fill": 0,
            "recovered_position": 0,
            "recovered_open_order": 0,
            "recovered_closed": 0,
            "auto_cleared_no_broker_evidence": 0,
            "path_a_origin_possible": 0,
            "broker_no_evidence": 0,
            "broker_truth_unavailable": 0,
            "ambiguous_broker_truth": 0,
            "permanent_order_reject": 0,
            "session_end_unresolved": 0,
            "manual_reconciliation_required": 0,
            "skipped": 0,
            "errors": [],
        }
        if not force and not path_run_id and not self._unknown_periodic_due(market_key):
            due = self._due_order_unknown_runs(market_key)
            if not due:
                return summary | {"skipped": 1}
            runs = due
        else:
            runs = (
                self._order_unknown_runs_cross_session(market_key)
                if include_cross_session
                else self._order_unknown_runs(market_key)
            )
        if path_run_id:
            direct_run = self.store.find_path_run(path_run_id)
            if (
                direct_run
                and str(direct_run.get("path_run_id", "") or "") == path_run_id
                and str(direct_run.get("market", "") or "").upper() == market_key
                and str(direct_run.get("runtime_mode", "") or "") == self.mode
                and str(direct_run.get("status", "") or "") == "ORDER_UNKNOWN"
                and str(direct_run.get("path_type", "") or "") == "claude_price"
            ):
                runs = [direct_run]
            else:
                runs = [run for run in runs if str(run.get("path_run_id", "") or "") == path_run_id]
        if not runs:
            self._last_unknown_reconcile_at[market_key] = time.time()
            return summary
        if refresh_snapshot:
            try:
                self.refresh_broker_truth(market_key, force=force or session_end or bool(path_run_id))
            except Exception as exc:
                summary["errors"].append(f"snapshot_refresh:{exc}")
        for run in runs:
            try:
                result = self._reconcile_order_unknown_run(
                    run,
                    market_key,
                    force=force,
                    session_end=session_end,
                    auto_clear_no_evidence=auto_clear_no_evidence,
                )
                summary["checked"] += 1
                summary[result] = int(summary.get(result, 0) or 0) + 1
            except Exception as exc:
                summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        self._last_unknown_reconcile_at[market_key] = time.time()
        if summary["checked"] or summary["errors"]:
            log.info(f"[PathB ORDER_UNKNOWN reconcile] {summary}")
        return summary

    def reconcile_order_unknowns_at_open(self, market: str) -> dict[str, Any]:
        market_key = str(market or "").upper()
        initial_errors: list[str] = []
        try:
            self.refresh_broker_truth(market_key, force=True)
        except Exception as exc:
            initial_errors.append(f"snapshot_refresh:{exc}")
        pathb_summary = self.reconcile_order_unknowns(
            market_key,
            force=True,
            include_cross_session=True,
            auto_clear_no_evidence=True,
            refresh_snapshot=False,
        )
        summary: dict[str, Any] = dict(pathb_summary)
        summary["errors"] = initial_errors + list(pathb_summary.get("errors") or [])

        unknown = getattr(self.bot, "v2_order_unknown", None)
        clear_fn = getattr(unknown, "auto_clear_at_session_open", None)
        if callable(clear_fn):
            try:
                escalator_summary = clear_fn(
                    market=market_key,
                    broker_snapshot=self.broker_truth.market_snapshot(market_key),
                )
                summary["escalator"] = escalator_summary
                summary["escalator_market_pause_cleared"] = bool(
                    escalator_summary.get("market_pause_cleared")
                )
            except Exception as exc:
                summary.setdefault("errors", []).append(f"escalator_auto_clear:{exc}")
        if summary.get("checked") or summary.get("escalator") or summary.get("errors"):
            log.info(f"[PathB ORDER_UNKNOWN session_open] {summary}")
        return summary

    def finalize_order_unknowns_at_session_close(self, market: str) -> dict[str, Any]:
        return self.reconcile_order_unknowns(str(market or "").upper(), force=True, session_end=True)

    def _reconcile_order_unknown_run(
        self,
        run: dict[str, Any],
        market: str,
        *,
        force: bool = False,
        session_end: bool = False,
        auto_clear_no_evidence: bool = False,
    ) -> str:
        path_run_id = str(run.get("path_run_id", "") or "")
        plan = self._plan_from_run(run)
        if not path_run_id or plan is None:
            return "skipped"
        permanent_detail = str((run.get("plan") or {}).get("order_unknown_detail", "") or "")
        if self._is_permanent_order_failure(permanent_detail):
            self._set_order_unknown_resolution(
                path_run_id,
                "permanent_order_reject",
                {"permanent_order_reject_detail": permanent_detail},
                next_retry=False,
            )
            self.store.update_path_run(
                path_run_id,
                status="CANCELLED",
                plan={"cancel_reason": "order_unknown_permanent_reject"},
                merge_plan=True,
            )
            return "permanent_order_reject"
        if not force and not self._unknown_recheck_due(run):
            return "skipped"

        ticker = self._ticker_key(market, plan.ticker)
        plan_json = run.get("plan") or {}
        manual_reconciliation_required = bool(
            plan_json.get("manual_reconciliation_required") or plan_json.get("session_end_unresolved")
        )
        exit_unknown = self._order_unknown_is_exit_side(run)
        closed_lifecycle = self._pathb_closed_lifecycle_evidence(
            market,
            ticker,
            plan.session_date,
            path_run_id=path_run_id,
            decision_id=str(run.get("decision_id", "") or plan.decision_id or ""),
        )
        if closed_lifecycle:
            self.store.update_path_run(
                path_run_id,
                status="CLOSED",
                plan={
                    "order_unknown_resolution": "pathb_closed_lifecycle_recovered",
                    "order_unknown_resolution_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "next_broker_truth_recheck_at": "",
                    "close_reason": str(closed_lifecycle.get("close_reason", "") or "CLOSED_USER_MANUAL"),
                    "exit_execution_id": str(closed_lifecycle.get("execution_id", "") or ""),
                    "exit_fill_confirmed": bool(closed_lifecycle.get("execution_id", "")),
                    "pnl_pct": float(closed_lifecycle.get("pnl_pct", 0) or 0),
                    "pathb_closed_lifecycle_evidence": closed_lifecycle,
                },
                merge_plan=True,
            )
            return "recovered_closed"
        local_pathb_pos = self._find_position(market, ticker, path_run_id=path_run_id)
        if local_pathb_pos and not exit_unknown:
            recovered_run = self._recover_order_unknown_local_holding(run, plan, local_pathb_pos)
            if recovered_run is not None:
                return "recovered_position"
        market_data = self.broker_truth.market_snapshot(market)
        market_data_available = not (
            bool(market_data.get("missing"))
            or bool(market_data.get("stale"))
            or str(market_data.get("error", "") or "")
        )
        if market_data_available:
            positions = self._broker_rows_for_ticker(market_data.get("positions", []), market, ticker)
            open_orders = self._broker_rows_for_ticker(market_data.get("open_orders", []), market, ticker)
            fills = self._broker_rows_for_ticker(market_data.get("today_fills", []), market, ticker)
            evidence_payload = {
                "broker_truth_last_success_at": str(market_data.get("last_success_at", "") or ""),
                "broker_position_evidence": bool(positions),
                "broker_open_order_evidence": bool(open_orders),
                "broker_today_fill_evidence": bool(fills),
            }
            if exit_unknown:
                return self._reconcile_exit_order_unknown_run(
                    path_run_id,
                    plan,
                    run,
                    positions,
                    open_orders,
                    fills,
                    evidence_payload,
                    session_end=session_end,
                )
            exact_fill = self._match_pathb_fill_by_execution(plan_json, fills)
            if exact_fill.get("row"):
                self._recover_order_unknown_fill(path_run_id, plan, run, positions, dict(exact_fill["row"]), evidence_payload)
                return "recovered_fill"
            exact_open = self._match_pathb_open_order_by_execution(plan_json, open_orders)
            if exact_open.get("row"):
                row = dict(exact_open["row"])
                execution_id = str(row.get("order_no", "") or (run.get("plan") or {}).get("entry_execution_id") or "")
                qty = int(row.get("order_qty", 0) or row.get("qty", 0) or 0)
                price = float(row.get("avg_price", 0) or row.get("price", 0) or plan.buy_zone_high)
                if execution_id:
                    if not (run.get("plan") or {}).get("entry_execution_id"):
                        self.adapter.mark_order_sent(
                            path_run_id,
                            execution_id=execution_id,
                            price=price,
                            qty=qty,
                            runtime_mode=self.mode,
                            brain_snapshot_id=self._brain_snapshot_id(market),
                        )
                    self.adapter.mark_order_acked(
                        path_run_id,
                        execution_id=execution_id,
                        runtime_mode=self.mode,
                        brain_snapshot_id=self._brain_snapshot_id(market),
                    )
                self._set_order_unknown_resolution(
                    path_run_id,
                    "pathb_open_order_recovered",
                    {**evidence_payload, "recovered_execution_id": execution_id, "recovered_qty": qty, "recovered_price": price},
                    next_retry=True,
                )
                return "recovered_open_order"
            if positions and str(plan_json.get("entry_execution_id", "") or ""):
                row = dict(positions[0])
                qty = int(float(row.get("qty", 0) or row.get("filled_qty", 0) or 0))
                price = float(row.get("avg_price", 0) or row.get("current_price", 0) or plan.buy_zone_high)
                row["filled_qty"] = qty
                row["avg_price"] = price
                row["order_no"] = str(plan_json.get("entry_execution_id", "") or row.get("order_no", "") or "")
                row["side"] = "buy"
                self._recover_order_unknown_fill(
                    path_run_id,
                    plan,
                    run,
                    positions,
                    row,
                    evidence_payload,
                    resolution="pathb_position_recovered",
                )
                return "recovered_position"
        path_a_lifecycle = self._path_a_lifecycle_evidence(
            market,
            ticker,
            plan.session_date,
            exclude_path_run_id=path_run_id,
            exclude_decision_id=str(run.get("decision_id", "") or plan.decision_id or ""),
            exclude_execution_ids={
                str(plan_json.get("entry_execution_id", "") or ""),
                str(plan_json.get("exit_execution_id", "") or ""),
            },
        )
        path_a_pending = self._path_a_pending_evidence(market, ticker)
        if path_a_lifecycle or path_a_pending:
            self._set_order_unknown_resolution(
                path_run_id,
                "path_a_origin_possible",
                {
                    "broker_truth_last_success_at": "",
                    "broker_position_evidence": False,
                    "broker_open_order_evidence": False,
                    "broker_today_fill_evidence": False,
                    "path_a_lifecycle_evidence": path_a_lifecycle,
                    "path_a_pending_evidence": path_a_pending,
                },
                next_retry=False,
            )
            if not local_pathb_pos:
                self.store.update_path_run(
                    path_run_id,
                    status="CANCELLED",
                    plan={"cancel_reason": "order_unknown_path_a_origin_possible"},
                    merge_plan=True,
                )
            return "path_a_origin_possible"

        market_data = self.broker_truth.market_snapshot(market)
        if bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
            if session_end:
                self._set_order_unknown_resolution(
                    path_run_id,
                    "session_end_unresolved",
                    {"session_end_unresolved": True, "manual_reconciliation_required": True},
                    next_retry=False,
                )
                return "session_end_unresolved"
            self._set_order_unknown_resolution(
                path_run_id,
                "broker_truth_unavailable",
                {"broker_truth_error": str(market_data.get("error", "") or ""), "broker_truth_stale": bool(market_data.get("stale"))},
                next_retry=True,
            )
            return "broker_truth_unavailable"

        positions = self._broker_rows_for_ticker(market_data.get("positions", []), market, ticker)
        open_orders = self._broker_rows_for_ticker(market_data.get("open_orders", []), market, ticker)
        fills = self._broker_rows_for_ticker(market_data.get("today_fills", []), market, ticker)
        evidence_payload = {
            "broker_truth_last_success_at": str(market_data.get("last_success_at", "") or ""),
            "broker_position_evidence": bool(positions),
            "broker_open_order_evidence": bool(open_orders),
            "broker_today_fill_evidence": bool(fills),
        }
        if exit_unknown:
            return self._reconcile_exit_order_unknown_run(
                path_run_id,
                plan,
                run,
                positions,
                open_orders,
                fills,
                evidence_payload,
                session_end=session_end,
            )

        fill_match = self._match_pathb_fill(plan, fills)
        if fill_match.get("ambiguous"):
            self._set_order_unknown_resolution(path_run_id, "ambiguous_broker_truth", evidence_payload, next_retry=True)
            return "ambiguous_broker_truth"
        if fill_match.get("row"):
            self._recover_order_unknown_fill(path_run_id, plan, run, positions, dict(fill_match["row"]), evidence_payload)
            return "recovered_fill"

        open_match = self._match_pathb_open_order(plan, open_orders)
        if open_match.get("ambiguous"):
            self._set_order_unknown_resolution(path_run_id, "ambiguous_broker_truth", evidence_payload, next_retry=True)
            return "ambiguous_broker_truth"
        if open_match.get("row"):
            row = dict(open_match["row"])
            execution_id = str(row.get("order_no", "") or (run.get("plan") or {}).get("entry_execution_id") or "")
            qty = int(row.get("order_qty", 0) or row.get("qty", 0) or 0)
            price = float(row.get("avg_price", 0) or row.get("price", 0) or plan.buy_zone_high)
            if execution_id:
                if not (run.get("plan") or {}).get("entry_execution_id"):
                    self.adapter.mark_order_sent(
                        path_run_id,
                        execution_id=execution_id,
                        price=price,
                        qty=qty,
                        runtime_mode=self.mode,
                        brain_snapshot_id=self._brain_snapshot_id(market),
                    )
                self.adapter.mark_order_acked(
                    path_run_id,
                    execution_id=execution_id,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
            self._set_order_unknown_resolution(
                path_run_id,
                "pathb_open_order_recovered",
                {**evidence_payload, "recovered_execution_id": execution_id, "recovered_qty": qty, "recovered_price": price},
                next_retry=True,
            )
            return "recovered_open_order"

        if positions:
            row = dict(positions[0])
            qty = int(float(row.get("qty", 0) or row.get("filled_qty", 0) or 0))
            price = float(row.get("avg_price", 0) or row.get("current_price", 0) or plan.buy_zone_high)
            row["filled_qty"] = qty
            row["avg_price"] = price
            row["order_no"] = str(plan_json.get("entry_execution_id", "") or row.get("order_no", "") or "")
            row["side"] = "buy"
            self._recover_order_unknown_fill(
                path_run_id,
                plan,
                run,
                positions,
                row,
                evidence_payload,
                resolution="pathb_position_recovered",
            )
            return "recovered_position"

        if session_end:
            self._set_order_unknown_resolution(
                path_run_id,
                "session_end_unresolved",
                {"session_end_unresolved": True, "manual_reconciliation_required": True},
                next_retry=False,
            )
            return "session_end_unresolved"
        if auto_clear_no_evidence:
            if manual_reconciliation_required:
                self._set_order_unknown_resolution(
                    path_run_id,
                    "manual_reconciliation_required",
                    {
                        **evidence_payload,
                        "manual_reconciliation_required": True,
                        "auto_clear_no_evidence_blocked": True,
                    },
                    next_retry=True,
                )
                return "manual_reconciliation_required"
            self._set_order_unknown_resolution(
                path_run_id,
                "auto_cleared_no_broker_evidence",
                {**evidence_payload, "order_unknown_auto_cleared": True},
                next_retry=False,
            )
            self.store.update_path_run(
                path_run_id,
                status="CANCELLED",
                plan={"cancel_reason": "order_unknown_auto_cleared_no_broker_evidence"},
                merge_plan=True,
            )
            return "auto_cleared_no_broker_evidence"
        self._set_order_unknown_resolution(path_run_id, "broker_no_evidence", evidence_payload, next_retry=True)
        return "broker_no_evidence"

    def _set_order_unknown_resolution(
        self,
        path_run_id: str,
        resolution: str,
        extra: dict[str, Any] | None = None,
        *,
        next_retry: bool,
    ) -> None:
        now_dt = datetime.now(KST)
        run = self.store.find_path_run(path_run_id) or {}
        plan = run.get("plan") or {}
        first_seen = str(
            plan.get("order_unknown_first_seen_at")
            or run.get("updated_at")
            or run.get("created_at")
            or ""
        )

        def _parse_dt(raw: str) -> datetime | None:
            text = str(raw or "").strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except Exception:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=KST)
            return parsed.astimezone(KST)

        first_seen_dt = _parse_dt(first_seen) or now_dt
        age_sec = max(0, int((now_dt - first_seen_dt).total_seconds()))
        attempts = int(plan.get("order_unknown_reconcile_attempts") or 0) + 1
        soft_sec = int(self.ORDER_UNKNOWN_SOFT_TIMEOUT_SEC)
        hard_sec = int(self.ORDER_UNKNOWN_HARD_TIMEOUT_SEC)
        min_attempts = int(self.ORDER_UNKNOWN_MIN_RECONCILE_ATTEMPTS)
        if age_sec >= hard_sec and attempts >= min_attempts:
            phase = "UNKNOWN_FINAL_BLOCKED"
        elif age_sec >= soft_sec:
            phase = "UNKNOWN_SOFT_TIMEOUT"
        else:
            phase = "UNKNOWN_PENDING"
        payload = {
            "order_unknown_resolution": str(resolution or ""),
            "order_unknown_resolution_at": now_dt.isoformat(timespec="seconds"),
            "order_unknown_first_seen_at": first_seen_dt.isoformat(timespec="seconds"),
            "order_unknown_phase": phase,
            "order_unknown_age_sec": age_sec,
            "order_unknown_reconcile_attempts": attempts,
            "order_unknown_soft_timeout_sec": soft_sec,
            "order_unknown_hard_timeout_sec": hard_sec,
            "order_unknown_min_reconcile_attempts": min_attempts,
        }
        if next_retry:
            payload["next_broker_truth_recheck_at"] = (now_dt + timedelta(minutes=5)).isoformat(timespec="seconds")
        else:
            payload["next_broker_truth_recheck_at"] = ""
        payload.update(extra or {})
        self.store.update_path_run(path_run_id, plan=payload, merge_plan=True)

    def _order_unknown_runs(self, market: str) -> list[dict[str, Any]]:
        return self.store.path_runs_for_session(
            market=str(market or "").upper(),
            runtime_mode=self.mode,
            session_date=self._session_date(str(market or "").upper()),
            status="ORDER_UNKNOWN",
            path_type="claude_price",
        )

    def _order_unknown_runs_cross_session(self, market: str) -> list[dict[str, Any]]:
        market_key = str(market or "").upper()
        candidates = self.store.path_runs_for_session(
            market=market_key,
            runtime_mode=self.mode,
            status="ORDER_UNKNOWN",
            path_type="claude_price",
        )
        retryable: list[dict[str, Any]] = []
        for run in candidates:
            plan = run.get("plan") or {}
            if self._is_permanent_order_failure(str(plan.get("order_unknown_detail", "") or "")):
                retryable.append(run)
                continue
            resolution = str(plan.get("order_unknown_resolution", "") or "")
            if resolution not in self.ORDER_UNKNOWN_OPEN_RETRY_RESOLUTIONS:
                continue
            retryable.append(run)
        sessions: list[str] = []
        for run in sorted(retryable, key=lambda item: str(item.get("session_date", "") or ""), reverse=True):
            session_date = str(run.get("session_date", "") or "")
            if session_date and session_date not in sessions:
                sessions.append(session_date)
            if len(sessions) >= self.ORDER_UNKNOWN_OPEN_LOOKBACK_SESSIONS:
                break
        allowed_sessions = set(sessions)
        return [
            run for run in retryable
            if not allowed_sessions or str(run.get("session_date", "") or "") in allowed_sessions
        ]

    @staticmethod
    def _order_unknown_is_exit_side(run: dict[str, Any]) -> bool:
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        detail = str(plan.get("order_unknown_detail", "") or "").lower()
        pending_reason = str(plan.get("pending_close_reason", "") or "").lower()
        return bool(
            plan.get("exit_execution_id")
            or plan.get("exit_qty")
            or plan.get("sell_order_sent_at")
            or "sell_" in detail
            or "closed_" in pending_reason
            or "pre_close" in pending_reason
        )

    def _reconcile_exit_order_unknown_run(
        self,
        path_run_id: str,
        plan: PricePlan,
        run: dict[str, Any],
        positions: list[dict[str, Any]],
        open_orders: list[dict[str, Any]],
        fills: list[dict[str, Any]],
        evidence_payload: dict[str, Any],
        *,
        session_end: bool,
    ) -> str:
        plan_json = run.get("plan") or {}
        execution_id = str(plan_json.get("exit_execution_id", "") or "")
        requested_qty = int(plan_json.get("exit_qty", 0) or 0)
        if requested_qty <= 0:
            pos = self._find_position(plan.market, plan.ticker, path_run_id=path_run_id) or self._find_position(plan.market, plan.ticker)
            requested_qty = int((pos or {}).get("qty", 0) or 0)

        sell_fills = self._matching_sell_fills(
            fills,
            execution_id=execution_id,
            strict_execution=bool(execution_id),
        )
        filled_qty = sum(int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) for row in sell_fills)
        remaining_balance_qty = self._broker_position_qty(positions)
        open_matches = self._matching_sell_open_orders(
            open_orders,
            execution_id=execution_id,
            strict_execution=bool(execution_id),
        )
        evidence = {
            **evidence_payload,
            "order_unknown_side": "exit",
            "exit_execution_id": execution_id,
            "broker_today_sell_fill_evidence": bool(sell_fills),
            "broker_sell_fill_qty": int(filled_qty),
            "broker_position_qty_after_sell": int(remaining_balance_qty),
            "broker_open_sell_order_evidence": bool(open_matches),
        }

        if requested_qty > 0 and filled_qty >= requested_qty:
            self._finalize_pathb_sell_close(
                plan,
                price=self._weighted_fill_price(sell_fills) or float(plan_json.get("exit_order_price", 0) or 0),
                qty=requested_qty,
                execution_id=execution_id or str((sell_fills[0] if sell_fills else {}).get("order_no", "") or ""),
                close_reason=str(plan_json.get("pending_close_reason") or run.get("pending_close_reason") or "CLOSED_CLAUDE_PRICE_PRE_CLOSE"),
                evidence=evidence,
            )
            self._set_order_unknown_resolution(path_run_id, "pathb_sell_fill_recovered", evidence, next_retry=False)
            return "recovered_closed"

        if filled_qty > 0:
            remaining = max(0, int(requested_qty or filled_qty) - int(filled_qty))
            self._update_local_pathb_remaining_qty(plan, remaining)
            if session_end:
                self._set_order_unknown_resolution(
                    path_run_id,
                    "session_end_unresolved",
                    {
                        **evidence,
                        "session_end_partial_sell_fill": True,
                        "remaining_qty": int(remaining),
                        "manual_reconciliation_required": True,
                    },
                    next_retry=True,
                )
                return "session_end_unresolved"
            self.sell_manager.mark_sell_partial(
                path_run_id,
                execution_id=execution_id,
                price=self._weighted_fill_price(sell_fills) or float(plan_json.get("exit_order_price", 0) or 0),
                filled_qty=int(filled_qty),
                remaining_qty=int(remaining),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(plan.market),
            )
            self._set_order_unknown_resolution(path_run_id, "partial_sell_fill", evidence, next_retry=not session_end)
            return "session_end_unresolved" if session_end else "ambiguous_broker_truth"

        if open_matches:
            if session_end:
                self._set_order_unknown_resolution(
                    path_run_id,
                    "session_end_unresolved",
                    {**evidence, "session_end_open_sell_order": True, "manual_reconciliation_required": True},
                    next_retry=True,
                )
                return "session_end_unresolved"
            self.sell_manager.mark_sell_acked(
                path_run_id,
                execution_id=execution_id or str(open_matches[0].get("order_no", "") or ""),
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(plan.market),
                detail="pathb_sell_open_order_recovered",
            )
            self._set_order_unknown_resolution(path_run_id, "pathb_sell_open_order_recovered", evidence, next_retry=True)
            return "recovered_open_order"

        if session_end:
            self._set_order_unknown_resolution(
                path_run_id,
                "session_end_unresolved",
                {**evidence, "session_end_unresolved": True, "manual_reconciliation_required": True},
                next_retry=False,
            )
            return "session_end_unresolved"

        if self._recover_exit_order_unknown_still_held(
            path_run_id,
            plan,
            run,
            requested_qty=requested_qty,
            broker_position_qty=remaining_balance_qty,
            execution_id=execution_id,
            evidence=evidence,
        ):
            return "recovered_position"

        self._set_order_unknown_resolution(
            path_run_id,
            "sell_fill_not_confirmed" if remaining_balance_qty > 0 else "broker_no_sell_evidence",
            evidence,
            next_retry=True,
        )
        return "ambiguous_broker_truth"

    def _recover_exit_order_unknown_still_held(
        self,
        path_run_id: str,
        plan: PricePlan,
        run: dict[str, Any],
        *,
        requested_qty: int,
        broker_position_qty: int,
        execution_id: str,
        evidence: dict[str, Any],
    ) -> bool:
        """Recover a stale sell ORDER_UNKNOWN when broker truth proves the position is still held."""
        if broker_position_qty <= 0:
            return False
        pos = self._find_position(plan.market, plan.ticker, path_run_id=path_run_id)
        if pos is None:
            return False
        try:
            local_qty = int(float(pos.get("qty", 0) or 0))
        except Exception:
            local_qty = 0
        if local_qty <= 0:
            return False
        expected_qty = int(requested_qty or local_qty)
        if expected_qty > 0 and (broker_position_qty < expected_qty or local_qty < expected_qty):
            return False
        if broker_position_qty != local_qty:
            return False

        plan_json = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        archived_position_fields = self._clear_pathb_sell_evidence_from_position(
            pos,
            market=plan.market,
            path_run_id=path_run_id,
            execution_id=execution_id,
            reason="broker_position_still_held_no_sell_evidence",
        )
        removed_pending_orders = self._remove_pathb_pending_sell_orders(
            plan.market,
            plan.ticker,
            path_run_id=path_run_id,
            execution_id=execution_id,
        )
        payload = {
            **evidence,
            "exit_sell_missing_still_held": True,
            "manual_reconciliation_required": False,
            "session_end_unresolved": False,
            "broker_position_qty_after_sell": int(broker_position_qty),
            "local_position_qty_after_recovery": int(local_qty),
            "stale_exit_execution_id": str(execution_id or ""),
            "stale_exit_qty": int(requested_qty or 0),
            "stale_sell_order_sent_at": str(plan_json.get("sell_order_sent_at", "") or ""),
            "stale_pending_close_reason": str(plan_json.get("pending_close_reason", "") or ""),
            "stale_pathb_position_fields": archived_position_fields,
            "stale_pending_orders_removed": int(removed_pending_orders),
            "exit_execution_id": "",
            "exit_qty": 0,
            "sell_order_sent_at": "",
            "pending_close_reason": "",
            "exit_order_price": 0,
        }
        self._set_order_unknown_resolution(
            path_run_id,
            "exit_sell_missing_still_held",
            payload,
            next_retry=False,
        )
        self.store.update_path_run(path_run_id, status="FILLED", plan={"recovered_to_filled_still_held": True}, merge_plan=True)
        self._release_pathb_sell_attempt_lock(plan.market, plan.ticker, path_run_id)
        log.warning(
            f"[PathB ORDER_UNKNOWN sell recovered as still held] {plan.market} {plan.ticker} "
            f"qty={local_qty} run={path_run_id} stale_order={execution_id or '-'}"
        )
        return True

    def _pathb_closed_lifecycle_evidence(
        self,
        market: str,
        ticker: str,
        session_date: str,
        *,
        path_run_id: str,
        decision_id: str = "",
    ) -> dict[str, Any]:
        events = self.store.events_for_session(market=market, runtime_mode=self.mode, session_date=session_date)
        key = self._ticker_key(market, ticker)
        evidence: dict[str, Any] = {}
        for event in events:
            if self._ticker_key(market, str(event.get("ticker", "") or "")) != key:
                continue
            if str(event.get("event_type", "") or "") != "CLOSED":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            payload_path_run_id = str(payload.get("path_run_id", "") or "")
            payload_path_type = str(payload.get("path_type", "") or "")
            event_decision_id = str(event.get("decision_id", "") or "")
            same_path_run = bool(path_run_id and payload_path_run_id == path_run_id)
            same_decision_pathb = bool(
                decision_id
                and event_decision_id == decision_id
                and payload_path_type == "claude_price"
            )
            if not same_path_run and not same_decision_pathb:
                continue
            evidence = {
                "event_id": event.get("event_id", 0),
                "execution_id": str(event.get("execution_id", "") or ""),
                "reason_code": str(event.get("reason_code", "") or ""),
                "close_reason": str(payload.get("close_reason", "") or event.get("reason_code", "") or ""),
                "pnl_pct": float(payload.get("pnl_pct", 0) or 0),
                "path_run_id": payload_path_run_id,
            }
        return evidence

    def _due_order_unknown_runs(self, market: str) -> list[dict[str, Any]]:
        return [run for run in self._order_unknown_runs(market) if self._unknown_recheck_due(run)]

    def _unknown_periodic_due(self, market: str) -> bool:
        last = float(self._last_unknown_reconcile_at.get(str(market or "").upper(), 0.0) or 0.0)
        return not last or (time.time() - last) >= 600.0

    def _unknown_recheck_due(self, run: dict[str, Any]) -> bool:
        plan = run.get("plan") or {}
        raw = str(plan.get("next_broker_truth_recheck_at", "") or "").strip()
        if not raw:
            return True
        try:
            due_at = datetime.fromisoformat(raw)
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=KST)
            return datetime.now(KST) >= due_at.astimezone(KST)
        except Exception:
            return True

    @staticmethod
    def _is_permanent_order_failure(detail: str) -> bool:
        return is_permanent_order_failure(detail)

    def _path_a_lifecycle_evidence(
        self,
        market: str,
        ticker: str,
        session_date: str,
        *,
        exclude_path_run_id: str = "",
        exclude_decision_id: str = "",
        exclude_execution_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        events = self.store.events_for_session(market=market, runtime_mode=self.mode, session_date=session_date)
        key = self._ticker_key(market, ticker)
        excluded_execs = {str(value or "") for value in (exclude_execution_ids or set()) if str(value or "")}
        evidence: list[dict[str, Any]] = []
        for event in events:
            if self._ticker_key(market, str(event.get("ticker", "") or "")) != key:
                continue
            if str(event.get("event_type", "") or "") not in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "CLOSED"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("path_type", "") or "") == "claude_price" or str(payload.get("path_run_id", "") or ""):
                continue
            execution_id = str(event.get("execution_id", "") or "")
            payload_order_no = str(payload.get("order_no", "") or "")
            if execution_id and execution_id in excluded_execs:
                continue
            if payload_order_no and payload_order_no in excluded_execs:
                continue
            if (
                exclude_decision_id
                and str(event.get("decision_id", "") or "") == exclude_decision_id
                and not execution_id
                and not payload_order_no
            ):
                continue
            if exclude_path_run_id and str(payload.get("path_run_id", "") or "") == exclude_path_run_id:
                continue
            evidence.append(
                {
                    "event_type": event.get("event_type", ""),
                    "execution_id": execution_id,
                    "reason_code": event.get("reason_code", ""),
                }
            )
        return evidence[:5]

    def _path_a_pending_evidence(self, market: str, ticker: str) -> list[dict[str, Any]]:
        key = self._ticker_key(market, ticker)
        evidence: list[dict[str, Any]] = []
        for order in list(getattr(self.bot, "pending_orders", []) or []):
            if str(order.get("market", market) or market).upper() != market:
                continue
            if self._ticker_key(market, str(order.get("ticker", "") or "")) != key:
                continue
            if str(order.get("path_type", "") or "") == "claude_price" or str(order.get("pathb_path_run_id", "") or ""):
                continue
            evidence.append({"order_no": order.get("order_no", ""), "qty": order.get("qty", 0)})
        return evidence[:5]

    def _broker_rows_for_ticker(self, rows: Any, market: str, ticker: str) -> list[dict[str, Any]]:
        key = self._ticker_key(market, ticker)
        out: list[dict[str, Any]] = []
        for row in list(rows or []):
            if not isinstance(row, dict):
                continue
            row_key = self._ticker_key(market, str(row.get("ticker", "") or ""))
            if row_key == key:
                out.append(row)
        return out

    def _match_pathb_fill(self, plan: PricePlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [row for row in rows if self._side_matches(row, "buy") and int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) > 0]
        candidates = self._filter_price_zone(plan, candidates)
        if len(candidates) == 1:
            return {"row": candidates[0]}
        if len(candidates) > 1:
            return {"ambiguous": True, "rows": candidates[:3]}
        return {}

    def _match_pathb_fill_by_execution(self, plan_json: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        execution_id = str(plan_json.get("entry_execution_id", "") or "")
        if not execution_id:
            return {}
        candidates = [
            row for row in rows
            if self._side_matches(row, "buy")
            and int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) > 0
            and str(row.get("order_no", "") or "") == execution_id
        ]
        if len(candidates) == 1:
            return {"row": candidates[0]}
        if len(candidates) > 1:
            return {"ambiguous": True, "rows": candidates[:3]}
        return {}

    def _match_pathb_open_order_by_execution(self, plan_json: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
        execution_id = str(plan_json.get("entry_execution_id", "") or "")
        if not execution_id:
            return {}
        candidates = [
            row for row in rows
            if self._side_matches(row, "buy")
            and int(row.get("remaining_qty", 0) or 0) > 0
            and str(row.get("order_no", "") or "") == execution_id
        ]
        if len(candidates) == 1:
            return {"row": candidates[0]}
        if len(candidates) > 1:
            return {"ambiguous": True, "rows": candidates[:3]}
        return {}

    def _recover_order_unknown_fill(
        self,
        path_run_id: str,
        plan: PricePlan,
        run: dict[str, Any],
        positions: list[dict[str, Any]],
        row: dict[str, Any],
        evidence_payload: dict[str, Any],
        *,
        resolution: str = "pathb_fill_recovered",
    ) -> None:
        qty = int(row.get("filled_qty", 0) or row.get("qty", 0) or row.get("order_qty", 0) or 0)
        price = float(row.get("avg_price", 0) or row.get("fill_price", 0) or row.get("current_price", 0) or plan.buy_zone_high)
        execution_id = str(row.get("order_no", "") or (run.get("plan") or {}).get("entry_execution_id") or "")
        expected_qty = int((run.get("plan") or {}).get("entry_qty", 0) or 0)
        partial = bool(expected_qty and qty > 0 and qty < expected_qty)
        if partial:
            self.adapter.mark_partial_filled(
                path_run_id,
                price=price,
                qty=qty,
                execution_id=execution_id,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(plan.market),
            )
        else:
            self.adapter.mark_filled(
                path_run_id,
                price=price,
                qty=qty,
                execution_id=execution_id,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(plan.market),
            )
        self._attach_recovered_broker_position(plan, positions, row, qty, price, execution_id)
        self._set_order_unknown_resolution(
            path_run_id,
            resolution,
            {**evidence_payload, "recovered_execution_id": execution_id, "recovered_qty": qty, "recovered_price": price},
            next_retry=False,
        )

    def _match_pathb_open_order(self, plan: PricePlan, rows: list[dict[str, Any]]) -> dict[str, Any]:
        candidates = [row for row in rows if self._side_matches(row, "buy") and int(row.get("remaining_qty", 0) or 0) > 0]
        candidates = self._filter_price_zone(plan, candidates)
        if len(candidates) == 1:
            return {"row": candidates[0]}
        if len(candidates) > 1:
            return {"ambiguous": True, "rows": candidates[:3]}
        return {}

    def _filter_price_zone(self, plan: PricePlan, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        low = float(plan.stop_loss or 0)
        high = float(plan.cancel_if_open_above or 0) or float(plan.buy_zone_high or 0) * 1.01
        if low <= 0 or high <= 0:
            return rows
        filtered: list[dict[str, Any]] = []
        for row in rows:
            price = float(row.get("avg_price", 0) or row.get("fill_price", 0) or row.get("price", 0) or 0)
            if price <= 0 or (low <= price <= high):
                filtered.append(row)
        return filtered

    @staticmethod
    def _side_matches(row: dict[str, Any], side: str) -> bool:
        return broker_row_side_matches(row, side, allow_missing_side=True)

    def _attach_recovered_broker_position(
        self,
        plan: PricePlan,
        positions: list[dict[str, Any]],
        fill_row: dict[str, Any],
        qty: int,
        price: float,
        execution_id: str,
    ) -> None:
        if self._find_position(plan.market, plan.ticker, path_run_id=plan.path_run_id):
            return
        if not positions and qty <= 0:
            return
        broker_pos = dict(positions[0]) if positions else {}
        broker_pos.setdefault("ticker", plan.ticker)
        broker_pos.setdefault("name", self._ticker_name(plan.ticker, plan.market))
        broker_pos.setdefault("qty", qty)
        broker_pos.setdefault("avg_price", price)
        broker_pos.setdefault("eval_price", broker_pos.get("current_price", price))
        template = {
            "order_no": execution_id,
            "filled_price_native": price,
            "fill_time": fill_row.get("fill_time", "") or fill_row.get("order_time", ""),
            "strategy": "claude_price",
            "source_strategy": "claude_price",
            "tp_pct": max(0.001, (plan.sell_target / price) - 1.0) if price > 0 else 0.025,
            "sl_pct": max(0.001, 1.0 - (plan.stop_loss / price)) if price > 0 else 0.015,
            "max_hold": 1 if bool(self.config.pathb_intraday_only) else int(plan.hold_days),
            "session_date": plan.session_date,
            "entry_session_date": plan.session_date,
            "v2_decision_id": plan.decision_id,
            "path_type": "claude_price",
            "pathb_path_run_id": plan.path_run_id,
            "pathb_plan": plan.to_dict(),
        }
        maker = getattr(self.bot, "_make_runtime_position_from_broker", None)
        if callable(maker):
            pos = maker(plan.ticker, plan.market, broker_pos, template)
        else:
            pos = {
                "ticker": plan.ticker,
                "name": broker_pos.get("name", ""),
                "entry": self._price_to_krw(price, plan.market),
                "qty": int(qty or broker_pos.get("qty", 0) or 0),
                "current_price": self._price_to_krw(float(broker_pos.get("current_price", price) or price), plan.market),
                "display_avg_price": price,
                "display_current_price": float(broker_pos.get("current_price", price) or price),
                "strategy": "claude_price",
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
                "pathb_plan": plan.to_dict(),
                "v2_decision_id": plan.decision_id,
            }
        self._attach_pathb_position_metadata(pos, plan)
        try:
            getattr(self.bot.risk, "positions").append(pos)
            self.bot._save_positions()
        except Exception as exc:
            log.warning(f"[PathB broker truth] recovered position attach failed {plan.market} {plan.ticker}: {exc}")

    def _exits_allowed(self) -> bool:
        mode = str(self.config.pathb_mode or "").strip().lower()
        return bool(self.config.pathb_enabled) and mode not in {"", "disabled", "off"}

    def _record_blocked(
        self,
        market: str,
        ticker: str,
        decision_id: str,
        reason_code: str,
        payload: dict[str, Any],
        path_run_id: str = "",
    ) -> None:
        try:
            self.bot._v2_record_lifecycle_event(
                "SAFETY_BLOCKED",
                market,
                ticker,
                decision_id=decision_id,
                reason_code=reason_code,
                payload={
                    **(payload or {}),
                    "path_type": "claude_price",
                    "path_run_id": path_run_id,
                },
            )
        except Exception as exc:
            log.warning(f"[PathB blocked record failed] {market} {ticker} {reason_code}: {exc}")
        if reason_code == "MAX_DAILY_ENTRIES":
            try:
                bot = getattr(self, "bot", None)
                alert = getattr(bot, "_maybe_alert_new_buy_block", None)
                if callable(alert):
                    try:
                        daily_count = self._base_daily_entry_count(market)
                    except Exception:
                        daily_count = None
                    try:
                        max_daily_entries = self._base_max_daily_entries(market)
                    except Exception:
                        max_daily_entries = None
                    alert(
                        market,
                        "MAX_DAILY_ENTRIES",
                        "market",
                        {
                            **(payload or {}),
                            "market": market,
                            "ticker": ticker,
                            "strategy": "claude_price",
                            "path_type": "claude_price",
                            "path_run_id": path_run_id,
                            "decision_id": decision_id,
                            "daily_count": daily_count,
                            "max_daily_entries": max_daily_entries,
                        },
                    )
            except Exception as exc:
                log.debug(f"[PathB blocked alert failed] {market} {ticker} {reason_code}: {exc}")
        try:
            bot = getattr(self, "bot", None)
            emit_signal = getattr(bot, "_audit_emit_signal", None)
            active_episode = getattr(bot, "_audit_active_episode", None)
            link_episode = getattr(bot, "_audit_link_signal_episode", None)
            if not callable(emit_signal):
                return
            signal_id = emit_signal(
                market,
                ticker,
                strategy="claude_price",
                signal_at=datetime.now(KST).isoformat(timespec="seconds"),
                signal_price=0.0,
                score=0.0,
                decision="BLOCKED",
                block_reason=reason_code,
                source="path_b_blocked",
                path_type="claude_price",
                path_run_id=path_run_id,
                decision_id=decision_id,
                payload={**(payload or {}), "stage": "pathb_record_blocked"},
            )
            if reason_code == "ORDER_UNKNOWN_UNRESOLVED" and signal_id and callable(active_episode):
                scope = str((payload or {}).get("scope") or "market")
                episode_id = active_episode(
                    market,
                    episode_type="ORDER_UNKNOWN_PAUSE",
                    scope=scope,
                    reason=reason_code,
                    ticker=ticker if scope == "ticker" else "",
                    payload={"stage": "pathb_record_blocked", "reason_code": reason_code, "payload": payload or {}},
                )
                if episode_id and callable(link_episode):
                    link_episode(signal_id, episode_id, reason="ORDER_UNKNOWN_PATHB_BLOCKED")
        except Exception:
            pass

    @staticmethod
    def _pathb_submit_guard_cancels_plan(reason_code: str) -> bool:
        terminal_reasons = {
            "kr_late_entry_closed",
            "max_entry_price_exceeded",
            "same_day_stopped",
            "kr_late_entry_current_price_missing",
            "kr_late_chase_order_time_block",
            "kr_stale_chase_order_time_block",
        }
        temporary_reasons = {
            "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK",
            "order_time_late_entry_metrics_unresolved_allow",
        }
        reason = str(reason_code or "").strip()
        if reason in temporary_reasons:
            return False
        return reason in terminal_reasons or bool(reason)

    def _kr_pathb_submit_gate(self, plan: PricePlan, signal: EntrySignal) -> dict[str, Any]:
        market = str(getattr(plan, "market", "") or "").upper()
        if market != "KR":
            return {"enabled": False, "allowed": True, "reason": "not_kr"}
        gate_fn = getattr(self.bot, "_kr_late_entry_order_time_gate", None)
        if not callable(gate_fn):
            return {"enabled": False, "allowed": True, "reason": "kr_submit_gate_unavailable"}
        try:
            current_price = float(getattr(signal, "price", 0.0) or getattr(signal, "limit_price", 0.0) or 0.0)
        except Exception:
            current_price = 0.0
        try:
            max_entry_price = float(getattr(plan, "cancel_if_open_above", None) or getattr(plan, "buy_zone_high", 0.0) or 0.0)
        except Exception:
            max_entry_price = 0.0
        gate = dict(
            gate_fn(
                plan.ticker,
                current_price=current_price,
                max_entry_price=max_entry_price,
                strategy="claude_price",
                signal_payload={
                    "path_run_id": plan.path_run_id,
                    "origin_action": getattr(plan, "origin_action", "") or "",
                    "origin_route": getattr(plan, "origin_route", "") or "",
                    "registration_scope": getattr(plan, "registration_scope", "") or "",
                    "created_at": getattr(plan, "created_at", "") or "",
                    "signal_price": current_price,
                    "signal_limit_price": getattr(signal, "limit_price", 0.0) or 0.0,
                    "signal_reason": getattr(signal, "reason", "") or "",
                },
            )
        )
        gate.setdefault("enabled", True)
        gate.setdefault("allowed", True)
        gate.setdefault("reason", "")
        gate["stage"] = "pathb_submit_gate"
        if gate.get("allowed") is False:
            gate["cancel_plan"] = self._pathb_submit_guard_cancels_plan(str(gate.get("reason") or ""))
        return gate

    def _plan_from_run(self, run: dict[str, Any]) -> PricePlan | None:
        raw_plan = run.get("plan") or run.get("plan_json") or {}
        if not isinstance(raw_plan, dict):
            return None
        try:
            plan = PricePlan(**{k: raw_plan.get(k) for k in PricePlan.__dataclass_fields__.keys()})
            errors = plan.validate(min_confidence=0.0)
            if errors:
                log.warning(
                    f"[PathB plan reload invalid] {run.get('market', '')} "
                    f"{run.get('ticker', raw_plan.get('ticker', ''))}: {errors}"
                )
                return None
            return plan
        except Exception:
            plan, _errors = parse_plan_from_claude(
                decision_id=str(run.get("decision_id", "") or raw_plan.get("decision_id", "") or ""),
                ticker=str(run.get("ticker", "") or raw_plan.get("ticker", "") or ""),
                market=str(run.get("market", "") or raw_plan.get("market", "") or ""),
                session_date=str(run.get("session_date", "") or raw_plan.get("session_date", "") or ""),
                raw=raw_plan,
                prompt_stage=str(raw_plan.get("prompt_stage", "PRE_SESSION") or "PRE_SESSION"),
                prompt_version=str(raw_plan.get("prompt_version", "pathb_price_v1.0") or "pathb_price_v1.0"),
                min_confidence=0.0,
            )
            if plan is not None:
                errors = plan.validate(min_confidence=0.0)
                if errors:
                    log.warning(
                        f"[PathB plan reload invalid] {run.get('market', '')} "
                        f"{run.get('ticker', raw_plan.get('ticker', ''))}: {errors}"
                    )
                    return None
            return plan

    def _current_native_price(self, market: str, ticker: str) -> float:
        key = self._ticker_key(market, ticker)
        raw = float(getattr(self.bot, "price_cache_raw", {}).get(key, 0) or 0)
        if raw > 0:
            return raw
        try:
            info = get_price(key, _bot_token(self.bot, market), market=market)
            price = float(info.get("price", 0) or 0)
            if price > 0:
                self.bot.price_cache_raw[key] = price
                self.bot.price_cache[key] = self._price_to_krw(price, market)
            return price
        except Exception as exc:
            log.debug(f"[PathB price] {market} {key} failed: {exc}")
            return 0.0

    def _active_exit_runs_for_market(self, market: str) -> list[dict[str, Any]]:
        market_key = str(market or "").upper()
        runs: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(run: dict[str, Any] | None) -> None:
            if not run:
                return
            path_run_id = str(run.get("path_run_id", "") or "")
            if not path_run_id or path_run_id in seen:
                return
            if str(run.get("market", "") or "").upper() != market_key:
                return
            if str(run.get("runtime_mode", "") or "") != self.mode:
                return
            seen.add(path_run_id)
            runs.append(run)

        for run in self.store.path_runs_for_session(
            market=market_key,
            runtime_mode=self.mode,
            session_date=self._session_date(market_key),
        ):
            add(run)

        for pos in self._local_pathb_positions(market_key):
            add(self.store.find_path_run(str(pos.get("pathb_path_run_id", "") or "")))
        return runs

    @staticmethod
    def _pathb_sell_in_flight(run: dict[str, Any] | None, pos: dict[str, Any] | None) -> bool:
        """Return True when a PathB sell is already requested but not reconciled."""
        run = run or {}
        pos = pos or {}
        status = str(run.get("status", "") or "").upper()
        if status in {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}:
            return True
        plan = run.get("plan") or {}
        if isinstance(plan, dict):
            if str(plan.get("exit_execution_id", "") or "").strip():
                return True
            if str(plan.get("sell_order_sent_at", "") or "").strip():
                return True
        if str(pos.get("pathb_closing", "") or "").strip():
            return True
        if str(pos.get("pathb_pending_sell_order_no", "") or "").strip():
            return True
        return False

    def _pathb_sell_attempt_lock_key(self, market: str, ticker: str, path_run_id: str) -> str:
        return ":".join(
            [
                str(market or "").upper(),
                self._ticker_key(str(market or "").upper(), ticker),
                str(path_run_id or ""),
            ]
        )

    def _pathb_sell_attempt_lock_ttl_sec(self) -> float:
        return max(1.0, _env_float("PATHB_SELL_ATTEMPT_LOCK_TTL_SEC", 60.0))

    def _acquire_pathb_sell_attempt_lock(self, market: str, ticker: str, path_run_id: str) -> bool:
        locks = getattr(self, "_pathb_sell_attempt_locks", None)
        if not isinstance(locks, dict):
            locks = {}
            self._pathb_sell_attempt_locks = locks
        now = time.monotonic()
        for key, expires_at in list(locks.items()):
            try:
                if float(expires_at or 0) <= now:
                    locks.pop(key, None)
            except Exception:
                locks.pop(key, None)
        key = self._pathb_sell_attempt_lock_key(market, ticker, path_run_id)
        try:
            expires_at = float(locks.get(key, 0) or 0)
        except Exception:
            expires_at = 0.0
        if expires_at > now:
            return False
        locks[key] = now + self._pathb_sell_attempt_lock_ttl_sec()
        return True

    def _release_pathb_sell_attempt_lock(self, market: str, ticker: str, path_run_id: str) -> None:
        locks = getattr(self, "_pathb_sell_attempt_locks", None)
        if isinstance(locks, dict):
            locks.pop(self._pathb_sell_attempt_lock_key(market, ticker, path_run_id), None)

    def _local_pathb_positions(self, market: str) -> list[dict[str, Any]]:
        market_key = str(market or "").upper()
        positions: list[dict[str, Any]] = []
        for pos in list(getattr(getattr(self.bot, "risk", None), "positions", []) or []):
            path_run_id = str(pos.get("pathb_path_run_id", "") or "")
            if not path_run_id:
                continue
            if self._ticker_market(str(pos.get("ticker", "") or "")) != market_key:
                continue
            try:
                qty = int(float(pos.get("qty", 0) or 0))
            except Exception:
                qty = 0
            if qty <= 0:
                continue
            positions.append(pos)
        return positions

    def _recover_order_unknown_local_holding(
        self,
        run: dict[str, Any],
        plan: PricePlan,
        pos: dict[str, Any],
    ) -> dict[str, Any] | None:
        if self._order_unknown_is_exit_side(run):
            return None
        try:
            qty = int(float(pos.get("qty", 0) or 0))
        except Exception:
            qty = 0
        if qty <= 0:
            return None
        entry_price = self._position_entry_native(pos, plan.market)
        if entry_price <= 0:
            entry_price = float(plan.buy_zone_high or plan.buy_zone_low or 0)
        execution_id = str(
            pos.get("pathb_entry_execution_id", "")
            or pos.get("v2_execution_id", "")
            or pos.get("order_no", "")
            or pos.get("buy_order_no", "")
            or (run.get("plan") or {}).get("entry_execution_id", "")
            or ""
        )
        payload: dict[str, Any] = {
            "order_unknown_resolution": "local_pathb_holding_recovered",
            "order_unknown_resolution_at": datetime.now(KST).isoformat(timespec="seconds"),
            "next_broker_truth_recheck_at": "",
            "local_position_evidence": True,
            "local_position_recovered_at": datetime.now(KST).isoformat(timespec="seconds"),
            "actual_entry_price": float(entry_price or 0),
            "filled_qty": qty,
        }
        if execution_id:
            payload["entry_execution_id"] = execution_id
        else:
            payload["local_recovery_missing_execution_id"] = True
        self.store.update_path_run(plan.path_run_id, status="FILLED", plan=payload, merge_plan=True)
        log.warning(
            f"[PathB ORDER_UNKNOWN local holding recovered] {plan.market} {plan.ticker} "
            f"qty={qty} entry={float(entry_price or 0):g} run={plan.path_run_id}"
        )
        return self.store.find_path_run(plan.path_run_id)

    def _recover_entry_pending_local_holding(
        self,
        run: dict[str, Any],
        plan: PricePlan,
        pos: dict[str, Any],
    ) -> dict[str, Any] | None:
        status = str(run.get("status", "") or "").upper()
        if status not in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}:
            return None
        try:
            qty = int(float(pos.get("qty", 0) or 0))
        except Exception:
            qty = 0
        if qty <= 0:
            return None
        entry_price = self._position_entry_native(pos, plan.market)
        if entry_price <= 0:
            entry_price = float(plan.buy_zone_high or plan.buy_zone_low or 0)
        plan_json = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        execution_id = str(
            pos.get("pathb_entry_execution_id", "")
            or pos.get("v2_execution_id", "")
            or pos.get("order_no", "")
            or pos.get("buy_order_no", "")
            or plan_json.get("entry_execution_id", "")
            or ""
        )
        payload: dict[str, Any] = {
            "entry_pending_resolution": "local_pathb_holding_recovered",
            "entry_pending_recovered_at": datetime.now(KST).isoformat(timespec="seconds"),
            "entry_pending_previous_status": status,
            "actual_entry_price": float(entry_price or 0),
            "filled_qty": qty,
        }
        if execution_id:
            payload["entry_execution_id"] = execution_id
        else:
            payload["local_recovery_missing_execution_id"] = True
        self.store.update_path_run(plan.path_run_id, status="FILLED", plan=payload, merge_plan=True)
        log.warning(
            f"[PathB entry pending local holding recovered] {plan.market} {plan.ticker} "
            f"status={status} qty={qty} entry={float(entry_price or 0):g} run={plan.path_run_id}"
        )
        return self.store.find_path_run(plan.path_run_id)

    def _position_entry_native(self, pos: dict[str, Any], market: str) -> float:
        market_key = str(market or "").upper()
        if market_key == "US":
            entry = float(
                pos.get("display_avg_price", 0)
                or pos.get("avg_price_native", 0)
                or pos.get("avg_price_usd", 0)
                or 0
            )
            if entry <= 0:
                entry = float(pos.get("entry", 0) or pos.get("avg_price", 0) or pos.get("entry_price", 0) or 0)
                fx = self._usd_krw()
                if entry > 1000 and fx > 0:
                    entry = entry / fx
            return entry
        return float(
            pos.get("entry", 0)
            or pos.get("avg_price", 0)
            or pos.get("display_avg_price", 0)
            or pos.get("entry_price", 0)
            or 0
        )

    def _clear_stale_pathb_closing_lock(self, pos: dict[str, Any], market: str, path_run_id: str) -> bool:
        raw = str(pos.get("pathb_closing", "") or "")
        if not raw:
            return False
        try:
            ttl_sec = float(os.getenv("PATHB_CLOSING_LOCK_TTL_SEC", "900") or 900)
        except Exception:
            ttl_sec = 900.0
        if ttl_sec <= 0:
            return False
        try:
            closing_at = datetime.fromisoformat(raw)
            if closing_at.tzinfo is None:
                closing_at = closing_at.replace(tzinfo=KST)
            age_sec = (datetime.now(KST) - closing_at.astimezone(KST)).total_seconds()
        except Exception:
            age_sec = ttl_sec + 1
        if age_sec < ttl_sec:
            return False
        ticker = str(pos.get("ticker", "") or "")
        if self._find_pending_order(str(market or "").upper(), ticker, path_run_id=path_run_id):
            return False
        try:
            qty = int(float(pos.get("qty", 0) or 0))
        except Exception:
            qty = 0
        if qty <= 0:
            return False

        archived = self._clear_pathb_sell_evidence_from_position(
            pos,
            market=str(market or "").upper(),
            path_run_id=path_run_id,
            execution_id="",
            reason="still_held_no_pending_order",
        )
        log.warning(
            f"[PathB stale closing cleared] {market} {ticker} age_sec={age_sec:.0f} "
            f"run={path_run_id}"
        )
        return bool(archived)

    def _clear_pathb_sell_evidence_from_position(
        self,
        pos: dict[str, Any],
        *,
        market: str,
        path_run_id: str,
        execution_id: str,
        reason: str,
    ) -> dict[str, Any]:
        archived: dict[str, Any] = {}
        for field in (
            "pathb_closing",
            "pathb_pending_sell_order_no",
            "pathb_pending_sell_qty",
            "pathb_pending_close_reason",
            "pathb_pending_sell_price",
        ):
            if field in pos:
                archived[field] = pos.pop(field)

        generic_order_no = str(pos.get("pending_sell_order_no", "") or "").strip()
        if generic_order_no and (not execution_id or generic_order_no == str(execution_id or "").strip()):
            for field in (
                "pending_sell_order_no",
                "pending_sell_qty",
                "pending_close_reason",
                "pending_sell_price",
            ):
                if field in pos:
                    archived[field] = pos.pop(field)

        if archived:
            pos["pathb_stale_closing_cleared_at"] = datetime.now(KST).isoformat(timespec="seconds")
            pos["pathb_stale_closing_clear_reason"] = str(reason or "still_held_no_pending_order")
            pos["pathb_stale_closing_cleared_fields"] = archived
            try:
                self._save_positions_if_possible()
            except Exception:
                pass
        return archived

    def _remove_pathb_pending_sell_orders(
        self,
        market: str,
        ticker: str,
        *,
        path_run_id: str,
        execution_id: str,
    ) -> int:
        orders = getattr(self.bot, "pending_orders", None)
        if not isinstance(orders, list) or not orders:
            return 0
        market_key = str(market or "").upper()
        ticker_key = self._ticker_key(market_key, ticker)
        execution_key = str(execution_id or "").strip()
        kept: list[dict[str, Any]] = []
        removed = 0
        for order in orders:
            if not isinstance(order, dict):
                kept.append(order)
                continue
            order_market = str(order.get("market", market_key) or market_key).upper()
            order_ticker = self._ticker_key(market_key, str(order.get("ticker", "") or ""))
            order_path_run_id = str(order.get("pathb_path_run_id") or order.get("path_run_id") or "").strip()
            order_no = str(
                order.get("order_no")
                or order.get("execution_id")
                or order.get("pathb_pending_sell_order_no")
                or order.get("pending_sell_order_no")
                or ""
            ).strip()
            side = str(order.get("side") or order.get("order_side") or order.get("action") or "").lower()
            same_order = bool(execution_key and order_no == execution_key)
            same_path = bool(path_run_id and order_path_run_id == path_run_id)
            same_ticker = order_market == market_key and order_ticker == ticker_key
            sell_like = not side or "sell" in side or side in {"s", "ask"}
            if same_ticker and sell_like and (same_path or same_order):
                removed += 1
                continue
            kept.append(order)
        if removed:
            orders[:] = kept
            try:
                self._save_pending_orders_if_possible()
            except Exception:
                pass
            try:
                self._save_positions_if_possible()
            except Exception:
                pass
        return removed

    def _find_position(self, market: str, ticker: str, *, path_run_id: str = "") -> dict[str, Any] | None:
        key = self._ticker_key(market, ticker)
        for pos in list(getattr(getattr(self.bot, "risk", None), "positions", []) or []):
            pos_key = self._ticker_key(market, str(pos.get("ticker", "") or ""))
            if pos_key != key:
                continue
            if path_run_id and str(pos.get("pathb_path_run_id", "") or "") != path_run_id:
                continue
            return pos
        return None

    def _find_pending_order(self, market: str, ticker: str, *, path_run_id: str = "") -> dict[str, Any] | None:
        key = self._ticker_key(market, ticker)
        for order in list(getattr(self.bot, "pending_orders", []) or []):
            if str(order.get("market", market) or market).upper() != market:
                continue
            if self._ticker_key(market, str(order.get("ticker", "") or "")) != key:
                continue
            if path_run_id and str(order.get("pathb_path_run_id", "") or "") != path_run_id:
                continue
            return order
        return None

    @staticmethod
    def _attach_pathb_order_metadata(order: dict[str, Any], plan: PricePlan) -> None:
        order["path_type"] = "claude_price"
        order["pathb_path_run_id"] = plan.path_run_id
        order["path_run_id"] = plan.path_run_id
        order["pathb_plan"] = plan.to_dict()
        order["v2_decision_id"] = plan.decision_id
        order["entry_route"] = "path_b"
        order["parent_decision_id"] = plan.decision_id
        order["strategy_used"] = "claude_price"
        order["route_source"] = "buy_zone_hit"
        order["pathb_origin_action"] = plan.origin_action
        order["pathb_origin_route"] = plan.origin_route
        order["pathb_registration_scope"] = plan.registration_scope
        order["not_patha_trade_ready"] = bool(plan.not_patha_trade_ready)
        order["pathb_origin_reason"] = plan.origin_reason
        order.setdefault("strategy", "claude_price")
        order.setdefault("source_strategy", "claude_price")

    @staticmethod
    def _attach_pathb_position_metadata(pos: dict[str, Any], plan: PricePlan) -> None:
        pos["path_type"] = "claude_price"
        pos["pathb_path_run_id"] = plan.path_run_id
        pos["path_run_id"] = plan.path_run_id
        pos["pathb_plan"] = plan.to_dict()
        pos["v2_decision_id"] = plan.decision_id
        pos["entry_route"] = "path_b"
        pos["parent_decision_id"] = plan.decision_id
        pos["strategy_used"] = "claude_price"
        pos["route_source"] = "buy_zone_hit"
        pos["pathb_origin_action"] = plan.origin_action
        pos["pathb_origin_route"] = plan.origin_route
        pos["pathb_registration_scope"] = plan.registration_scope
        pos["not_patha_trade_ready"] = bool(plan.not_patha_trade_ready)
        pos["pathb_origin_reason"] = plan.origin_reason
        pos.setdefault("strategy", "claude_price")
        pos.setdefault("source_strategy", "claude_price")

    def _native_hard_stop(self, pos: dict[str, Any], market: str) -> float | None:
        raw_stop = float(pos.get("sl", 0) or 0)
        if raw_stop <= 0:
            return None
        return raw_stop / self._usd_krw() if market == "US" else raw_stop

    def _native_loss_cap_stop(self, pos: dict[str, Any], market: str) -> float | None:
        risk = getattr(self.bot, "risk", None)
        loss_cap_price = getattr(risk, "loss_cap_price", None)
        if not callable(loss_cap_price):
            return None
        try:
            price = float(loss_cap_price(pos, native=(market == "US")) or 0)
        except Exception:
            return None
        return price if price > 0 else None

    def _pathb_exit_meta(self, pos: dict[str, Any], market: str, close_reason: str) -> dict[str, Any]:
        risk = getattr(self.bot, "risk", None)
        meta: dict[str, Any] = {
            "strategy_stop_price": float(pos.get("sl", 0) or 0),
            "peak_pnl_pct": float(pos.get("peak_pnl_pct", 0) or 0),
            "position_mfe_pct": float(pos.get("peak_pnl_pct", 0) or 0),
            "position_mae_pct": float(pos.get("trough_pnl_pct", 0) or 0),
        }
        try:
            if callable(getattr(risk, "position_loss_budget_krw", None)):
                meta["loss_budget_krw"] = float(risk.position_loss_budget_krw(pos) or 0)
            if callable(getattr(risk, "loss_cap_price", None)):
                meta["loss_cap_price"] = float(risk.loss_cap_price(pos) or 0)
            if callable(getattr(risk, "profit_floor_price", None)):
                meta["profit_floor_price"] = float(risk.profit_floor_price(pos) or 0)
            if callable(getattr(risk, "profit_floor_triggered", None)):
                meta["profit_floor_triggered"] = bool(risk.profit_floor_triggered(pos))
        except Exception:
            pass
        if close_reason == "CLOSED_LOSS_CAP":
            meta["effective_stop_price"] = float(meta.get("loss_cap_price", 0) or 0)
            meta["exit_owner"] = "system_hard_rule"
        elif close_reason == "CLOSED_HARD_STOP":
            meta["effective_stop_price"] = float(meta.get("strategy_stop_price", 0) or 0)
            meta["exit_owner"] = "system_hard_rule"
        elif close_reason == "CLOSED_CLAUDE_PRICE_STOP":
            meta["effective_stop_price"] = float(meta.get("strategy_stop_price", 0) or 0)
            meta["exit_owner"] = "claude_price_policy"
        elif close_reason == "CLOSED_MFE_BREAKEVEN":
            entry = float(pos.get("entry", 0) or pos.get("avg_price", 0) or 0)
            try:
                buffer_pct = float(os.getenv("PATHB_MFE_BREAKEVEN_BUFFER_PCT", "0.001") or 0.001)
            except Exception:
                buffer_pct = 0.001
            meta["effective_stop_price"] = entry * (1.0 + max(0.0, buffer_pct)) if entry > 0 else 0.0
            meta["exit_owner"] = "mfe_breakeven_policy"
        elif close_reason == "CLOSED_PROFIT_LADDER":
            meta["exit_owner"] = "profit_ladder_policy"
        elif close_reason == "CLOSED_CLAUDE_SELL":
            meta["exit_owner"] = "claude_sell_policy"
        return meta

    def _maybe_run_pre_close_carry_review(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        minutes_to_close: float,
    ) -> dict[str, Any]:
        if not bool(self.config.pathb_intraday_only):
            return {"reviewed": False, "reason": "intraday_only_disabled"}
        cutoff = int(self.config.new_entry_cutoff_minutes_before_close)
        if float(minutes_to_close or 999.0) > self.PRE_CLOSE_CARRY_REVIEW_MINUTES:
            return {"reviewed": False, "reason": "outside_review_window"}
        if float(minutes_to_close or 999.0) <= float(cutoff):
            return {"reviewed": False, "reason": "inside_force_exit_window"}
        run = self.store.find_path_run(plan.path_run_id)
        plan_json = (run or {}).get("plan") or {}
        if str(plan_json.get("carry_reviewed_at", "") or ""):
            return {"reviewed": False, "reason": "already_reviewed"}

        gate = self._pre_close_carry_gate(plan, pos, current)
        if not bool(gate.get("allowed")):
            decision_payload = {
                "decision": "CARRY",
                "reason": str(gate.get("reason") or "carry_gate_rejected"),
                "confidence": 0.0,
                "advice": {},
                "error": str(gate.get("reason") or "carry_gate_rejected"),
            }
        else:
            decision_payload = self._run_pre_close_carry_review(plan, pos, current, minutes_to_close)
        decision = str(decision_payload.get("decision", "CARRY") or "CARRY").upper()
        if decision not in {"SELL", "CARRY"}:
            decision = "CARRY"
        payload = {
            "carry_source": "pathb_preclose",
            "carry_reviewed_at": datetime.now(KST).isoformat(timespec="seconds"),
            "carry_review_minutes_to_close": float(minutes_to_close or 0),
            "carry_decision": decision,
            "carry_reason": str(decision_payload.get("reason", "") or "")[:500],
            "carry_confidence": float(decision_payload.get("confidence", 0.0) or 0.0),
            "carry_advice": decision_payload.get("advice") if isinstance(decision_payload.get("advice"), dict) else {},
        }
        if str(decision_payload.get("error", "") or ""):
            payload["carry_review_error"] = str(decision_payload.get("error", "") or "")[:500]
        if str(decision_payload.get("reject_reason", "") or ""):
            payload["carry_reject_reason"] = str(decision_payload.get("reject_reason", "") or "")[:500]
        self.store.update_path_run(plan.path_run_id, plan=payload, merge_plan=True)
        log.warning(
            f"[PathB carry review] {plan.market} {plan.ticker} decision={decision} "
            f"pnl={self._position_pnl_pct(pos, current, plan.market):+.2f}% close_in={float(minutes_to_close or 0):.1f}m"
        )
        return {"reviewed": True, **payload}

    def _pre_close_carry_gate(self, plan: PricePlan, pos: dict[str, Any], current: float) -> dict[str, Any]:
        if float(current or 0) <= 0:
            return {"allowed": False, "reason": "missing_current_price"}
        if self._order_unknown_blocked(plan.market):
            return {"allowed": False, "reason": "active_order_unknown_block"}
        try:
            self.refresh_broker_truth(plan.market, force=False)
        except Exception as exc:
            return {"allowed": False, "reason": f"broker_truth_refresh_failed:{exc}"}
        market_data = self.broker_truth.market_snapshot(plan.market)
        if (
            bool(market_data.get("missing"))
            or bool(market_data.get("stale"))
            or str(market_data.get("error", "") or "")
        ):
            return {"allowed": False, "reason": "broker_truth_untrusted"}
        broker_positions = self._broker_rows_for_ticker(market_data.get("positions", []), plan.market, plan.ticker)
        if not broker_positions:
            return {"allowed": False, "reason": "broker_position_missing"}
        qty = int(float(pos.get("qty", 0) or 0))
        if qty <= 0:
            return {"allowed": False, "reason": "local_position_missing"}
        return {"allowed": True, "reason": ""}

    def _run_pre_close_carry_review(
        self,
        plan: PricePlan,
        pos: dict[str, Any],
        current: float,
        minutes_to_close: float,
    ) -> dict[str, Any]:
        try:
            from minority_report.hold_advisor import ask as advisor_ask

            advisor_pos = dict(pos)
            advisor_current = float(current or 0)
            market_key = str(plan.market or "").upper()
            if market_key == "US":
                fx = self._usd_krw()
                advisor_pos["display_current_price"] = advisor_current
                if fx > 0:
                    advisor_pos["current_price"] = advisor_current * fx
                if float(advisor_pos.get("display_avg_price", 0) or 0) <= 0 and fx > 0:
                    entry_value = float(advisor_pos.get("entry", 0) or advisor_pos.get("avg_price", 0) or 0)
                    if entry_value > 1000:
                        advisor_pos["display_avg_price"] = entry_value / fx
                    elif entry_value > 0:
                        advisor_pos["display_avg_price"] = entry_value
            else:
                advisor_pos["current_price"] = advisor_current
                advisor_pos["display_current_price"] = advisor_current
            advisor_pos["pathb_plan"] = plan.to_dict()
            advisor_pos["minutes_to_close"] = float(minutes_to_close or 0)
            builder = getattr(self.bot, "_advisor_pos", None)
            if callable(builder):
                try:
                    advisor_pos = builder(advisor_pos, plan.market)
                except Exception:
                    pass
            digest = self._pre_close_carry_digest(plan.market)
            advice = advisor_ask(
                advisor_pos,
                plan.market,
                digest,
                decision_stage="PRE_CLOSE_CARRY",
                minutes_to_close=float(minutes_to_close or 0),
            )
            action = str((advice or {}).get("action", "SELL") or "SELL").upper()
            decision = "SELL" if action == "SELL" else "CARRY"
            return {
                "decision": decision,
                "reason": self._hold_advice_reason(advice) or action,
                "confidence": float((advice or {}).get("confidence", 0.0) or 0.0),
                "advice": advice if isinstance(advice, dict) else {},
            }
        except Exception as exc:
            log.warning(f"[PathB carry review] hold_advisor failed {plan.market} {plan.ticker}; default CARRY: {exc}")
            return {
                "decision": "CARRY",
                "reason": f"hold_advisor_failed:{exc}",
                "confidence": 0.0,
                "advice": {},
                "error": f"hold_advisor_failed:{exc}",
            }

    def _pre_close_carry_digest(self, market: str) -> str:
        digest = ""
        try:
            digest = str((getattr(self.bot, "today_judgment", {}) or {}).get("digest_prompt", "") or "")
        except Exception:
            digest = ""
        ctx_builder = getattr(self.bot, "_build_intraday_context", None)
        if callable(ctx_builder):
            try:
                intraday = str(ctx_builder(market) or "")
                if intraday:
                    digest = digest + "\n\n[Intraday]\n" + intraday if digest else intraday
            except Exception:
                pass
        return digest

    @staticmethod
    def _hold_advice_reason(advice: Any) -> str:
        if not isinstance(advice, dict):
            return ""
        reason = str(advice.get("reason", "") or "")
        if reason:
            return reason[:500]
        action = str(advice.get("action", "") or "")
        votes = advice.get("votes") if isinstance(advice.get("votes"), dict) else {}
        for vote in votes.values():
            if not isinstance(vote, dict):
                continue
            if action and str(vote.get("action", "") or "").upper() != action.upper():
                continue
            vote_reason = str(vote.get("reason", "") or "")
            if vote_reason:
                return vote_reason[:500]
        return ""

    def _position_pnl_pct(self, pos: dict[str, Any], current: float, market: str = "") -> float:
        market_key = str(market or pos.get("market", "") or "").upper()
        current_native = float(current or 0)
        if market_key == "US":
            fx = self._usd_krw()
            entry = float(pos.get("display_avg_price", 0) or 0)
            if entry <= 0:
                entry = float(
                    pos.get("avg_price", 0)
                    or pos.get("entry_price", 0)
                    or pos.get("entry", 0)
                    or 0
                )
                if entry > 1000 and fx > 0:
                    entry = entry / fx
            if current_native > 1000 and fx > 0:
                current_native = current_native / fx
        else:
            entry = float(
                pos.get("entry", 0)
                or pos.get("avg_price", 0)
                or pos.get("display_avg_price", 0)
                or pos.get("entry_price", 0)
                or 0
            )
        if entry <= 0 or current_native <= 0:
            return 0.0
        return (current_native / entry - 1.0) * 100.0

    def _pre_close_force_exit(self, path_run_id: str, minutes_to_close: float) -> bool:
        if not bool(self.config.pathb_intraday_only):
            return False
        run = self.store.find_path_run(path_run_id)
        plan_json = (run or {}).get("plan") or {}
        if str(plan_json.get("carry_decision", "") or "").upper() != "SELL":
            return False
        return self.sell_manager.pre_close_exit_needed(
            path_run_id,
            minutes_to_close=int(minutes_to_close),
            config_cutoff=int(self.config.new_entry_cutoff_minutes_before_close),
        )

    def _active_path_for_ticker(self, market: str, ticker: str, *, exclude: str = "") -> dict[str, Any] | None:
        for run in self.store.active_path_runs_for_ticker(
            market=market,
            ticker=self._ticker_key(market, ticker),
            session_date=self._session_date(market),
            runtime_mode=self.mode,
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if exclude and str(run.get("path_run_id", "")) == exclude:
                continue
            return run
        return None

    def _patha_holding(self, market: str, ticker: str) -> bool:
        key = self._ticker_key(market, ticker)
        for pos in list(getattr(getattr(self.bot, "risk", None), "positions", []) or []):
            if self._ticker_key(market, str(pos.get("ticker", "") or "")) != key:
                continue
            return str(pos.get("path_type", "") or "") != "claude_price"
        return False

    def _pathb_open_position_count(self, market: str) -> int:
        return sum(
            1
            for pos in list(getattr(getattr(self.bot, "risk", None), "positions", []) or [])
            if self._ticker_market(str(pos.get("ticker", "") or "")) == market
            and str(pos.get("path_type", "") or "") == "claude_price"
        )

    def _pathb_daily_count(self, market: str) -> int:
        return len(self.daily_entry_run_ids(market))

    def daily_entry_count(self, market: str) -> int:
        return self._pathb_daily_count(market)

    def daily_entry_run_ids(self, market: str) -> set[str]:
        run_ids: set[str] = set()
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if str(run.get("status", "")) in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "SELL_SENT", "CLOSED", "ORDER_UNKNOWN"}:
                path_run_id = str(run.get("path_run_id") or "")
                if path_run_id:
                    run_ids.add(path_run_id)
        return run_ids

    def _base_daily_entry_count(self, market: str) -> int:
        try:
            v2 = getattr(self.bot, "v2", None)
            if v2 is not None and hasattr(v2, "daily_entry_count"):
                return int(v2.daily_entry_count(market) or 0)
        except Exception:
            pass
        try:
            return int(getattr(self.bot, "_daily_entry_count", {}).get(market, 0) or 0)
        except Exception:
            return 0

    def _base_max_daily_entries(self, market: str = "") -> int | None:
        try:
            v2 = getattr(self.bot, "v2", None)
            if v2 is not None and hasattr(v2, "max_daily_entries"):
                return v2.max_daily_entries(market)
        except Exception:
            return None
        return None

    def _order_unknown_blocked(self, market: str) -> bool:
        unknown = getattr(self.bot, "v2_order_unknown", None)
        market_key = str(market or "").upper()
        try:
            if unknown is not None and bool(unknown.should_block_market(market_key) or unknown.should_block_global()):
                return True
        except Exception:
            pass
        try:
            unresolved = [
                run for run in self.store.path_runs_for_session(
                    market=market_key,
                    runtime_mode=self.mode,
                    status="ORDER_UNKNOWN",
                    path_type="claude_price",
                )
                if (
                    str((run.get("plan") or {}).get("order_unknown_resolution", "") or "")
                    in self.ORDER_UNKNOWN_OPEN_RETRY_RESOLUTIONS
                    and not self._is_permanent_order_failure(
                        str((run.get("plan") or {}).get("order_unknown_detail", "") or "")
                    )
                )
            ]
            return bool(unresolved)
        except Exception:
            return False

    def _new_buy_block_state(self, market: str, ticker: str = "", strategy: str = "path_b") -> dict[str, Any]:
        fn = getattr(self.bot, "_new_buy_block_state", None)
        gate: dict[str, Any] | None = None
        if callable(fn):
            try:
                gate = dict(fn(market, ticker=ticker, strategy=strategy) or {"allowed": True})
            except TypeError:
                gate = dict(fn(market, ticker, strategy) or {"allowed": True})
            except Exception as exc:
                return {
                    "allowed": False,
                    "blocked": True,
                    "reason": "BROKER_UNTRUSTED",
                    "scope": "market",
                    "details": {"error": str(exc), "stage": "pathb_new_buy_gate"},
                }
            if not bool(gate.get("allowed", True)):
                return gate
        if self._order_unknown_blocked(market):
            return {
                "allowed": False,
                "blocked": True,
                "reason": "ORDER_UNKNOWN_UNRESOLVED",
                "scope": "market",
                "details": {"market": str(market or "").upper(), "ticker": str(ticker or ""), "strategy": strategy},
            }
        if gate is not None:
            return gate
        return {"allowed": True, "blocked": False, "reason": "", "scope": "", "details": {}}

    def _broker_trust_level(self, market: str) -> str:
        try:
            return str(getattr(self.bot, "_broker_state", {}).get(market, {}).get("trust_level", "trusted") or "trusted")
        except Exception:
            return "trusted"

    def _daily_pnl_pct(self, market: str) -> float:
        try:
            return float(self.bot._market_realized_daily_return_pct(market))
        except Exception:
            return 0.0

    def _equity_daily_pnl_pct(self, market: str) -> float:
        try:
            return float(self.bot._market_daily_return_pct(market))
        except Exception:
            try:
                return float(self.bot._daily_pnl_pct(market))
            except Exception:
                return 0.0

    def _session_date(self, market: str) -> str:
        try:
            return str(self.bot._current_session_date_str(market))
        except Exception:
            return datetime.now(KST).date().isoformat()

    def _minutes_to_close(self, market: str) -> float:
        try:
            return float(self.bot._minutes_to_close(market))
        except Exception:
            return 999.0

    def _session_active_for_market(self, market: str) -> bool:
        market_key = str(market or "").upper()
        try:
            return bool(getattr(self.bot, "session_active", False)) and str(getattr(self.bot, "current_market", "") or "").upper() == market_key
        except Exception:
            return False

    def _price_to_krw(self, price: float, market: str) -> float:
        try:
            return float(self.bot._price_to_krw(price, market))
        except Exception:
            return float(price or 0) if market == "KR" else float(price or 0) * self._usd_krw()

    def _pathb_min_order_krw(self, market: str) -> float:
        if str(market or "").upper() == "US":
            krw_min = float(getattr(self.config, "us_min_order_krw", 0) or 0)
            if krw_min > 0:
                return krw_min
            return float(self.config.us_min_order_usd) * self._usd_krw()
        return float(self.config.kr_min_order_krw)

    @staticmethod
    def _pathb_stop_ticker_only(record: dict[str, Any], market: str) -> bool:
        if not _env_bool("STOP_CLUSTER_PATHB_TICKER_ONLY_ENABLED", True):
            return False
        try:
            qty = int(float(record.get("qty", 0) or 0))
        except Exception:
            qty = 0
        try:
            entry_krw = float(record.get("entry", 0) or record.get("risk_price_krw", 0) or 0)
        except Exception:
            entry_krw = 0.0
        try:
            pnl_pct = abs(float(record.get("pnl_pct", 0) or 0))
        except Exception:
            pnl_pct = 0.0
        try:
            max_cost = float(os.getenv("STOP_CLUSTER_PATHB_TICKER_ONLY_MAX_COST_KRW", "250000") or 0)
        except Exception:
            max_cost = 250000.0
        try:
            max_loss_pct = float(os.getenv("STOP_CLUSTER_PATHB_TICKER_ONLY_MAX_LOSS_PCT", "2.5") or 0)
        except Exception:
            max_loss_pct = 2.5
        entry_cost = entry_krw * max(qty, 0)
        cost_ok = max_cost <= 0 or (entry_cost > 0 and entry_cost <= max_cost)
        loss_ok = max_loss_pct <= 0 or pnl_pct <= max_loss_pct
        return bool(cost_ok and loss_ok)

    def _pathb_qty(self, market: str, price_krw: float, *, cash_krw: float) -> int:
        price = float(price_krw or 0)
        if price <= 0:
            return 0
        cash = max(0.0, float(cash_krw or 0))
        budget = float(self.config.pathb_fixed_order_krw)
        early_gate = self._us_early_entry_soft_gate(market)
        if early_gate.get("active"):
            budget *= max(0.1, min(1.0, float(early_gate.get("size_mult") or 0.5)))
        min_order = self._pathb_min_order_krw(market)
        decision = calculate_order_quantity(
            price=price,
            base_budget=budget,
            hard_budget_cap=budget,
            cash_available=cash,
            min_order=min_order,
            size_intent="normal",
            allow_one_share_over_budget=bool(self.config.pathb_allow_one_share_over_budget) and not early_gate.get("active"),
            one_share_max_account_pct=float(self.config.pathb_one_share_over_budget_max_account_pct),
            total_equity=self._pathb_total_equity_krw(market, fallback_cash_krw=cash),
        )
        if "one_share_over_budget_allowed" in decision.warnings:
            max_notional = float(self.config.pathb_one_share_over_budget_max_krw or 0)
            if max_notional > 0 and float(decision.notional or 0) > max_notional:
                return 0
        return max(0, int(decision.qty or 0))

    def _us_early_entry_soft_gate(self, market: str) -> dict[str, Any]:
        try:
            gate = getattr(getattr(self, "bot", None), "_us_early_entry_soft_gate", None)
            if callable(gate):
                result = gate(market)
                return dict(result or {})
        except Exception:
            pass
        return {"active": False, "market": str(market or "").upper()}

    def _pathb_total_equity_krw(self, market: str, *, fallback_cash_krw: float) -> float:
        try:
            getter = getattr(self.bot, "_market_equity_reference_context", None)
            if callable(getter):
                ctx = getter(str(market or "").upper())
                total = float((ctx or {}).get("total_krw", 0) or 0)
                if total > 0:
                    return total
        except Exception:
            pass
        try:
            risk = getattr(self.bot, "risk", None)
            total = float(getattr(risk, "session_start_equity", 0) or 0)
            if total > 0:
                return total
        except Exception:
            pass
        return max(float(fallback_cash_krw or 0), 1.0)

    def _usd_krw(self) -> float:
        return float(getattr(self.bot, "usd_krw_rate", 0) or 1350)

    def _brain_snapshot_id(self, market: str) -> str:
        market_key = str(market or "").upper() or "UNKNOWN"
        try:
            v2 = getattr(self.bot, "v2", None)
            snapshot_id = str(getattr(v2, "brain_snapshot_ids", {}).get(market_key, "") or "")
            if snapshot_id:
                return snapshot_id
        except Exception:
            pass
        return f"pathb_cold_start_{market_key.lower()}"

    def _ticker_market(self, ticker: str) -> str:
        try:
            return str(self.bot._ticker_market(ticker))
        except Exception:
            return infer_ticker_market(ticker, unknown="KR")

    def _ticker_key(self, market: str, ticker: str) -> str:
        raw = str(ticker or "").strip()
        return raw.upper() if str(market or "").upper() == "US" else raw

    def _ticker_name(self, ticker: str, market: str) -> str:
        try:
            return str(self.bot._lookup_ticker_name(ticker, market) or "")
        except Exception:
            return ""

    @staticmethod
    def _scan_due(cache: dict[str, float], market: str, interval_sec: int) -> bool:
        last = float(cache.get(market, 0.0) or 0.0)
        return not last or (time.time() - last) >= float(interval_sec)
