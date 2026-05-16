from __future__ import annotations

from typing import Any


POST_RANK_VERSION = "candidate_post_rank_v1"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _quality_bonus(row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = row.get("candidate_quality_score")
    if score in (None, ""):
        reasons.append("quality_missing")
        return 0.0, reasons
    bonus = (_as_float(score, 50.0) - 50.0) * 0.20
    gaps = row.get("quality_data_gaps") or []
    gap_set = {str(item) for item in (gaps if isinstance(gaps, (list, tuple, set)) else [gaps]) if str(item)}
    if "ohlcv_missing" in gap_set:
        bonus = 0.0
        reasons.append("quality_ohlcv_missing")
    elif "index_history_missing" in gap_set or "flow_missing" in gap_set:
        bonus *= 0.5
        reasons.append("quality_partial_missing")
    return bonus, reasons


def _cohort_adjustment(row: dict[str, Any]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    adjustment = 0.0
    reliability = row.get("trainer_cohort_reliability")
    if reliability not in (None, ""):
        rel = _as_float(reliability, 0.5)
        adjustment += (rel - 0.5) * 12.0
        reasons.append("cohort_reliability")
    penalty = row.get("trainer_cohort_penalty")
    if penalty not in (None, ""):
        adjustment -= max(0.0, _as_float(penalty, 0.0))
        reasons.append("cohort_penalty")
    return adjustment, reasons


def apply_candidate_post_rank(
    candidates: list[dict[str, Any]],
    *,
    market: str,
    enforce: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach quality/cohort post-rank shadow metadata; optionally enforce order."""

    market_key = "US" if str(market or "").upper() == "US" else "KR"
    rows = [dict(row or {}) for row in candidates or [] if isinstance(row, dict)]
    if market_key != "KR":
        return rows, {
            "version": POST_RANK_VERSION,
            "market": market_key,
            "enabled": False,
            "reason": "market_not_kr",
        }

    scored: list[tuple[float, int, dict[str, Any]]] = []
    missing = 0
    for idx, row in enumerate(rows, start=1):
        base_score = _as_float(row.get("screen_score"), 0.0)
        reasons: list[str] = []
        q_bonus, q_reasons = _quality_bonus(row)
        c_adj, c_reasons = _cohort_adjustment(row)
        reasons.extend(q_reasons)
        reasons.extend(c_reasons)
        if "quality_missing" in reasons and not c_reasons:
            missing += 1
        adjusted = base_score + q_bonus + c_adj
        row["post_rank_version"] = POST_RANK_VERSION
        row["post_rank_enabled"] = True
        row["post_rank_shadow_only"] = not enforce
        row["actual_rank"] = idx
        row["post_rank_base_score"] = round(base_score, 4)
        row["post_rank_adjusted_score"] = round(adjusted, 4)
        row["post_rank_reason"] = ",".join(reasons) if reasons else "base"
        scored.append((adjusted, idx, row))

    ordered = sorted(scored, key=lambda item: (-item[0], item[1]))
    rank_by_id: dict[int, int] = {id(row): rank for rank, (_score, _idx, row) in enumerate(ordered, start=1)}
    annotated: list[dict[str, Any]] = []
    for _score, _idx, row in scored:
        adjusted_rank = rank_by_id.get(id(row), _idx)
        row["adjusted_rank"] = adjusted_rank
        row["rank_delta"] = int(row.get("actual_rank") or _idx) - adjusted_rank
        annotated.append(row)

    output = [row for _score, _idx, row in ordered] if enforce else annotated
    return output, {
        "version": POST_RANK_VERSION,
        "market": market_key,
        "enabled": True,
        "enforce": bool(enforce),
        "count": len(rows),
        "missing_quality_count": missing,
        "max_abs_rank_delta": max((abs(int(row.get("rank_delta") or 0)) for row in annotated), default=0),
    }
