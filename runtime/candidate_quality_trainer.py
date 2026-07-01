from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from runtime.candidate_quality_labels import FUTURE_LABEL_FIELDS


TRAINER_SCORE_VERSION = "trainer_quality_v1"

TRAINER_STATES = ("PLAN_A", "PLAN_B", "WATCH", "BENCH", "QUARANTINE")


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


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw in (None, ""):
            return float(default)
        return float(str(raw).replace(",", ""))
    except Exception:
        return float(default)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _trainer_weight(name: str, default: float) -> float:
    return _env_float(f"CANDIDATE_TRAINER_{name}", default)


def _trainer_env_float(primary: str, default: float, *fallback_names: str) -> float:
    for name in (primary, *fallback_names):
        raw = os.getenv(name)
        if raw not in (None, ""):
            return _env_float(name, default)
    return float(default)


def _trainer_threshold_config() -> dict[str, float]:
    return {
        "plan_a_score_min": _trainer_env_float("TRAINER_PLAN_A_SCORE_MIN", 62.0),
        "plan_a_risk_max": _trainer_env_float("TRAINER_PLAN_A_RISK_MAX", 35.0),
        "plan_b_score_min": _trainer_env_float("TRAINER_PLAN_B_SCORE_MIN", 55.0),
        "plan_b_risk_max": _trainer_env_float("TRAINER_PLAN_B_RISK_MAX", 70.0),
        "watch_score_min": _trainer_env_float("TRAINER_WATCH_SCORE_MIN", 45.0),
        "watch_risk_max": _trainer_env_float("TRAINER_WATCH_RISK_MAX", 80.0),
        "kr_weak_immediate_bucket_penalty": _trainer_env_float(
            "TRAINER_KR_WEAK_IMMEDIATE_BUCKET_PENALTY",
            -8.0,
            "CANDIDATE_TRAINER_KR_WEAK_IMMEDIATE_BUCKET_PENALTY",
        ),
        "kr_weak_immediate_bucket_risk": _trainer_env_float(
            "TRAINER_KR_WEAK_IMMEDIATE_BUCKET_RISK",
            8.0,
            "CANDIDATE_TRAINER_KR_WEAK_IMMEDIATE_BUCKET_RISK",
        ),
    }


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
    stale_cycle_count = int(_as_float(_first_present(row, "stale_cycle_count", "repeated_failed_ready_count"), 0.0))
    stale_cycle_threshold = max(1, int(_env_float("TRAINER_STALE_CYCLE_THRESHOLD", 3.0)))
    stale_cycle = bool(row.get("stale_cycle")) or stale_cycle_count >= stale_cycle_threshold

    components: dict[str, float] = {"base": 50.0}
    risk_components: dict[str, float] = {"base_risk": 20.0}
    trainer_config = _trainer_threshold_config()

    if market_key == "KR":
        if _env_bool("CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED", True):
            quality_score = _first_present(row, "candidate_quality_score", default=None)
            if quality_score not in (None, ""):
                gaps_raw = row.get("quality_data_gaps") or []
                if isinstance(gaps_raw, str):
                    gaps = {gaps_raw}
                else:
                    gaps = {str(item) for item in gaps_raw if str(item)}
                weight = _env_float("CANDIDATE_TRAINER_QUALITY_SCORE_WEIGHT", 0.3)
                gap_multiplier = 1.0
                if "ohlcv_missing" in gaps:
                    gap_multiplier = 0.0
                elif "index_history_missing" in gaps or "flow_missing" in gaps:
                    gap_multiplier = 0.5
                components["kr_quality_score_bonus"] = (_as_float(quality_score, 50.0) - 50.0) * weight * gap_multiplier
        if "KOSDAQ" in market_type:
            components["kr_kosdaq_prior"] = _trainer_weight("KR_KOSDAQ_PRIOR", 12.0)
        elif "KOSPI" in market_type:
            components["kr_kospi_penalty"] = _trainer_weight("KR_KOSPI_PENALTY", -10.0)
            risk_components["kr_kospi_risk"] = _trainer_weight("KR_KOSPI_RISK", 5.0)
        if cb in {"0~3", "3~7"}:
            components["kr_early_change_bin"] = _trainer_weight("KR_EARLY_CHANGE_BIN", 10.0)
        elif cb == "7~15":
            components["kr_late_change_penalty"] = _trainer_weight("KR_LATE_CHANGE_PENALTY", -5.0)
            risk_components["kr_late_change_risk"] = _trainer_weight("KR_LATE_CHANGE_RISK", 8.0)
        elif cb == "15+":
            components["kr_chase_change_penalty"] = _trainer_weight("KR_CHASE_CHANGE_PENALTY", -12.0)
            risk_components["kr_chase_change_risk"] = _trainer_weight("KR_CHASE_CHANGE_RISK", 15.0)
        if primary_bucket in {"liquidity_leader", "unclassified"}:
            components["kr_supported_bucket"] = _trainer_weight("KR_SUPPORTED_BUCKET", 5.0)
        if primary_bucket in {"momentum", "momentum_now", "gap_pullback", "opening_range_pullback"}:
            components["kr_weak_immediate_bucket"] = trainer_config["kr_weak_immediate_bucket_penalty"]
            risk_components["kr_weak_immediate_bucket_risk"] = trainer_config["kr_weak_immediate_bucket_risk"]
        if liquidity == "mid":
            components["kr_mid_liquidity"] = _trainer_weight("KR_MID_LIQUIDITY", 4.0)
        elif liquidity == "high":
            components["kr_high_liquidity_chase_penalty"] = _trainer_weight("KR_HIGH_LIQUIDITY_CHASE_PENALTY", -2.0)
        # catalyst(뉴스/실적) 가중 (2026-07-01, 운영자 승인 enforce, KR 한정). 토글 off면 무영향.
        # 근거: catalyst 후보가 KR 멀티데이서 robust(무catalyst 종가 -2.5~-5.7% vs 방어, placebo 초과).
        # 6월 단일기간이라 라이브 net 추적 필수. revert=KR_CATALYST_SCORE_BONUS_ENABLED=false.
        if _env_bool("KR_CATALYST_SCORE_BONUS_ENABLED", False):
            _has_catalyst = bool(row.get("news_or_earnings_sources")) or bool(row.get("news_prompt_eligible"))
            if not _has_catalyst:
                _ctags = row.get("quality_tags") or row.get("news_or_earnings_sources_json") or []
                if isinstance(_ctags, str):
                    _has_catalyst = any(tag in _ctags for tag in ("direct_catalyst", "earnings_or_guidance"))
                else:
                    _has_catalyst = any(str(_t) in {"direct_catalyst", "earnings_or_guidance"} for _t in _ctags)
            if _has_catalyst:
                components["kr_catalyst_bonus"] = _trainer_weight("KR_CATALYST_BONUS", 12.0)
                row["catalyst_flagged"] = True
    else:
        if _env_bool("CANDIDATE_TRAINER_US_QUALITY_SCORE_ENABLED", False):
            quality_score = _first_present(row, "candidate_quality_score", default=None)
            if quality_score not in (None, ""):
                gaps_raw = row.get("quality_data_gaps") or []
                if isinstance(gaps_raw, str):
                    gaps = {gaps_raw}
                else:
                    gaps = {str(item) for item in gaps_raw if str(item)}
                weight = _env_float("CANDIDATE_TRAINER_US_QUALITY_SCORE_WEIGHT", 0.2)
                gap_multiplier = 0.5 if "history_incomplete" in gaps else 1.0
                if "ohlcv_missing" in gaps or "history_unavailable" in gaps:
                    gap_multiplier = 0.0
                components["us_quality_score_bonus"] = (_as_float(quality_score, 50.0) - 50.0) * weight * gap_multiplier
        if liquidity == "high":
            components["us_high_liquidity"] = _trainer_weight("US_HIGH_LIQUIDITY", 14.0)
        elif liquidity == "mid":
            components["us_mid_liquidity_penalty"] = _trainer_weight("US_MID_LIQUIDITY_PENALTY", -8.0)
            risk_components["us_mid_liquidity_risk"] = _trainer_weight("US_MID_LIQUIDITY_RISK", 4.0)
        if primary_bucket in {"momentum_now", "opening_range_pullback"}:
            components["us_preferred_bucket"] = _trainer_weight("US_PREFERRED_BUCKET", 12.0)
        elif primary_bucket == "gap_pullback":
            components["us_gap_pullback_watch"] = _trainer_weight("US_GAP_PULLBACK_WATCH", 4.0)
        elif primary_bucket in {"unclassified", "unknown", ""}:
            components["us_unclassified_penalty"] = _trainer_weight("US_UNCLASSIFIED_PENALTY", -8.0)
        if cb in {"3~7", "7~15"}:
            components["us_preferred_change_bin"] = _trainer_weight("US_PREFERRED_CHANGE_BIN", 10.0)
        elif cb in {"0~3", "15+"}:
            components["us_poor_change_bin"] = _trainer_weight("US_POOR_CHANGE_BIN_PENALTY", -6.0)
            risk_components["us_poor_change_risk"] = _trainer_weight("US_POOR_CHANGE_RISK", 6.0)
        # catalyst(뉴스/실적) 가중 (2026-07-01, 운영자 승인 enforce). US는 1~2일 horizon서 유효
        # (catalyst-placebo 1일 +0.32·2일 +0.22, 단 3일 소멸=착시라 KR보다 약함). KR(+12)보다 보수적 +10.
        # revert=US_CATALYST_SCORE_BONUS_ENABLED=false. net 추적 필수(6월단일).
        if _env_bool("US_CATALYST_SCORE_BONUS_ENABLED", False):
            _has_catalyst = bool(row.get("news_or_earnings_sources")) or bool(row.get("news_prompt_eligible"))
            if not _has_catalyst:
                _ctags = row.get("quality_tags") or row.get("news_or_earnings_sources_json") or []
                if isinstance(_ctags, str):
                    _has_catalyst = any(tag in _ctags for tag in ("direct_catalyst", "earnings_or_guidance"))
                else:
                    _has_catalyst = any(str(_t) in {"direct_catalyst", "earnings_or_guidance"} for _t in _ctags)
            if _has_catalyst:
                components["us_catalyst_bonus"] = _trainer_weight("US_CATALYST_BONUS", 10.0)
                row["catalyst_flagged"] = True

    raw_rank_val = int(_as_float(_first_present(row, "raw_rank"), 0.0))
    raw_rank_cap = int(_env_float("TRAINER_RAW_RANK_BONUS_CAP", 25.0))
    raw_rank_step = _trainer_weight("RAW_RANK_BONUS_PER_STEP", 0.3)
    if 0 < raw_rank_val <= raw_rank_cap:
        components["raw_rank_bonus"] = raw_rank_step * (raw_rank_cap + 1 - raw_rank_val)

    if age_min > 120:
        components["stale_age_penalty"] = _trainer_weight("STALE_AGE_PENALTY", -10.0)
        risk_components["stale_age_risk"] = _trainer_weight("STALE_AGE_RISK", 10.0)
    elif age_min > 60:
        components["aging_candidate_penalty"] = _trainer_weight("AGING_CANDIDATE_PENALTY", -5.0)
        risk_components["aging_candidate_risk"] = _trainer_weight("AGING_CANDIDATE_RISK", 5.0)
    if chase_pct > 8.0:
        components["chase_penalty"] = _trainer_weight("CHASE_PENALTY", -10.0)
        risk_components["chase_risk"] = _trainer_weight("CHASE_RISK", 10.0)
    elif chase_pct > 4.0:
        components["mild_chase_penalty"] = _trainer_weight("MILD_CHASE_PENALTY", -5.0)
        risk_components["mild_chase_risk"] = _trainer_weight("MILD_CHASE_RISK", 5.0)
    if fhb in {"at_high", "near_high"}:
        risk_components["high_zone_risk"] = _trainer_weight("HIGH_ZONE_RISK", 6.0)
        if cb == "15+":
            components["high_zone_chase_penalty"] = _trainer_weight("HIGH_ZONE_CHASE_PENALTY", -5.0)
    if trainer_tier in {"BENCH", "QUARANTINE"}:
        components["trainer_tier_penalty"] = _trainer_weight("TRAINER_TIER_PENALTY", -20.0)
        risk_components["trainer_tier_risk"] = _trainer_weight("TRAINER_TIER_RISK", 20.0)
    if freshness in {"stale", "old"}:
        components["freshness_penalty"] = _trainer_weight("FRESHNESS_PENALTY", -12.0)
        risk_components["freshness_risk"] = _trainer_weight("FRESHNESS_RISK", 12.0)
    if _data_quality_bad(row):
        components["data_quality_penalty"] = _trainer_weight("DATA_QUALITY_PENALTY", -40.0)
        risk_components["data_quality_risk"] = _trainer_weight("DATA_QUALITY_RISK", 40.0)
    if stale_cycle:
        components["stale_cycle_penalty"] = _env_float("TRAINER_STALE_CYCLE_PENALTY", -8.0)
    if status in {"trade_ready", "buy_ready", "probe_ready"}:
        components["runtime_ready_bonus"] = _trainer_weight("RUNTIME_READY_BONUS", 8.0)

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
                "config": {key: round(value, 4) for key, value in trainer_config.items()},
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
    config = _trainer_threshold_config()
    if plan_a_score >= config["plan_a_score_min"] and risk_score <= config["plan_a_risk_max"]:
        return "PLAN_A"
    if pathb_wait_score >= config["plan_b_score_min"] and risk_score <= config["plan_b_risk_max"]:
        return "PLAN_B"
    if prompt_score >= config["watch_score_min"] and risk_score <= config["watch_risk_max"]:
        return "WATCH"
    return "BENCH"
