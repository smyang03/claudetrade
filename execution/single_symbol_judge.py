from __future__ import annotations

import json
import os
import time
from typing import Any

from minority_report.claude_utils import extract_json, claude_response_meta


ALLOWED_ACTIONS = {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT", "WAIT_RECHECK", "REJECT"}
ALLOWED_ROUTES = {"plan_a", "path_b", "wait", "reject"}
PATHB_REQUIRED_FIELDS = ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
PATHB_BAD_DATA_QUALITY = {"minute_missing", "missing", "bad", "stale", "invalid", "fail_closed"}


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return float(default)


def _pct_diff(value: float, reference: float) -> float | None:
    if value <= 0 or reference <= 0:
        return None
    return (float(value) / float(reference) - 1.0) * 100.0


def _pathb_support_levels(features: dict[str, Any] | None) -> dict[str, float]:
    raw = dict(features or {})
    current = _num(raw.get("current_price"), 0.0)
    levels: dict[str, float] = {}
    anchor = _num(raw.get("anchor_price"), 0.0)
    if anchor > 0:
        levels["open_anchor"] = anchor
    vwap_distance = raw.get("vwap_distance_pct")
    if current > 0 and vwap_distance not in (None, ""):
        denom = 1.0 + (_num(vwap_distance, 0.0) / 100.0)
        if denom > 0:
            levels["estimated_vwap"] = current / denom
    pullback = raw.get("pullback_from_high_pct")
    if current > 0 and pullback not in (None, ""):
        denom = 1.0 + (_num(pullback, 0.0) / 100.0)
        if denom > 0:
            levels["estimated_open_high"] = current / denom
    return levels


def _compact(value: Any, *, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def build_single_symbol_judge_prompt(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    features: dict[str, Any] | None = None,
    strategy_feasibility: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> str:
    market_key = _market_key(market)
    ticker_key = _ticker_key(ticker, market_key)
    payload = {
        "market": market_key,
        "ticker": ticker_key,
        "candidate": candidate or {},
        "post_open_features": features or {},
        "strategy_feasibility": strategy_feasibility or {},
        "risk_context": risk_context or {},
    }
    return (
        "You are deciding whether ONE already-screened live trading candidate can receive a PathB waiting price plan now.\n"
        "Return JSON only. Do not include markdown.\n"
        "Allowed action: PULLBACK_WAIT, WAIT_RECHECK, REJECT. Do not use BUY_READY or PROBE_READY.\n"
        "Allowed route: path_b, wait, reject.\n"
        "You may judge setup quality, route, invalidation, and price plan. You must not decide order quantity, order amount, or override broker/risk gates.\n"
        "Use PULLBACK_WAIT only when the buy zone is anchored to structural support/retest evidence such as VWAP, open anchor, opening range breakout retest, or a controlled pullback from high.\n"
        "Pullback entry rule: buy_zone_high must sit at least 0.5% BELOW the current price. A zone that fills immediately at the current price is a chase entry; use WAIT_RECHECK instead.\n"
        "For route=path_b or action=PULLBACK_WAIT, include buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence, invalid_if, structural_basis, zone_basis.\n"
        "If post-open features are missing/stale, the support zone is unclear, reward/risk is weak, or the setup has faded, use WAIT_RECHECK or REJECT.\n"
        "Use market-native prices: KR in KRW, US in USD.\n"
        "If the setup is noisy or needs more evidence, use WAIT_RECHECK with recheck_after_min.\n"
        "If the setup should not be traded today, use REJECT.\n"
        "Input:\n"
        f"{_compact(payload, max_chars=int(os.getenv('SINGLE_SYMBOL_JUDGE_INPUT_MAX_CHARS', '6500')))}\n"
        "JSON schema:\n"
        '{"ticker":"AAPL","market":"US","action":"PULLBACK_WAIT","route":"path_b","confidence":0.72,'
        '"reason":"short reason","invalid_if":"condition","recheck_after_min":5,'
        '"buy_zone_low":198.2,"buy_zone_high":201.0,"sell_target":208.0,"stop_loss":195.0,"hold_days":2,'
        '"structural_basis":"VWAP retest","zone_basis":"near VWAP/open-anchor support",'
        '"chase_above":203.5,"do_not_buy_if":["condition"]}'
    )


def parse_single_symbol_judge_response(raw: str) -> dict[str, Any]:
    return extract_json(str(raw or "").strip())


def _normalize_action(raw_action: Any) -> str:
    action = str(raw_action or "").strip().upper()
    aliases = {
        "BUY": "BUY_READY",
        "PROBE": "PROBE_READY",
        "WAIT": "WAIT_RECHECK",
        "HOLD": "WAIT_RECHECK",
        "AVOID": "REJECT",
        "NO_TRADE": "REJECT",
    }
    action = aliases.get(action, action)
    return action if action in ALLOWED_ACTIONS else "WAIT_RECHECK"


def _normalize_route(raw_route: Any, action: str) -> str:
    route = str(raw_route or "").strip().lower()
    route = {
        "pathb": "path_b",
        "path_b_wait": "path_b",
        "claude_price": "path_b",
        "plan_a_buy": "plan_a",
        "watch": "wait",
    }.get(route, route)
    if route not in ALLOWED_ROUTES:
        if action == "PULLBACK_WAIT":
            return "path_b"
        if action in {"BUY_READY", "PROBE_READY"}:
            return "plan_a"
        if action == "REJECT":
            return "reject"
        return "wait"
    return route


def _pullback_wait_late_mover_block(features: dict[str, Any] | None, result: dict[str, Any]) -> str:
    """late_mover 후보의 PULLBACK_WAIT 승격 차단 (#3 soft-block floor, single_symbol_judge 측).

    repeated_failed_ready는 route 레벨 gate_info 신호라 action_routing에서 처리하고,
    여기서는 features에 존재하는 momentum_state/freshness 기반 late_mover만 막는다.
    운영자 확정 신호(2026-06-10): repeated_failed_ready + late_mover 2종.
    """
    feature_pack = dict(features or {})
    momentum = str(feature_pack.get("momentum_state") or (result or {}).get("momentum_state") or "").strip().lower()
    if momentum == "late_mover":
        return "late_mover"
    freshness = str(feature_pack.get("freshness_verdict") or (result or {}).get("freshness_verdict") or "").strip().upper()
    if freshness in {"STALE", "LATE_CHASE"}:
        return "late_mover"
    return ""


def validate_pathb_price_plan(
    result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> list[str]:
    missing = [key for key in PATHB_REQUIRED_FIELDS if result.get(key) in (None, "")]
    errors = [f"missing_{key}" for key in missing]
    low = _num(result.get("buy_zone_low"), 0.0)
    high = _num(result.get("buy_zone_high"), 0.0)
    target = _num(result.get("sell_target"), 0.0)
    stop = _num(result.get("stop_loss") or result.get("stop_reference"), 0.0)
    hold_days = _num(result.get("hold_days"), 0.0)
    confidence = _num(result.get("confidence"), 0.0)
    feature_pack = dict(features or {})
    current = _num(feature_pack.get("current_price"), 0.0)
    strict_features = bool(features) or bool((risk_context or {}).get("pathb_plan_before_registration_only"))
    min_reward_risk = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MIN_REWARD_RISK", "1.1"), 1.1)
    max_zone_width_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_ZONE_WIDTH_PCT", "2.5"), 2.5)
    max_zone_above_current_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_ZONE_ABOVE_CURRENT_PCT", "0.35"), 0.35)
    max_zone_support_distance_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_SUPPORT_DISTANCE_PCT", "1.8"), 1.8)
    if low <= 0:
        errors.append("buy_zone_low_nonpositive")
    if high <= 0:
        errors.append("buy_zone_high_nonpositive")
    if high and low and high < low:
        errors.append("buy_zone_high_below_low")
    if target <= high:
        errors.append("sell_target_not_above_buy_zone")
    if stop <= 0:
        errors.append("stop_loss_nonpositive")
    elif low and stop >= low:
        errors.append("stop_loss_not_below_buy_zone")
    if hold_days < 1:
        errors.append("hold_days_below_one")
    if not (0.0 < confidence <= 1.0):
        errors.append("confidence_out_of_range")
    if not str(result.get("invalid_if") or "").strip():
        errors.append("missing_invalid_if")

    if strict_features:
        quality = str(feature_pack.get("data_quality") or "").strip().lower()
        if not feature_pack:
            errors.append("missing_post_open_features_for_pathb_plan")
        if current <= 0:
            errors.append("missing_current_price_for_pathb_plan")
        if quality in PATHB_BAD_DATA_QUALITY:
            errors.append("post_open_feature_quality_fail_closed")
        if str(feature_pack.get("momentum_state") or "").strip().lower() == "fade":
            errors.append("post_open_momentum_fade")
        if feature_pack.get("opening_range_break") is False and _num(feature_pack.get("vwap_distance_pct"), -999.0) <= 0:
            errors.append("orb_failed_without_vwap_support")
        if current > 0 and high > 0:
            high_vs_current = _pct_diff(high, current)
            if high_vs_current is not None and high_vs_current > max_zone_above_current_pct:
                errors.append("buy_zone_above_current_chase_risk")
            if high_vs_current is not None and high_vs_current < -6.0:
                errors.append("buy_zone_too_far_below_current")
        if current > 0 and low > 0 and high > 0:
            zone_width_pct = ((high - low) / current) * 100.0
            if zone_width_pct > max_zone_width_pct:
                errors.append("buy_zone_too_wide")
        if high > 0 and target > high and stop > 0 and stop < high:
            reward_pct = (target / high - 1.0) * 100.0
            risk_pct = (high / stop - 1.0) * 100.0
            reward_risk = reward_pct / risk_pct if risk_pct > 0 else 0.0
            if reward_risk < min_reward_risk:
                errors.append("reward_risk_below_min")
        if low > 0 and high > 0 and current > 0:
            levels = _pathb_support_levels(feature_pack)
            center = (low + high) / 2.0
            if not levels:
                errors.append("missing_structural_support_level")
            else:
                distances = [
                    abs((center / level - 1.0) * 100.0)
                    for level in levels.values()
                    if level > 0
                ]
                if distances and min(distances) > max_zone_support_distance_pct:
                    errors.append("buy_zone_not_near_structural_support")
        if not str(result.get("structural_basis") or result.get("zone_basis") or "").strip():
            errors.append("missing_structural_basis")
    return list(dict.fromkeys(errors))


def normalize_single_symbol_judge_result(
    result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(result or {})
    market = _market_key(str(raw.get("market") or ""))
    ticker = _ticker_key(raw.get("ticker"), market)
    action = _normalize_action(raw.get("action"))
    route = _normalize_route(raw.get("route"), action)
    if route == "path_b" and action in {"BUY_READY", "PROBE_READY"}:
        action = "PULLBACK_WAIT"
    if action == "PULLBACK_WAIT":
        route = "path_b"
    if action == "WAIT_RECHECK":
        route = "wait"
    if action == "REJECT":
        route = "reject"

    pullback_soft_block_reason = ""
    pullback_soft_block_enforced = False
    if action == "PULLBACK_WAIT":
        pullback_soft_block_reason = _pullback_wait_late_mover_block(features, raw)
        if pullback_soft_block_reason:
            gate_mode = str(os.getenv("PULLBACK_WAIT_SOFT_BLOCK_GATE_MODE", "enforce") or "enforce").strip().lower()
            pullback_soft_block_enforced = gate_mode in {"live", "enforce", "block"}
            if pullback_soft_block_enforced:
                # late_mover는 per-cycle transient 분류이므로 REJECT(장기 TTL 락아웃)가 아니라
                # WAIT_RECHECK로 보류한다. action_routing의 WATCH 강등과 동일하게 다음 사이클
                # 재평가를 허용하고, registration만 막는다.
                action = "WAIT_RECHECK"
                route = "wait"

    raw_valid = raw.get("valid")
    errors = list(raw.get("errors") or []) if isinstance(raw.get("errors"), list) else []
    out = {
        **raw,
        "ticker": ticker,
        "market": market,
        "action": action,
        "route": route,
        "confidence": max(0.0, min(1.0, _num(raw.get("confidence"), 0.0))),
        "reason": str(raw.get("reason") or "").strip(),
        "invalid_if": str(raw.get("invalid_if") or raw.get("invalidation_condition") or "").strip(),
        "recheck_after_min": max(0.0, _num(raw.get("recheck_after_min"), 0.0)),
        "valid": bool(raw_valid) if raw_valid is not None else True,
        "errors": errors,
    }
    if raw.get("audit_reason") not in (None, ""):
        out["audit_reason"] = str(raw.get("audit_reason") or "")
    if raw.get("stop_loss") in (None, "") and raw.get("stop_reference") not in (None, ""):
        out["stop_loss"] = raw.get("stop_reference")
    if route == "path_b":
        errors = validate_pathb_price_plan(out, features=features, risk_context=risk_context)
        if errors:
            out["valid"] = False
            out["errors"] = errors
            out["action"] = "WAIT_RECHECK"
            out["route"] = "wait"
            out["reason"] = out["reason"] or "invalid_pathb_price_plan"
            out["audit_reason"] = "early_judge_pathb_price_plan_missing" if any(
                error.startswith("missing_") for error in errors
            ) else "early_judge_pathb_price_plan_invalid"
    if pullback_soft_block_reason:
        out["pullback_wait_soft_block_reason"] = pullback_soft_block_reason
        out["pullback_wait_soft_block_enforced"] = pullback_soft_block_enforced
        if pullback_soft_block_enforced:
            out["audit_reason"] = f"pullback_wait_soft_block:{pullback_soft_block_reason}"
            if not out["reason"]:
                out["reason"] = f"pullback_wait_soft_block:{pullback_soft_block_reason}"
    return out


def call_single_symbol_judge(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    features: dict[str, Any] | None = None,
    strategy_feasibility: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import anthropic
    from credit_tracker import record as credit_record
    from minority_report.raw_call_logger import save as save_raw_call

    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens = int(float(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_TOKENS", "900") or 900))
    prompt = build_single_symbol_judge_prompt(
        market=market,
        ticker=ticker,
        candidate=candidate,
        features=features or {},
        strategy_feasibility=strategy_feasibility or {},
        risk_context=risk_context or {},
    )
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    started = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    raw = resp.content[0].text.strip()
    parse_error = False
    try:
        parsed = parse_single_symbol_judge_response(raw)
        normalized = normalize_single_symbol_judge_result(parsed, features=features or {}, risk_context=risk_context or {})
    except Exception as exc:
        parse_error = True
        normalized = {
            "ticker": _ticker_key(ticker, market),
            "market": _market_key(market),
            "action": "WAIT_RECHECK",
            "route": "wait",
            "valid": False,
            "errors": [f"parse_error:{type(exc).__name__}"],
            "reason": "single_symbol_judge_parse_failed",
        }
    try:
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "single_symbol_judge", model=model,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
    except Exception:
        pass
    try:
        _cm = claude_response_meta(resp)
        save_raw_call(
            label="single_symbol_judge",
            prompt=prompt,
            raw_response=raw,
            parsed=normalized,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            duration_ms=duration_ms,
            market=_market_key(market),
            model=model,
            parse_error=parse_error,
            parse_stage="single_symbol_judge_v1",
            prompt_version="single_symbol_judge_v1",
            cache_creation_input_tokens=_cm["cache_creation_input_tokens"],
            cache_read_input_tokens=_cm["cache_read_input_tokens"],
            request_id=_cm["request_id"],
            service_tier=_cm["service_tier"],
            extra={"ticker": _ticker_key(ticker, market)},
        )
    except Exception:
        pass
    return normalized
