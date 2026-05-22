from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class TradeReadySlotConfigTests(unittest.TestCase):
    def test_us_opening_range_pullback_slot_can_be_overridden_by_env(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.enable_kr_momentum_shrink = True
        bot.enable_continuation_live = False

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "US")

        self.assertEqual(config["opening_range_pullback"], 2)
        self.assertEqual(config["momentum"], 2)

    def test_us_slot_override_does_not_change_kr(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.enable_kr_momentum_shrink = True
        bot.enable_continuation_live = False

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "KR")

        self.assertEqual(config["opening_range_pullback"], 1)


if __name__ == "__main__":
    unittest.main()
