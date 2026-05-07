from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


_SAFE_PART = re.compile(r"[^A-Za-z0-9_.-]+")


def compact_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return datetime.now().strftime("%Y%m%dT%H%M%S")
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y%m%dT%H%M%S")
    except Exception:
        compact = re.sub(r"[^0-9T]", "", text)
        return compact[:15] if compact else datetime.now().strftime("%Y%m%dT%H%M%S")


def safe_part(value: Any) -> str:
    text = str(value or "").strip()
    return _SAFE_PART.sub("_", text) if text else "unknown"


def candidate_trace_id(
    *,
    session_date: str,
    market: str,
    ticker: str,
    first_seen_at: Any = None,
    cycle_id: Any = None,
) -> str:
    date_part = safe_part(str(session_date or "").replace("-", ""))
    market_part = safe_part(str(market or "").upper())
    ticker_part = safe_part(str(ticker or "").upper())
    first_seen_part = compact_timestamp(first_seen_at)
    cycle_part = safe_part(cycle_id or f"cycle_{datetime.now().strftime('%H%M%S')}")
    return "|".join([date_part, market_part, ticker_part, first_seen_part, cycle_part])


def funnel_log_path(event_type: str, session_date: str, market: str) -> Path:
    event = safe_part(event_type)
    day = safe_part(str(session_date or "").replace("-", ""))
    market_part = safe_part(str(market or "").upper())
    return get_runtime_path("logs", "funnel", f"{event}_{day}_{market_part}.jsonl")


def append_funnel_event(
    *,
    event_type: str,
    session_date: str,
    market: str,
    payload: dict[str, Any],
) -> Path:
    path = funnel_log_path(event_type, session_date, market)
    row = {
        "event_type": event_type,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "session_date": session_date,
        "market": str(market or "").upper(),
        **dict(payload or {}),
    }
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path
