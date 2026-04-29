from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from decision.claude_price_plan import PricePlan, parse_plan_from_claude
from execution.claude_price_adapter import ClaudePriceAdapter, EntrySignal
from execution.claude_price_sell_manager import ClaudePriceSellManager, ExitSignal
from execution.safety_gate import PathBSafetyGate, SafetyContext
from kis_api import cancel_order, get_balance, get_price, place_order, precheck_order
from lifecycle.event_store import EventStore
from logger import get_trading_logger
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime_paths import get_runtime_path
from telegram_reporter import buy_order_alert, send as tg_send


KST = ZoneInfo("Asia/Seoul")
log = get_trading_logger()


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
        self.control_store = PathBControlStore(self.mode)
        self._last_entry_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_exit_scan_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self._last_unknown_reconcile_at: dict[str, float] = {"KR": 0.0, "US": 0.0}
        self.broker_truth = BrokerTruthSnapshot(
            runtime_mode=self.mode,
            token_provider=lambda: str(getattr(self.bot, "token", "") or ""),
            balance_provider=self._balance_for_snapshot,
            date_provider=lambda market: self._session_date(str(market or "").upper()),
        )

    def status(self) -> dict[str, Any]:
        control = self.control_store.load()
        return {
            "enabled": self.is_enabled(),
            "configured_enabled": bool(self.config.pathb_enabled),
            "mode": self.config.pathb_mode,
            "runtime_mode": self.mode,
            "operator_enabled": control.enabled,
            "emergency_disabled": control.emergency_disabled or bool(self.config.pathb_emergency_disable),
            "fixed_order_krw": int(self.config.pathb_fixed_order_krw),
            "max_positions": int(self.config.pathb_max_positions),
            "max_daily_entries": int(self.config.pathb_max_daily_entries),
            "min_confidence": float(self.config.pathb_min_confidence),
            "updated_at": control.updated_at,
            "updated_by": control.updated_by,
            "reason": control.reason,
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

    def register_from_selection_meta(self, market: str, meta: dict[str, Any]) -> list[str]:
        if not self.is_enabled():
            return []
        market = str(market or "").upper()
        trade_ready = list(meta.get("trade_ready") or [])
        price_targets = meta.get("price_targets") or {}
        decision_ids = meta.get("v2_decision_ids") or {}
        session_date = self._session_date(market)
        registered: list[str] = []
        missing_price_targets: list[str] = []
        for ticker in trade_ready:
            key = self._ticker_key(market, ticker)
            raw_plan = price_targets.get(ticker) or price_targets.get(key)
            if not raw_plan:
                missing_price_targets.append(key)
                continue
            if self._active_path_for_ticker(market, key):
                continue
            decision_id = str(decision_ids.get(ticker) or decision_ids.get(key) or "")
            if not decision_id:
                try:
                    decision_id = str(self.bot._v2_decision_id_for_ticker(market, key) or "")
                except Exception:
                    decision_id = ""
            if not decision_id:
                continue
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
                    {"errors": errors, "raw_plan": raw_plan},
                )
                continue
            path_run_id = self.adapter.register_plan(
                plan,
                runtime_mode=self.mode,
                brain_snapshot_id=self._brain_snapshot_id(market),
            )
            registered.append(path_run_id)
            log.info(
                f"[PathB plan] {market} {key} zone={plan.buy_zone_low:g}-{plan.buy_zone_high:g} "
                f"target={plan.sell_target:g} stop={plan.stop_loss:g} conf={plan.confidence:.2f}"
            )
        if missing_price_targets:
            log.warning(
                f"[PathB plan missing] {market} trade_ready without price_targets: "
                f"{missing_price_targets}"
            )
        return registered

    def scan_waiting_entries(self, market: str, *, force: bool = False) -> None:
        market = str(market or "").upper()
        if not self.is_enabled():
            return
        if not force and not self._scan_due(self._last_entry_scan_at, market, 10):
            return
        self._last_entry_scan_at[market] = time.time()
        self.reconcile_order_unknowns(market, force=False)
        self.reconcile_buy_pending_cancel_above(market, force=False)
        entry_gate = self._new_buy_block_state(market, strategy="path_b")
        if not bool(entry_gate.get("allowed", True)):
            log.warning(
                f"[PathB entry scan blocked] {market} {entry_gate.get('reason')} "
                f"scope={entry_gate.get('scope')}"
            )
            return
        for run in self.adapter.get_waiting_runs(market, self.mode, self._session_date(market)):
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            current = self._current_native_price(market, plan.ticker)
            if current <= 0:
                continue
            signal = self.adapter.check_entry(plan.path_run_id, current)
            if signal.reason == "cancel_if_open_above":
                self.adapter.cancel_plan(
                    plan.path_run_id,
                    reason="cancel_if_open_above",
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
                continue
            if not signal.signal:
                continue
            self._submit_buy(plan, signal)

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

    def scan_exits(self, market: str, *, force: bool = False) -> None:
        market = str(market or "").upper()
        if not self._exits_allowed():
            return
        if not force and not self._scan_due(self._last_exit_scan_at, market, 10):
            return
        self._last_exit_scan_at[market] = time.time()
        self.reconcile_sell_pending(market, force=False)
        self.reconcile_filled_positions(market, force=False)
        minutes_to_close = self._minutes_to_close(market)
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if str(run.get("status", "")) not in {"FILLED", "PARTIAL_FILLED"}:
                continue
            plan = self._plan_from_run(run)
            if plan is None:
                continue
            pos = self._find_position(market, plan.ticker, path_run_id=plan.path_run_id)
            if not pos:
                continue
            current = self._current_native_price(market, plan.ticker)
            if current <= 0:
                continue
            hard_stop_price = self._native_hard_stop(pos, market)
            loss_cap_price = self._native_loss_cap_stop(pos, market)
            if (
                loss_cap_price is not None
                and loss_cap_price > 0
                and (hard_stop_price is None or loss_cap_price >= hard_stop_price)
                and current <= loss_cap_price
            ):
                exit_signal = ExitSignal(True, "loss_cap", "CLOSED_LOSS_CAP", current, plan.path_run_id)
            else:
                exit_signal = self.sell_manager.check_exit(plan.path_run_id, current, hard_stop_price=hard_stop_price)
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
            return
        self.adapter.mark_filled(
            path_run_id,
            price=price,
            qty=qty,
            execution_id=str(order.get("v2_execution_id", "") or order.get("order_no", "") or ""),
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(str(order.get("market", run.get("market", "KR")) or "KR")),
        )

    def cancel_waiting(self, market: str, *, reason: str) -> int:
        count = 0
        market = str(market or "").upper()
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if str(run.get("status", "")) not in {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED"}:
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
        return self.sell_manager.expire_all_waiting(
            str(market or "").upper(),
            self.mode,
            self._session_date(str(market or "").upper()),
            brain_snapshot_id=self._brain_snapshot_id(str(market or "").upper()),
        )

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
        entry_gate = self._new_buy_block_state(market, plan.ticker, strategy="path_b")
        if not bool(entry_gate.get("allowed", True)):
            reason = str(entry_gate.get("reason") or "MARKET_CLOSED")
            self._record_blocked(market, plan.ticker, plan.decision_id, reason, entry_gate, plan.path_run_id)
            return False
        risk_price_krw = self._price_to_krw(signal.limit_price, market)
        cash_krw = float(getattr(getattr(self.bot, "risk", None), "cash", 0) or 0)
        min_order_krw = self._pathb_min_order_krw(market)
        qty = self._pathb_qty(market, risk_price_krw, cash_krw=cash_krw)
        order_cost = float(qty) * float(risk_price_krw)
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
            max_daily_entries=self._base_max_daily_entries(),
            daily_pnl_pct=self._daily_pnl_pct(market),
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
        self.adapter.mark_hit(
            plan.path_run_id,
            price=signal.price,
            runtime_mode=self.mode,
            brain_snapshot_id=self._brain_snapshot_id(market),
        )
        try:
            pre = precheck_order(plan.ticker, qty, signal.limit_price, "buy", self.bot.token, market=market)
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
            result = place_order(plan.ticker, qty, signal.limit_price, "buy", self.bot.token, market=market)
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

    def _submit_sell(self, plan: PricePlan, pos: dict[str, Any], signal: ExitSignal) -> bool:
        market = plan.market
        if str(pos.get("pathb_closing", "") or ""):
            return False
        pos["pathb_closing"] = datetime.now(KST).isoformat(timespec="seconds")
        qty = int(pos.get("qty", 0) or 0)
        order_price = self._compute_sell_order_price(market, signal.price)
        if qty <= 0 or order_price < 0:
            pos.pop("pathb_closing", None)
            return False

        try:
            pre = precheck_order(plan.ticker, qty, order_price, "sell", self.bot.token, market=market)
        except Exception as exc:
            pos.pop("pathb_closing", None)
            self._note_sell_failure(market, plan.ticker, signal.reason, f"pathb_precheck_exception:{exc}")
            log.error(f"[PathB SELL PRECHECK EXCEPTION] {market} {plan.ticker}: {exc}")
            return False
        if not pre.get("ok"):
            pos.pop("pathb_closing", None)
            self._note_sell_failure(market, plan.ticker, signal.reason, str(pre.get("msg", "") or "precheck_failed"))
            log.error(f"[PathB SELL PRECHECK FAILED] {market} {plan.ticker}: {pre}")
            return False

        try:
            result = place_order(plan.ticker, qty, order_price, "sell", self.bot.token, market=market)
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
        log.warning(
            f"[PathB SELL SENT] {market} {plan.ticker} qty={qty} order_price={order_price:g} "
            f"order={execution_id} reason={signal.close_reason}"
        )
        return True

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

    def _broker_remaining_qty(self, market: str, ticker: str) -> int | None:
        if self.is_paper:
            return 0
        key = self._ticker_key(market, ticker)
        try:
            balance = get_balance(self.bot.token, market=market, force_refresh=True)
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
        return get_balance(self.bot.token, market=str(market or "").upper(), force_refresh=bool(force))

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
                    self.bot.token,
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
        runs: list[dict[str, Any]] = []
        for status in ("SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"):
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
        sell_fills = self._matching_sell_fills(fills, execution_id=execution_id)
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

        open_matches = self._matching_sell_open_orders(open_orders, execution_id=execution_id)
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

    def _matching_sell_fills(self, fills: list[dict[str, Any]], *, execution_id: str = "") -> list[dict[str, Any]]:
        rows = [row for row in fills if self._side_matches(row, "sell") and int(row.get("filled_qty", 0) or row.get("qty", 0) or 0) > 0]
        if execution_id:
            matched = [row for row in rows if str(row.get("order_no", "") or "") == execution_id]
            if matched:
                return matched
        return rows

    def _matching_sell_open_orders(self, open_orders: list[dict[str, Any]], *, execution_id: str = "") -> list[dict[str, Any]]:
        rows = [row for row in open_orders if self._side_matches(row, "sell") and int(row.get("remaining_qty", 0) or 0) > 0]
        if execution_id:
            matched = [row for row in rows if str(row.get("order_no", "") or "") == execution_id]
            if matched:
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
        if close_reason in {"CLOSED_LOSS_CAP", "CLOSED_HARD_STOP", "CLOSED_CLAUDE_PRICE_STOP"}:
            try:
                key = plan.ticker.upper() if market == "US" else plan.ticker
                self.bot._v2_same_day_stop_tickers.setdefault(market, set()).add(key)
                self.bot._daily_sl_count[market] = int(self.bot._daily_sl_count.get(market, 0) or 0) + 1
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
        log.warning(
            f"[PathB SELL CLOSED] {market} {plan.ticker} qty={qty} price={price_native:g} "
            f"reason={close_reason} order={execution_id}"
        )

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

    def reconcile_order_unknowns(
        self,
        market: str,
        *,
        force: bool = False,
        path_run_id: str = "",
        session_end: bool = False,
    ) -> dict[str, Any]:
        market_key = str(market or "").upper()
        summary: dict[str, Any] = {
            "market": market_key,
            "checked": 0,
            "recovered_fill": 0,
            "recovered_open_order": 0,
            "path_a_origin_possible": 0,
            "broker_no_evidence": 0,
            "broker_truth_unavailable": 0,
            "ambiguous_broker_truth": 0,
            "permanent_order_reject": 0,
            "session_end_unresolved": 0,
            "skipped": 0,
            "errors": [],
        }
        if not force and not path_run_id and not self._unknown_periodic_due(market_key):
            due = self._due_order_unknown_runs(market_key)
            if not due:
                return summary | {"skipped": 1}
            runs = due
        else:
            runs = self._order_unknown_runs(market_key)
        if path_run_id:
            runs = [run for run in runs if str(run.get("path_run_id", "") or "") == path_run_id]
        if not runs:
            self._last_unknown_reconcile_at[market_key] = time.time()
            return summary
        try:
            self.refresh_broker_truth(market_key, force=force or session_end or bool(path_run_id))
        except Exception as exc:
            summary["errors"].append(f"snapshot_refresh:{exc}")
        for run in runs:
            try:
                result = self._reconcile_order_unknown_run(run, market_key, force=force, session_end=session_end)
                summary["checked"] += 1
                summary[result] = int(summary.get(result, 0) or 0) + 1
            except Exception as exc:
                summary["errors"].append(f"{run.get('path_run_id', '?')}:{exc}")
        self._last_unknown_reconcile_at[market_key] = time.time()
        if summary["checked"] or summary["errors"]:
            log.info(f"[PathB ORDER_UNKNOWN reconcile] {summary}")
        return summary

    def finalize_order_unknowns_at_session_close(self, market: str) -> dict[str, Any]:
        summary = self.reconcile_order_unknowns(str(market or "").upper(), force=True, session_end=False)
        final = self.reconcile_order_unknowns(str(market or "").upper(), force=True, session_end=True)
        merged = dict(summary)
        for key, value in final.items():
            if isinstance(value, int):
                merged[key] = int(merged.get(key, 0) or 0) + value
            elif key == "errors":
                merged[key] = list(merged.get(key, []) or []) + list(value or [])
        return merged

    def _reconcile_order_unknown_run(
        self,
        run: dict[str, Any],
        market: str,
        *,
        force: bool = False,
        session_end: bool = False,
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
            return "permanent_order_reject"
        if session_end:
            self._set_order_unknown_resolution(
                path_run_id,
                "session_end_unresolved",
                {"session_end_unresolved": True},
                next_retry=False,
            )
            return "session_end_unresolved"
        if not force and not self._unknown_recheck_due(run):
            return "skipped"

        ticker = self._ticker_key(market, plan.ticker)
        path_a_lifecycle = self._path_a_lifecycle_evidence(market, ticker, plan.session_date)
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
            return "path_a_origin_possible"

        market_data = self.broker_truth.market_snapshot(market)
        if bool(market_data.get("missing")) or bool(market_data.get("stale")) or str(market_data.get("error", "") or ""):
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

        fill_match = self._match_pathb_fill(plan, fills)
        if fill_match.get("ambiguous"):
            self._set_order_unknown_resolution(path_run_id, "ambiguous_broker_truth", evidence_payload, next_retry=True)
            return "ambiguous_broker_truth"
        if fill_match.get("row"):
            row = dict(fill_match["row"])
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
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
            else:
                self.adapter.mark_filled(
                    path_run_id,
                    price=price,
                    qty=qty,
                    execution_id=execution_id,
                    runtime_mode=self.mode,
                    brain_snapshot_id=self._brain_snapshot_id(market),
                )
            self._attach_recovered_broker_position(plan, positions, row, qty, price, execution_id)
            self._set_order_unknown_resolution(
                path_run_id,
                "pathb_fill_recovered",
                {**evidence_payload, "recovered_execution_id": execution_id, "recovered_qty": qty, "recovered_price": price},
                next_retry=False,
            )
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
        payload = {
            "order_unknown_resolution": str(resolution or ""),
            "order_unknown_resolution_at": datetime.now(KST).isoformat(timespec="seconds"),
        }
        if next_retry:
            payload["next_broker_truth_recheck_at"] = (datetime.now(KST) + timedelta(minutes=5)).isoformat(timespec="seconds")
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
        text = str(detail or "").lower()
        permanent_markers = (
            "해당종목정보가 없습니다",
            "exchange mapping",
            "exchange code",
            "unsupported symbol",
            "symbol not found",
            "unknown exchange",
            "ovrs_excg_cd",
        )
        return any(marker.lower() in text for marker in permanent_markers)

    def _path_a_lifecycle_evidence(self, market: str, ticker: str, session_date: str) -> list[dict[str, Any]]:
        events = self.store.events_for_session(market=market, runtime_mode=self.mode, session_date=session_date)
        key = self._ticker_key(market, ticker)
        evidence: list[dict[str, Any]] = []
        for event in events:
            if self._ticker_key(market, str(event.get("ticker", "") or "")) != key:
                continue
            if str(event.get("event_type", "") or "") not in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "CLOSED"}:
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("path_type", "") or "") == "claude_price" or str(payload.get("path_run_id", "") or ""):
                continue
            evidence.append(
                {
                    "event_type": event.get("event_type", ""),
                    "execution_id": event.get("execution_id", ""),
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
        raw = str(row.get("side", "") or "").strip().lower()
        if not raw:
            return True
        if side == "buy":
            return raw in {"buy", "b", "매수", "02"}
        return raw in {"sell", "s", "매도", "01"}

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

    def _plan_from_run(self, run: dict[str, Any]) -> PricePlan | None:
        raw_plan = run.get("plan") or run.get("plan_json") or {}
        if not isinstance(raw_plan, dict):
            return None
        try:
            return PricePlan(**{k: raw_plan.get(k) for k in PricePlan.__dataclass_fields__.keys()})
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
            return plan

    def _current_native_price(self, market: str, ticker: str) -> float:
        key = self._ticker_key(market, ticker)
        raw = float(getattr(self.bot, "price_cache_raw", {}).get(key, 0) or 0)
        if raw > 0:
            return raw
        try:
            info = get_price(key, self.bot.token, market=market)
            price = float(info.get("price", 0) or 0)
            if price > 0:
                self.bot.price_cache_raw[key] = price
                self.bot.price_cache[key] = self._price_to_krw(price, market)
            return price
        except Exception as exc:
            log.debug(f"[PathB price] {market} {key} failed: {exc}")
            return 0.0

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
        order["pathb_plan"] = plan.to_dict()
        order["v2_decision_id"] = plan.decision_id
        order.setdefault("strategy", "claude_price")
        order.setdefault("source_strategy", "claude_price")

    @staticmethod
    def _attach_pathb_position_metadata(pos: dict[str, Any], plan: PricePlan) -> None:
        pos["path_type"] = "claude_price"
        pos["pathb_path_run_id"] = plan.path_run_id
        pos["pathb_plan"] = plan.to_dict()
        pos["v2_decision_id"] = plan.decision_id
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
        elif close_reason in {"CLOSED_HARD_STOP", "CLOSED_CLAUDE_PRICE_STOP"}:
            meta["effective_stop_price"] = float(meta.get("strategy_stop_price", 0) or 0)
            meta["exit_owner"] = "system_hard_rule"
        return meta

    def _pre_close_force_exit(self, path_run_id: str, minutes_to_close: float) -> bool:
        if not bool(self.config.pathb_intraday_only):
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
        count = 0
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=self.mode,
            session_date=self._session_date(market),
        ):
            if str(run.get("path_type", "")) != "claude_price":
                continue
            if str(run.get("status", "")) in {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "SELL_SENT", "CLOSED", "ORDER_UNKNOWN"}:
                count += 1
        return count

    def _base_daily_entry_count(self, market: str) -> int:
        try:
            return int(getattr(self.bot, "_daily_entry_count", {}).get(market, 0) or 0)
        except Exception:
            return 0

    def _base_max_daily_entries(self) -> int | None:
        try:
            v2 = getattr(self.bot, "v2", None)
            if v2 is not None and hasattr(v2, "max_daily_entries"):
                return v2.max_daily_entries()
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
                    session_date=self._session_date(market_key),
                    status="ORDER_UNKNOWN",
                    path_type="claude_price",
                )
            ]
            return len(unresolved) >= 2
        except Exception:
            return False

    def _new_buy_block_state(self, market: str, ticker: str = "", strategy: str = "path_b") -> dict[str, Any]:
        fn = getattr(self.bot, "_new_buy_block_state", None)
        if callable(fn):
            try:
                return dict(fn(market, ticker=ticker, strategy=strategy) or {"allowed": True})
            except TypeError:
                return dict(fn(market, ticker, strategy) or {"allowed": True})
            except Exception as exc:
                return {
                    "allowed": False,
                    "blocked": True,
                    "reason": "BROKER_UNTRUSTED",
                    "scope": "market",
                    "details": {"error": str(exc), "stage": "pathb_new_buy_gate"},
                }
        if self._order_unknown_blocked(market):
            return {
                "allowed": False,
                "blocked": True,
                "reason": "ORDER_UNKNOWN_UNRESOLVED",
                "scope": "market",
                "details": {"market": str(market or "").upper(), "ticker": str(ticker or ""), "strategy": strategy},
            }
        return {"allowed": True, "blocked": False, "reason": "", "scope": "", "details": {}}

    def _broker_trust_level(self, market: str) -> str:
        try:
            return str(getattr(self.bot, "_broker_state", {}).get(market, {}).get("trust_level", "trusted") or "trusted")
        except Exception:
            return "trusted"

    def _daily_pnl_pct(self, market: str) -> float:
        try:
            return float(self.bot._market_daily_return_pct(market))
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

    def _pathb_qty(self, market: str, price_krw: float, *, cash_krw: float) -> int:
        price = float(price_krw or 0)
        if price <= 0:
            return 0
        budget = min(float(self.config.pathb_fixed_order_krw), max(0.0, float(cash_krw or 0)))
        min_order = self._pathb_min_order_krw(market)
        qty = int(budget // price) if budget > 0 else 0
        if min_order > 0 and qty * price < min_order:
            min_qty = int(math.ceil(min_order / price))
            if min_qty * price <= float(cash_krw or 0):
                qty = min_qty
        return max(0, qty)

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
            return "US" if str(ticker or "").isalpha() else "KR"

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
