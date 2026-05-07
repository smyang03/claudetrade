from __future__ import annotations

import unittest

from trading_bot import TradingBot


class SessionCacheResetTests(unittest.TestCase):
    def test_reset_session_live_caches_removes_intraday_or_and_post_open_state(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._intraday_high = {"AAPL": 10.0, "aapl": 9.0, "MSFT": 20.0}
        bot._intraday_low = {"AAPL": 8.0, "aapl": 7.0, "MSFT": 18.0}
        bot._or_high = {"AAPL": 10.0, "aapl": 9.0, "MSFT": 20.0}
        bot._or_low = {"AAPL": 8.0, "aapl": 7.0, "MSFT": 18.0}
        bot._or_formed = {"AAPL": True, "aapl": True, "MSFT": True}
        bot._post_open_price_history = {"US:AAPL": [{"price": 10.0}], "US:MSFT": [{"price": 20.0}]}
        bot._post_open_anchor = {"US:AAPL": {"anchor_price": 9.5}, "US:MSFT": {"anchor_price": 19.5}}
        bot._post_open_feature_last_emit = {"US:AAPL": 1.0, "US:MSFT": 2.0}

        TradingBot._reset_session_live_caches(bot, "US", ["aapl"])

        for cache in (
            bot._intraday_high,
            bot._intraday_low,
            bot._or_high,
            bot._or_low,
            bot._or_formed,
        ):
            self.assertNotIn("AAPL", cache)
            self.assertNotIn("aapl", cache)
            self.assertIn("MSFT", cache)
        self.assertNotIn("US:AAPL", bot._post_open_price_history)
        self.assertNotIn("US:AAPL", bot._post_open_anchor)
        self.assertNotIn("US:AAPL", bot._post_open_feature_last_emit)
        self.assertIn("US:MSFT", bot._post_open_price_history)
        self.assertIn("US:MSFT", bot._post_open_anchor)
        self.assertIn("US:MSFT", bot._post_open_feature_last_emit)


if __name__ == "__main__":
    unittest.main()
