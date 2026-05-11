from __future__ import annotations

from datetime import datetime
from typing import Any


TRAINER_SCORE_VERSION = "trainer_quality_v1"

TRAINER_STATES = ("PLAN_A", "PLAN_B", "WATCH", "BENCH", "QUARANTINE")

FUTURE_LABEL_FIELDS = {
    "forward_1d",
    "forward_3d",
    "forward_5d",
    "ret30",
    "ret60",
    "mfe30",
    "mfe60",
    "mae30",
    "mae60",
    "return_pct",
    "max_runup_pct",
    "max_drawdown_pct",
    "pnl_pct",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, float(value)))


def _first_present(candidate: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in candidate and candidate.get(key) not in (None, ""):
            return candidate.get(key)
    return default


def normalize_ticker(ticker: Any, market: str) -> str:
    text = _text(ticker)
    return text.upper() if _upper(market) == "US" else text


def change_bin(change_pct: Any) -> str:
    value = _as_float(change_pct, 0.0)
    if value <= -5.0:
        return "<=-5"
    if value < 0.0:
        return "-5~0"
    if value < 3.0:
        return "0~3"
    if value < 7.0:
        return "3~7"
    if value < 15.0:
        return "7~15"
    return "15+"


def from_high_bin(from_high_pct: Any) -> str:
    if from_high_pct in (None, ""):
        return "unknown"
    value = _as_float(from_high_pct, 0.0)
    if value <= -5.0:
        return "deep"
    if value <= -2.0:
        return "pullback"
    if value <= -0.5:
        return "near_high"
    return "at_high"


def _candidate_age_min(candidate: dict[str, Any], *, now: datetime | None = None) -> float:
    explicit = _first_present(candidate, "candidate_age_min", "age_min")
    if explicit not in (None, ""):
        return _as_float(explicit, 0.0)
    detected = _text(_first_present(candidate, "candidate_detected_at", "first_seen_at", "detected_at"))
    if not detected:
        return 0.0
    try:
        parsed = datetime.fromisoformat(detected.replace("Z", "+00:00"))
        current = now or datetime.now(parsed.tzinfo) if parsed.tzinfo else now or datetime.now()
        return max(0.0, (current - parsed).total_seconds() / 60.0)
    except Exception:
        return 0.0


def _data_quality_bad(candidate: dict[str, Any]) -> bool:
    flags = candidate.get("data_quality_flags")
    if isinstance(flags, str):
        flag_text = flags.lower()
    else:
        flag_text = " ".join(str(item).lower() for item in (flags or []))
    data_quality = _lower(_first_present(candidate, "data_quality", "history_status", "screen_quality"))
    return (
        data_quality in {"bad", "missing", "stale", "invalid", "partial_bad"}
        or "bad_data" in flag_text
        or "missing" in flag_text
        or "invalid" in flag_text
    )


def _source_tags(candidate: dict[str, Any], *, market: str, cb: str, fhb: str) -> list[str]:
    tags = []
    for raw in (
        _first_present(candidate, "source", "candidate_source", default="base_universe"),
        _first_present(candidate, "primary_bucket", "category", default="unclassified"),
        _first_present(candidate, "liquidity_bucket", default="unknown_liq"),
        _first_present(candidate, "market_type", default="unknown_board"),
        cb,
        fhb,
        _first_present(candidate, "freshness_verdict", default="unknown_freshness"),
    ):
        text = _text(raw)
        if text:
            tags.append(text)
    return [f"{_upper(market)}:{tag}" for tag in tags]


def score_candidate_for_trainer(
    candidate: dict[str, Any],
    *,
    market: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a live-known candidate score package.

    This function intentionally does not read outcome/forward labels. Any such
    fields present on the candidate are ignored and reported in the component
    payload so tests and reports can catch leakage-prone inputs.
    """
    row = dict(candidate or {})
    market_key = "US" if _upper(market or row.get("market")) == "US" else "KR"
    ticker = normalize_ticker(row.get("ticker"), market_key)
    primary_bucket = _lower(_first_present(row, "primary_bucket", "category", default="unclassified")) or "unclassified"
    market_type = _upper(_first_present(row, "market_type", default=""))
    liquidity = _lower(_first_present(row, "liquidity_bucket", default="unknown")) or "unknown"
    change_pct = _as_float(_first_present(row, "change_pct", "change_rate"), 0.0)
    cb = change_bin(change_pct)
    fhb = _lower(_first_present(row, "from_high_bucket", default="")) or from_high_bin(row.get("from_high_pct"))
    age_min = _candidate_age_min(row, now=now)
    chase_pct = _as_float(_first_present(row, "price_change_since_first_seen_pct", "price_change_candidate_to_order_pct"), 0.0)
    trainer_tier = _upper(_first_present(row, "trainer_tier", "lifecycle_state", default=""))
    freshness = _lower(_first_present(row, "freshness_verdict", default=""))
    status = _lower(_first_present(row, "status", default=""))
    policy_tags = " ".join(str(item).lower() for item in (row.get("policy_tags") or row.get("risk_tags") or []))

    components: dict[str, float] = {"base": 50.0}
    risk_components: dict[str, float] = {"base_risk": 20.0}

    if market_key == "KR":
        if "KOSDAQ" in market_type:
            components["kr_kosdaq_prior"] = 12.0
        elif "KOSPI" in market_type:
            components["kr_kospi_penalty"] = -10.0
            risk_components["kr_kospi_risk"] = 5.0
        if cb in {"0~3", "3~7"}:
            components["kr_early_change_bin"] = 10.0
        elif cb == "7~15":
            components["kr_late_change_penalty"] = -5.0
            risk_components["kr_late_change_risk"] = 8.0
        elif cb == "15+":
            components["kr_chase_change_penalty"] = -12.0
            risk_components["kr_chase_change_risk"] = 15.0
        if primary_bucket in {"liquidity_leader", "unclassified"}:
            components["kr_supported_bucket"] = 5.0
        if primary_bucket in {"momentum", "momentum_now", "gap_pullback", "opening_range_pullback"}:
            components["kr_weak_immediate_bucket"] = -8.0
            risk_components["kr_weak_immediate_bucket_risk"] = 8.0
        if liquidity == "mid":
            components["kr_mid_liquidity"] = 4.0
        elif liquidity == "high":
            components["kr_high_liquidity_chase_penalty"] = -2.0
    else:
        if liquidity == "high":
            components["us_high_liquidity"] = 14.0
        elif liquidity == "mid":
            components["us_mid_liquidity_penalty"] = -8.0
            risk_components["us_mid_liquidity_risk"] = 4.0
        if primary_bucket in {"momentum_now", "opening_range_pullback"}:
            components["us_preferred_bucket"] = 12.0
        elif primary_bucket == "gap_pullback":
            components["us_gap_pullback_watch"] = 4.0
        elif primary_bucket in {"unclassified", "unknown", ""}:
            components["us_unclassified_penalty"] = -8.0
        if cb in {"3~7", "7~15"}:
            components["us_preferred_change_bin"] = 10.0
        elif cb in {"0~3", "15+"}:
            components["us_poor_change_bin"] = -6.0
            risk_components["us_poor_change_risk"] = 6.0

    if age_min > 120:
        components["stale_age_penalty"] = -10.0
        risk_components["stale_age_risk"] = 10.0
    elif age_min > 60:
        components["aging_candidate_penalty"] = -5.0
        risk_components["aging_candidate_risk"] = 5.0
    if chase_pct > 8.0:
        components["chase_penalty"] = -10.0
        risk_components["chase_risk"] = 10.0
    elif chase_pct > 4.0:
        components["mild_chase_penalty"] = -5.0
        risk_components["mild_chase_risk"] = 5.0
    if fhb in {"at_high", "near_high"}:
        risk_components["high_zone_risk"] = 6.0
        if cb == "15+":
            components["high_zone_chase_penalty"] = -5.0
    if trainer_tier in {"BENCH", "QUARANTINE"}:
        components["trainer_tier_penalty"] = -20.0
        risk_components["trainer_tier_risk"] = 20.0
    if freshness in {"stale", "old"}:
        components["freshness_penalty"] = -12.0
        risk_components["freshness_risk"] = 12.0
    if _data_quality_bad(row):
        components["data_quality_penalty"] = -40.0
        risk_components["data_quality_risk"] = 40.0
    if status in {"trade_ready", "buy_ready", "probe_ready"}:
        components["runtime_ready_bonus"] = 8.0

    prompt_score = _clamp(sum(components.values()))
    risk_score = _clamp(sum(risk_components.values()))
    confirmation = 0.0
    post_open = row.get("post_open_features") if isinstance(row.get("post_open_features"), dict) else {}
    momentum_state = _lower(_first_present(row, "post_open_momentum_state", default=post_open.get("momentum_state")))
    if momentum_state in {"early_strength", "sustained", "confirming", "strong"}:
        confirmation += 10.0
    if _as_float(post_open.get("ret_5m_pct"), 0.0) > 0:
        confirmation += 4.0
    plan_a_score = _clamp(prompt_score - risk_score * 0.45 + confirmation)
    wait_bonus = 0.0
    if fhb in {"at_high", "near_high"} or cb in {"7~15", "15+"}:
        wait_bonus += 10.0
    if primary_bucket in {"gap_pullback", "opening_range_pullback", "liquidity_leader"}:
        wait_bonus += 5.0
    pathb_wait_score = _clamp(prompt_score + min(risk_score, 40.0) * 0.20 + wait_bonus)

    state = classify_trainer_scores(
        prompt_score=prompt_score,
        plan_a_score=plan_a_score,
        pathb_wait_score=pathb_wait_score,
        risk_score=risk_score,
        hard_quarantine=(
            _data_quality_bad(row)
            or trainer_tier == "QUARANTINE"
            or "hard_safety" in policy_tags
            or status in {"hard_block", "blocked", "quarantine"}
        ),
    )
    future_fields_present = sorted(key for key in FUTURE_LABEL_FIELDS if key in row)

    out = dict(row)
    out.update(
        {
            "ticker": ticker,
            "market": market_key,
            "change_bin": cb,
            "from_high_bucket": fhb,
            "trainer_prompt_score": round(prompt_score, 4),
            "trainer_plan_a_score": round(plan_a_score, 4),
            "trainer_pathb_wait_score": round(pathb_wait_score, 4),
            "trainer_risk_score": round(risk_score, 4),
            "trainer_candidate_state": state,
            "trainer_score_components": {
                "version": TRAINER_SCORE_VERSION,
                "prompt": {key: round(value, 4) for key, value in components.items()},
                "risk": {key: round(value, 4) for key, value in risk_components.items()},
                "confirmation": round(confirmation, 4),
                "wait_bonus": round(wait_bonus, 4),
                "future_fields_ignored": future_fields_present,
            },
            "source_tags": _source_tags(row, market=market_key, cb=cb, fhb=fhb),
            "candidate_pool_version": TRAINER_SCORE_VERSION,
        }
    )
    return out


def classify_trainer_scores(
    *,
    prompt_score: float,
    plan_a_score: float,
    pathb_wait_score: float,
    risk_score: float,
    hard_quarantine: bool = False,
) -> str:
    if hard_quarantine:
        return "QUARANTINE"
    if plan_a_score >= 62.0 and risk_score <= 35.0:
        return "PLAN_A"
    if pathb_wait_score >= 55.0 and risk_score <= 70.0:
        return "PLAN_B"
    if prompt_score >= 45.0 and risk_score <= 80.0:
        return "WATCH"
    return "BENCH"

