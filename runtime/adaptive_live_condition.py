from __future__ import annotations

from typing import Any


VERSION = "adaptive_live_condition.v2"


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
    watch_shadow: list[str] = []

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
            and (pullback is None or pullback >= -3.0)
            and (
                (r3 is not None and r3 >= 0.8 and (r5 is None or r5 >= 0.0))
                or (r10 is not None and r10 >= 3.0)
            )
        )
        late_micro_ok = (
            regime == "risk_on"
            and momentum_state == "late_mover"
            and opening_break
            and (pullback is None or pullback >= -2.5)
            and (from_high is None or from_high < 30.0)
            and (r30 is not None and r30 >= 8.0)
        )
        mixed_probe_ok = (
            regime == "mixed"
            and momentum_state not in {"fade", "late_mover"}
            and opening_break
            and (pullback is None or pullback >= -2.0)
            and r3 is not None and r3 >= 1.2
            and r5 is not None and r5 >= 0.2
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
            "claude_reask": claude_reask,
            "reask_reason": "live_evidence_changed" if claude_reask else "",
            "non_executable": True,
            "execution_owner": "claude",
            "local_promotion_allowed": False,
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
        "decisions": decisions,
        "reask_claude_shadow": reask_claude_shadow,
        "suggested_probe_ready_shadow": suggested_probe_ready_shadow,
        "suggested_micro_probe_shadow": suggested_micro_probe_shadow,
        "probe_ready_shadow": [],
        "micro_probe_shadow": [],
        "watch_shadow": watch_shadow,
        "counts": {
            "watchlist": len(watchlist),
            "reask_claude_shadow": len(reask_claude_shadow),
            "suggested_probe_ready_shadow": len(suggested_probe_ready_shadow),
            "suggested_micro_probe_shadow": len(suggested_micro_probe_shadow),
            "probe_ready_shadow": 0,
            "micro_probe_shadow": 0,
            "watch_shadow": len(watch_shadow),
        },
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
