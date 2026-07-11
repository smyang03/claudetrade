from __future__ import annotations

import os
from typing import Any

from runtime.live_evidence_pack import build_fade_recovered_shadow


VERSION = "adaptive_live_condition.v3"
BULLISH_PROBE_VERSION = "kr_bullish_strength_probe.v1"


RISK_ON_MODES = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL"}
RISK_OFF_MODES = {"HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR", "CAUTIOUS"}


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _bool_true(value: Any) -> bool:
    return bool(value is True or str(value).strip().lower() in {"1", "true", "yes", "on"})


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        if raw in (None, ""):
            return float(default)
        return float(str(raw).replace(",", ""))
    except Exception:
        return float(default)


def _threshold(market: str, name: str, default: float) -> float:
    market_key = str(market or "").upper()
    return _env_float(f"ADAPTIVE_LIVE_{name}_{market_key}", _env_float(f"ADAPTIVE_LIVE_{name}", default))


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def infer_market_regime(consensus_mode: str = "", market_context: dict[str, Any] | None = None) -> str:
    context = market_context if isinstance(market_context, dict) else {}
    raw = str(
        context.get("market_regime")
        or context.get("regime")
        or consensus_mode
        or ""
    ).strip().upper()
    if raw in {"RISK_ON", "BULL", "STRONG_BULL"} or raw in RISK_ON_MODES:
        return "risk_on"
    if raw in {"RISK_OFF", "BEAR", "STRONG_BEAR"} or raw in RISK_OFF_MODES:
        return "risk_off"
    return "mixed"


def _rank_scores(values: dict[str, float | None]) -> dict[str, float]:
    present = sorted(v for v in values.values() if v is not None)
    if not present:
        return {}
    if len(present) == 1:
        return {key: (1.0 if value is not None else 0.0) for key, value in values.items()}
    out: dict[str, float] = {}
    denom = max(1, len(present) - 1)
    for key, value in values.items():
        if value is None:
            out[key] = 0.0
            continue
        below = sum(1 for item in present if item <= value) - 1
        out[key] = max(0.0, min(1.0, below / denom))
    return out


def _features_for(meta: dict[str, Any], market: str, ticker: str) -> dict[str, Any]:
    features_map = meta.get("_post_open_features_by_ticker")
    if not isinstance(features_map, dict):
        features_map = meta.get("post_open_features_by_ticker")
    if not isinstance(features_map, dict):
        return {}
    key = _ticker_key(market, ticker)
    raw = features_map.get(key) or features_map.get(ticker)
    if raw:
        return dict(raw)
    if str(market or "").upper() == "US":
        for raw_key, value in features_map.items():
            if str(raw_key).upper() == key and isinstance(value, dict):
                return dict(value)
    return {}


