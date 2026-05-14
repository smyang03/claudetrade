from __future__ import annotations

import unittest

from trading_bot import TradingBot


class WatchTriggerShadowStrategyTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.selection_meta = {"US": {}, "KR": {}}
        bot._last_screen_candidates = {"US": [], "KR": []}
        return bot

    def test_strategy_falls_back_to_candidate_action_primary_bucket(self) -> None:
        bot = self._bot()
        bot.selection_meta["US"] = {
            "candidate_actions": [
                {"ticker": "AAPL", "primary_bucket": "pullback_watch"},
            ]
        }

        strategy, source = TradingBot._watch_trigger_shadow_strategy_for_ticker(bot, "US", "AAPL")

        self.assertEqual(strategy, "gap_pullback")
        self.assertEqual(source, "candidate_action.primary_bucket")

    def test_strategy_falls_back_to_last_screen_candidate_category(self) -> None:
        bot = self._bot()
        bot._last_screen_candidates["US"] = [
            {"ticker": "MSFT", "category": "day_losers", "price": 100.0, "volume": 1_000_000}
        ]

        strategy, source = TradingBot._watch_trigger_shadow_strategy_for_ticker(bot, "US", "MSFT")

        self.assertEqual(strategy, "mean_reversion")
        self.assertEqual(source, "last_screen_candidate.category")


if __name__ == "__main__":
    unittest.main()
