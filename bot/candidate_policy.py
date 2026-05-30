"""Candidate filtering and selection policy helpers.

The trading loop keeps a wide watch universe, but only a narrower
TRADE_READY subset is allowed to place orders.
"""

from __future__ import annotations

import os
from typing import Iterable

from runtime.selection_compact_schema import (
    canonicalize_compact_selection,
    is_compact_selection_response,
    reference_prices_from_candidates,
)


KR_DEFAULT_EXCLUDED_TICKERS = {
    # Derivative/ETF products observed in live failures or repeated selection.
    "114800",  # KODEX inverse
    "252670",  # KODEX 200 futures inverse 2X
    "252710",
    "412570",
    "462330",
}

KR_DERIVATIVE_KEYWORDS = (
    "인버스",
    "레버리지",
    "선물",
    "2X",
    "곱버스",
    "ETN",
)

KR_ETF_BRAND_KEYWORDS = (
    "KODEX",
    "TIGER",
    "KBSTAR",
    "ACE",
    "SOL ",
    "RISE",
    "HANARO",
    "ARIRANG",
    "KOSEF",
)

US_DEFAULT_EXCLUDED_TICKERS = {
    # Leveraged / inverse / volatility products that should never enter the stock candidate loop.
    "SQQQ",
    "TQQQ",
    "SOXL",
    "SOXS",
    "SPXL",
    "SPXS",
    "TNA",
    "TZA",
    "SPDN",
    "SPXU",
    "SH",
    "PSQ",
    "SDS",
    "QID",
    "UVXY",
    "SVIX",
    "NVDL",
    "NVDQ",
    "TSLL",
    "TSLQ",
    "LABU",
    "LABD",
    "TECL",
    "TECS",
    "UDOW",
    "SDOW",
    "FNGU",
    "FNGD",
}

US_PRODUCT_KEYWORDS = (
    " ETF",
    " ETN",
    "PROSHARES",
    "DIREXION",
    "ISHARES",
    "SPDR",
    "VANGUARD",
    "INVESCO",
    "GRANITESHARES",
    "ULTRAPRO",
    "ULTRA ",
    "INVERSE",
    "LEVERAGED",
    " 2X",
    " 3X",
    " BULL ",
    " BEAR ",
)


def _env_set(name: str, default: Iterable[str] = ()) -> set[str]:
    raw = os.getenv(name, "")
    values = [x.strip().upper() for x in raw.split(",") if x.strip()]
    return set(values) if values else {str(x).upper() for x in default}


def selection_limits(market: str) -> dict[str, int]:
    market = market.upper()
    if market == "US":
        return {
            "watch_max": int(os.getenv("US_WATCHLIST_MAX", "30")),
            "trade_max": int(os.getenv("US_TRADE_READY_MAX", "15")),
        }
    return {
        "watch_max": int(os.getenv("KR_WATCHLIST_MAX", "30")),
        "trade_max": int(os.getenv("KR_TRADE_READY_MAX", "15")),
    }


def normalize_ticker(ticker: str, market: str) -> str:
    ticker = str(ticker or "").strip()
    return ticker.upper() if market.upper() == "US" else ticker


def product_block_reason(candidate: dict, market: str) -> str:
    market = market.upper()
    ticker = normalize_ticker(candidate.get("ticker", ""), market)
    name = str(candidate.get("name", "") or "")
    upper_name = name.upper()

    if market == "KR":
        excluded = _env_set("KR_UNTRADABLE_TICKERS", KR_DEFAULT_EXCLUDED_TICKERS)
        if ticker in excluded:
            return "kr_untradable_product"
        if any(k in name for k in KR_DERIVATIVE_KEYWORDS) or any(k in upper_name for k in KR_DERIVATIVE_KEYWORDS):
            return "kr_derivative_etf"
        block_all_etf = os.getenv("KR_BLOCK_ALL_ETF_PRODUCTS", "false").lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        if block_all_etf and any(k in upper_name for k in KR_ETF_BRAND_KEYWORDS):
            return "kr_etf_blocked"
    elif market == "US":
        excluded = _env_set("US_UNTRADABLE_TICKERS", US_DEFAULT_EXCLUDED_TICKERS)
        if ticker in excluded:
            return "us_untradable_product"
        if any(keyword in upper_name for keyword in US_PRODUCT_KEYWORDS):
            return "us_structured_product"

    return ""


def filter_tradable_candidates(candidates: list[dict], market: str) -> tuple[list[dict], list[dict]]:
    filtered: list[dict] = []
    removed: list[dict] = []
    for candidate in candidates or []:
        reason = product_block_reason(candidate, market)
        if reason:
            blocked = dict(candidate)
            blocked["blocked_reason"] = reason
            removed.append(blocked)
            continue
        filtered.append(candidate)
    return filtered, removed


