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
    default_hard_cap = 28 if market_key == "KR" else 24
    configured_hard_cap = int(hard_cap if hard_cap is not None else default_hard_cap)
    cap = configured_hard_cap if configured_hard_cap > 0 else target_count
    if cap <= 0:
        cap = target_count

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
