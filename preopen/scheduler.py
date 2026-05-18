from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta
import os
from typing import Any
from zoneinfo import ZoneInfo

from bot.session_date import KST, resolve_session_date_str


_EXCHANGE_MAP = {"KR": "XKRX", "US": "XNYS"}
_EC_CACHE: dict[str, Any] = {}


def market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _parse_hhmm(value: str, default: dt_time) -> dt_time:
    try:
        hh, mm = str(value or "").split(":", 1)
        return dt_time(int(hh), int(mm))
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _combine(now_dt: datetime, value: dt_time) -> datetime:
    return datetime.combine(now_dt.date(), value, tzinfo=KST)


def _session_day(session_date: str):
    return datetime.fromisoformat(str(session_date)).date()


def _ny_session_time_to_kst(session_date: str, value: dt_time) -> datetime:
    return datetime.combine(_session_day(session_date), value, tzinfo=ZoneInfo("America/New_York")).astimezone(KST)


def _to_kst(value) -> datetime | None:
    try:
        if hasattr(value, "to_pydatetime"):
            value = value.to_pydatetime()
        if value.tzinfo is None:
            value = value.replace(tzinfo=ZoneInfo("UTC"))
        return value.astimezone(KST)
    except Exception:
        return None


def _exchange_session_open_dt(market: str, session_date: str) -> datetime | None:
    exchange = _EXCHANGE_MAP.get(market_key(market))
    if not exchange:
        return None
    try:
        import exchange_calendars as ec

        if exchange not in _EC_CACHE:
            _EC_CACHE[exchange] = ec.get_calendar(exchange)
        cal = _EC_CACHE[exchange]
        if hasattr(cal, "session_open"):
            opened = cal.session_open(str(session_date))
            return _to_kst(opened)
        schedule = getattr(cal, "schedule", None)
        if schedule is not None:
            row = schedule.loc[str(session_date)]
            opened = row.get("market_open") if hasattr(row, "get") else row["market_open"]
            return _to_kst(opened)
    except Exception:
        return None
    return None


def _exchange_session_close_dt(market: str, session_date: str) -> datetime | None:
    exchange = _EXCHANGE_MAP.get(market_key(market))
    if not exchange:
        return None
    try:
        import exchange_calendars as ec

        if exchange not in _EC_CACHE:
            _EC_CACHE[exchange] = ec.get_calendar(exchange)
        cal = _EC_CACHE[exchange]
        if hasattr(cal, "session_close"):
            closed = cal.session_close(str(session_date))
            return _to_kst(closed)
        schedule = getattr(cal, "schedule", None)
        if schedule is not None:
            row = schedule.loc[str(session_date)]
            closed = row.get("market_close") if hasattr(row, "get") else row["market_close"]
            return _to_kst(closed)
    except Exception:
        return None
    return None


def regular_open_dt(market: str, session_date: str) -> datetime:
    mkt = market_key(market)
    if mkt == "US":
        calendar_open = _exchange_session_open_dt(mkt, session_date)
        if calendar_open is not None:
            return calendar_open
        ny_tz = ZoneInfo("America/New_York")
        return datetime.combine(_session_day(session_date), dt_time(9, 30), tzinfo=ny_tz).astimezone(KST)
    return datetime.combine(_session_day(session_date), dt_time(9, 0), tzinfo=KST)


def regular_close_dt(market: str, session_date: str) -> datetime:
    mkt = market_key(market)
    calendar_close = _exchange_session_close_dt(mkt, session_date)
    if calendar_close is not None:
        return calendar_close
    if mkt == "US":
        ny_tz = ZoneInfo("America/New_York")
        return datetime.combine(_session_day(session_date), dt_time(16, 0), tzinfo=ny_tz).astimezone(KST)
    return datetime.combine(_session_day(session_date), dt_time(15, 30), tzinfo=KST)


