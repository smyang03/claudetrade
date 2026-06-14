from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from config.v2 import DEFAULT_V2_CONFIG, V2Config
from lifecycle.event_store import EventStore


PATHB_BUY_IN_PROGRESS_STATUSES = frozenset(
    {"HIT", "ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED"}
)
PATHB_SELL_IN_PROGRESS_STATUSES = frozenset(
    {"SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED"}
)


@dataclass(frozen=True)
class ArbiterDecision:
    allowed: bool
    reason_code: str = ""
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    shadow: dict[str, Any] = field(default_factory=dict)


class PathExecutionArbiter:
    """Coordinates Path A/Path B execution conflicts without owning SafetyGate rules."""

    def __init__(self, store: EventStore | None = None, config: V2Config = DEFAULT_V2_CONFIG):
        self.store = store or EventStore()
        self.config = config

    def evaluate_path_a_entry(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        current_price: float | None = None,
        strategy: str = "",
    ) -> ArbiterDecision:
        market_key = _normalize_market(market)
        ticker_key = _normalize_ticker(market_key, ticker)
        runs = [
            run
            for run in self.store.active_path_runs_for_ticker(
                market=market_key,
                ticker=ticker_key,
                session_date=session_date,
                runtime_mode=runtime_mode,
            )
            if str(run.get("path_type") or "") == "claude_price"
        ]

        shadow: dict[str, Any] = {}
        for run in runs:
            status = str(run.get("status") or "").upper()
            details = _run_details(run, market_key, ticker_key)
            if status == "ORDER_UNKNOWN":
                return ArbiterDecision(
                    False,
                    "PATHB_ORDER_UNKNOWN_SAME_TICKER",
                    "Path B ORDER_UNKNOWN blocks same-ticker entry",
                    details,
                    shadow,
                )
            if status in PATHB_BUY_IN_PROGRESS_STATUSES:
                return ArbiterDecision(
                    False,
                    "PATHB_ORDER_IN_PROGRESS",
                    "Path B buy order is already in progress for this ticker",
                    details,
                    shadow,
                )
            if status in PATHB_SELL_IN_PROGRESS_STATUSES:
                return ArbiterDecision(
                    False,
                    "PATHB_SELL_IN_PROGRESS",
                    "Path B sell order is in progress for this ticker",
                    details,
                    shadow,
                )
            if status == "WAITING":
                waiting_shadow = {
                    **_waiting_same_ticker_shadow(run, current_price),
                    **self._waiting_price_chase_shadow(run, current_price),
                }
                waiting_shadow["pathb_waiting_strategy"] = str(strategy or "")
                shadow.update(waiting_shadow)

        cancel_shadow = self._cancel_if_open_above_shadow(
            market=market_key,
            runtime_mode=runtime_mode,
            session_date=session_date,
            ticker=ticker_key,
            current_price=current_price,
        )
        if cancel_shadow:
            cancel_shadow["pathb_cancel_strategy"] = str(strategy or "")
            shadow.update(cancel_shadow)

        return ArbiterDecision(True, details={"market": market_key, "ticker": ticker_key}, shadow=shadow)

    def _cancel_if_open_above_shadow(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        current_price: float | None,
    ) -> dict[str, Any]:
        for run in self.store.path_runs_for_session(
            market=market,
            runtime_mode=runtime_mode,
            session_date=session_date,
            status="CANCELLED",
            path_type="claude_price",
        ):
            if _normalize_ticker(market, str(run.get("ticker", "") or "")) != ticker:
                continue
            plan = run.get("plan") or {}
            if str(plan.get("cancel_reason", "") or "") != "cancel_if_open_above":
                continue
            out = {
                "pathb_cancel_price_chase": True,
                "pathb_cancel_reason": "cancel_if_open_above",
                "pathb_cancel_path_run_id": str(run.get("path_run_id") or ""),
                "pathb_cancel_buy_zone_high": plan.get("buy_zone_high"),
                "pathb_cancel_open_above": plan.get("cancel_if_open_above"),
            }
            if current_price is not None:
                try:
                    out["pathb_cancel_current_price"] = float(current_price)
                except Exception:
                    pass
            return out
        return {}

    @staticmethod
    def _waiting_price_chase_shadow(run: dict[str, Any], current_price: float | None) -> dict[str, Any]:
        if current_price is None:
            return {}
        try:
            price = float(current_price or 0.0)
            plan = run.get("plan") or {}
            buy_zone_high = float(plan.get("buy_zone_high") or 0.0)
        except Exception:
            return {}
        if price <= 0 or buy_zone_high <= 0 or price <= buy_zone_high:
            return {}
        chase_pct = ((price / buy_zone_high) - 1.0) * 100.0
        return {
            "pathb_waiting_price_chase": True,
            "pathb_waiting_price_chase_pct": round(chase_pct, 4),
            "pathb_waiting_buy_zone_high": buy_zone_high,
            "pathb_waiting_current_price": price,
            "pathb_waiting_warning": bool(price > buy_zone_high * 1.01),
            "pathb_waiting_path_run_id": str(run.get("path_run_id") or ""),
        }


