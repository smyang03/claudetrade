from __future__ import annotations

from datetime import date, datetime, timedelta, time as dt_time

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))


KST = ZoneInfo("Asia/Seoul")

_KNOWN_MARKET_HOLIDAYS = {
    # KRX announced these 2026 full-market closures after the installed
    # exchange_calendars package data was generated.
    "KR": frozenset(
        {
            date(2026, 6, 3),
            date(2026, 7, 17),
        }
    ),
}


def _coerce_date(value) -> date | None:
    try:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


def is_known_market_holiday(market: str, check_date) -> bool:
    day = _coerce_date(check_date)
    if day is None:
        return False
    return day in _KNOWN_MARKET_HOLIDAYS.get(str(market or "").upper(), frozenset())


def resolve_session_date(market: str, now_dt: datetime | None = None) -> date:
    """Return the trading session date using the bot's KST session boundary rules."""
    now_dt = now_dt or datetime.now(KST)
    current_date = now_dt.date()
    if str(market or "").upper() == "US" and now_dt.time() < dt_time(5, 0):
        return current_date - timedelta(days=1)
    return current_date


def resolve_session_date_str(market: str, now_dt: datetime | None = None) -> str:
    return resolve_session_date(market, now_dt).isoformat()


def resolve_session_ymd(market: str, now_dt: datetime | None = None) -> str:
    return resolve_session_date(market, now_dt).strftime("%Y%m%d")
