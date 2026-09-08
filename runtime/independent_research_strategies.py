"""Versioned research signals only. No broker imports, orders or PnL claims."""
from __future__ import annotations

import hashlib
import json
import math
import statistics
from datetime import datetime, timedelta, timezone

CONTRACTS = {
    "r_multiasset_trend_v1": {
        "universe": ["SPY", "EFA", "IEF", "GLD"],
        "lookbacks": [126, 252], "ma_sessions": 200, "vol_sessions": 63,
        "max_asset_weight": .25, "rebalance": "first_session_of_month_open",
        "decision": "prior_month_last_complete_close", "long_only": True,
        "selection": "both_returns_positive_and_above_ma200",
        "allocation": "inverse_vol_across_full_universe_then_cap; failed_weights_cash",
        "benchmark": "same_universe_equal_weight_monthly",
    },
    "r_earnings_quality_drift_v1": {
        "market": "US", "daily_cap": 1, "max_positions": 4,
        "max_position_weight": .25, "eps_surprise_min": .10,
        "revenue_surprise_min": .02, "event_max_age_days": 7,
        "reaction_volume_multiple": 1.5, "prior20_min_dollar_volume": 20_000_000,
        "selection": "recurring_eps_and_revenue_beat_guidance_raised_price_confirmation",
        "ranking": "eps_surprise_desc_then_ticker_then_event_id",
        "entry": "next_session_open_after_complete_reaction_bar",
        "hold_sessions": 20, "close_stop_pct": -8,
        "exit": "stop_decided_at_close_execute_next_open; otherwise_H20_close",
        "take_profit": None, "duplicate_rule": "one_entry_per_event_no_ticker_overlap",
        "benchmark": "same_entry_exit_SPY_and_price_only_earnings_gap",
    },
}
COMMON = {"authority": "RESEARCH_ONLY", "live_eligible": False,
          "execution_validation": "NOT_IMPLEMENTED", "cost_validation": "PENDING",
          "input_schema": "independent_research_inputs_v1"}


def stamp(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timezone_required")
    return result.astimezone(timezone.utc)


def number(value, positive=False):
    if isinstance(value, bool):
        raise ValueError("boolean_is_not_number")
    result = float(value)
    if not math.isfinite(result) or (positive and result <= 0):
        raise ValueError("invalid_number")
    return result


def fingerprint(strategy):
    return hashlib.sha256(json.dumps({**COMMON, **CONTRACTS[strategy]}, sort_keys=True).encode()).hexdigest()


def envelope(strategy):
    return {**COMMON, "strategy_id": strategy, "contract_hash": fingerprint(strategy),
            "status": "BLOCKED", "signals": [], "rejections": []}


def validate_snapshot(snapshot, now):
    if snapshot["schema_version"] != COMMON["input_schema"] or not snapshot["source"]:
        raise ValueError("input_schema_or_source_missing")
    observed = stamp(snapshot["observed_at"])
    cutoff = stamp(snapshot["cutoff_at"])
    if not cutoff <= observed <= now or now - cutoff > timedelta(days=4):
        raise ValueError("stale_or_future_snapshot")
    asof = datetime.strptime(snapshot["asof_session"], "%Y-%m-%d").date()
    entry = datetime.strptime(snapshot["entry_session"], "%Y-%m-%d").date()
    if not asof < entry or asof > cutoff.date():
        raise ValueError("invalid_session_sequence")
    return cutoff


def trend(snapshot, cutoff):
    sid = "r_multiasset_trend_v1"
    out = envelope(sid)
    contract = CONTRACTS[sid]
    metrics = {}
    expected_dates = None
    for ticker in contract["universe"]:
        item = snapshot["prices"][ticker]
        if item["price_basis"] != "split_and_distribution_adjusted":
            raise ValueError("unverified_price_basis:" + ticker)
        bars = item["bars"]
        dates = [b["session"] for b in bars]
        if (len(bars) < 253 or dates != sorted(set(dates))
                or dates[-1] != snapshot["asof_session"]):
            raise ValueError("history_missing_or_misaligned:" + ticker)
        for bar in bars:
            datetime.strptime(bar["session"], "%Y-%m-%d")
            if stamp(bar["known_at"]) > cutoff:
                raise ValueError("future_bar:" + ticker)
        if expected_dates is not None and dates[-253:] != expected_dates:
            raise ValueError("cross_asset_calendar_mismatch")
        expected_dates = dates[-253:]
        closes = [number(b["adjusted_close"], positive=True) for b in bars[-253:]]
        daily = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - 63, len(closes))]
        vol = statistics.stdev(daily) * math.sqrt(252)
        if vol <= 0 or not math.isfinite(vol):
            raise ValueError("invalid_volatility:" + ticker)
        r6, r12 = closes[-1] / closes[-127] - 1, closes[-1] / closes[-253] - 1
        passed = r6 > 0 and r12 > 0 and closes[-1] > statistics.mean(closes[-200:])
        metrics[ticker] = {"return_126": r6, "return_252": r12, "annual_vol": vol, "passes": passed}
    total = sum(1 / m["annual_vol"] for m in metrics.values())
    weights = {t: min(contract["max_asset_weight"], (1 / m["annual_vol"]) / total) if m["passes"] else 0
               for t, m in metrics.items()}
    out.update(status="OBSERVING", metrics=metrics, target_weights=weights,
               cash_weight=1 - sum(weights.values()))
    # Daily diagnostics are not daily rebalance instructions.
    if snapshot["asof_session"][:7] != snapshot["entry_session"][:7]:
        out.update(status="RESEARCH_TARGET", signals=[{"entry_session": snapshot["entry_session"],
                   "target_weights": weights, "cash_weight": out["cash_weight"]}])
    return out


