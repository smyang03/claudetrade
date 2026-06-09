from __future__ import annotations

import math
import os
from typing import Any

from preopen.storage import load_preopen_state, log_path, read_jsonl_tail


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _market_env_int(market: str, suffix: str, default: int) -> int:
    market_key = str(market or "").upper()
    raw = os.getenv(f"{market_key}_{suffix}")
    if raw not in (None, ""):
        try:
            return int(raw)
        except Exception:
            return default
    return _env_int(suffix, default)


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


def _target_limit(market: str, limit: int | None) -> int:
    if limit is not None:
        return int(limit)
    default = 120 if str(market or "").upper() == "KR" else 60
    return _market_env_int(market, "PREOPEN_NEWS_TARGET_LIMIT", default)


def _candidate_log_rows(market: str, session_date: str, *, mode: str) -> list[dict[str, Any]]:
    if not _env_bool("PREOPEN_NEWS_INCLUDE_CANDIDATE_LOG", True):
        return []
    tail_limit = max(1, _env_int("PREOPEN_NEWS_CANDIDATE_LOG_TAIL_LIMIT", 1000))
    try:
        return read_jsonl_tail(log_path("candidates", market, session_date, mode=mode), tail_limit)
    except Exception:
        return []


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
    target_limit = _target_limit(market_key, limit)
    age_limit = max_age_min if max_age_min is not None else _env_int("PREOPEN_NEWS_STATE_MAX_AGE_MIN", 0)
    state = load_preopen_state(
        market_key,
        session_date=session_date,
        max_age_min=age_limit,
        mode=mode,
    )
    candidates = list(state.get("candidates") or [])
    log_candidates = _candidate_log_rows(market_key, session_date, mode=mode)
    targets: dict[str, str] = {}
    ordered_candidates = sorted(enumerate(candidates), key=_candidate_sort_key)
    ordered_candidates.extend(sorted(enumerate(log_candidates, start=len(candidates)), key=_candidate_sort_key))
    for _idx, row in ordered_candidates:
        ticker = _normalize_ticker(market_key, row.get("ticker"))
        if not ticker or ticker in targets:
            continue
        name = str(row.get("name") or ticker).strip() or ticker
        targets[ticker] = name
        if target_limit > 0 and len(targets) >= target_limit:
            break
    return targets
