from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from runtime import selection_smart_skip
from trading_bot import KST, TradingBot


def _bot(context: dict | None = None) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    now = datetime.now(KST)
    ctx = context or selection_smart_skip.market_context_components(
        market_change_pct=0.2,
        secondary_change_pct=0.1,
        session_phase="morning",
        consensus_mode="BALANCED",
    )
    ctx_hash = selection_smart_skip.sha256_text(json.dumps(ctx, ensure_ascii=False, sort_keys=True))[:20]
    bot.selection_meta = {
        "US": {
            "selection_snapshot_ts": now.isoformat(timespec="seconds"),
            "_smart_skip_context_hash": ctx_hash,
            "watchlist": ["AAPL"],
            "trade_ready": ["AAPL"],
            "reasons": {"AAPL": "test reason"},
            "price_targets": {"AAPL": {"buy_zone_low": 100, "buy_zone_high": 101}},
        }
    }
    bot.today_judgment = {"consensus": {"mode": "BALANCED"}}
    bot.today_ticker_reasons = {"US": {"AAPL": "selected"}}
    bot._build_intraday_context = lambda market: ""
    bot._selection_session_phase = lambda market: {"phase": "morning"}
    bot._get_market_change_pct = lambda market: 0.2
    bot._get_secondary_change_pct = lambda market: 0.1
    bot._buy_time_confirm_call_counts = {"US": 0}
    bot.risk = SimpleNamespace()
    return bot


def _signal() -> dict:
    return {
        "ticker": "AAPL",
        "strategy_name": "momentum",
        "price": 100.5,
        "risk_price": 100.5,
        "score": 0.8,
        "sig_row": {"ticker": "AAPL"},
    }


class PathABuyTimeConfirmTests(unittest.TestCase):
    def test_fresh_selection_skips_confirm(self) -> None:
        bot = _bot()
        bot._buy_time_confirm_judge_client = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no call"))

        result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["decision"], "SKIPPED_FRESH")
        self.assertEqual(bot._buy_time_confirm_call_counts["US"], 0)

    def test_stale_selection_calls_confirm_and_allows_confirm_buy(self) -> None:
        bot = _bot()
        stale_ts = (datetime.now(KST) - timedelta(minutes=31)).isoformat(timespec="seconds")
        bot.selection_meta["US"]["selection_snapshot_ts"] = stale_ts
        bot._buy_time_confirm_judge_client = lambda *args, **kwargs: {
            "decision": "CONFIRM_BUY",
            "reason": "still valid",
        }

        result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["decision"], "CONFIRM_BUY")
        self.assertEqual(bot._buy_time_confirm_call_counts["US"], 1)

    def test_session_cap_returns_unavailable_proceed_in_normal_context(self) -> None:
        bot = _bot()
        stale_ts = (datetime.now(KST) - timedelta(minutes=31)).isoformat(timespec="seconds")
        bot.selection_meta["US"]["selection_snapshot_ts"] = stale_ts
        bot._buy_time_confirm_call_counts["US"] = 20

        result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["decision"], "CONFIRM_UNAVAILABLE_PROCEED")
        self.assertEqual(result["confirm_result"]["confirm_unavailable_reason"], "cap_exceeded")

    def test_unavailable_in_adverse_context_defers(self) -> None:
        adverse = selection_smart_skip.market_context_components(
            market_change_pct=-3.5,
            secondary_change_pct=-1.0,
            session_phase="morning",
            consensus_mode="BALANCED",
        )
        bot = _bot(context=adverse)
        stale_ts = (datetime.now(KST) - timedelta(minutes=31)).isoformat(timespec="seconds")
        bot.selection_meta["US"]["selection_snapshot_ts"] = stale_ts
        bot._get_market_change_pct = lambda market: -3.5
        bot._get_secondary_change_pct = lambda market: -1.0
        bot._buy_time_confirm_call_counts["US"] = 20

        result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())

        self.assertFalse(result["allowed"])
        self.assertEqual(result["decision"], "DEFER")
        self.assertTrue(result["full_rescreen_requested"])

    def test_unavailable_in_adverse_context_can_be_configured_to_proceed(self) -> None:
        adverse = selection_smart_skip.market_context_components(
            market_change_pct=-3.5,
            secondary_change_pct=-1.0,
            session_phase="morning",
            consensus_mode="BALANCED",
        )
        bot = _bot(context=adverse)
        stale_ts = (datetime.now(KST) - timedelta(minutes=31)).isoformat(timespec="seconds")
        bot.selection_meta["US"]["selection_snapshot_ts"] = stale_ts
        bot._get_market_change_pct = lambda market: -3.5
        bot._get_secondary_change_pct = lambda market: -1.0
        bot._buy_time_confirm_call_counts["US"] = 20

        with patch.dict("os.environ", {"BUY_TIME_CONFIRM_ADVERSE_CONTEXT_BLOCK": "false"}, clear=False):
            result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())

        self.assertTrue(result["allowed"])
        self.assertEqual(result["decision"], "CONFIRM_UNAVAILABLE_PROCEED")
        self.assertEqual(result["confirm_result"]["confirm_unavailable_reason"], "cap_exceeded")

    def test_full_rescreen_request_drains_one_forced_manual_rescreen(self) -> None:
        bot = _bot()
        stale_ts = (datetime.now(KST) - timedelta(minutes=61)).isoformat(timespec="seconds")
        bot.selection_meta["US"]["selection_snapshot_ts"] = stale_ts
        calls = []

        def manual_rescreen(market: str, *, source_type: str, trigger: str):
            calls.append(
                {
                    "market": market,
                    "source_type": source_type,
                    "trigger": trigger,
                    "force_call": os.environ.get("SELECTION_SMART_SKIP_FORCE_CALL"),
                }
            )
            return ["MSFT"]

        bot.manual_rescreen = manual_rescreen
        original_force = os.environ.get("SELECTION_SMART_SKIP_FORCE_CALL")

        result = TradingBot._patha_buy_time_confirm_decision(bot, "US", _signal())
        queued = TradingBot._queue_buy_time_full_rescreen_request(bot, "US", result)
        drained = TradingBot._drain_buy_time_full_rescreen_request(bot, "US")

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "selection_snapshot_too_stale")
        self.assertTrue(queued)
        self.assertTrue(drained["success"])
        self.assertEqual(calls, [
            {
                "market": "US",
                "source_type": "buy_time_confirm_rescreen",
                "trigger": "selection_snapshot_too_stale",
                "force_call": "true",
            }
        ])
        self.assertEqual(bot.selection_meta["US"]["_buy_time_full_rescreen_last_result"]["selected"], ["MSFT"])
        self.assertEqual(os.environ.get("SELECTION_SMART_SKIP_FORCE_CALL"), original_force)


if __name__ == "__main__":
    unittest.main()