def earnings(snapshot, cutoff):
    sid = "r_earnings_quality_drift_v1"
    contract = CONTRACTS[sid]
    out = envelope(sid)
    input_status = snapshot.get("earnings_input_status", {})
    if input_status.get("status") == "BLOCKED":
        out["rejections"] = [{"reason": input_status.get("reason", "fundamental_inputs_incomplete")}]
        out["input_coverage"] = input_status
        return out
    events = snapshot["earnings"]
    if not isinstance(events, list):
        raise ValueError("earnings_must_be_list")
    candidates = []
    seen = set()
    for event in events:
        label = event.get("event_id") if isinstance(event, dict) else None
        try:
            ticker = event["ticker"]
            if not label or not ticker or label in seen:
                raise ValueError("missing_or_duplicate_event")
            seen.add(label)
            published = stamp(event["published_at"])
            estimate_at = stamp(event["estimate_observed_at"])
            actual_at = stamp(event["actual_observed_at"])
            if not estimate_at < published <= actual_at <= cutoff:
                raise ValueError("point_in_time_sequence_invalid")
            if cutoff - published > timedelta(days=contract["event_max_age_days"]):
                raise ValueError("stale_event")
            if not event["source_document"] or event["eps_basis"] != "comparable_recurring_diluted":
                raise ValueError("earnings_quality_unverified")
            eps = number(event["eps_actual"], True) / number(event["eps_estimate"], True) - 1
            revenue = number(event["revenue_actual"], True) / number(event["revenue_estimate"], True) - 1
            if (not stamp(event["previous_guidance_observed_at"]) < published
                    or not published <= stamp(event["guidance_observed_at"]) <= cutoff
                    or not event["guidance_period"] == event["previous_guidance_period"]
                    or not event["guidance_metric"] == event["previous_guidance_metric"]
                    or not event["guidance_metric"]):
                raise ValueError("guidance_not_comparable")
            guidance_up = number(event["guidance_mid"], True) > number(event["previous_guidance_mid"], True)
            reaction = event["reaction_bar"]
            if reaction["session"] != snapshot["asof_session"] or not published <= stamp(reaction["closed_at"]) <= cutoff:
                raise ValueError("reaction_bar_unavailable")
            close = number(reaction["close"], True)
            confirms = (close > number(reaction["open"], True)
                        and close > number(reaction["previous_close"], True)
                        and number(reaction["volume"], True) >= contract["reaction_volume_multiple"] * number(reaction["prior20_mean_volume"], True)
                        and number(reaction["prior20_mean_dollar_volume"], True) >= contract["prior20_min_dollar_volume"])
            if eps < contract["eps_surprise_min"] or revenue < contract["revenue_surprise_min"] or not guidance_up or not confirms:
                raise ValueError("rule_not_met")
            candidates.append({"ticker": ticker, "event_id": label, "eps_surprise": eps,
                               "revenue_surprise": revenue, "entry_session": snapshot["entry_session"],
                               "target_weight_ceiling": contract["max_position_weight"]})
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            out["rejections"].append({"event_id": label, "reason": str(exc)})
    candidates.sort(key=lambda x: (-x["eps_surprise"], x["ticker"], x["event_id"]))
    out.update(status="RESEARCH_CANDIDATE" if candidates else "NO_CANDIDATE",
               signals=candidates[:1], candidate_count=len(candidates))
    return out


def evaluate(snapshot, now=None):
    now = now or datetime.now(timezone.utc)
    results = []
    for sid, fn in zip(CONTRACTS, (trend, earnings)):
        try:
            cutoff = validate_snapshot(snapshot, now)
            result = fn(snapshot, cutoff)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            result = envelope(sid)
            result["rejections"] = [{"reason": str(exc)}]
        results.append(result)
    return {**COMMON, "generated_at": now.isoformat(), "strategies": results,
            "input_hash": hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()}