def default_outcome_offsets_min(market: str, session_date: str) -> tuple[int, ...]:
    opened = regular_open_dt(market, session_date)
    closed = regular_close_dt(market, session_date)
    total_min = int(max(5, (closed - opened).total_seconds() // 60))
    offsets = [5]
    offset = 30
    while offset <= total_min:
        offsets.append(offset)
        offset += 30
    return tuple(dict.fromkeys(offsets))


def is_trading_day(market: str, session_date: str) -> bool:
    exchange = _EXCHANGE_MAP.get(market_key(market), "XNYS")
    try:
        import exchange_calendars as ec

        if exchange not in _EC_CACHE:
            _EC_CACHE[exchange] = ec.get_calendar(exchange)
        return bool(_EC_CACHE[exchange].is_session(str(session_date)))
    except Exception:
        try:
            day = datetime.fromisoformat(str(session_date)).date()
            return day.weekday() < 5
        except Exception:
            return True


@dataclass(frozen=True)
class PreopenJob:
    market: str
    session_date: str
    kind: str
    job_id: str
    due_at: str
    script: str
    args: tuple[str, ...]
    offset_min: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["args"] = list(self.args)
        return payload

    @property
    def display_command(self) -> str:
        return "python " + " ".join([self.script, *self.args])


def collector_interval_min(market: str, override: int | None = None) -> int:
    if override is not None and override > 0:
        return int(override)
    return 30 if market_key(market) == "US" else 15


def collector_window(market: str) -> tuple[dt_time, dt_time]:
    mkt = market_key(market)
    if mkt == "US":
        session_date = resolve_session_date_str("US", datetime.now(KST))
        start_dt, end_dt = collector_window_dt("US", session_date)
        return start_dt.time(), end_dt.time()
    return dt_time(8, 0), dt_time(9, 0)


def collector_window_dt(market: str, session_date: str) -> tuple[datetime, datetime]:
    mkt = market_key(market)
    if mkt == "US":
        opened = regular_open_dt(mkt, session_date)
        return _ny_session_time_to_kst(session_date, dt_time(4, 0)), opened - timedelta(minutes=5)
    day = _session_day(session_date)
    return datetime.combine(day, dt_time(8, 0), tzinfo=KST), datetime.combine(day, dt_time(9, 0), tzinfo=KST)


def regular_open_time(market: str, session_date: str | None = None) -> dt_time:
    if market_key(market) == "US":
        session_date = session_date or resolve_session_date_str("US", datetime.now(KST))
        return regular_open_dt("US", session_date).time()
    if session_date:
        return regular_open_dt(market, session_date).time()
    return dt_time(9, 0)


def due_jobs(
    *,
    now_dt: datetime | None = None,
    markets: list[str] | tuple[str, ...] = ("KR", "US"),
    mode: str = "live",
    collector_interval_override_min: int | None = None,
    outcome_offsets_min: tuple[int, ...] | None = None,
    outcome_catchup_min: int = 180,
    force: bool = False,
    completed_job_ids: set[str] | None = None,
) -> list[PreopenJob]:
    now_dt = now_dt or datetime.now(KST)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=KST)
    else:
        now_dt = now_dt.astimezone(KST)
    completed = completed_job_ids or set()
    jobs: list[PreopenJob] = []
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"

    for raw_market in markets:
        mkt = market_key(raw_market)
        session_date = resolve_session_date_str(mkt, now_dt)
        if not is_trading_day(mkt, session_date):
            continue

        start_dt, end_dt = collector_window_dt(mkt, session_date)
        interval = collector_interval_min(mkt, collector_interval_override_min)
        if start_dt <= now_dt <= end_dt:
            elapsed_min = max(0, int((now_dt - start_dt).total_seconds() // 60))
            bucket = elapsed_min // interval
            due_dt = start_dt + timedelta(minutes=bucket * interval)
            job_id = f"{runtime_mode}:{session_date}:{mkt}:collector:{bucket:03d}"
            if force or job_id not in completed:
                jobs.append(PreopenJob(
                    market=mkt,
                    session_date=session_date,
                    kind="collector",
                    job_id=job_id,
                    due_at=due_dt.isoformat(timespec="seconds"),
                    script="tools/preopen_collector.py",
                    args=("--market", mkt, "--mode", runtime_mode, "--once"),
                ))

        open_dt = regular_open_dt(mkt, session_date)
        news_lead_min = max(1, _env_int("PREOPEN_NEWS_LEAD_MIN", 20))
        news_due_dt = open_dt - timedelta(minutes=news_lead_min)
        news_late_by = (now_dt - news_due_dt).total_seconds() / 60.0
        if 0 <= news_late_by <= max(0, int(outcome_catchup_min)):
            job_id = f"{runtime_mode}:{session_date}:{mkt}:news"
            if force or job_id not in completed:
                jobs.append(PreopenJob(
                    market=mkt,
                    session_date=session_date,
                    kind="news",
                    job_id=job_id,
                    due_at=news_due_dt.isoformat(timespec="seconds"),
                    script="tools/collect_preopen_candidate_news.py",
                    args=("--market", mkt, "--session-date", session_date, "--mode", runtime_mode),
                ))

        offsets = outcome_offsets_min if outcome_offsets_min is not None else default_outcome_offsets_min(mkt, session_date)
        for offset in offsets:
            due_dt = open_dt + timedelta(minutes=int(offset))
            late_by = (now_dt - due_dt).total_seconds() / 60.0
            if late_by < 0 or late_by > max(0, int(outcome_catchup_min)):
                continue
            job_id = f"{runtime_mode}:{session_date}:{mkt}:outcome:{int(offset)}m"
            if force or job_id not in completed:
                jobs.append(PreopenJob(
                    market=mkt,
                    session_date=session_date,
                    kind="outcome",
                    job_id=job_id,
                    due_at=due_dt.isoformat(timespec="seconds"),
                    script="tools/preopen_outcome_updater.py",
                    args=("--market", mkt, "--mode", runtime_mode, "--offset-min", str(int(offset)), "--once"),
                    offset_min=int(offset),
                ))

    return jobs
