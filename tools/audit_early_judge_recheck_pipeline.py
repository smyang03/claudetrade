from __future__ import annotations

"""Deterministic KR/US audit for the bounded WATCH -> Claude recheck pipeline.

The harness never connects to a broker or a real model.  It creates point-in-time
candidate fixtures and proves that every transient path is consumed exactly once,
that local/shadow evidence cannot promote a ticker directly, and that terminal
Claude plans leave no orphan retry rows behind.
"""

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_bot import TradingBot  # noqa: E402


DEFAULT_OUTPUT = ROOT / "state" / "early_judge_recheck_pipeline_audit.json"


def _ticker(market: str, suffix: str = "") -> str:
    return ("PYPL" if market == "US" else "005930") + suffix


def _features(market: str, price: float | None = None) -> dict[str, Any]:
    return {
        "current_price": float(price if price is not None else (54.0 if market == "US" else 75_000.0)),
        "opening_range_break": True,
        "volume_ratio_open": 2.2,
        "vwap_distance_pct": 0.4,
        "pullback_from_high_pct": -0.8,
        "ret_3m_pct": 1.1,
        "ret_5m_pct": 0.3,
        "ret_10m_pct": 3.2,
        "ret_30m_pct": 5.0,
        "momentum_state": "early_strength",
        "data_quality": "minute_complete",
    }


def _plan(market: str, ticker: str) -> dict[str, Any]:
    if market == "US":
        low, high, target, stop = 53.0, 54.0, 58.0, 51.5
    else:
        low, high, target, stop = 74_000.0, 75_000.0, 81_000.0, 72_000.0
    return {
        "ticker": ticker,
        "market": market,
        "action": "PULLBACK_WAIT",
        "route": "path_b",
        "confidence": 0.75,
        "reason": "deterministic audit pullback",
        "buy_zone_low": low,
        "buy_zone_high": high,
        "sell_target": target,
        "stop_loss": stop,
        "hold_days": 1,
        "invalid_if": "breaks structural support",
        "structural_basis": "VWAP retest",
    }


def _bot(market: str, ticker: str) -> tuple[TradingBot, list[dict[str, Any]], list[str]]:
    bot = TradingBot.__new__(TradingBot)
    bot.is_paper = True
    bot.runtime_config = None
    bot.selection_meta = {
        "KR": {},
        "US": {},
    }
    bot.selection_meta[market] = {
        "watchlist": [ticker],
        "trade_ready": [],
        "candidate_actions": [
            {"ticker": ticker, "action": "WATCH", "strategy": "momentum", "confidence": 0.6}
        ],
        "_final_prompt_pool": [
            {
                "ticker": ticker,
                "trainer_candidate_state": "PLAN_B",
                "trainer_prompt_score": 80.0,
                "strategy": "momentum",
            }
        ],
    }
    bot.today_tickers = {"KR": [], "US": []}
    bot.today_tickers[market] = [ticker]
    bot.trade_ready_tickers = {"KR": [], "US": []}
    bot.today_judgment = {"consensus": {"mode": "MILD_BULL"}}
    bot.pending_orders = []
    bot.risk = SimpleNamespace(positions=[])
    bot.pathb = None
    bot._v2_same_day_stop_tickers = {"KR": set(), "US": set()}
    bot._last_post_open_features_by_ticker = {"KR": {}, "US": {}}
    bot._last_post_open_features_by_ticker[market][ticker] = _features(market)
    bot.session_active = True
    bot.current_market = market
    bot._adaptive_live_market_context = lambda *args, **kwargs: {"fresh": True, "market_regime": "risk_on"}
    events: list[dict[str, Any]] = []
    calls: list[str] = []
    bot._write_funnel_event = lambda event, event_market, payload: events.append(
        {"event": event, "market": event_market, **dict(payload)}
    )
    bot._apply_selection_meta = lambda *args, **kwargs: kwargs.get("meta_override")
    bot._single_symbol_judge_client = lambda **kwargs: calls.append(kwargs["ticker"]) or _plan(
        kwargs["market"], kwargs["ticker"]
    )
    return bot, events, calls


def _row(market: str, ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "market": market,
        "trainer_candidate_state": "PLAN_B",
        "trainer_prompt_score": 80.0,
        "strategy": "momentum",
        "post_open_features": _features(market),
    }


