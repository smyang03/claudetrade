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


if __name__ == "__main__":
    unittest.main()
