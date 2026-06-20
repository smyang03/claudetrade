from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from execution.claude_price_adapter import ClaudePriceAdapter
from lifecycle.models import LifecycleEventType


KST = ZoneInfo("Asia/Seoul")


def _env_rate(name: str, default: float) -> float:
    try:
        raw = str(os.getenv(name, "") or "").strip()
        return float(raw) if raw else float(default)
    except Exception:
        return float(default)


def _fee_rates_for_market(market: str) -> tuple[float, float]:
    """(매수율, 매도율). KR 매도는 거래세 포함. US는 편도 동일율 (운영자 확인값)."""
    if str(market or "").upper() == "US":
        per_side = _env_rate("US_FEE_RATE_PER_SIDE", 0.0025)
        return per_side, per_side
    return _env_rate("KR_FEE_RATE_BUY", 0.00015), _env_rate("KR_FEE_RATE_SELL", 0.00195)


def _fx_spread_rate_per_side(market: str) -> float:
    """환전 스프레드(편도). US 해외주식은 매수(원→달러)·매도(달러→원) 환전 2회가 발생하고,
    usd_krw는 참조환율이라 이 스프레드가 net에 안 잡힌다. 수수료와 별도로 차감해야 정직한 net.
    한투는 해외거래 시 환전 우대(스프레드 ~0.1%/회) 자동적용 → 기본 0.001. KR은 환전 없음 → 0."""
    if str(market or "").upper() != "US":
        return 0.0
    return _env_rate("US_FX_SPREAD_RATE_PER_SIDE", 0.001)


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

    def _close_cost_meta(
        self,
        path_run_id: str,
        *,
        exit_native: float,
        usd_krw: float = 0.0,
        entry_native_override: float = 0.0,
        qty_override: float = 0.0,
    ) -> dict[str, Any]:
        """수수료/환율 반영 net 손익 추정. 입력 부족 시 빈 dict (gross 기록은 불변).

        진입가 기준은 KIS 체결가(브로커 truth) 우선 — 2026-06-11 운영자 확정.
        호출부가 브로커 포지션 단가를 override로 주면 plan 기록가(주문가일 수 있음)보다 우선한다.
        """
        run = self.store.find_path_run(path_run_id) or {}
        plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
        market = str(run.get("market") or "").upper()
        plan_entry = float(plan.get("actual_entry_price") or 0)
        entry = float(entry_native_override or 0) or plan_entry
        entry_price_source = "broker_position" if float(entry_native_override or 0) > 0 else "plan_recorded"
        qty = float(qty_override or 0) or float(plan.get("filled_qty") or 0)
        exit_px = float(exit_native or 0)
        if entry <= 0 or exit_px <= 0 or qty <= 0:
            return {}
        buy_rate, sell_rate = _fee_rates_for_market(market)
        fee_pct_round_trip = (buy_rate + sell_rate) * 100.0
        fx_spread_rate = _fx_spread_rate_per_side(market)
        fx_spread_pct_round_trip = fx_spread_rate * 2.0 * 100.0  # 매수+매도 환전 2회
        pnl_pct_gross = (exit_px / entry - 1.0) * 100.0
        pnl_pct_net_est = pnl_pct_gross - fee_pct_round_trip
        meta: dict[str, Any] = {
            "fee_pct_round_trip": round(fee_pct_round_trip, 4),
            "pnl_pct_net_est": round(pnl_pct_net_est, 4),
            # FX 스프레드 차감 net (정직한 자) — 기존 pnl_pct_net_est는 수수료만이라 보존
            "fx_spread_pct_round_trip": round(fx_spread_pct_round_trip, 4),
            "pnl_pct_net_after_fx_est": round(pnl_pct_net_est - fx_spread_pct_round_trip, 4),
            "entry_price_source": entry_price_source,
            "entry_native_used": round(entry, 6),
        }
        if market == "US":
            entry_fx = float(plan.get("usd_krw_at_fill") or 0) or float(usd_krw or 0)
            exit_fx = float(usd_krw or 0) or entry_fx
            if entry_fx <= 0 or exit_fx <= 0:
                return meta
            entry_cost_krw = entry * qty * entry_fx
            exit_value_krw = exit_px * qty * exit_fx
            meta["entry_fx"] = round(entry_fx, 2)
            meta["exit_fx"] = round(exit_fx, 2)
            meta["fx_change_pct"] = round((exit_fx / entry_fx - 1.0) * 100.0, 4)
        else:
            entry_cost_krw = entry * qty
            exit_value_krw = exit_px * qty
        fee_krw_est = entry_cost_krw * buy_rate + exit_value_krw * sell_rate
        meta["fee_krw_est"] = round(fee_krw_est, 0)
        meta["pnl_krw_net_est"] = round(exit_value_krw - entry_cost_krw - fee_krw_est, 0)
        fx_spread_krw_est = (entry_cost_krw + exit_value_krw) * fx_spread_rate
        meta["fx_spread_krw_est"] = round(fx_spread_krw_est, 0)
        meta["pnl_krw_net_after_fx_est"] = round(
            exit_value_krw - entry_cost_krw - fee_krw_est - fx_spread_krw_est, 0
        )
        return meta

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
        usd_krw: float = 0.0,
        entry_native_override: float = 0.0,
        qty_override: float = 0.0,
        mfe_pct: float | None = None,
        mae_pct: float | None = None,
        entry_market_regime: str = "",
    ) -> None:
        try:
            cost_meta = self._close_cost_meta(
                path_run_id,
                exit_native=price,
                usd_krw=usd_krw,
                entry_native_override=entry_native_override,
                qty_override=qty_override,
            )
        except Exception:
            cost_meta = {}
        # ORDER_ACKED 중 청산(매수 fill 반영 전 손절 레이스) 시 plan entry를 브로커 단가로 백필
        entry_backfill: dict[str, Any] = {}
        try:
            run = self.store.find_path_run(path_run_id) or {}
            plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
            if float(plan.get("actual_entry_price") or 0) <= 0 and float(entry_native_override or 0) > 0:
                entry_backfill["actual_entry_price"] = float(entry_native_override)
                entry_backfill["entry_price_source"] = "broker_close_backfill"
                if float(plan.get("filled_qty") or 0) <= 0 and float(qty_override or 0) > 0:
                    entry_backfill["filled_qty"] = int(qty_override)
        except Exception:
            entry_backfill = {}
        self.store.update_path_run(
            path_run_id,
            status="CLOSED",
            plan={
                "actual_exit_price": float(price or 0),
                "pnl_pct": float(pnl_pct or 0),
                "close_reason": close_reason,
                "exit_execution_id": execution_id,
                "exit_fill_confirmed": bool(execution_id),
                **cost_meta,
                **entry_backfill,
            },
            merge_plan=True,
        )
        # observe-only MFE/MAE를 CLOSED payload에 실어 학습 sync(v2_learning_performance.mfe_pct)까지
        # 전달한다. Phase 1c가 계산한 값이 여기서 끊겨 학습 원장이 95% NULL이던 배선 버그 수정.
        # ladder 입력(peak_pnl_pct)은 무접촉 — observed_* 별도 키만 흐른다.
        closed_extra = {
            "close_reason": close_reason,
            "price": float(price or 0),
            "pnl_pct": float(pnl_pct or 0),
            **cost_meta,
        }
        if mfe_pct is not None:
            closed_extra["position_mfe_pct"] = float(mfe_pct)
        if mae_pct is not None:
            closed_extra["position_mae_pct"] = float(mae_pct)
        # 진입 시점 국면(모드)을 CLOSED payload에 실어 sync(market_regime)까지 전달. 국면별 적중·
        # 국면조건부 capture 분석의 귀속 기준. 빈값이면 위조하지 않고 생략(mfe/mae와 동일 규율).
        if entry_market_regime:
            closed_extra["entry_market_regime"] = str(entry_market_regime)
        self.adapter._append_event(
            LifecycleEventType.CLOSED,
            path_run_id,
            runtime_mode=runtime_mode,
            brain_snapshot_id=brain_snapshot_id,
            execution_id=execution_id or None,
            position_id=position_id or None,
            reason_code=close_reason,
            path_status="CLOSED",
            extra=closed_extra,
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