def _valid_list(values, valid_order: list[str], market: str, max_items: int | None = None) -> list[str]:
    if not isinstance(values, list):
        return []
    valid = set(valid_order)
    out: list[str] = []
    for value in values:
        ticker = normalize_ticker(value, market)
        if ticker in valid and ticker not in out:
            out.append(ticker)
        if max_items is not None and len(out) >= max_items:
            break
    return out


def _normalized_dict(values, market: str) -> dict:
    if not isinstance(values, dict):
        return {}
    return {normalize_ticker(k, market): v for k, v in values.items()}


def _candidate_actions_v2_requested(parsed: dict) -> bool:
    raw_env = os.getenv("CANDIDATE_ACTIONS_V2_ENABLED", "")
    if raw_env.strip().lower() in {"1", "true", "yes", "y", "on"}:
        return True
    if is_compact_selection_response(parsed):
        return True
    actions = parsed.get("candidate_actions") if isinstance(parsed, dict) else None
    if not isinstance(actions, list):
        return False
    return any(
        isinstance(item, dict)
        and str(item.get("schema_version") or "").strip() == "candidate_actions.v2"
        for item in actions
    )


def normalize_selection_result(
    parsed: dict,
    candidates: list[dict],
    market: str,
    *,
    stop_reason: str = "",
    reference_prices: dict[str, float] | None = None,
    source_prompt_id: str = "",
    allow_legacy_auto_ready: bool = False,
) -> dict:
    """Normalize Claude output into WATCH and TRADE_READY lists.

    Backward compatible with the legacy {"tickers": [...]} response.
    If Claude explicitly returns "trade_ready": [], order permission remains empty.
    """
    parsed = parsed or {}
    market = market.upper()
    limits = selection_limits(market)
    if is_compact_selection_response(parsed):
        compact_watch_max_raw = os.getenv("CLAUDE_SELECTION_COMPACT_WATCH_MAX", "")
        compact_trade_max_raw = os.getenv("CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX", "")
        try:
            compact_watch_max = min(limits["watch_max"], int(float(compact_watch_max_raw))) if compact_watch_max_raw else min(limits["watch_max"], 15)
        except Exception:
            compact_watch_max = min(limits["watch_max"], 15)
        try:
            compact_trade_max = min(limits["trade_max"], int(float(compact_trade_max_raw))) if compact_trade_max_raw else min(limits["trade_max"], 5)
        except Exception:
            compact_trade_max = min(limits["trade_max"], 5)
        return canonicalize_compact_selection(
            parsed,
            candidates or [],
            market,
            reference_prices=reference_prices or reference_prices_from_candidates(candidates or [], market),
            stop_reason=stop_reason,
            source_prompt_id=source_prompt_id,
            watch_max=compact_watch_max,
            trade_max=compact_trade_max,
        )
    valid_order = [
        normalize_ticker(c.get("ticker", ""), market)
        for c in candidates or []
        if c.get("ticker")
    ]
    valid_order = list(dict.fromkeys(valid_order))

    parse_recovered = bool(parsed.get("_parse_recovered"))
    v2_requested = _candidate_actions_v2_requested(parsed)
    legacy_tickers = _valid_list(parsed.get("tickers"), valid_order, market, limits["watch_max"])
    watchlist = _valid_list(parsed.get("watchlist"), valid_order, market, limits["watch_max"])
    if not watchlist:
        if legacy_tickers:
            watchlist = legacy_tickers
        elif not parse_recovered:
            watchlist = valid_order[: limits["watch_max"]]
        else:
            watchlist = []

    legacy_auto_ready_promoted = False
    if parse_recovered:
        trade_ready = []
    elif "trade_ready" in parsed:
        trade_ready = _valid_list(parsed.get("trade_ready"), valid_order, market, limits["trade_max"])
    elif v2_requested:
        trade_ready = []
    elif allow_legacy_auto_ready:
        # Legacy output had one list only; preserve order but cap order permission.
        trade_ready = watchlist[: limits["trade_max"]]
        legacy_auto_ready_promoted = bool(trade_ready)
    else:
        trade_ready = []

    if not watchlist and trade_ready:
        watchlist = list(trade_ready)
    if not watchlist and not parse_recovered:
        watchlist = valid_order[: limits["watch_max"]]

    # TRADE_READY must always be visible in WATCH for monitoring.
    watchlist = list(dict.fromkeys(trade_ready + watchlist))[: limits["watch_max"]]
    trade_ready = [ticker for ticker in trade_ready if ticker in watchlist]

    reasons = parsed.get("reasons", {}) if isinstance(parsed.get("reasons"), dict) else {}
    veto = parsed.get("veto", {}) if isinstance(parsed.get("veto"), dict) else {}
    risk_tags = parsed.get("risk_tags", {}) if isinstance(parsed.get("risk_tags"), dict) else {}
    recommended_strategy = (
        parsed.get("recommended_strategy", {})
        if isinstance(parsed.get("recommended_strategy"), dict)
        else {}
    )
    max_position_pct = (
        parsed.get("max_position_pct", {})
        if isinstance(parsed.get("max_position_pct"), dict)
        else {}
    )
    max_order_cap_pct = _normalized_dict(parsed.get("max_order_cap_pct"), market)
    allocation_intent = _normalized_dict(parsed.get("allocation_intent"), market)
    risk_budget_pct = _normalized_dict(parsed.get("risk_budget_pct"), market)
    size_reason = _normalized_dict(parsed.get("size_reason"), market)
    if not max_order_cap_pct and max_position_pct:
        max_order_cap_pct = _normalized_dict(max_position_pct, market)
    if not max_position_pct and max_order_cap_pct:
        max_position_pct = dict(max_order_cap_pct)
    raw_price_targets = parsed.get("price_targets", {}) if isinstance(parsed.get("price_targets"), dict) else {}
    candidate_actions_present = "candidate_actions" in parsed and isinstance(parsed.get("candidate_actions"), list)
    raw_candidate_actions = parsed.get("candidate_actions", [])
    candidate_actions = raw_candidate_actions if isinstance(raw_candidate_actions, list) else []
    candidate_actions_missing_contract = bool(v2_requested and not candidate_actions_present)
    candidate_actions_empty = bool(candidate_actions_present and not candidate_actions)
    price_targets = {}
    trade_ready_set = set(trade_ready)
    for key, value in raw_price_targets.items():
        ticker = normalize_ticker(key, market)
        if ticker not in trade_ready_set or not isinstance(value, dict):
            continue
        price_targets[ticker] = dict(value)
    missing_price_targets = [ticker for ticker in trade_ready if ticker not in price_targets]
    price_target_ratio = (len(price_targets) / len(trade_ready)) if trade_ready else 1.0

    return {
        "watchlist": watchlist,
        "trade_ready": trade_ready,
        "reasons": {normalize_ticker(k, market): str(v) for k, v in reasons.items()},
        "veto": {normalize_ticker(k, market): str(v) for k, v in veto.items()},
        "risk_tags": {
            normalize_ticker(k, market): [str(x) for x in v[:5]] if isinstance(v, list) else [str(v)]
            for k, v in risk_tags.items()
        },
        "recommended_strategy": {normalize_ticker(k, market): str(v) for k, v in recommended_strategy.items()},
        "max_position_pct": _normalized_dict(max_position_pct, market),
        "allocation_intent": {
            k: str(v).strip().lower()
            for k, v in allocation_intent.items()
            if str(v).strip().lower() in {"probe", "small", "normal", "aggressive"}
        },
        "max_order_cap_pct": max_order_cap_pct,
        "risk_budget_pct": risk_budget_pct,
        "size_reason": {k: str(v) for k, v in size_reason.items()},
        "price_targets": price_targets,
        "candidate_actions": candidate_actions,
        "_price_target_coverage": {
            "trade_ready_count": len(trade_ready),
            "price_target_count": len(price_targets),
            "missing": missing_price_targets,
            "ratio": round(price_target_ratio, 4),
        },
        "_parse_recovered": parse_recovered,
        "_fallback_mode": str(parsed.get("_fallback_mode", "") or ""),
        "_candidate_actions_v2_requested": bool(v2_requested),
        "_legacy_auto_ready_promoted": bool(legacy_auto_ready_promoted),
        "_legacy_auto_ready_blocked": bool(not allow_legacy_auto_ready and not parse_recovered and "trade_ready" not in parsed and not v2_requested),
        "_legacy_auto_ready_allowed_source": "explicit_opt_in" if allow_legacy_auto_ready else "",
        "_selection_raw_schema": "legacy",
        "_selection_stop_reason": str(stop_reason or ""),
        "_candidate_actions_present": bool(candidate_actions_present),
        "_candidate_actions_empty": bool(candidate_actions_empty),
        "_candidate_actions_missing_contract": bool(candidate_actions_missing_contract),
        "_candidate_actions_source": "candidate_actions_v1" if candidate_actions_present else "",
    }
