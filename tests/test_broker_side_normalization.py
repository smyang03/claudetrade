from __future__ import annotations

import unittest

from runtime.pathb_runtime import PathBRuntime
from trading_bot import TradingBot


class BrokerSideNormalizationTests(unittest.TestCase):
    def test_broker_row_side_matches_kis_numeric_sell_code(self) -> None:
        row = {"order_side": "01"}

        self.assertTrue(TradingBot._broker_row_side_matches(row, "sell"))
        self.assertFalse(TradingBot._broker_row_side_matches(row, "buy"))

    def test_broker_row_side_matches_kis_numeric_buy_code(self) -> None:
        row = {"tr_side": "02"}

        self.assertTrue(TradingBot._broker_row_side_matches(row, "buy"))
        self.assertFalse(TradingBot._broker_row_side_matches(row, "sell"))

    def test_broker_sell_fill_rows_excludes_kis_buy_rows(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        rows = [
            {"ticker": "005930", "order_side": "01", "filled_qty": 3, "order_no": "10"},
            {"ticker": "005930", "order_side": "02", "filled_qty": 4, "order_no": "11"},
            {"ticker": "005930", "side": "sell", "filled_qty": 2, "order_no": "12"},
        ]

        matches = TradingBot._broker_sell_fill_rows(bot, rows)

        self.assertEqual([row["order_no"] for row in matches], ["10", "12"])

    def test_broker_sell_open_order_rows_excludes_kis_buy_rows(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        rows = [
            {"ticker": "005930", "order_side": "01", "remaining_qty": 3, "order_no": "10"},
            {"ticker": "005930", "order_side": "02", "remaining_qty": 4, "order_no": "11"},
            {"ticker": "005930", "side": "sell", "remaining_qty": 2, "order_no": "12"},
        ]

        matches = TradingBot._broker_sell_open_order_rows(bot, rows)

        self.assertEqual([row["order_no"] for row in matches], ["10", "12"])

    def test_broker_row_side_matches_preserves_korean_aliases(self) -> None:
        self.assertTrue(TradingBot._broker_row_side_matches({"side": "\ub9e4\ub3c4"}, "sell"))
        self.assertFalse(TradingBot._broker_row_side_matches({"side": "\ub9e4\ub3c4"}, "buy"))
        self.assertTrue(TradingBot._broker_row_side_matches({"side": "\ub9e4\uc218"}, "buy"))
        self.assertFalse(TradingBot._broker_row_side_matches({"side": "\ub9e4\uc218"}, "sell"))

    def test_broker_row_side_matches_raw_kis_field_names(self) -> None:
        self.assertTrue(TradingBot._broker_row_side_matches({"SLL_BUY_DVSN_CD": "01"}, "sell"))
        self.assertTrue(TradingBot._broker_row_side_matches({"SLL_BUY_DVSN": "02"}, "buy"))
        self.assertTrue(TradingBot._broker_row_side_matches({"SELN_BYOV_CLS": "1"}, "sell"))
        self.assertTrue(TradingBot._broker_row_side_matches({"SELN_BYOV_CLS": "2"}, "buy"))

    def test_broker_row_side_matches_conflicting_side_fields_are_ambiguous(self) -> None:
        row = {"side": "buy", "order_side": "01"}

        self.assertFalse(TradingBot._broker_row_side_matches(row, "sell"))
        self.assertFalse(TradingBot._broker_row_side_matches(row, "buy"))

    def test_pathb_runtime_side_matches_uses_raw_kis_fields(self) -> None:
        self.assertTrue(PathBRuntime._side_matches({"order_side": "01"}, "sell"))
        self.assertFalse(PathBRuntime._side_matches({"order_side": "01"}, "buy"))
        self.assertTrue(PathBRuntime._side_matches({"tr_side": "02"}, "buy"))
        self.assertFalse(PathBRuntime._side_matches({"tr_side": "02"}, "sell"))

    def test_pathb_runtime_side_matches_conflicting_side_fields_do_not_fallback(self) -> None:
        row = {"side": "sell", "tr_side": "02"}

        self.assertFalse(PathBRuntime._side_matches(row, "sell"))
        self.assertFalse(PathBRuntime._side_matches(row, "buy"))

    def test_pathb_runtime_side_matches_keeps_no_side_fallback(self) -> None:
        self.assertTrue(PathBRuntime._side_matches({"ticker": "005930"}, "sell"))
        self.assertTrue(PathBRuntime._side_matches({"ticker": "005930"}, "buy"))
        self.assertFalse(PathBRuntime._side_matches({"order_side": ""}, "sell"))


if __name__ == "__main__":
    unittest.main()
