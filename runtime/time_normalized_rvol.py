from __future__ import annotations

import csv
import os
import statistics
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime_paths import get_runtime_path


MARKET_TIMEZONE = {"KR": ZoneInfo("Asia/Seoul"), "US": ZoneInfo("America/New_York")}
MARKET_OPEN_MINUTE = {"KR": 9 * 60, "US": 9 * 60 + 30}
RUNTIME_TIMEZONE = ZoneInfo("Asia/Seoul")


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _parse_dt(value: Any, *, market: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    timezone = MARKET_TIMEZONE[_market_key(market)]
    if parsed.tzinfo is None:
        # Runtime candles and ``known_at`` are serialized in the host's KST
        # clock, often without an offset after normalization.  Treating a
        # naive US timestamp such as 22:36 as New York local time turns
        # open+6m into open+786m and invalidates the same-time RVOL profile.
        parsed = parsed.replace(tzinfo=RUNTIME_TIMEZONE)
    return parsed.astimezone(timezone)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _elapsed_minute(ts: datetime, *, market: str) -> int:
    return max(0, ts.hour * 60 + ts.minute - MARKET_OPEN_MINUTE[_market_key(market)])


def _session_cumulative_volume(
    rows: list[dict[str, Any]],
    *,
    market: str,
    through_elapsed_min: int,
    before_session_date: str = "",
) -> dict[str, float]:
    sessions: dict[str, list[tuple[int, float]]] = {}
    for row in rows or []:
        ts = _parse_dt(row.get("ts") or row.get("timestamp"), market=market)
        volume = _number(row.get("volume"))
        if ts is None or volume is None or volume < 0:
            continue
        session_date = ts.date().isoformat()
        if before_session_date and session_date >= before_session_date:
            continue
        elapsed = _elapsed_minute(ts, market=market)
        if elapsed < 0 or elapsed > through_elapsed_min:
            continue
        sessions.setdefault(session_date, []).append((elapsed, volume))

    cumulative: dict[str, float] = {}
    for session_date, values in sessions.items():
        if not values:
            continue
        latest_elapsed = max(item[0] for item in values)
        # A session missing most of the requested window would make RVOL
        # artificially large.  Allow at most a two-minute collection gap.
        if latest_elapsed < max(0, through_elapsed_min - 2):
            continue
        cumulative[session_date] = sum(item[1] for item in values)
    return cumulative


def compute_time_normalized_rvol(
    *,
    current_rows: list[dict[str, Any]],
    historical_rows: list[dict[str, Any]],
    market: str,
    known_at: Any,
    session_date: str,
    lookback_sessions: int = 20,
    min_sessions: int = 5,
) -> dict[str, Any]:
    """Compute cumulative-volume RVOL against prior sessions at the same time.

    The same pure function is used by live serving and historical tests.  All
    reference sessions are strictly earlier than ``session_date``.
    """

    market_key = _market_key(market)
    known = _parse_dt(known_at, market=market_key)
    if known is None:
        return {
            "time_normalized_rvol": None,
            "rvol_profile_sessions": 0,
            "rvol_profile_method": "median_prior_same_elapsed",
            "rvol_profile_status": "known_at_invalid",
        }
    elapsed = _elapsed_minute(known, market=market_key)
    current = _session_cumulative_volume(
        current_rows,
        market=market_key,
        through_elapsed_min=elapsed,
    )
    current_volume = current.get(str(session_date))
    if current_volume is None and current:
        current_volume = current.get(max(current))
    history = _session_cumulative_volume(
        historical_rows,
        market=market_key,
        through_elapsed_min=elapsed,
        before_session_date=str(session_date),
    )
    selected_dates = sorted(history)[-max(1, int(lookback_sessions)) :]
    expected_values = [history[key] for key in selected_dates if history[key] > 0]
    expected = statistics.median(expected_values) if expected_values else None
    status = "ok"
    ratio = None
    if current_volume is None or current_volume <= 0:
        status = "current_volume_missing"
    elif len(expected_values) < max(1, int(min_sessions)):
        status = "history_insufficient"
    elif expected is None or expected <= 0:
        status = "expected_volume_missing"
    else:
        ratio = float(current_volume) / float(expected)
    return {
        "time_normalized_rvol": ratio,
        "rvol_current_cumulative_volume": current_volume,
        "rvol_expected_cumulative_volume": expected,
        "rvol_profile_sessions": len(expected_values),
        "rvol_profile_elapsed_min": elapsed,
        "rvol_profile_method": "median_prior_same_elapsed",
        "rvol_profile_status": status,
        "rvol_profile_last_session": selected_dates[-1] if selected_dates else "",
    }


class LocalTimeNormalizedRvolStore:
    """Read-only local minute-history provider with a per-session memory cache."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lookback_sessions: int = 20,
        min_sessions: int = 5,
    ) -> None:
        configured = str(os.getenv("INTRADAY_RVOL_HISTORY_ROOT", "") or "").strip()
        self.root = Path(root or configured or get_runtime_path("data", "price", "minute"))
        self.lookback_sessions = max(1, int(lookback_sessions))
        self.min_sessions = max(1, int(min_sessions))
        self._cache: dict[tuple[str, str, str], tuple[float, list[dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def _path(self, market: str, ticker: str) -> Path:
        prefix = _market_key(market).lower()
        symbol = str(ticker or "").upper() if prefix == "us" else str(ticker or "")
        return self.root / prefix / f"{prefix}_{symbol}.csv"

    def _history(self, market: str, ticker: str, session_date: str) -> list[dict[str, Any]]:
        path = self._path(market, ticker)
        if not path.exists():
            return []
        try:
            modified = path.stat().st_mtime
        except OSError:
            return []
        key = (_market_key(market), str(ticker), str(session_date))
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] == modified:
                return list(cached[1])
        rows: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    ts = _parse_dt(row.get("ts"), market=market)
                    if ts is None or ts.date().isoformat() >= str(session_date):
                        continue
                    rows.append(
                        {
                            "ts": ts.isoformat(),
                            "volume": row.get("volume"),
                        }
                    )
        except (OSError, csv.Error):
            return []
        with self._lock:
            self._cache[key] = (modified, list(rows))
        return rows

    def snapshot(
        self,
        *,
        current_rows: list[dict[str, Any]],
        market: str,
        ticker: str,
        known_at: Any,
        session_date: str,
    ) -> dict[str, Any]:
        return compute_time_normalized_rvol(
            current_rows=current_rows,
            historical_rows=self._history(market, ticker, session_date),
            market=market,
            known_at=known_at,
            session_date=session_date,
            lookback_sessions=self.lookback_sessions,
            min_sessions=self.min_sessions,
        )
