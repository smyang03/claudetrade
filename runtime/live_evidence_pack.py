from __future__ import annotations

from typing import Any


VERSION = "live_evidence_pack.v1"

CORE_MOMENTUM_FIELDS = ("ret_3m_pct", "ret_5m_pct")
CONFIRMATION_FIELDS = ("opening_range_break", "vwap_distance_pct", "volume_ratio_open")


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


def _boolish(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(value)


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _features_for(meta: dict[str, Any], market: str, ticker: str) -> dict[str, Any]:
    maps = (
        meta.get("_post_open_features_by_ticker"),
        meta.get("post_open_features_by_ticker"),
        meta.get("post_open_features"),
    )
    key = _ticker_key(market, ticker)
    for raw_map in maps:
        if not isinstance(raw_map, dict):
            continue
        raw = raw_map.get(key) or raw_map.get(ticker)
        if isinstance(raw, dict):
            return dict(raw)
        if str(market or "").upper() == "US":
            for raw_key, value in raw_map.items():
                if str(raw_key).upper() == key and isinstance(value, dict):
                    return dict(value)
    return {}


def _lookup_action(actions: list[dict[str, Any]], market: str, ticker: str) -> dict[str, Any]:
    key = _ticker_key(market, ticker)
    for action in actions or []:
        if _ticker_key(market, (action or {}).get("ticker")) == key:
            return dict(action or {})
    return {}


def _lookup_route(routes: list[dict[str, Any]], market: str, ticker: str) -> dict[str, Any]:
    key = _ticker_key(market, ticker)
    for route in routes or []:
        if _ticker_key(market, (route or {}).get("ticker")) == key:
            return dict(route or {})
    return {}


def classify_live_evidence_state(features: dict[str, Any]) -> tuple[str, list[str]]:
    missing: list[str] = []
    current_price = _num(features.get("current_price") or features.get("price"))
    if current_price is None:
        missing.append("current_price")

    if all(_num(features.get(field)) is None for field in CORE_MOMENTUM_FIELDS):
        missing.extend(CORE_MOMENTUM_FIELDS)

    opening_range_break = _boolish(features.get("opening_range_break"))
    vwap_distance = _num(features.get("vwap_distance_pct"))
    volume_ratio = _num(features.get("volume_ratio_open"))
    if opening_range_break is None:
        missing.append("opening_range_break")
    if vwap_distance is None:
        missing.append("vwap_distance_pct")
    if volume_ratio is None:
        missing.append("volume_ratio_open")

    if len(missing) >= 4:
        return "missing", list(dict.fromkeys(missing))
    if missing:
        return "partial", list(dict.fromkeys(missing))
    return "confirmed", []


def build_live_evidence_pack(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any] | None = None,
    features: dict[str, Any] | None = None,
    timing: dict[str, Any] | None = None,
    gate_info: dict[str, Any] | None = None,
    action: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    key = _ticker_key(market_key, ticker)
    candidate = dict(candidate or {})
    features = dict(features or {})
    timing = dict(timing or {})
    gate_info = dict(gate_info or {})
    action = dict(action or {})
    route = dict(route or {})

    current_price = (
        _num(features.get("current_price"))
        or _num(candidate.get("price"))
        or _num(candidate.get("current_price"))
        or _num(action.get("current_price"))
    )
    if current_price is not None and "current_price" not in features:
        features["current_price"] = current_price

    data_state, missing_fields = classify_live_evidence_state(features)
    momentum_state = str(features.get("momentum_state") or "unknown").strip().lower()
    data_quality = str(features.get("data_quality") or "unknown").strip().lower()

    hard_blocks: list[str] = []
    block_reason = str(gate_info.get("block_reason") or route.get("runtime_gate_reason") or route.get("reason") or "")
    if bool(gate_info.get("blocked")) and block_reason:
        hard_blocks.append(block_reason)
    if str(route.get("final_action") or "").upper() == "HARD_BLOCK" and block_reason:
        hard_blocks.append(block_reason)

    positive: list[str] = []
    negative: list[str] = []
    if _num(features.get("ret_3m_pct")) is not None or _num(features.get("ret_5m_pct")) is not None:
        positive.append("fresh_momentum_present")
    if _boolish(features.get("opening_range_break")) is True:
        positive.append("opening_range_break")
    if _num(features.get("vwap_distance_pct")) is not None:
        positive.append("vwap_context_present")
    if _num(features.get("volume_ratio_open")) is not None:
        positive.append("volume_context_present")
    if momentum_state == "fade":
        negative.append("fade")
    if data_state != "confirmed":
        negative.append(f"data_{data_state}")
    if hard_blocks:
        negative.extend(hard_blocks)

    action_ceiling = "BUY_READY"
    if hard_blocks or momentum_state == "fade":
        action_ceiling = "WATCH"
    elif data_state == "missing":
        action_ceiling = "WAIT_CONFIRMATION"
    elif data_state == "partial" or data_quality in {"first_observed", "unknown", "missing"}:
        action_ceiling = "PROBE_READY"

    requested_action = str(action.get("action") or route.get("requested_action") or "").strip().upper()
    route_action = str(route.get("final_action") or "").strip().upper()
    execution_state = "watch_only"
    if route_action == "HARD_BLOCK" or hard_blocks:
        execution_state = "blocked"
    elif route_action in {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT"}:
        execution_state = "routed"
    elif requested_action in {"BUY_READY", "PROBE_READY"}:
        execution_state = "awaiting_route"
    elif data_state != "confirmed":
        execution_state = "data_incomplete"

    snapshot = timing.get("entry_timing_snapshot") if isinstance(timing.get("entry_timing_snapshot"), dict) else {}
    return {
        "version": VERSION,
        "market": market_key,
        "ticker": key,
        "data_state": data_state,
        "data_quality": data_quality,
        "missing_fields": missing_fields,
        "action_ceiling": action_ceiling,
        "positive_evidence": list(dict.fromkeys(positive)),
        "negative_evidence": list(dict.fromkeys(negative)),
        "post_open_confirmation": {
            "ret_3m_pct": _num(features.get("ret_3m_pct")),
            "ret_5m_pct": _num(features.get("ret_5m_pct")),
            "ret_10m_pct": _num(features.get("ret_10m_pct")),
            "ret_30m_pct": _num(features.get("ret_30m_pct")),
            "opening_range_break": _boolish(features.get("opening_range_break")),
            "vwap_distance_pct": _num(features.get("vwap_distance_pct")),
            "volume_ratio_open": _num(features.get("volume_ratio_open")),
            "momentum_state": momentum_state,
        },
        "execution_timing": {
            "first_seen_at": snapshot.get("candidate_detected_at") or timing.get("candidate_detected_at"),
            "candidate_age_min": timing.get("candidate_age_min"),
            "candidate_source": timing.get("candidate_source") or snapshot.get("candidate_source"),
            "current_price": current_price,
            "price_change_since_first_seen_pct": snapshot.get("price_change_candidate_to_order_pct"),
            "candidate_to_order_delay_min": snapshot.get("candidate_to_order_delay_min"),
        },
        "decision_trace": {
            "claude_action": requested_action,
            "route_action": route_action,
            "route": route.get("route") or "",
            "route_reason": route.get("reason") or "",
            "runtime_gate_reason": route.get("runtime_gate_reason") or "",
            "execution_state": execution_state,
            "block_reason": block_reason,
            "execution_owner": "claude",
            "local_promotion_allowed": False,
        },
        "risk_control_view": {
            "hard_blocks": list(dict.fromkeys(hard_blocks)),
            "soft_gates": ["data_incomplete"] if data_state != "confirmed" else [],
            "override_allowed": not bool(hard_blocks),
            "action_ceiling": action_ceiling,
            "system_may_promote": False,
            "reask_claude_required_for_promotion": True,
        },
    }


def summarize_live_evidence(packs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    state_counts: dict[str, int] = {}
    ceiling_counts: dict[str, int] = {}
    missing_counts: dict[str, int] = {}
    for pack in packs.values():
        state = str(pack.get("data_state") or "unknown")
        ceiling = str(pack.get("action_ceiling") or "unknown")
        state_counts[state] = state_counts.get(state, 0) + 1
        ceiling_counts[ceiling] = ceiling_counts.get(ceiling, 0) + 1
        for field in pack.get("missing_fields") or []:
            key = str(field or "")
            if key:
                missing_counts[key] = missing_counts.get(key, 0) + 1
    return {
        "version": VERSION,
        "counts": {
            "total": len(packs),
            "data_state": state_counts,
            "action_ceiling": ceiling_counts,
            "missing_fields": missing_counts,
        },
    }


def attach_live_evidence_summary(
    *,
    market: str,
    selection_meta: dict[str, Any],
    max_items: int = 30,
) -> dict[str, Any]:
    meta = dict(selection_meta or {})
    market_key = str(market or "").upper()
    actions = list(meta.get("candidate_actions") or [])
    routes = list(meta.get("_candidate_action_routes") or [])
    ordered = list(
        dict.fromkeys(
            list(meta.get("trade_ready") or [])
            + [str((item or {}).get("ticker") or "") for item in actions]
            + list(meta.get("watchlist") or [])
        )
    )
    packs: dict[str, dict[str, Any]] = {}
    for ticker in ordered[: max(0, int(max_items or 0)) or len(ordered)]:
        key = _ticker_key(market_key, ticker)
        if not key:
            continue
        features = _features_for(meta, market_key, key)
        action = _lookup_action(actions, market_key, key)
        route = _lookup_route(routes, market_key, key)
        packs[key] = build_live_evidence_pack(
            market=market_key,
            ticker=key,
            features=features,
            action=action,
            route=route,
        )
    summary = summarize_live_evidence(packs)
    summary["packs"] = packs
    summary["shadow_only"] = True
    meta["_live_evidence"] = summary
    return meta
