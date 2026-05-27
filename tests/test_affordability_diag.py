from __future__ import annotations

import unittest

from trading_bot import TradingBot


class AffordabilityDiagTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bot = TradingBot.__new__(TradingBot)

    def test_high_price_qty_zero_records_shortfall(self) -> None:
        diag = self.bot._affordability_diag(
            price_krw=250_000,
            qty=0,
            order_cost_krw=0,
            order_budget_krw=120_000,
            available_budget_krw=500_000,
            cash_krw=500_000,
            min_effective_order_krw=45_000,
        )

        self.assertFalse(diag["affordable_1_share_bool"])
        self.assertEqual(diag["price_per_share_krw"], 250_000)
        self.assertEqual(diag["shortfall_krw"], 130_000)
        self.assertEqual(diag["affordability_reason"], "unaffordable_high_price")

    def test_min_order_not_met_is_distinct_from_qty_zero(self) -> None:
        diag = self.bot._affordability_diag(
            price_krw=20_000,
            qty=1,
            order_cost_krw=20_000,
            order_budget_krw=100_000,
            available_budget_krw=100_000,
            cash_krw=100_000,
            min_effective_order_krw=50_000,
        )

        self.assertTrue(diag["affordable_1_share_bool"])
        self.assertEqual(diag["shortfall_krw"], 0)
        self.assertEqual(diag["affordability_reason"], "min_order_not_met")

    def test_cash_too_low_takes_precedence_for_qty_zero(self) -> None:
        diag = self.bot._affordability_diag(
            price_krw=80_000,
            qty=0,
            order_cost_krw=0,
            order_budget_krw=120_000,
            available_budget_krw=120_000,
            cash_krw=50_000,
            min_effective_order_krw=30_000,
        )

        self.assertFalse(diag["affordable_1_share_bool"])
        self.assertEqual(diag["shortfall_krw"], 30_000)
        self.assertEqual(diag["affordability_reason"], "cash_too_low")

    def test_affordability_detail_includes_operator_context(self) -> None:
        detail = self.bot._affordability_detail(
            {
                "affordability_reason": "unaffordable_high_price",
                "price_per_share_krw": 287_000,
                "order_budget_krw": 110_000,
                "shortfall_krw": 177_000,
            }
        )

        self.assertIn("unaffordable_high_price", detail)
        self.assertIn("price_krw=287,000", detail)
        self.assertIn("budget_krw=110,000", detail)
        self.assertIn("shortfall_krw=177,000", detail)

    def test_market_budget_available_subtracts_pending_buy_orders(self) -> None:
        self.bot.risk = type("Risk", (), {"cash": 500_000.0})()
        self.bot.pending_orders = [
            {"market": "KR", "ticker": "069540", "qty": 33, "risk_price_krw": 6_780.0},
            {"market": "KR", "ticker": "006400", "qty": 1, "risk_price_krw": 642_000.0, "side": "sell"},
            {"market": "US", "ticker": "IBM", "qty": 1, "risk_price_krw": 380_000.0},
        ]

        self.assertEqual(self.bot._pending_order_reserved_cost_krw("KR"), 223_740.0)
        self.assertEqual(self.bot._market_budget_available("KR"), 276_260.0)

    def test_market_budget_available_uses_broker_orderable_cash(self) -> None:
        self.bot.risk = type("Risk", (), {"cash": 3_365_827.0})()
        self.bot.pending_orders = []
        self.bot._broker_state = {}
        self.bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
            "missing": False,
            "stale": False,
            "error": "",
            "account_summary": {"orderable_cash": 1_941_524.0},
            "open_orders": [],
        }

        self.assertEqual(self.bot._market_budget_available("KR"), 1_941_524.0)

    def test_market_budget_available_subtracts_broker_truth_open_buy_orders(self) -> None:
        self.bot.risk = type("Risk", (), {"cash": 500_000.0})()
        self.bot.pending_orders = []
        self.bot.price_cache = {"069540": 6_780.0}
        self.bot.price_cache_raw = {}
        self.bot._broker_state = {}
        self.bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
            "missing": False,
            "stale": False,
            "error": "",
            "account_summary": {"orderable_cash": 500_000.0},
            "open_orders": [
                {"market": "KR", "ticker": "069540", "side": "buy", "order_no": "0012214400", "remaining_qty": 33},
                {"market": "KR", "ticker": "006400", "side": "sell", "order_no": "sell1", "remaining_qty": 1},
            ],
        }

        self.assertEqual(self.bot._pending_order_reserved_cost_krw("KR"), 223_740.0)
        self.assertEqual(self.bot._market_budget_available("KR"), 276_260.0)

    def test_us_market_budget_available_does_not_double_subtract_broker_orderable_open_orders(self) -> None:
        self.bot.risk = type("Risk", (), {"cash": 1_000_000.0})()
        self.bot.pending_orders = [
            {
                "market": "US",
                "ticker": "AMD",
                "side": "buy",
                "order_no": "us-open-1",
                "remaining_qty": 1,
                "risk_price_krw": 500_000.0,
            }
        ]
        self.bot._broker_state = {}
        self.bot._broker_truth_market_snapshot = lambda market, force=False, ttl_sec=None: {
            "missing": False,
            "stale": False,
            "error": "",
            "account_summary": {"orderable_cash_krw": 500_000.0},
            "open_orders": [
                {
                    "market": "US",
                    "ticker": "AMD",
                    "side": "buy",
                    "order_no": "us-open-1",
                    "remaining_qty": 1,
                    "risk_price_krw": 500_000.0,
                }
            ],
        }

        self.assertEqual(self.bot._market_budget_available("US"), 500_000.0)

        self.bot.pending_orders.append(
            {
                "market": "US",
                "ticker": "MSFT",
                "side": "buy",
                "order_no": "local-only-1",
                "remaining_qty": 1,
                "risk_price_krw": 100_000.0,
            }
        )
        self.assertEqual(self.bot._market_budget_available("US"), 400_000.0)


if __name__ == "__main__":
    unittest.main()