def _force_due(bot: TradingBot, market: str) -> None:
    for item in bot._early_judge_recheck_queue.get(market, []):
        item["due_at"] = (datetime.now() - timedelta(seconds=1)).isoformat(timespec="seconds")


def _record(name: str, market: str, checks: dict[str, bool], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "pipeline": name,
        "market": market,
        "status": "PASS" if checks and all(checks.values()) else "FAIL",
        "checks": checks,
        "data": data,
    }


def _with_env(values: dict[str, str], callback: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    previous = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        return callback()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def audit_market(market: str) -> list[dict[str, Any]]:
    common_env = {
        "EARLY_JUDGE_TRIGGER_ENABLED": "true",
        f"{market}_EARLY_JUDGE_TRIGGER_ENABLED": "true",
        "EARLY_JUDGE_RECHECK_CONSUMER_ENABLED": "true",
        "EARLY_JUDGE_COOLDOWN_MIN": "0",
        "EARLY_JUDGE_MAX_CALLS_PER_TICKER_PER_SESSION": "2",
        "ADAPTIVE_REASK_CLAUDE_MAX_PER_CYCLE": "2",
    }
    records: list[dict[str, Any]] = []

    def judgment_gate_case() -> dict[str, Any]:
        ticker = _ticker(market)
        bot, events, calls = _bot(market, ticker)
        bot._in_entry_blackout = lambda _market: False
        before_ready = list(bot.trade_ready_tickers[market])
        queued = bot._queue_judgment_gate_recheck(
            market,
            ticker,
            block_reason="non_executable_judgment_phase:preopen",
            price=_features(market)["current_price"],
            mode="MILD_BULL",
        )
        queue_reason = str((bot._early_judge_recheck_queue[market][0] or {}).get("reason") or "")
        _force_due(bot, market)
        consumed = bot.run_early_judge_rechecks(market)
        return _record(
            "judgment_not_executable_replay",
            market,
            {
                "candidate_queued": queued and queue_reason == "judgment_not_executable",
                "no_direct_trade_ready": before_ready == bot.trade_ready_tickers[market] == [],
                "claude_owner_called": calls == [ticker],
                "terminal_queue_empty": len(bot._early_judge_recheck_queue[market]) == 0,
            },
            {"calls": calls, "consume_status": consumed.get("status"), "events": len(events)},
        )

    def blackout_case() -> dict[str, Any]:
        ticker = _ticker(market)
        bot, events, calls = _bot(market, ticker)
        state = {"blackout": True}
        bot._in_entry_blackout = lambda _market: state["blackout"]
        before_ready = list(bot.trade_ready_tickers[market])
        first = bot.maybe_run_early_judge_triggers(market, source="audit_blackout", rows=[_row(market, ticker)])
        initial_calls = list(calls)
        queued_once = len(bot._early_judge_recheck_queue[market]) == 1
        # Re-observation must upsert, not leak a duplicate queue row.
        bot.maybe_run_early_judge_triggers(market, source="audit_blackout_repeat", rows=[_row(market, ticker)])
        deduped = len(bot._early_judge_recheck_queue[market]) == 1
        _force_due(bot, market)
        state["blackout"] = False
        consumed = bot.run_early_judge_rechecks(market)
        return _record(
            "entry_blackout_replay",
            market,
            {
                "initial_call_blocked": first == [] and initial_calls == [],
                "queued_once": queued_once,
                "duplicate_upserted": deduped,
                "no_local_promotion_before_claude": before_ready == [],
                "claude_called_once_after_blackout": calls == [ticker],
                "terminal_queue_empty": len(bot._early_judge_recheck_queue[market]) == 0,
            },
            {
                "calls": calls,
                "consume_status": consumed.get("status"),
                "events": len(events),
                "queue_after": len(bot._early_judge_recheck_queue[market]),
            },
        )

    def wait_case() -> dict[str, Any]:
        ticker = _ticker(market)
        bot, events, calls = _bot(market, ticker)
        bot._in_entry_blackout = lambda _market: False

        def judge(**kwargs):
            calls.append(kwargs["ticker"])
            if len(calls) == 1:
                return {
                    "ticker": kwargs["ticker"],
                    "market": kwargs["market"],
                    "action": "WAIT_RECHECK",
                    "route": "wait",
                    "reason": "audit wait",
                    "recheck_after_min": 5,
                }
            return _plan(kwargs["market"], kwargs["ticker"])

        bot._single_symbol_judge_client = judge
        first = bot.maybe_run_early_judge_triggers(market, source="audit_wait", rows=[_row(market, ticker)])
        queued_attempt = int(bot._early_judge_recheck_queue[market][0].get("attempts") or 0)
        _force_due(bot, market)
        consumed = bot.run_early_judge_rechecks(market)
        return _record(
            "wait_recheck_consumer",
            market,
            {
                "wait_was_queued": bool(first and first[0].get("action") == "WAIT_RECHECK"),
                "attempt_accounted": queued_attempt == 1,
                "called_exactly_twice": calls == [ticker, ticker],
                "terminal_queue_empty": len(bot._early_judge_recheck_queue[market]) == 0,
            },
            {"calls": calls, "consume_status": consumed.get("status"), "events": len(events)},
        )

    def shadow_signal_case() -> dict[str, Any]:
        ticker = _ticker(market)
        bot, events, calls = _bot(market, ticker)
        bot._in_entry_blackout = lambda _market: False
        before_ready = list(bot.trade_ready_tickers[market])
        bot._log_watch_trigger_shadow(
            market,
            ticker,
            price=_features(market)["current_price"],
            mode="MILD_BULL",
            strategy="momentum",
            signal_fired=True,
            result="would_promote",
        )
        queued = list(bot._early_judge_recheck_queue[market])
        _force_due(bot, market)
        consumed = bot.run_early_judge_rechecks(market)
        return _record(
            "watch_signal_to_claude",
            market,
            {
                "signal_queued": len(queued) == 1,
                "no_direct_trade_ready": before_ready == bot.trade_ready_tickers[market] == [],
                "claude_owner_called": calls == [ticker],
                "terminal_queue_empty": len(bot._early_judge_recheck_queue[market]) == 0,
            },
            {"calls": calls, "consume_status": consumed.get("status"), "events": len(events)},
        )

    def adaptive_case() -> dict[str, Any]:
        ticker = _ticker(market)
        bot, events, calls = _bot(market, ticker)
        bot._in_entry_blackout = lambda _market: False
        rows = bot._early_judge_rows_from_adaptive_reask(market)
        consumed = bot.run_early_judge_rechecks(market)
        return _record(
            "adaptive_reask_to_claude",
            market,
            {
                "adaptive_reask_created": len(rows) == 1 and rows[0].get("adaptive_reask") is True,
                "no_local_promotion": bot.trade_ready_tickers[market] == [],
                "claude_owner_called": calls == [ticker],
                "terminal_queue_empty": len(bot._early_judge_recheck_queue[market]) == 0,
            },
            {"calls": calls, "consume_status": consumed.get("status"), "events": len(events)},
        )

    records.append(_with_env({**common_env, "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false"}, judgment_gate_case))
    records.append(_with_env({**common_env, "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false"}, blackout_case))
    records.append(_with_env({**common_env, "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false"}, wait_case))
    records.append(
        _with_env(
            {
                **common_env,
                "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false",
                "WATCH_TRIGGER_REASK_CLAUDE_ENABLED": "true",
            },
            shadow_signal_case,
        )
    )
    records.append(_with_env({**common_env, "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "true"}, adaptive_case))
    return records


def build_report() -> dict[str, Any]:
    records = audit_market("KR") + audit_market("US")
    return {
        "schema_version": 1,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "scope": ["KR", "US"],
        "authority_contract": "local evidence -> Claude single-symbol judge -> existing runtime gates",
        "direct_local_promotion": False,
        "record_count": len(records),
        "pass_count": sum(1 for record in records if record.get("status") == "PASS"),
        "fail_count": sum(1 for record in records if record.get("status") != "PASS"),
        "records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_report()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(
            f"early_judge_recheck_pipeline_audit pass={report['pass_count']} "
            f"fail={report['fail_count']} output={output}"
        )
    return 0 if report["fail_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
