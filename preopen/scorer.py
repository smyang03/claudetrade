from __future__ import annotations

from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def grade_from_score(score: float, *, excluded: bool = False) -> str:
    if excluded:
        return "X"
    if score >= 0.75:
        return "A"
    if score >= 0.50:
        return "B"
    return "C"


def score_us_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    risk_tags: list[str] = list(candidate.get("risk_tags") or [])
    quality_tags: list[str] = list(candidate.get("quality_tags") or [])

    change_raw = candidate.get("extended_change_pct")
    if change_raw in (None, ""):
        change_raw = candidate.get("gap_pct")
    change_pct = _num(change_raw)
    dollar_volume = _num(candidate.get("extended_dollar_volume"))
    spread_pct = _num(candidate.get("spread_pct"), default=999.0)
    volume_ratio = _num(candidate.get("volume_ratio"))
    source_overlap = int(_num(candidate.get("source_overlap_count"), 1))
    data_quality = str(candidate.get("data_quality") or "").lower()

    if change_pct >= 8:
        score += 0.30
        reasons.append("premarket_strength")
    elif change_pct >= 4:
        score += 0.18

    if dollar_volume >= 5_000_000:
        score += 0.25
        reasons.append("dollar_volume_quality")
    elif dollar_volume >= 1_000_000:
        score += 0.12
    elif dollar_volume > 0:
        risk_tags.append("thin_volume")

    if volume_ratio >= 3:
        score += 0.08
        reasons.append("volume_ratio_quality")

    if spread_pct <= 0.3:
        score += 0.18
        reasons.append("tight_spread")
    elif spread_pct <= 0.7:
        score += 0.08
    elif spread_pct < 999:
        risk_tags.append("wide_spread")

    if bool(candidate.get("news_or_earnings_flag")):
        score += 0.12
        reasons.append("catalyst")

    if source_overlap >= 2:
        score += 0.10
        reasons.append("source_overlap")

    if change_pct >= 18:
        score -= 0.12
        risk_tags.append("overextended")

    if bool(candidate.get("stale")):
        score -= 0.20
        risk_tags.append("stale_data")
    if data_quality in {"poor", "unavailable", "token_expired"}:
        score -= 0.20
        risk_tags.append("poor_data_quality")

    score = max(0.0, min(1.0, score))
    excluded = "unsupported_symbol" in risk_tags
    candidate["preopen_score"] = round(score, 4)
    candidate["preopen_grade"] = grade_from_score(score, excluded=excluded)
    candidate["preopen_reason"] = reasons
    candidate["risk_tags"] = sorted(set(risk_tags))
    candidate["quality_tags"] = sorted(set(quality_tags))
    return candidate


def score_kr_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    score = 0.0
    reasons: list[str] = []
    risk_tags: list[str] = list(candidate.get("risk_tags") or [])
    quality_tags: list[str] = list(candidate.get("quality_tags") or [])

    indicative_raw = candidate.get("extended_change_pct")
    if indicative_raw in (None, ""):
        indicative_raw = candidate.get("gap_pct")
    indicative_pct = _num(indicative_raw)
    prior_value = _num(candidate.get("prior_day_traded_value"))
    open_confirm = _num(candidate.get("open_volume_confirmation"))
    volume_ratio = _num(candidate.get("volume_ratio"))
    source_overlap = int(_num(candidate.get("source_overlap_count"), 1))
    data_quality = str(candidate.get("data_quality") or "").lower()

    if indicative_pct >= 6:
        score += 0.24
        reasons.append("indicative_strength")
    elif indicative_pct >= 3:
        score += 0.12

    if prior_value >= 10_000_000_000:
        score += 0.25
        reasons.append("prior_day_liquidity")
    elif prior_value >= 3_000_000_000:
        score += 0.12
    elif prior_value > 0:
        risk_tags.append("low_liquidity")

    if bool(candidate.get("news_or_earnings_flag")):
        score += 0.12
        reasons.append("disclosure_or_news")

    if open_confirm > 0:
        score += 0.18
        reasons.append("open_volume_confirmation")

    if volume_ratio >= 3:
        score += 0.08
        reasons.append("volume_ratio_quality")

    if source_overlap >= 2:
        score += 0.08
        reasons.append("source_overlap")

    if indicative_pct >= 20:
        score -= 0.15
        risk_tags.append("limit_up_chase_risk")

    if bool(candidate.get("stale")):
        score -= 0.20
        risk_tags.append("stale_data")
    if data_quality in {"poor", "unavailable", "token_expired"}:
        score -= 0.20
        risk_tags.append("poor_data_quality")

    score = max(0.0, min(1.0, score))
    excluded = any(tag in risk_tags for tag in ("halt_risk", "management_issue", "unsupported_symbol"))
    candidate["preopen_score"] = round(score, 4)
    candidate["preopen_grade"] = grade_from_score(score, excluded=excluded)
    candidate["preopen_reason"] = reasons
    candidate["risk_tags"] = sorted(set(risk_tags))
    candidate["quality_tags"] = sorted(set(quality_tags))
    return candidate


def score_candidates(market: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scorer = score_us_candidate if str(market or "").upper() == "US" else score_kr_candidate
    scored = [scorer(dict(candidate)) for candidate in candidates]
    scored.sort(key=lambda item: float(item.get("preopen_score", 0.0) or 0.0), reverse=True)
    for idx, item in enumerate(scored, start=1):
        item["shadow_preopen_rank"] = idx
    return scored
