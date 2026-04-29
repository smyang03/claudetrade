from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from execution.claude_price_adapter import ClaudePriceAdapter
from lifecycle.models import LifecycleEventType


KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class ExitSignal:
    signal: bool
    reason: str = ""
    close_reason: str = ""
    price: float = 0.0
    path_run_id: str = ""


@dataclass(frozen=True)
class StopRevisionResult:
    ok: bool
    reason: str = ""


class ClaudePriceSellManager:
    def __init__(self, adapter: ClaudePriceAdapter, config: V2Config = DEFAULT_V2_CONFIG):
        self.adapter = adapter
        self.store = adapter.store
        self.config = config

    def check_exit(
        self,
        path_run_id: str,
        current_price: float,
        *,
        hard_stop_price: float | None = None,
    ) -> ExitSignal:
        run = self.store.find_path_run(path_run_id)
        if not run:
            return ExitSignal(False, "path_run_not_found", path_run_id=path_run_id)
        status = str(run.get("status") or "")
        if status == "ORDER_UNKNOWN":
            return ExitSignal(False, "order_unknown", path_run_id=path_run_id)
        if status not in {"FILLED", "PARTIAL_FILLED"}:
            return ExitSignal(False, "not_filled", path_run_id=path_run_id)
        plan = run.get("plan") or {}
        price = float(current_price or 0)
        if price <= 0:
            return ExitSignal(False, "invalid_price", path_run_id=path_run_id)
        if hard_stop_price is not None and float(hard_stop_price or 0) > 0 and price <= float(hard_stop_price):
            return ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", price, path_run_id)
        stop_loss = float(plan.get("stop_loss") or 0)
        if stop_loss > 0 and price <= stop_loss:
            return ExitSignal(True, "claude_stop_loss", "CLOSED_CLAUDE_PRICE_STOP", price, path_run_id)
        sell_target = float(plan.get("sell_target") or 0)
        if sell_target > 0 and price >= sell_target:
            return ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", price, path_run_id)
        return ExitSignal(False, "hold", path_run_id=path_run_id)

    def request_stop_revision(
        self,
        path_run_id: str,
        *,
        new_stop_loss: float,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> StopRevisionResult:
        run = self.store.find_path_run(path_run_id)
        if not run:
            return StopRevisionResult(False, "path_run_not_found")
        if str(run.get("status") or "") not in {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED"}:
            return StopRevisionResult(False, "status_not_revisable")
        plan = dict(run.get("plan") or {})
        current_stop = float(plan.get("stop_loss") or 0)
        proposed = float(new_stop_loss or 0)
        if proposed <= 0:
            return StopRevisionResult(False, "invalid_stop")
        if not self.config.pathb_allow_stop_loss_lowering and proposed < current_stop:
            return StopRevisionResult(False, "stop_lowering_forbidden")
        plan["stop_loss"] = proposed
        self.store.update_path_run(path_run_id, plan=plan)
        self.adapter._append_event(
            LifecycleEventType.CLAUDE_PRICE_REVISED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status=str(run.get("status") or ""),
            extra={"new_stop_loss": proposed, "previous_stop_loss": current_stop},
        )
        return StopRevisionResult(True, "revised")

    def mark_sell_order_sent(
        self,
        path_run_id: str,
        *,
        execution_id: str,
        price: float,
        qty: int,
        close_reason: str,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="SELL_SENT",
            plan={
                "exit_execution_id": execution_id,
                "exit_order_price": float(price or 0),
                "exit_qty": int(qty or 0),
                "pending_close_reason": close_reason,
                "sell_order_sent_at": datetime.now(KST).isoformat(timespec="seconds"),
            },
            merge_plan=True,
        )
        self.adapter._append_event(
            LifecycleEventType.ORDER_SENT,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="SELL_SENT",
            extra={"price": float(price or 0), "qty": int(qty or 0), "side": "sell", "close_reason": close_reason},
        )

    def mark_sell_acked(
        self,
        path_run_id: str,
        *,
        execution_id: str,
        runtime_mode: str,
        brain_snapshot_id: str,
        detail: str = "",
    ) -> None:
        run = self.store.find_path_run(path_run_id)
        if not run:
            return
        if str(run.get("status") or "") != "SELL_ACKED":
            self.store.update_path_run(
                path_run_id,
                status="SELL_ACKED",
                plan={"sell_ack_detail": detail},
                merge_plan=True,
            )
            self.adapter._append_event(
                LifecycleEventType.ORDER_ACKED,
                path_run_id,
                runtime_mode=runtime_mode,
                brain_snapshot_id=brain_snapshot_id,
                execution_id=execution_id,
                path_status="SELL_ACKED",
                extra={"side": "sell", "detail": detail},
            )
        elif detail:
            self.store.update_path_run(
                path_run_id,
                plan={"sell_ack_detail": detail},
                merge_plan=True,
            )

    def mark_sell_partial(
        self,
        path_run_id: str,
        *,
        execution_id: str,
        price: float,
        filled_qty: int,
        remaining_qty: int,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="SELL_PARTIAL_FILLED",
            plan={
                "partial_exit_price": float(price or 0),
                "partial_exit_qty": int(filled_qty or 0),
                "remaining_exit_qty": int(remaining_qty or 0),
            },
            merge_plan=True,
        )
        self.adapter._append_event(
            LifecycleEventType.PARTIAL_FILLED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="SELL_PARTIAL_FILLED",
            extra={
                "side": "sell",
                "price": float(price or 0),
                "filled_qty": int(filled_qty or 0),
                "remaining_qty": int(remaining_qty or 0),
                "market_fallback_wait_sec": int(self.config.pathb_sell_partial_wait_sec),
            },
        )

    def mark_closed(
        self,
        path_run_id: str,
        *,
        close_reason: str,
        price: float,
        pnl_pct: float,
        runtime_mode: str,
        brain_snapshot_id: str,
        execution_id: str = "",
        position_id: str = "",
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="CLOSED",
            plan={
                "actual_exit_price": float(price or 0),
                "pnl_pct": float(pnl_pct or 0),
                "close_reason": close_reason,
                "exit_execution_id": execution_id,
                "exit_fill_confirmed": bool(execution_id),
            },
            merge_plan=True,
        )
        self.adapter._append_event(
            LifecycleEventType.CLOSED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id or None,
            position_id=position_id or None,
            reason_code=close_reason,
            path_status="CLOSED",
            extra={"close_reason": close_reason, "price": float(price or 0), "pnl_pct": float(pnl_pct or 0)},
        )
        if close_reason == "CLOSED_CLAUDE_PRICE_TARGET":
            event_type = LifecycleEventType.CLAUDE_PRICE_TARGET_HIT
        elif close_reason == "CLOSED_CLAUDE_PRICE_STOP":
            event_type = LifecycleEventType.CLAUDE_PRICE_STOP_HIT
        else:
            return
        self.adapter._append_event(
            event_type,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id or None,
            position_id=position_id or None,
            reason_code=close_reason,
            path_status="CLOSED",
            extra={"price": float(price or 0), "pnl_pct": float(pnl_pct or 0)},
        )

    def pre_close_exit_needed(self, path_run_id: str, *, minutes_to_close: int, config_cutoff: int = 10) -> bool:
        run = self.store.find_path_run(path_run_id)
        return bool(
            run
            and str(run.get("status") or "") in {"FILLED", "PARTIAL_FILLED"}
            and int(minutes_to_close) <= int(config_cutoff)
        )

    def expire_all_waiting(self, market: str, runtime_mode: str, session_date: str, *, brain_snapshot_id: str) -> int:
        count = 0
        for status in ("WAITING", "HIT"):
            for run in self.store.path_runs_for_session(
                market=market,
                runtime_mode=runtime_mode,
                session_date=session_date,
                status=status,
                path_type="claude_price",
            ):
                if self.adapter.mark_expired(
                    str(run["path_run_id"]),
                    runtime_mode=runtime_mode,
                    brain_snapshot_id=brain_snapshot_id,
                ):
                    count += 1
        return count
