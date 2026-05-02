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