class SameDayReentryGuard:
    """Blocks same-session live re-entry after a real CLOSED event."""

    STRICT_CLOSE_REASONS = frozenset(
        {
            "CLOSED_LOSS_CAP",
            "CLOSED_HARD_STOP",
            "CLOSED_CLAUDE_PRICE_STOP",
            "LOSS_CAP",
            "STOP_LOSS",
            "HARD_STOP",
        }
    )

    def __init__(self, store: EventStore | None = None, config: V2Config = DEFAULT_V2_CONFIG):
        self.store = store or EventStore()
        self.config = config

    def evaluate(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
        now: datetime | None = None,
    ) -> ArbiterDecision:
        market_key = _normalize_market(market)
        ticker_key = _normalize_ticker(market_key, ticker)
        current = _ensure_aware(now or datetime.now(timezone.utc))
        # 멀티데이 반복 손실 종목 쿨다운(IREN/IONQ형 반복 적자 차단). same-day 게이트보다 선행.
        repeat_block = self._repeat_loss_gate(market_key, runtime_mode, ticker_key, current)
        if repeat_block is not None:
            return repeat_block
        last_closed = self._last_closed_event(
            market=market_key,
            runtime_mode=runtime_mode,
            session_date=session_date,
            ticker=ticker_key,
        )
        if not last_closed:
            return ArbiterDecision(True, details={"market": market_key, "ticker": ticker_key})

        reason = str(last_closed.get("reason_code") or "")
        payload = last_closed.get("payload") or {}
        close_reason = str(payload.get("close_reason") or reason)
        pnl_pct = self._payload_pnl_pct(payload)

        closed_at = _parse_dt(str(last_closed.get("occurred_at") or last_closed.get("created_at") or ""))
        age_min = None
        if closed_at is not None:
            age_min = max(0.0, (current - closed_at.astimezone(current.tzinfo)).total_seconds() / 60.0)
        cooldown = self._cooldown_minutes(market_key, close_reason, pnl_pct)
        shadow = {
            "same_day_reentry": True,
            "same_day_reentry_closed_event_id": last_closed.get("event_id"),
            "same_day_reentry_closed_decision_id": last_closed.get("decision_id"),
            "same_day_reentry_closed_execution_id": last_closed.get("execution_id"),
            "same_day_reentry_closed_position_id": last_closed.get("position_id"),
            "same_day_reentry_close_reason": close_reason,
            "same_day_reentry_pnl_pct": pnl_pct,
            "same_day_reentry_closed_at": str(last_closed.get("occurred_at") or ""),
            "same_day_reentry_age_minutes": round(age_min, 2) if age_min is not None else None,
            "same_day_reentry_cooldown_minutes": cooldown,
        }
        if self._is_strict_stop_close(close_reason, pnl_pct):
            shadow["same_day_reentry_policy"] = "session_block_after_stop"
            return ArbiterDecision(
                False,
                "SAME_DAY_REENTRY_AFTER_STOP",
                "same-day re-entry is blocked after a stop/loss close",
                {"market": market_key, "ticker": ticker_key, **shadow},
                shadow,
            )
        shadow["same_day_reentry_policy"] = "profit_exit_cooldown"
        if age_min is None or age_min < cooldown:
            return ArbiterDecision(
                False,
                "SAME_DAY_REENTRY_COOLDOWN",
                "same-day re-entry cooldown is active",
                {"market": market_key, "ticker": ticker_key, **shadow},
                shadow,
            )
        return ArbiterDecision(True, details={"market": market_key, "ticker": ticker_key}, shadow=shadow)

    def _payload_pnl_pct(self, payload: dict[str, Any]) -> float | None:
        try:
            if "pnl_pct" not in payload:
                return None
            return float(payload.get("pnl_pct"))
        except Exception:
            return None

    def _is_strict_stop_close(self, close_reason: str, pnl_pct: float | None = None) -> bool:
        if pnl_pct is not None and pnl_pct < 0:
            return True
        return str(close_reason or "").upper() in self.STRICT_CLOSE_REASONS

    def _cooldown_minutes(self, market: str, close_reason: str = "", pnl_pct: float | None = None) -> int:
        if self._is_strict_stop_close(close_reason, pnl_pct):
            return (
                int(self.config.us_reentry_cooldown_minutes)
                if market == "US"
                else int(self.config.kr_reentry_cooldown_minutes)
            )
        return (
            int(self.config.us_profit_reentry_cooldown_minutes)
            if market == "US"
            else int(self.config.kr_profit_reentry_cooldown_minutes)
        )

    def _last_closed_event(
        self,
        *,
        market: str,
        runtime_mode: str,
        session_date: str,
        ticker: str,
    ) -> dict[str, Any] | None:
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM lifecycle_events
                WHERE market=? AND runtime_mode=? AND session_date=? AND ticker=? AND event_type='CLOSED'
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT 20
                """,
                (market, runtime_mode, session_date, ticker),
            ).fetchall()
        for row in rows:
            event = self.store._event_row_to_dict(row)
            if _is_broker_sync_close(event):
                continue
            return event
        return None

    def _repeat_loss_gate(
        self,
        market: str,
        runtime_mode: str,
        ticker: str,
        now: datetime,
    ) -> "ArbiterDecision | None":
        """최근 lookback일 내 같은 종목이 반복 손실 청산되면 cooldown 동안 재진입을 차단한다.

        IREN(10회 진입 누적 -2.5%)/IONQ(5회 -9.1%)형 반복 적자 종목이 진입 후 즉시 역행
        (loss_cap MFE 중앙 +0.39%, 88%가 MFE<1%)하는 것을 막는다. broker-sync close는 제외.
        """
        if str(os.getenv("PATHB_REPEAT_LOSS_GATE_ENABLED", "true")).strip().lower() not in ("1", "true", "yes", "on"):
            return None
        try:
            lookback_days = int(float(os.getenv("PATHB_REPEAT_LOSS_LOOKBACK_DAYS", "10") or 10))
            max_losses = int(float(os.getenv("PATHB_REPEAT_LOSS_MAX", "3") or 3))
            cooldown_hours = float(os.getenv("PATHB_REPEAT_LOSS_COOLDOWN_HOURS", "48") or 48)
        except Exception:
            lookback_days, max_losses, cooldown_hours = 10, 3, 48.0
        if max_losses <= 0:
            return None
        cutoff = now - timedelta(days=lookback_days)
        with self.store.connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM lifecycle_events
                WHERE market=? AND runtime_mode=? AND ticker=? AND event_type='CLOSED'
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT 40
                """,
                (market, runtime_mode, ticker),
            ).fetchall()
        losses = 0
        last_loss_at: datetime | None = None
        for row in rows:
            event = self.store._event_row_to_dict(row)
            if _is_broker_sync_close(event):
                continue
            occ = _parse_dt(str(event.get("occurred_at") or event.get("created_at") or ""))
            if occ is None:
                continue
            occ = occ.astimezone(now.tzinfo)
            if occ < cutoff:
                break
            payload = event.get("payload") or {}
            pnl = self._payload_pnl_pct(payload)
            close_reason = str(payload.get("close_reason") or event.get("reason_code") or "")
            is_loss = (pnl is not None and pnl < 0) or close_reason.upper() in self.STRICT_CLOSE_REASONS
            if is_loss:
                losses += 1
                if last_loss_at is None:
                    last_loss_at = occ
        if losses >= max_losses and last_loss_at is not None:
            age_hours = (now - last_loss_at).total_seconds() / 3600.0
            if age_hours < cooldown_hours:
                shadow = {
                    "repeat_loss_count": losses,
                    "repeat_loss_lookback_days": lookback_days,
                    "repeat_loss_last_at": str(last_loss_at),
                    "repeat_loss_cooldown_hours": cooldown_hours,
                    "repeat_loss_age_hours": round(age_hours, 2),
                }
                return ArbiterDecision(
                    False,
                    "REPEAT_LOSS_COOLDOWN",
                    f"repeated losses ({losses}) within {lookback_days}d; cooldown active",
                    {"market": market, "ticker": ticker, **shadow},
                    shadow,
                )
        return None


