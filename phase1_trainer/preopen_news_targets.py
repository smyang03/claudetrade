from __future__ import annotations

import math
import os
from typing import Any

from preopen.storage import load_preopen_state


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _normalize_ticker(market: str, value: Any) -> str:
    ticker = str(value or "").strip()
    if not ticker:
        return ""
    if str(market or "").upper() == "KR":
        digits = "".join(ch for ch in ticker if ch.isdigit())
        return digits.zfill(6) if digits else ""
    return ticker.upper()


def _rank_number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return default if math.isnan(number) else number


def _candidate_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[float, float, float, int]:
    idx, row = item
    shadow_rank = _rank_number(row.get("shadow_preopen_rank"), float("inf"))
    provider_rank = _rank_number(row.get("provider_rank"), float("inf"))
    preopen_score = _rank_number(row.get("preopen_score"), float("-inf"))
    return (shadow_rank, provider_rank, -preopen_score, idx)


def load_preopen_news_targets(
    market: str,
    session_date: str,
    *,
    limit: int | None = None,
    mode: str = "live",
    max_age_min: int | None = None,
) -> dict[str, str]:
    """Load preopen candidates as an ordered ticker -> display-name map."""
    market_key = str(market or "").upper()
    target_limit = limit if limit is not None else _env_int("PREOPEN_NEWS_TARGET_LIMIT", 60)
    age_limit = max_age_min if max_age_min is not None else _env_int("PREOPEN_NEWS_STATE_MAX_AGE_MIN", 0)
    state = load_preopen_state(
        market_key,
        session_date=session_date,
        max_age_min=age_limit,
        mode=mode,
    )
    candidates = state.get("candidates") or []
    targets: dict[str, str] = {}
    for _idx, row in sorted(enumerate(candidates), key=_candidate_sort_key):
        ticker = _normalize_ticker(market_key, row.get("ticker"))
        if not ticker or ticker in targets:
            continue
        name = str(row.get("name") or ticker).strip() or ticker
        targets[ticker] = name
        if target_limit > 0 and len(targets) >= target_limit:
            break
    return targets