def _prompt_rows(selection_meta: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in list((selection_meta or {}).get("_final_prompt_pool") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    ]


def _action_map(selection_meta: dict[str, Any], market: str) -> dict[str, dict[str, Any]]:
    return {
        _ticker_key(market, row.get("ticker")): dict(row)
        for row in list((selection_meta or {}).get("candidate_actions") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    }


def _route_map(selection_meta: dict[str, Any], market: str) -> dict[str, dict[str, Any]]:
    return {
        _ticker_key(market, row.get("ticker")): dict(row)
        for row in list((selection_meta or {}).get("_candidate_action_routes") or [])
        if isinstance(row, dict) and str(row.get("ticker") or "").strip()
    }


def _minutes_after_kr_open(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        from datetime import datetime

        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return float((stamp.hour * 60 + stamp.minute) - (9 * 60))
    except Exception:
        return None


def build_kr_bullish_strength_probe(
    *,
    market: str,
    selection_meta: dict[str, Any],
    consensus_mode: str,
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Choose at most one deterministic KR strength candidate for measurement only.

    This contract never changes Claude actions, trade_ready, routing, or order state.
    It exists to measure whether broad bullish sessions justify a tightly bounded
    probe instead of allowing every evidence-ready candidate to end as WATCH.
    """
    market_key = str(market or "").upper()
    meta = dict(selection_meta or {})
    context = dict(market_context or {})
    mode = str(consensus_mode or meta.get("consensus_mode") or "").strip().upper()
    enabled = _bool_true(os.getenv("KR_BULLISH_STRENGTH_PROBE_SHADOW_ENABLED", "true"))
    index_change_pct = _num(context.get("index_change_pct"))
    secondary_change_pct = _num(context.get("secondary_index_change_pct"))
    breadth_up_ratio_pct = _num(context.get("breadth_up_ratio_pct"))
    elapsed_min = _minutes_after_kr_open(meta.get("selection_snapshot_ts"))
    thresholds = {
        "index_change_min_pct": _env_float("KR_BULLISH_PROBE_INDEX_CHANGE_MIN_PCT", 2.0),
        "breadth_up_ratio_min_pct": _env_float("KR_BULLISH_PROBE_BREADTH_MIN_PCT", 60.0),
        "trainer_plan_a_min": _env_float("KR_BULLISH_PROBE_PLAN_A_MIN", 65.0),
        "trainer_risk_max": _env_float("KR_BULLISH_PROBE_RISK_MAX", 35.0),
        "entry_window_max_min": _env_float("KR_BULLISH_PROBE_ENTRY_WINDOW_MAX_MIN", 90.0),
        "round_trip_cost_pct": _env_float("KR_BULLISH_PROBE_ROUND_TRIP_COST_PCT", 0.5),
        "order_cap_krw": _env_float("KR_BULLISH_PROBE_ORDER_CAP_KRW", 200000.0),
    }
    market_checks = {
        "enabled": enabled,
        "kr_market": market_key == "KR",
        "risk_on_mode": mode in RISK_ON_MODES,
        "fresh_market_context": _bool_true(context.get("fresh")),
        "index_strong": index_change_pct is not None and index_change_pct >= thresholds["index_change_min_pct"],
        "breadth_strong": breadth_up_ratio_pct is not None and breadth_up_ratio_pct >= thresholds["breadth_up_ratio_min_pct"],
        "inside_entry_window": elapsed_min is not None and 0.0 <= elapsed_min <= thresholds["entry_window_max_min"],
    }
    market_eligible = all(market_checks.values())
    actions = _action_map(meta, market_key)
    routes = _route_map(meta, market_key)
    eligible: list[dict[str, Any]] = []
    rejected: dict[str, list[str]] = {}

    for prompt_rank, row in enumerate(_prompt_rows(meta), start=1):
        ticker = _ticker_key(market_key, row.get("ticker"))
        action = actions.get(ticker, {})
        route = routes.get(ticker, {})
        score = _num(row.get("trainer_plan_a_score"))
        risk_score = _num(row.get("trainer_risk_score"))
        evidence_ceiling = str(
            row.get("selection_evidence_action_ceiling")
            or row.get("evidence_action_ceiling")
            or ((route.get("runtime_gate") or {}).get("evidence_action_ceiling"))
            or ""
        ).strip().upper()
        claude_action = str(action.get("action") or "WATCH").strip().upper()
        route_final = str(route.get("final_action") or claude_action or "WATCH").strip().upper()
        blockers = [str(value) for value in list(action.get("blocking_factors") or []) if str(value)]
        hard_block_tokens = (
            "hard", "risk_off", "halt", "forbidden", "stale", "missing", "liquidity",
            "afford", "extreme_chase", "news_risk", "quarantine", "same_day_stop",
        )
        hard_blockers = [
            value for value in blockers
            if any(token in value.strip().lower() for token in hard_block_tokens)
        ]
        runtime_reason = str(route.get("runtime_gate_reason") or "").strip()
        reasons: list[str] = []
        if str(row.get("trainer_candidate_state") or "").strip().upper() != "PLAN_A":
            reasons.append("not_plan_a")
        if score is None or score < thresholds["trainer_plan_a_min"]:
            reasons.append("plan_a_score_below_min")
        if risk_score is None or risk_score > thresholds["trainer_risk_max"]:
            reasons.append("risk_score_above_max_or_missing")
        if evidence_ceiling != "BUY_READY":
            reasons.append("evidence_ceiling_not_buy_ready")
        if claude_action != "WATCH" or route_final not in {"", "WATCH"}:
            reasons.append("not_watch_missed_opportunity")
        if hard_blockers:
            reasons.append("claude_hard_blocking_factors")
        if runtime_reason and runtime_reason not in {"watch", "judgment_not_executable"}:
            reasons.append("runtime_hard_block")
        if reasons:
            rejected[ticker] = reasons
            continue
        eligible.append(
            {
                "ticker": ticker,
                "prompt_rank": int(row.get("prompt_rank") or row.get("prompt_rank_after_trim") or prompt_rank),
                "trainer_plan_a_score": score,
                "trainer_risk_score": risk_score,
                "evidence_action_ceiling": evidence_ceiling,
                "claude_action": claude_action,
                "claude_reason_code": str(action.get("reason_code") or action.get("reason") or ""),
                "recheck_condition": str(action.get("invalidation_condition") or ""),
                "soft_blocking_factors": [value for value in blockers if value not in hard_blockers],
                "reference_price": _num(row.get("price") or row.get("current_price")),
            }
        )

    eligible.sort(
        key=lambda item: (
            -float(item.get("trainer_plan_a_score") or 0.0),
            float(item.get("trainer_risk_score") or 999.0),
            int(item.get("prompt_rank") or 9999),
            str(item.get("ticker") or ""),
        )
    )
    selected = dict(eligible[0]) if market_eligible and eligible else {}
    if selected:
        reference_price = _num(selected.get("reference_price"))
        quantity = int(thresholds["order_cap_krw"] // reference_price) if reference_price and reference_price > 0 else 0
        selected["simulated_quantity"] = quantity
        selected["simulated_invested_krw"] = quantity * reference_price if reference_price else 0.0
    return {
        "version": BULLISH_PROBE_VERSION,
        "enabled": enabled,
        "shadow_only": True,
        "non_executable": True,
        "local_promotion_allowed": False,
        "max_candidates": 1,
        "consensus_mode": mode,
        "market_context": {
            "fresh": bool(context.get("fresh")),
            "source": str(context.get("source") or ""),
            "index_change_pct": index_change_pct,
            "secondary_index_change_pct": secondary_change_pct,
            "breadth_up_ratio_pct": breadth_up_ratio_pct,
            "minutes_after_open": elapsed_min,
        },
        "thresholds": thresholds,
        "market_checks": market_checks,
        "market_eligible": market_eligible,
        "eligible_count": len(eligible),
        "eligible": eligible,
        "selected": selected,
        "selected_ticker": str(selected.get("ticker") or ""),
        "rejected": rejected,
        "maturation": {
            "paths": ["immediate", "volume_surge", "wait_30m", "wait_60m"],
            "net_cost_pct": thresholds["round_trip_cost_pct"],
            "order_cap_krw": thresholds["order_cap_krw"],
            "minimum_distinct_sessions": int(_env_float("KR_BULLISH_PROBE_MIN_SESSIONS", 20.0)),
            "promotion_is_manual": True,
        },
    }


def build_adaptive_live_condition(
    *,
    market: str,
    selection_meta: dict[str, Any],
    consensus_mode: str = "",
    market_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    meta = dict(selection_meta or {})
    watchlist = list(dict.fromkeys(meta.get("watchlist") or meta.get("_raw_watchlist") or []))
    regime = infer_market_regime(consensus_mode, market_context)
    feature_rows: dict[str, dict[str, Any]] = {}
    ret_3m_values: dict[str, float | None] = {}
    ret_5m_values: dict[str, float | None] = {}
    ret_10m_values: dict[str, float | None] = {}
    ret_30m_values: dict[str, float | None] = {}

    for ticker in watchlist:
        key = _ticker_key(market_key, ticker)
        features = _features_for(meta, market_key, key)
        feature_rows[key] = features
        ret_3m_values[key] = _num(features.get("ret_3m_pct"))
        ret_5m_values[key] = _num(features.get("ret_5m_pct"))
        ret_10m_values[key] = _num(features.get("ret_10m_pct"))
        ret_30m_values[key] = _num(features.get("ret_30m_pct"))

    ranks = {
        "ret_3m": _rank_scores(ret_3m_values),
        "ret_5m": _rank_scores(ret_5m_values),
        "ret_10m": _rank_scores(ret_10m_values),
        "ret_30m": _rank_scores(ret_30m_values),
    }

    decisions: dict[str, dict[str, Any]] = {}
    reask_claude_shadow: list[str] = []
    suggested_probe_ready_shadow: list[str] = []
    suggested_micro_probe_shadow: list[str] = []
    fade_recovered_shadow: list[str] = []
    watch_shadow: list[str] = []
    thresholds = {
        "r3_min": _threshold(market_key, "R3_MIN", 0.8),
        "r5_min": _threshold(market_key, "R5_MIN", 0.0),
        "r10_min": _threshold(market_key, "R10_MIN", 3.0),
        "r30_min": _threshold(market_key, "R30_MIN", 8.0),
        "pullback_min": _threshold(market_key, "PULLBACK_MIN", -3.0),
        "late_pullback_min": _threshold(market_key, "LATE_PULLBACK_MIN", -2.5),
        "mixed_r3_min": _threshold(market_key, "MIXED_R3_MIN", 1.2),
        "mixed_r5_min": _threshold(market_key, "MIXED_R5_MIN", 0.2),
        "mixed_pullback_min": _threshold(market_key, "MIXED_PULLBACK_MIN", -2.0),
    }

    for ticker in watchlist:
        key = _ticker_key(market_key, ticker)
        features = feature_rows.get(key) or {}
        r3 = ret_3m_values.get(key)
        r5 = ret_5m_values.get(key)
        r10 = ret_10m_values.get(key)
        r30 = ret_30m_values.get(key)
        pullback = _num(features.get("pullback_from_high_pct"))
        from_high = _num(features.get("from_open_high_pct"))
        volume_ratio = _num(features.get("volume_ratio_open"))
        vwap_distance = _num(features.get("vwap_distance_pct"))
        opening_break = _bool_true(features.get("opening_range_break"))
        momentum_state = str(features.get("momentum_state") or "unknown").strip().lower()
        data_quality = str(features.get("data_quality") or "unknown").strip().lower()
        missing_vwap_volume = volume_ratio is None and vwap_distance is None
        fade_recovered = build_fade_recovered_shadow(market=market_key, features=features)
        if fade_recovered.get("fade_recovered_shadow"):
            fade_recovered_shadow.append(key)

        score = 0.0
        score += {"risk_on": 20.0, "mixed": 5.0, "risk_off": -25.0}.get(regime, 0.0)
        score += ranks["ret_3m"].get(key, 0.0) * 12.0
        score += ranks["ret_5m"].get(key, 0.0) * 10.0
        score += ranks["ret_10m"].get(key, 0.0) * 12.0
        score += ranks["ret_30m"].get(key, 0.0) * 8.0
        if opening_break:
            score += 18.0
        if pullback is not None:
            if pullback >= -2.0:
                score += 8.0
            elif pullback < -5.0:
                score -= 8.0
        if momentum_state == "early_strength":
            score += 10.0
        elif momentum_state == "early_probe_only":
            score += 6.0
        elif momentum_state == "late_mover":
            score -= 4.0
        elif momentum_state == "fade":
            score -= 35.0
        if data_quality in {"first_observed", "unknown", "missing"}:
            score -= 6.0
        if missing_vwap_volume:
            score -= 6.0
        if from_high is not None and from_high >= 30.0:
            score -= 12.0

        reasons: list[str] = []
        blockers: list[str] = []
        action = "WATCH"
        size_intent = "none"
        suggested_claude_action = ""
        suggested_size_intent = "none"
        action_ceiling = "BUY_READY"

        if data_quality in {"first_observed", "unknown", "missing"} or missing_vwap_volume:
            action_ceiling = "PROBE_READY"
            if data_quality in {"first_observed", "unknown", "missing"}:
                blockers.append(f"data_quality:{data_quality}")
            if missing_vwap_volume:
                blockers.append("missing_vwap_volume")
        if momentum_state == "late_mover":
            action_ceiling = "MICRO_PROBE"
            blockers.append("late_mover_ceiling")
        if momentum_state == "fade":
            action_ceiling = "WATCH"
            blockers.append("fade")
        if regime == "risk_off":
            action_ceiling = "WATCH"
            blockers.append("risk_off_regime")
        if from_high is not None and from_high >= 30.0:
            action_ceiling = "WATCH"
            blockers.append("extreme_chase")

        early_probe_ok = (
            regime == "risk_on"
            and momentum_state not in {"fade", "late_mover"}
            and opening_break
            and (pullback is None or pullback >= thresholds["pullback_min"])
            and (
                (r3 is not None and r3 >= thresholds["r3_min"] and (r5 is None or r5 >= thresholds["r5_min"]))
                or (r10 is not None and r10 >= thresholds["r10_min"])
            )
        )
        late_micro_ok = (
            regime == "risk_on"
            and momentum_state == "late_mover"
            and opening_break
            and (pullback is None or pullback >= thresholds["late_pullback_min"])
            and (from_high is None or from_high < 30.0)
            and (r30 is not None and r30 >= thresholds["r30_min"])
        )
        mixed_probe_ok = (
            regime == "mixed"
            and momentum_state not in {"fade", "late_mover"}
            and opening_break
            and (pullback is None or pullback >= thresholds["mixed_pullback_min"])
            and r3 is not None and r3 >= thresholds["mixed_r3_min"]
            and r5 is not None and r5 >= thresholds["mixed_r5_min"]
        )

        if early_probe_ok or mixed_probe_ok:
            suggested_claude_action = "PROBE_READY"
            suggested_size_intent = "probe"
            reasons.append("or_break_with_short_momentum")
        elif late_micro_ok:
            suggested_claude_action = "MICRO_PROBE"
            suggested_size_intent = "micro"
            reasons.append("late_mover_30m_continuation")

        if action_ceiling == "WATCH":
            suggested_claude_action = ""
            suggested_size_intent = "none"
        elif action_ceiling == "MICRO_PROBE" and suggested_claude_action == "PROBE_READY":
            suggested_claude_action = "MICRO_PROBE"
            suggested_size_intent = "micro"

        claude_reask = bool(suggested_claude_action)
        if claude_reask:
            action = "REASK_CLAUDE"
            size_intent = "none"
            reask_claude_shadow.append(key)
            if suggested_claude_action == "PROBE_READY":
                suggested_probe_ready_shadow.append(key)
            elif suggested_claude_action == "MICRO_PROBE":
                suggested_micro_probe_shadow.append(key)
        else:
            action = "WATCH"
            size_intent = "none"
            watch_shadow.append(key)

        decisions[key] = {
            "ticker": key,
            "action": action,
            "size_intent": size_intent,
            "suggested_claude_action": suggested_claude_action,
            "suggested_size_intent": suggested_size_intent,
            "fade_recovered_shadow": bool(fade_recovered.get("fade_recovered_shadow")),
            "fade_recovered_reason": str(fade_recovered.get("fade_recovered_reason") or ""),
            "fade_recovered_checks": dict(fade_recovered.get("fade_recovered_checks") or {}),
            "fade_recovered_suggested_action": "PROBE_READY" if fade_recovered.get("fade_recovered_shadow") else "",
            "claude_reask": claude_reask,
            "reask_reason": "live_evidence_changed" if claude_reask else "",
            "non_executable": True,
            "execution_owner": "claude",
            "local_promotion_allowed": False,
            "thresholds": dict(thresholds),
            "score": round(score, 3),
            "market_regime": regime,
            "action_ceiling": action_ceiling,
            "reason_codes": reasons,
            "blockers": blockers,
            "momentum_state": momentum_state,
            "data_quality": data_quality,
            "ret_3m_pct": r3,
            "ret_5m_pct": r5,
            "ret_10m_pct": r10,
            "ret_30m_pct": r30,
            "opening_range_break": opening_break,
            "pullback_from_high_pct": pullback,
            "from_open_high_pct": from_high,
            "volume_ratio_open": volume_ratio,
            "vwap_distance_pct": vwap_distance,
            "missing_vwap_volume": missing_vwap_volume,
        }

    return {
        "version": VERSION,
        "market": market_key,
        "market_regime": regime,
        "shadow_only": True,
        "non_executable": True,
        "execution_owner": "claude",
        "local_promotion_allowed": False,
        "thresholds": dict(thresholds),
        "decisions": decisions,
        "reask_claude_shadow": reask_claude_shadow,
        "suggested_probe_ready_shadow": suggested_probe_ready_shadow,
        "suggested_micro_probe_shadow": suggested_micro_probe_shadow,
        "fade_recovered_shadow": fade_recovered_shadow,
        "probe_ready_shadow": [],
        "micro_probe_shadow": [],
        "watch_shadow": watch_shadow,
        "counts": {
            "watchlist": len(watchlist),
            "reask_claude_shadow": len(reask_claude_shadow),
            "suggested_probe_ready_shadow": len(suggested_probe_ready_shadow),
            "suggested_micro_probe_shadow": len(suggested_micro_probe_shadow),
            "fade_recovered_shadow": len(fade_recovered_shadow),
            "probe_ready_shadow": 0,
            "micro_probe_shadow": 0,
            "watch_shadow": len(watch_shadow),
        },
        "kr_bullish_strength_probe": build_kr_bullish_strength_probe(
            market=market_key,
            selection_meta=meta,
            consensus_mode=consensus_mode,
            market_context=market_context,
        ),
    }


def attach_adaptive_live_condition_shadow(
    *,
    market: str,
    selection_meta: dict[str, Any],
    consensus_mode: str = "",
    market_context: dict[str, Any] | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    meta = dict(selection_meta or {})
    if not enabled:
        meta["_adaptive_live_condition"] = {
            "version": VERSION,
            "market": str(market or "").upper(),
            "enabled": False,
            "shadow_only": True,
            "non_executable": True,
            "execution_owner": "claude",
            "local_promotion_allowed": False,
        }
        return meta
    meta["_adaptive_live_condition"] = build_adaptive_live_condition(
        market=market,
        selection_meta=meta,
        consensus_mode=consensus_mode,
        market_context=market_context,
    )
    return meta
