from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from decision.claude_price_plan import PricePlan
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent, LifecycleEventType
from lifecycle.path_context import attach_path_context


PATHB_ACTIVE_ENTRY_STATUSES = {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}


@dataclass(frozen=True)
class EntrySignal:
    signal: bool
    reason: str = ""
    price: float = 0.0
    limit_price: float = 0.0
    path_run_id: str = ""


def kr_tick_size(price: float) -> int:
    value = float(price or 0)
    if value < 2_000:
        return 1
    if value < 5_000:
        return 5
    if value < 20_000:
        return 10
    if value < 50_000:
        return 50
    if value < 200_000:
        return 100
    if value < 500_000:
        return 500
    return 1_000


def round_up_to_kr_tick(price: float) -> float:
    tick = kr_tick_size(price)
    return float(math.ceil(float(price or 0) / tick) * tick)


def round_down_to_kr_tick(price: float) -> float:
    tick = kr_tick_size(price)
    return float(math.floor(float(price or 0) / tick) * tick)


def round_up_to_cent(price: float) -> float:
    return math.ceil(float(price or 0) * 100.0) / 100.0


def round_down_to_cent(price: float) -> float:
    return math.floor(float(price or 0) * 100.0) / 100.0


class ClaudePriceAdapter:
    def __init__(self, store: EventStore | None = None, config: V2Config = DEFAULT_V2_CONFIG):
        self.store = store or EventStore()
        self.config = config

    def register_plan(self, plan: PricePlan, *, runtime_mode: str, brain_snapshot_id: str) -> str:
        errors = plan.validate(min_confidence=self.config.pathb_min_confidence)
        if errors:
            raise ValueError(f"invalid Claude price plan: {errors}")
        self.store.create_path_run(
            path_run_id=plan.path_run_id,
            decision_id=plan.decision_id,
            path_type="claude_price",
            market=plan.market,
            runtime_mode=runtime_mode,
            session_date=plan.session_date,
            ticker=plan.ticker,
            status="WAITING",
            plan=plan.to_dict(),
        )
        self._append_event(
            LifecycleEventType.CLAUDE_PRICE_PLAN_CREATED,
            plan.path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status="WAITING",
            extra={"plan": plan.to_dict()},
        )
        self._append_event(
            LifecycleEventType.CLAUDE_PRICE_WAITING,
            plan.path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status="WAITING",
        )
        return plan.path_run_id

    def check_entry(self, path_run_id: str, current_price: float) -> EntrySignal:
        run = self.store.find_path_run(path_run_id)
        if not run:
            return EntrySignal(False, "path_run_not_found", path_run_id=path_run_id)
        if str(run.get("status") or "") != "WAITING":
            return EntrySignal(False, "not_waiting", path_run_id=path_run_id)
        plan = run.get("plan") or {}
        price = float(current_price or 0)
        if price <= 0:
            return EntrySignal(False, "invalid_price", path_run_id=path_run_id)
        cancel_above = float(plan.get("cancel_if_open_above") or 0)
        if cancel_above > 0 and price > cancel_above:
            return EntrySignal(False, "cancel_if_open_above", price=price, path_run_id=path_run_id)
        low = float(plan.get("buy_zone_low") or 0)
        high = float(plan.get("buy_zone_high") or 0)
        if not (low <= price <= high):
            return EntrySignal(False, "outside_buy_zone", price=price, path_run_id=path_run_id)
        limit_price = self.compute_buy_limit(str(run.get("market") or ""), price, high)
        if limit_price < price:
            return EntrySignal(False, "ZONE_EDGE_NO_VALID_LIMIT", price=price, limit_price=limit_price, path_run_id=path_run_id)
        return EntrySignal(True, "buy_zone_hit", price=price, limit_price=limit_price, path_run_id=path_run_id)

    def compute_buy_limit(self, market: str, current_price: float, buy_zone_high: float) -> float:
        market_key = str(market or "").upper()
        if market_key == "US":
            slip = round_up_to_cent(float(current_price) * float(self.config.pathb_us_slippage_cap))
            cap = round_down_to_cent(float(buy_zone_high))
            return min(slip, cap)
        slip = round_up_to_kr_tick(float(current_price) * float(self.config.pathb_kr_slippage_cap))
        cap = round_down_to_kr_tick(float(buy_zone_high))
        return min(slip, cap)

    def mark_hit(self, path_run_id: str, *, price: float, runtime_mode: str, brain_snapshot_id: str) -> None:
        self.store.update_path_run(path_run_id, status="HIT", plan={"hit_price": float(price or 0)}, merge_plan=True)
        self._append_event(
            LifecycleEventType.CLAUDE_PRICE_HIT,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status="HIT",
            extra={"price": float(price or 0)},
        )

    def mark_order_sent(
        self,
        path_run_id: str,
        *,
        execution_id: str,
        price: float,
        qty: int,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="ORDER_SENT",
            plan={"entry_execution_id": execution_id, "entry_order_price": float(price or 0), "entry_qty": int(qty or 0)},
            merge_plan=True,
        )
        self._append_event(
            LifecycleEventType.ORDER_SENT,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="ORDER_SENT",
            extra={"price": float(price or 0), "qty": int(qty or 0), "side": "buy"},
        )

    def mark_order_acked(self, path_run_id: str, *, execution_id: str, runtime_mode: str, brain_snapshot_id: str) -> None:
        self.store.update_path_run(path_run_id, status="ORDER_ACKED")
        self._append_event(
            LifecycleEventType.ORDER_ACKED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="ORDER_ACKED",
        )

    def mark_partial_filled(
        self,
        path_run_id: str,
        *,
        price: float,
        qty: int,
        execution_id: str,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="PARTIAL_FILLED",
            plan={"partial_entry_price": float(price or 0), "partial_entry_qty": int(qty or 0)},
            merge_plan=True,
        )
        self._append_event(
            LifecycleEventType.PARTIAL_FILLED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="PARTIAL_FILLED",
            extra={"price": float(price or 0), "qty": int(qty or 0), "side": "buy"},
        )

    def mark_filled(
        self,
        path_run_id: str,
        *,
        price: float,
        qty: int,
        execution_id: str,
        runtime_mode: str,
        brain_snapshot_id: str,
    ) -> None:
        self.store.update_path_run(
            path_run_id,
            status="FILLED",
            plan={"actual_entry_price": float(price or 0), "filled_qty": int(qty or 0), "entry_execution_id": execution_id},
            merge_plan=True,
        )
        self._append_event(
            LifecycleEventType.FILLED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id,
            path_status="FILLED",
            extra={"price": float(price or 0), "qty": int(qty or 0), "side": "buy"},
        )

    def mark_order_unknown(
        self,
        path_run_id: str,
        *,
        detail: str,
        runtime_mode: str,
        brain_snapshot_id: str,
        execution_id: str = "",
    ) -> None:
        self.store.update_path_run(path_run_id, status="ORDER_UNKNOWN", plan={"order_unknown_detail": detail}, merge_plan=True)
        self._append_event(
            LifecycleEventType.ORDER_UNKNOWN,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id or None,
            path_status="ORDER_UNKNOWN",
            extra={"detail": detail},
        )

    def mark_expired(self, path_run_id: str, *, runtime_mode: str, brain_snapshot_id: str) -> bool:
        run = self.store.find_path_run(path_run_id)
        if not run or str(run.get("status") or "") not in {"WAITING", "HIT"}:
            return False
        self.store.update_path_run(path_run_id, status="EXPIRED")
        self._append_event(
            LifecycleEventType.CLAUDE_PRICE_EXPIRED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status="EXPIRED",
        )
        self._record_miss_quality(path_run_id, cancel_reason="EXPIRED")
        return True

    def cancel_plan(self, path_run_id: str, *, reason: str, runtime_mode: str, brain_snapshot_id: str) -> bool:
        run = self.store.find_path_run(path_run_id)
        if not run or str(run.get("status") or "") not in {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED"}:
            return False
        self.store.update_path_run(path_run_id, status="CANCELLED", plan={"cancel_reason": reason}, merge_plan=True)
        self._append_event(
            LifecycleEventType.CLAUDE_PRICE_CANCELLED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            path_status="CANCELLED",
            extra={"reason": reason},
        )
        self._record_miss_quality(path_run_id, cancel_reason=reason)
        return True

    def get_waiting_runs(self, market: str, runtime_mode: str, session_date: str) -> list[dict[str, Any]]:
        return self.store.path_runs_for_session(
            market=market,
            runtime_mode=runtime_mode,
            session_date=session_date,
            status="WAITING",
            path_type="claude_price",
        )

    def _append_event(
        self,
        event_type: str | LifecycleEventType,
        path_run_id: str,
        *,
        runtime_mode: str,
        brain_snapshot_id: str,
        path_status: str,
        execution_id: str | None = None,
        position_id: str | None = None,
        reason_code: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        run = self.store.find_path_run(path_run_id)
        if not run:
            raise KeyError(f"path_run_id not found: {path_run_id}")
        payload = attach_path_context(
            extra or {},
            path_type=str(run.get("path_type") or "claude_price"),
            path_run_id=path_run_id,
            parent_decision_id=str(run.get("decision_id") or ""),
            path_status=path_status,
        )
        self.store.append(
            LifecycleEvent(
                event_type=event_type,
                market=str(run.get("market") or ""),
                runtime_mode=runtime_mode,
                session_date=str(run.get("session_date") or ""),
                ticker=str(run.get("ticker") or ""),
                decision_id=str(run.get("decision_id") or ""),
                prompt_version=str((run.get("plan") or {}).get("prompt_version") or "pathb_price_v1"),
                brain_snapshot_id=brain_snapshot_id,
                execution_id=execution_id,
                position_id=position_id,
                reason_code=reason_code,
                payload=payload,
            )
        )

    def _record_miss_quality(self, path_run_id: str, *, cancel_reason: str) -> None:
        run = self.store.find_path_run(path_run_id)
        if not run:
            return
        plan = run.get("plan") or {}
        if not isinstance(plan, dict):
            plan = {}

        def _num(key: str) -> float | None:
            try:
                value = float(plan.get(key) or 0)
            except Exception:
                value = 0.0
            return value if value > 0 else None

        try:
            self.store.record_pathb_miss_quality(
                path_run_id=path_run_id,
                decision_id=str(run.get("decision_id") or plan.get("decision_id") or ""),
                market=str(run.get("market") or plan.get("market") or ""),
                runtime_mode=str(run.get("runtime_mode") or ""),
                session_date=str(run.get("session_date") or plan.get("session_date") or ""),
                ticker=str(run.get("ticker") or plan.get("ticker") or ""),
                cancel_reason=str(cancel_reason or "CANCELLED"),
                cancelled_at=None,
                current_at_plan=_num("current_at_plan"),
                open_price=_num("open_price"),
                buy_zone_low=_num("buy_zone_low"),
                buy_zone_high=_num("buy_zone_high"),
                cancel_if_open_above=_num("cancel_if_open_above"),
                cancel_trigger_price=_num("cancel_trigger_price"),
                reference_price=_num("reference_price") or _num("entry_order_price") or _num("buy_zone_high"),
                market_close_at=str(plan.get("market_close_at") or ""),
                payload={
                    "recorded_by": "claude_price_adapter",
                    "path_status": str(run.get("status") or ""),
                    "cancel_trigger_source": str(plan.get("cancel_trigger_source") or ""),
                },
            )
        except Exception:
            return
