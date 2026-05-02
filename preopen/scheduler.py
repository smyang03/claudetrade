from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Any

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


def _combine(now_dt: datetime, value: dt_time) -> datetime:
    return datetime.combine(now_dt.date(), value, tzinfo=KST)


def _window_contains(now_dt: datetime, start: dt_time, end: dt_time) -> bool:
    cur = now_dt.time()
    if start <= end:
        return start <= cur <= end
    return cur >= start or cur <= end


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
        return dt_time(17, 0), dt_time(22, 25)
    return dt_time(8, 0), dt_time(9, 0)


def regular_open_time(market: str) -> dt_time:
    return dt_time(22, 30) if market_key(market) == "US" else dt_time(9, 0)


def due_jobs(
    *,
    now_dt: datetime | None = None,
    markets: list[str] | tuple[str, ...] = ("KR", "US"),
    mode: str = "live",
    collector_interval_override_min: int | None = None,
    outcome_offsets_min: tuple[int, ...] = (5, 30, 60),
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

        start_t, end_t = collector_window(mkt)
        interval = collector_interval_min(mkt, collector_interval_override_min)
        if _window_contains(now_dt, start_t, end_t):
            start_dt = _combine(now_dt, start_t)
            if end_t < start_t and now_dt.time() <= end_t:
                start_dt -= timedelta(days=1)
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

        open_dt = _combine(now_dt, regular_open_time(mkt))
        if mkt == "US" and now_dt.time() < dt_time(5, 0):
            open_dt -= timedelta(days=1)
        for offset in outcome_offsets_min:
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
                    args=("--market", mkt, "--offset-min", str(int(offset)), "--once"),
                    offset_min=int(offset),
                ))

    return jobs
