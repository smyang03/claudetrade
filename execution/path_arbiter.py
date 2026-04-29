from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
                waiting_shadow = self._waiting_price_chase_shadow(run, current_price)
                if waiting_shadow:
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

        current = _ensure_aware(now or datetime.now(timezone.utc))
        closed_at = _parse_dt(str(last_closed.get("occurred_at") or last_closed.get("created_at") or ""))
        age_min = None
        if closed_at is not None:
            age_min = max(0.0, (current - closed_at.astimezone(current.tzinfo)).total_seconds() / 60.0)
        cooldown = self._cooldown_minutes(market_key)
        shadow = {
            "same_day_reentry": True,
            "same_day_reentry_close_reason": close_reason,
            "same_day_reentry_closed_at": str(last_closed.get("occurred_at") or ""),
            "same_day_reentry_age_minutes": round(age_min, 2) if age_min is not None else None,
            "same_day_reentry_cooldown_minutes": cooldown,
        }
        if age_min is None or age_min < cooldown:
            return ArbiterDecision(
                False,
                "SAME_DAY_REENTRY_COOLDOWN",
                "same-day re-entry cooldown is active",
                {"market": market_key, "ticker": ticker_key, **shadow},
                shadow,
            )
        return ArbiterDecision(True, details={"market": market_key, "ticker": ticker_key}, shadow=shadow)

    def _cooldown_minutes(self, market: str) -> int:
        return (
            int(self.config.us_reentry_cooldown_minutes)
            if market == "US"
            else int(self.config.kr_reentry_cooldown_minutes)
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
