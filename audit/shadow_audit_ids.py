from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else timezone(timedelta(hours=9))


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            dt = datetime.now(KST)
        else:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                dt = datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def minute_bucket(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%Y%m%dT%H%M")


def normalize_price_for_id(market: str, price: Any) -> str:
    try:
        value = float(price or 0)
    except Exception:
        value = 0.0
    if str(market or "").upper() == "KR":
        return str(int(round(value)))
    return f"{value:.4f}"


def stable_hash(parts: list[Any], *, prefix: str) -> str:
    raw = "|".join(str(part or "").strip() for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def make_signal_id(
    *,
    runtime_mode: str,
    market: str,
    session_date: str,
    ticker: str,
    strategy: str,
    signal_at: Any,
    signal_price: Any,
    source: str,
) -> str:
    market_key = str(market or "").upper()
    ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
    return stable_hash(
        [
            str(runtime_mode or "").lower(),
            market_key,
            str(session_date or ""),
            ticker_key,
            str(strategy or ""),
            minute_bucket(signal_at),
            normalize_price_for_id(market_key, signal_price),
            str(source or ""),
        ],
        prefix="sig",
    )


def make_episode_id(
    *,
    runtime_mode: str,
    market: str,
    session_date: str,
    episode_type: str,
    scope: str,
    ticker: str = "",
    started_at: Any,
    reason: str,
) -> str:
    return stable_hash(
        [
            str(runtime_mode or "").lower(),
            str(market or "").upper(),
            str(session_date or ""),
            str(episode_type or ""),
            str(scope or ""),
            str(ticker or ""),
            minute_bucket(started_at),
            str(reason or ""),
        ],
        prefix="ep",
    )

