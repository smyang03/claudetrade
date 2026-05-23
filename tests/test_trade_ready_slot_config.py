from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class TradeReadySlotConfigTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.enable_kr_momentum_shrink = True
        bot.enable_continuation_live = False
        bot.pending_orders = []
        bot.today_judgment = {}
        bot._data_insufficient_watch_tickers = {}
        return bot

    def test_us_opening_range_pullback_slot_can_be_overridden_by_env(self) -> None:
        bot = self._bot()

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "US")

        self.assertEqual(config["opening_range_pullback"], 2)
        self.assertEqual(config["momentum"], 2)

    def test_us_slot_override_does_not_change_kr(self) -> None:
        bot = self._bot()

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "KR")

        self.assertEqual(config["opening_range_pullback"], 1)

    def test_held_trade_ready_does_not_consume_strategy_slot(self) -> None:
        bot = self._bot()
        bot.risk = type("Risk", (), {"positions": [{"market": "US", "ticker": "QCOM", "qty": 1}]})()
        meta = {
            "watchlist": ["NOK", "QCOM", "ASTS", "CRDO"],
            "trade_ready": ["NOK", "QCOM", "ASTS", "CRDO"],
            "recommended_strategy": {
                "NOK": "opening_range_pullback",
                "QCOM": "opening_range_pullback",
                "ASTS": "opening_range_pullback",
                "CRDO": "opening_range_pullback",
            },
        }

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["NOK", "ASTS"])
        self.assertEqual(normalized["_runtime_filtered_trade_ready"]["QCOM"], "already_holding")
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["CRDO"],
            "slot_cap:opening_range_pullback",
        )

    def test_pending_order_does_not_consume_strategy_slot(self) -> None:
        bot = self._bot()
        bot.risk = type("Risk", (), {"positions": []})()
        bot.pending_orders = [{"market": "US", "ticker": "QCOM", "qty": 1, "order_no": "pending-1"}]
        meta = {
            "watchlist": ["NOK", "QCOM", "ASTS", "CRDO"],
            "trade_ready": ["NOK", "QCOM", "ASTS", "CRDO"],
            "recommended_strategy": {
                "NOK": "opening_range_pullback",
                "QCOM": "opening_range_pullback",
                "ASTS": "opening_range_pullback",
                "CRDO": "opening_range_pullback",
            },
        }

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["NOK", "ASTS"])
        self.assertEqual(normalized["_runtime_filtered_trade_ready"]["QCOM"], "pending_order")
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["CRDO"],
            "slot_cap:opening_range_pullback",
        )


if __name__ == "__main__":
    unittest.main()
