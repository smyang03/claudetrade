from __future__ import annotations

import math
import os
from typing import Any


COMPACT_SCHEMA_VERSION = "selection_compact.v1"
COMPACT_ACTIONS = {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT", "WATCH", "AVOID"}
ACTIONABLE_ACTIONS = {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT"}
READY_ACTIONS = {"BUY_READY", "PROBE_READY"}
PRICE_TARGET_KEY_MAP = {
    "ref": "reference_price",
    "lo": "buy_zone_low",
    "hi": "buy_zone_high",
    "tgt": "sell_target",
    "stp": "stop_loss",
    "days": "hold_days",
    "d": "hold_days",
    "conf": "confidence",
    "cf": "confidence",
}
REQUIRED_PRICE_TARGET_KEYS = ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
ALLOWED_TOP_KEYS = {"wl", "tr", "ca"}
ALLOWED_ACTION_KEYS = {"t", "a", "s", "c", "fr", "mat", "ceil", "rc", "blk", "inv", "pt"}
ALLOWED_PT_KEYS = {"ref", "lo", "hi", "tgt", "stp", "days", "conf", "d", "cf"}


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or str(value).strip() == "":
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(float(os.getenv(name, str(default))))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def compact_schema_enabled(default: bool = False) -> bool:
    return _env_bool("CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED", default)


def compact_limits(*, watch_default: int, trade_default: int) -> tuple[int, int]:
    watch_max = _env_int("CLAUDE_SELECTION_COMPACT_WATCH_MAX", min(15, watch_default), 1, max(1, watch_default))
    trade_max = _env_int("CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX", min(5, trade_default), 0, max(0, trade_default))
    return watch_max, trade_max


def is_compact_selection_response(parsed: Any) -> bool:
    return isinstance(parsed, dict) and any(key in parsed for key in ("wl", "tr", "ca"))


def normalize_ticker(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _positive_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "").replace("$", ""))
        if not math.isfinite(parsed) or parsed <= 0:
            return None
        return parsed
    except Exception:
        return None


def _bounded_confidence(value: Any) -> float:
    parsed = _positive_float(value)
    if parsed is None:
        return 0.0
    return max(0.0, min(1.0, float(parsed)))


def reference_prices_from_candidates(candidates: list[dict[str, Any]], market: str) -> dict[str, float]:
    out: dict[str, float] = {}
    price_keys = (
        "p",
        "price",
        "current_price",
        "last_price",
        "close",
        "last",
        "native_price",
        "현재가",
    )
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        ticker = normalize_ticker(candidate.get("ticker") or candidate.get("code"), market)
        if not ticker:
            continue
        for key in price_keys:
            value = _positive_float(candidate.get(key))
            if value is not None:
                out[ticker] = value
                break
    return out


def compact_output_contract(*, watch_max: int, trade_max: int) -> str:
    return f"""MACHINE-COMPACT OUTPUT CONTRACT.
Return strict JSON only. No markdown.
Use keys only: wl,tr,ca.
wl=max {int(watch_max)} tickers. tr=max {int(trade_max)} tickers.
ca exactly one item per wl ticker, same order.
ca item keys only: t,a,s,c,fr,mat,ceil,rc,blk,inv,pt.
Field meanings:
t=ticker, a=action, s=strategy, c=numeric confidence 0.0-1.0.
fr=freshness label, mat=setup maturity label, ceil=max action allowed by evidence.
rc=short reason code, blk=array of blockers, inv=short invalidation condition.
Allowed actions: BUY_READY, PROBE_READY, PULLBACK_WAIT, WATCH, AVOID.
pt only for BUY_READY/PROBE_READY/PULLBACK_WAIT.
WATCH/AVOID omit pt.
pt keys only: ref,lo,hi,tgt,stp,days,conf.
pt.days must be numeric hold_days, not direction text.
pt.conf must be numeric confidence 0.0-1.0, not a reason or confirmation phrase.
Legacy d/cf are parser-compatible aliases only; do not output d/cf.
ref must equal supplied p= price when available.
tr may contain only BUY_READY or PROBE_READY tickers.
Do not include reasons, veto, risk_tags, allocation, budgets, sizing, long strings, candidate_actions, price_targets, recommended_strategy, or extra keys.
JSON shape: {{"wl":["T"],"tr":["T"],"ca":[{{"t":"T","a":"WATCH","s":"momentum","c":0.0,"fr":"UNKNOWN","mat":"WEAK","ceil":"WATCH","rc":"WATCH_ONLY","blk":[],"inv":"setup_invalid"}}]}}"""


def _valid_order(candidates: list[dict[str, Any]], market: str) -> list[str]:
    values = [
        normalize_ticker(candidate.get("ticker") or candidate.get("code"), market)
        for candidate in candidates or []
        if isinstance(candidate, dict) and (candidate.get("ticker") or candidate.get("code"))
    ]
    return list(dict.fromkeys([value for value in values if value]))


def _valid_list(values: Any, valid_order: list[str], market: str, max_items: int) -> list[str]:
    if not isinstance(values, list):
        return []
    valid = set(valid_order)
    out: list[str] = []
    for value in values:
        ticker = normalize_ticker(value, market)
        if ticker in valid and ticker not in out:
            out.append(ticker)
        if len(out) >= max_items:
            break
    return out


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(str(value).replace(",", "").replace("$", ""))
        if not math.isfinite(parsed):
            return None
        return parsed
    except Exception:
        return None


def _to_int(value: Any) -> int | None:
    parsed = _to_float(value)
    if parsed is None:
        return None
    return int(parsed)


def _compact_price_targets(
    raw: Any,
    reference_price: float | None,
    warnings: list[str],
    *,
    action_confidence: float | None = None,
    default_hold_days: int = 1,
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    target: dict[str, Any] = {}
    extra_keys = sorted(str(key) for key in raw.keys() if str(key) not in ALLOWED_PT_KEYS)
    if extra_keys:
        warnings.append("price_target_extra_keys")
    for short_key, full_key in PRICE_TARGET_KEY_MAP.items():
        if short_key not in raw:
            continue
        if full_key in target:
            continue
        if full_key == "hold_days":
            parsed_int = _to_int(raw.get(short_key))
            if parsed_int is not None and parsed_int > 0:
                target[full_key] = parsed_int
            else:
                warnings.append("price_target_hold_days_non_numeric")
        elif full_key == "confidence":
            parsed = _to_float(raw.get(short_key))
            if parsed is not None:
                target[full_key] = parsed
            else:
                warnings.append("price_target_confidence_non_numeric")
        else:
            parsed = _to_float(raw.get(short_key))
            if parsed is not None:
                target[full_key] = parsed
    if reference_price is not None:
        raw_ref = _to_float(target.get("reference_price"))
        if raw_ref is None:
            target["reference_price"] = float(reference_price)
            warnings.append("reference_price_filled_from_input")
        elif abs(raw_ref - float(reference_price)) > 0.01:
            target["reference_price"] = float(reference_price)
            warnings.append("reference_price_corrected_to_input")
    if "confidence" in target:
        target["confidence"] = max(0.0, min(1.0, float(target["confidence"])))
    elif action_confidence is not None:
        target["confidence"] = max(0.0, min(1.0, float(action_confidence)))
        warnings.append("price_target_confidence_filled_from_action")
    buy_low = _to_float(target.get("buy_zone_low"))
    buy_high = _to_float(target.get("buy_zone_high"))
    sell_target = _to_float(target.get("sell_target"))
    stop_loss = _to_float(target.get("stop_loss"))
    reference = _to_float(target.get("reference_price"))
    if (
        "hold_days" not in target
        and any(value is not None for value in (buy_low, buy_high, sell_target, stop_loss, reference))
        and default_hold_days > 0
    ):
        target["hold_days"] = int(default_hold_days)
        warnings.append("price_target_hold_days_filled_default")
    if buy_low and stop_loss and buy_low > stop_loss:
        risk = buy_low - stop_loss
        target.setdefault("risk_pct", round((risk / buy_low) * 100.0, 4))
    else:
        risk = None
    if sell_target and buy_high and sell_target > buy_high:
        reward = sell_target - buy_high
        target.setdefault("reward_pct", round((reward / buy_high) * 100.0, 4))
    else:
        reward = None
    if risk and reward:
        target.setdefault("reward_risk", round(reward / risk, 4))
    if reference and "buy_zone_low" not in target:
        target["buy_zone_low"] = reference
    if reference and "buy_zone_high" not in target:
        target["buy_zone_high"] = reference
    return target


def _list_str(value: Any, limit: int = 4) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value[:limit] if str(item).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


def canonicalize_compact_selection(
    parsed: dict[str, Any],
    candidates: list[dict[str, Any]],
    market: str,
    *,
    reference_prices: dict[str, float] | None = None,
    stop_reason: str = "",
    source_prompt_id: str = "",
    watch_max: int | None = None,
    trade_max: int | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    valid_order = _valid_order(candidates, market_key)
    default_watch = len(valid_order) if valid_order else 15
    default_trade = min(5, default_watch)
    env_watch, env_trade = compact_limits(watch_default=default_watch, trade_default=default_trade)
    watch_cap = int(watch_max if watch_max is not None else env_watch)
    trade_cap = int(trade_max if trade_max is not None else env_trade)
    refs = {normalize_ticker(key, market_key): float(value) for key, value in (reference_prices or {}).items() if _positive_float(value)}
    warnings: list[str] = []
    errors: list[str] = []
    extra_top_keys = sorted(str(key) for key in (parsed or {}).keys() if key not in ALLOWED_TOP_KEYS and not str(key).startswith("_"))
    if extra_top_keys:
        warnings.append("compact_extra_top_keys")
    watchlist = _valid_list((parsed or {}).get("wl"), valid_order, market_key, watch_cap)
    raw_trade = _valid_list((parsed or {}).get("tr"), valid_order, market_key, trade_cap)
    raw_actions = (parsed or {}).get("ca")
    if not isinstance(raw_actions, list):
        raw_actions = []
        errors.append("candidate_actions_missing")
    elif not raw_actions:
        errors.append("candidate_actions_empty")
    if not watchlist and valid_order and not errors:
        watchlist = valid_order[:watch_cap]
        warnings.append("watchlist_filled_from_candidates")
    watch_set = set(watchlist)
    actions_by_ticker: dict[str, dict[str, Any]] = {}
    off_list: list[str] = []
    extra_item_keys: list[str] = []
    for raw_item in raw_actions:
        if not isinstance(raw_item, dict):
            warnings.append("compact_action_not_object")
            continue
        extra_item_keys.extend(sorted(str(key) for key in raw_item.keys() if key not in ALLOWED_ACTION_KEYS))
        ticker = normalize_ticker(raw_item.get("t"), market_key)
        if not ticker:
            warnings.append("compact_action_missing_ticker")
            continue
        if ticker not in watch_set:
            off_list.append(ticker)
            continue
        if ticker in actions_by_ticker:
            warnings.append(f"compact_duplicate_action:{ticker}")
            continue
        actions_by_ticker[ticker] = dict(raw_item)
    if extra_item_keys:
        warnings.append("compact_extra_item_keys")
    if off_list:
        errors.append("off_list_candidate_actions")
    missing_actions = [ticker for ticker in watchlist if ticker not in actions_by_ticker]
    if missing_actions:
        errors.append("candidate_actions_coverage_incomplete")
    canonical_actions: list[dict[str, Any]] = []
    recommended_strategy: dict[str, str] = {}
    price_targets: dict[str, dict[str, Any]] = {}
    reasons: dict[str, str] = {}
    action_by_ticker: dict[str, str] = {}
    fatal_contract = bool(errors or stop_reason == "max_tokens")
    if stop_reason == "max_tokens":
        errors.append("stop_reason_max_tokens")

    for ticker in watchlist:
        raw_item = actions_by_ticker.get(ticker) or {"t": ticker, "a": "WATCH", "s": "", "c": 0.0, "rc": "MISSING_ACTION"}
        item_warnings: list[str] = []
        action = str(raw_item.get("a") or "WATCH").strip().upper()
        if action not in COMPACT_ACTIONS:
            item_warnings.append(f"invalid_action:{action or 'EMPTY'}")
            action = "WATCH"
        strategy = str(raw_item.get("s") or "").strip()
        if not strategy:
            item_warnings.append("missing_strategy")
            if action in ACTIONABLE_ACTIONS:
                action = "WATCH"
        reason_code = str(raw_item.get("rc") or action or "WATCH").strip()[:80]
        if reason_code:
            reasons[ticker] = reason_code
        confidence = _bounded_confidence(raw_item.get("c"))
        reference_price = refs.get(ticker)
        target = _compact_price_targets(
            raw_item.get("pt"),
            reference_price,
            item_warnings,
            action_confidence=confidence,
        )
        if action in ACTIONABLE_ACTIONS:
            missing_target_keys = [key for key in REQUIRED_PRICE_TARGET_KEYS if key not in target]
            if missing_target_keys:
                item_warnings.append("missing_price_targets:" + ",".join(missing_target_keys))
                action = "WATCH"
                target = {}
        elif target:
            item_warnings.append("non_actionable_price_targets_ignored")
            target = {}
        if fatal_contract and action in ACTIONABLE_ACTIONS:
            item_warnings.append("compact_contract_failed_demoted")
            action = "WATCH"
            target = {}
        freshness = str(raw_item.get("fr") or "UNKNOWN").strip().upper() or "UNKNOWN"
        maturity = str(raw_item.get("mat") or "WEAK").strip().upper() or "WEAK"
        ceiling = str(raw_item.get("ceil") or action).strip().upper() or action
        blocking_factors = _list_str(raw_item.get("blk"), 4)
        if action in READY_ACTIONS and confidence <= 0:
            item_warnings.append("ready_confidence_missing_demoted")
            action = "WATCH"
            target = {}
        if action in READY_ACTIONS and ceiling not in READY_ACTIONS:
            item_warnings.append("ready_exceeds_action_ceiling_demoted")
            action = "WATCH"
            target = {}
        if action in READY_ACTIONS and blocking_factors:
            item_warnings.append("ready_with_blockers_demoted")
            action = "WATCH"
            target = {}
        size_intent = "normal" if action == "BUY_READY" else ("probe" if action == "PROBE_READY" else "none")
        why_not_watch = ""
        if action in READY_ACTIONS:
            why_not_watch = ":".join(part for part in (reason_code, freshness, maturity) if part)
            if not why_not_watch:
                item_warnings.append("why_not_watch_synthesis_failed")
                action = "WATCH"
                target = {}
                size_intent = "none"
        if strategy:
            recommended_strategy[ticker] = strategy
        if target and action in ACTIONABLE_ACTIONS:
            price_targets[ticker] = dict(target)
        action_by_ticker[ticker] = action
        canonical_actions.append(
            {
                "ticker": ticker,
                "market": market_key,
                "schema_version": "candidate_actions.v2",
                "action": action,
                "confidence": confidence,
                "size_intent": size_intent,
                "strategy": strategy,
                "reason": reason_code or action,
                "reason_code": reason_code,
                "freshness_verdict": freshness,
                "setup_maturity": maturity,
                "why_not_watch": why_not_watch,
                "action_ceiling_ack": ceiling,
                "blocking_factors": blocking_factors,
                "soft_gate_overrides": [],
                "required_confirmations": [],
                "entry_type": strategy or action.lower(),
                "invalidation_condition": str(raw_item.get("inv") or reason_code or "setup_invalid")[:120],
                "price_targets": dict(target),
                "source_prompt_id": source_prompt_id,
                "contract_warnings": item_warnings,
                "warnings": item_warnings,
            }
        )
        warnings.extend(f"{ticker}:{warning}" for warning in item_warnings)

    ready_from_actions = [ticker for ticker in watchlist if action_by_ticker.get(ticker) in READY_ACTIONS]
    if raw_trade:
        trade_ready = [ticker for ticker in raw_trade if ticker in ready_from_actions][:trade_cap]
        removed = [ticker for ticker in raw_trade if ticker not in ready_from_actions]
        if removed:
            warnings.append("trade_ready_non_ready_actions_removed")
    else:
        trade_ready = ready_from_actions[:trade_cap]
        if ready_from_actions:
            warnings.append("trade_ready_filled_from_actions")
    if fatal_contract:
        trade_ready = []
    missing_price_targets = [ticker for ticker in trade_ready if ticker not in price_targets]
    coverage = {
        "trade_ready_count": len(trade_ready),
        "price_target_count": len(price_targets),
        "missing": missing_price_targets,
        "ratio": round((len(price_targets) / len(trade_ready)) if trade_ready else 1.0, 4),
    }
    validation = {
        "schema": COMPACT_SCHEMA_VERSION,
        "ok": not bool(errors),
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "watchlist_count": len(watchlist),
        "trade_ready_count": len(trade_ready),
        "candidate_actions_count": len(canonical_actions),
        "missing_action_tickers": missing_actions,
        "off_list_action_tickers": off_list,
        "extra_top_keys": extra_top_keys,
        "extra_item_keys": sorted(set(extra_item_keys)),
        "stop_reason": str(stop_reason or ""),
    }
    return {
        "watchlist": watchlist,
        "trade_ready": trade_ready,
        "reasons": reasons,
        "veto": {},
        "risk_tags": {},
        "recommended_strategy": recommended_strategy,
        "max_position_pct": {},
        "allocation_intent": {},
        "max_order_cap_pct": {},
        "risk_budget_pct": {},
        "size_reason": {},
        "price_targets": price_targets,
        "candidate_actions": canonical_actions,
        "_price_target_coverage": coverage,
        "_parse_recovered": False,
        "_fallback_mode": "selection_truncated" if stop_reason == "max_tokens" else str((parsed or {}).get("_fallback_mode", "") or ""),
        "_candidate_actions_v2_requested": True,
        "_legacy_auto_ready_promoted": False,
        "_selection_schema_version": COMPACT_SCHEMA_VERSION,
        "_selection_raw_schema": "compact",
        "_selection_prompt_contract": COMPACT_SCHEMA_VERSION,
        "_selection_stop_reason": str(stop_reason or ""),
        "_selection_reference_prices": refs,
        "_candidate_actions_source": "compact_candidate_actions_v1",
        "_candidate_actions_present": isinstance((parsed or {}).get("ca"), list),
        "_candidate_actions_empty": isinstance((parsed or {}).get("ca"), list) and not bool((parsed or {}).get("ca")),
        "_candidate_actions_missing_contract": bool(errors),
        "_compact_validation": validation,
    }
