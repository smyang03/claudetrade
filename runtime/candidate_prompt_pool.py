from __future__ import annotations

from collections import Counter
from typing import Any

from runtime.candidate_quality_trainer import (
    TRAINER_SCORE_VERSION,
    normalize_ticker,
    score_candidate_for_trainer,
)


PROMPT_POOL_VERSION = "trainer_prompt_pool_v1"
STATE_RANK = {
    "PLAN_A": 5,
    "PLAN_B": 4,
    "WATCH": 3,
    "BENCH": 2,
    "QUARANTINE": 0,
}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "")))
    except Exception:
        return default


def _score(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except Exception:
        return 0.0


def _merge_duplicate(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    if _score(incoming, "trainer_prompt_score") > _score(existing, "trainer_prompt_score"):
        winner, loser = dict(incoming), existing
    else:
        winner, loser = dict(existing), incoming
    sources = []
    for row in (existing, incoming):
        for source in row.get("source_tags") or []:
            if source not in sources:
                sources.append(source)
        source = str(row.get("source") or row.get("candidate_source") or "").strip()
        if source and source not in sources:
            sources.append(source)
    winner["source_tags"] = sources
    winner["raw_rank"] = min(
        _as_int(existing.get("raw_rank"), 999999),
        _as_int(incoming.get("raw_rank"), 999999),
    )
    winner.setdefault("merged_duplicate_count", 1)
    winner["merged_duplicate_count"] = int(winner.get("merged_duplicate_count") or 1) + int(loser.get("merged_duplicate_count") or 1)
    return winner


def _sort_key(row: dict[str, Any]) -> tuple:
    state = str(row.get("trainer_candidate_state") or "WATCH").upper()
    return (
        -STATE_RANK.get(state, 1),
        -_score(row, "trainer_prompt_score"),
        _score(row, "trainer_risk_score"),
        _as_int(row.get("raw_rank"), 999999),
        str(row.get("ticker") or ""),
    )


def _excluded_reason(row: dict[str, Any], *, cap_excluded: bool = False) -> str:
    state = str(row.get("trainer_candidate_state") or "").upper()
    if state == "QUARANTINE":
        return "trainer_quarantine"
    if cap_excluded:
        return "prompt_cap"
    return "not_prompt_eligible"


def _ticker_key(row: dict[str, Any], market_key: str) -> str:
    return normalize_ticker((row or {}).get("ticker"), market_key)


def _dedupe_by_ticker(rows: list[dict[str, Any]], *, market_key: str) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = _ticker_key(row, market_key)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return deduped


def build_plan_a_overlay_prompt_pool(
    current_prompt_pool: list[dict[str, Any]],
    scored_pool: list[dict[str, Any]],
    *,
    market: str,
    cap: int,
    keep_current: int = 15,
    plan_a_max: int = 4,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    market_default_cap = 24 if market_key == "US" else 28
    try:
        effective_cap = min(max(0, int(cap or 0)), market_default_cap)
    except Exception:
        effective_cap = market_default_cap
    try:
        keep_count = max(0, int(keep_current or 0))
    except Exception:
        keep_count = 15
    try:
        plan_a_limit = max(0, int(plan_a_max or 0))
    except Exception:
        plan_a_limit = 4

    current_rows = _dedupe_by_ticker([dict(row or {}) for row in current_prompt_pool or []], market_key=market_key)
    scored_rows = _dedupe_by_ticker([dict(row or {}) for row in scored_pool or []], market_key=market_key)
    current_top = current_rows[:keep_count]
    current_top_keys = {_ticker_key(row, market_key) for row in current_top}
    current_fill_source = current_rows[keep_count:]

    plan_a_candidates = sorted(
        [
            row for row in scored_rows
            if str(row.get("trainer_candidate_state") or "").upper() == "PLAN_A"
            and _ticker_key(row, market_key) not in current_top_keys
        ],
        key=_sort_key,
    )

    base_meta = {
        "overlay_plan_a_available": len(plan_a_candidates),
        "overlay_keep_current": keep_count,
        "overlay_plan_a_max": plan_a_limit,
        "overlay_plan_b_used": False,
    }
    if not plan_a_candidates or plan_a_limit <= 0 or effective_cap <= 0:
        result = [dict(row) for row in current_rows[:effective_cap]]
        for rank, row in enumerate(result, start=1):
            row["prompt_rank"] = rank
            row["prompt_overlay_added"] = False
        result_keys = {_ticker_key(row, market_key) for row in result}
        return result, {
            **base_meta,
            "overlay_candidate_state": "current_only",
            "overlay_plan_a_added": 0,
            "overlay_added_tickers": [],
            "overlay_removed_tickers": [
                _ticker_key(row, market_key)
                for row in current_rows
                if _ticker_key(row, market_key) not in result_keys
            ],
        }

    overlay_added = plan_a_candidates[:plan_a_limit]
    overlay_added_keys = {_ticker_key(row, market_key) for row in overlay_added}
    result = _dedupe_by_ticker(current_top + overlay_added + current_fill_source, market_key=market_key)
    result = result[:effective_cap]
    result_keys = {_ticker_key(row, market_key) for row in result}
    overlay_added_tickers = [
        _ticker_key(row, market_key)
        for row in overlay_added
        if _ticker_key(row, market_key) in result_keys
    ]
    overlay_removed_tickers = [
        _ticker_key(row, market_key)
        for row in current_rows
        if _ticker_key(row, market_key) not in result_keys
    ]
    for rank, row in enumerate(result, start=1):
        key = _ticker_key(row, market_key)
        row["prompt_rank"] = rank
        row["prompt_overlay_added"] = bool(key in overlay_added_tickers)

    return result, {
        **base_meta,
        "overlay_candidate_state": "overlay_candidate" if overlay_added_tickers else "current_only",
        "overlay_plan_a_added": len(overlay_added_tickers),
        "overlay_added_tickers": overlay_added_tickers,
        "overlay_removed_tickers": overlay_removed_tickers,
    }


def build_trainer_prompt_pool(
    candidates: list[dict[str, Any]],
    *,
    market: str,
    target: int = 30,
    hard_cap: int | None = None,
    reorder_enabled: bool = True,
) -> dict[str, Any]:
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    target_count = max(0, int(target or 0))
    default_hard_cap = 28 if market_key == "KR" else 32
    configured_hard_cap = int(hard_cap if hard_cap is not None else default_hard_cap)
    cap = configured_hard_cap if configured_hard_cap > 0 else target_count
    if cap <= 0:
        cap = target_count

    # liquidity_bucket 미설정 후보를 price*volume 백분위로 보완
    _turnovers: list[float] = []
    for _raw in candidates or []:
        if not isinstance(_raw, dict):
            continue
        try:
            _t = float(str(_raw.get("price") or 0) or "0") * float(str(_raw.get("volume") or 0) or "0")
        except Exception:
            _t = 0.0
        if _t > 0:
            _turnovers.append(_t)
    _turnovers.sort()

    def _infer_liquidity(raw: dict[str, Any]) -> str:
        if raw.get("liquidity_bucket"):
            return str(raw["liquidity_bucket"])
        try:
            t = float(str(raw.get("price") or 0) or "0") * float(str(raw.get("volume") or 0) or "0")
        except Exception:
            t = 0.0
        if t <= 0 or not _turnovers:
            return "unknown"
        higher = sum(1 for v in _turnovers if v > t)
        pct = 1.0 - higher / len(_turnovers)
        if pct >= 0.67:
            return "high"
        if pct >= 0.34:
            return "mid"
        return "low"

    scored_by_ticker: dict[str, dict[str, Any]] = {}
    legacy_order: list[str] = []
    for idx, raw in enumerate(candidates or [], start=1):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        ticker = normalize_ticker(row.get("ticker"), market_key)
        if not ticker:
            continue
        row.setdefault("raw_rank", idx)
        row["ticker"] = ticker
        if not row.get("liquidity_bucket"):
            row["liquidity_bucket"] = _infer_liquidity(raw)
        scored = score_candidate_for_trainer(row, market=market_key)
        key = ticker.upper() if market_key == "US" else ticker
        legacy_order.append(key)
        if key in scored_by_ticker:
            scored_by_ticker[key] = _merge_duplicate(scored_by_ticker[key], scored)
        else:
            scored_by_ticker[key] = scored

    scored_pool = list(scored_by_ticker.values())
    if reorder_enabled:
        ordered = sorted(scored_pool, key=_sort_key)
    else:
        order_index = {ticker: idx for idx, ticker in enumerate(legacy_order)}
        ordered = sorted(scored_pool, key=lambda row: order_index.get(str(row.get("ticker") or ""), 999999))

    eligible = [row for row in ordered if str(row.get("trainer_candidate_state") or "").upper() != "QUARANTINE"]
    prompt_pool = [dict(row) for row in eligible[:cap]]
    prompt_keys = {str(row.get("ticker") or "") for row in prompt_pool}
    for rank, row in enumerate(prompt_pool, start=1):
        row["prompt_rank"] = rank
        row["trainer_score_rank"] = rank
        row["final_prompt_included"] = True
        row["prompt_pool_version"] = PROMPT_POOL_VERSION

    excluded: list[dict[str, Any]] = []
    for row in ordered:
        ticker = str(row.get("ticker") or "")
        if ticker in prompt_keys:
            continue
        cap_excluded = row in eligible[cap:]
        reason = _excluded_reason(row, cap_excluded=cap_excluded)
        prompt_excluded_reason = "hard_cap_cutoff" if cap_excluded else reason
        excluded.append(
            {
                "ticker": ticker,
                "reason": reason,
                "prompt_excluded_reason": prompt_excluded_reason,
                "raw_rank": row.get("raw_rank"),
                "trainer_score_rank": ordered.index(row) + 1,
                "trainer_prompt_score": row.get("trainer_prompt_score"),
                "trainer_plan_a_score": row.get("trainer_plan_a_score"),
                "trainer_pathb_wait_score": row.get("trainer_pathb_wait_score"),
                "trainer_risk_score": row.get("trainer_risk_score"),
                "trainer_candidate_state": row.get("trainer_candidate_state"),
                "source_tags": list(row.get("source_tags") or []),
                "candidate": row,
            }
        )

    states = Counter(str(row.get("trainer_candidate_state") or "UNKNOWN").upper() for row in scored_pool)
    return {
        "version": PROMPT_POOL_VERSION,
        "score_version": TRAINER_SCORE_VERSION,
        "market": market_key,
        "target": target_count,
        "hard_cap": cap,
        "full_pool": scored_pool,
        "scored_pool": scored_pool,
        "prompt_pool": prompt_pool,
        "excluded_from_prompt": excluded,
        "metrics": {
            "full_pool_count": len(scored_pool),
            "prompt_pool_count": len(prompt_pool),
            "excluded_count": len(excluded),
            "state_counts": dict(states),
            "legacy_order": legacy_order[:cap],
            "trainer_order": [str(row.get("ticker") or "") for row in prompt_pool],
            "reorder_enabled": bool(reorder_enabled),
        },
    }