def build_late_entry_payload(
    *,
    entry_elapsed_min: float | None = None,
    change_pct_at_entry: float | None = None,
    from_high_pct: float | None = None,
    selected_reason: str = "",
    arbiter_shadow: dict[str, Any] | None = None,
    reentry_shadow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shadow: dict[str, Any] = {}
    shadow.update(arbiter_shadow or {})
    shadow.update(reentry_shadow or {})
    is_at_high = False
    try:
        is_at_high = from_high_pct is not None and float(from_high_pct) >= -1.5
    except Exception:
        is_at_high = False
    score = 0.0
    try:
        if entry_elapsed_min is not None and float(entry_elapsed_min) >= 120:
            score += 0.25
        if entry_elapsed_min is not None and float(entry_elapsed_min) >= 180:
            score += 0.15
        if change_pct_at_entry is not None and float(change_pct_at_entry) >= 10:
            score += 0.25
        if change_pct_at_entry is not None and float(change_pct_at_entry) >= 20:
            score += 0.15
        if is_at_high:
            score += 0.15
        if bool(shadow.get("same_day_reentry")):
            score += 0.20
        if bool(shadow.get("pathb_waiting_price_chase")):
            score += 0.15
    except Exception:
        score = 0.0
    return {
        "late_entry_score": round(min(score, 1.0), 4),
        "entry_elapsed_min": _round_or_none(entry_elapsed_min),
        "change_pct_at_entry": _round_or_none(change_pct_at_entry),
        "from_high_pct": _round_or_none(from_high_pct),
        "is_at_high": bool(is_at_high),
        "same_day_reentry": bool(shadow.get("same_day_reentry", False)),
        "pathb_order_unknown_conflict": False,
        "pathb_waiting_price_chase": bool(shadow.get("pathb_waiting_price_chase", False)),
        "pathb_waiting_price_chase_pct": shadow.get("pathb_waiting_price_chase_pct"),
        "selected_reason": str(selected_reason or ""),
        **shadow,
    }


def _run_details(run: dict[str, Any], market: str, ticker: str) -> dict[str, Any]:
    return {
        "market": market,
        "ticker": ticker,
        "path_type": str(run.get("path_type") or ""),
        "path_run_id": str(run.get("path_run_id") or ""),
        "path_status": str(run.get("status") or ""),
        "decision_id": str(run.get("decision_id") or ""),
    }


def _waiting_same_ticker_shadow(run: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    plan = run.get("plan") or {}
    out = {
        "pathb_waiting_same_ticker": True,
        "pathb_waiting_shadow_reason": "PATHB_WAITING_SAME_TICKER_SHADOW",
        "pathb_waiting_path_run_id": str(run.get("path_run_id") or ""),
        "pathb_waiting_status": str(run.get("status") or ""),
        "pathb_waiting_buy_zone_low": plan.get("buy_zone_low"),
        "pathb_waiting_buy_zone_high": plan.get("buy_zone_high"),
    }
    if current_price is not None:
        try:
            out["pathb_waiting_current_price"] = float(current_price)
        except Exception:
            pass
    return out


def _is_broker_sync_close(event: dict[str, Any]) -> bool:
    payload = event.get("payload") or {}
    reason = str(payload.get("close_reason") or event.get("reason_code") or "")
    return reason == "CLOSED_BROKER_SYNC"


def _normalize_market(market: str) -> str:
    return str(market or "").strip().upper()


def _normalize_ticker(market: str, ticker: str) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if _normalize_market(market) == "US" else raw


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return _ensure_aware(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _round_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 4)
    except Exception:
        return None
