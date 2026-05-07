from __future__ import annotations

import unittest

from runtime.sizing_contract import calculate_order_quantity, probe_stop_weight


class SizingContractTests(unittest.TestCase):
    def test_min_order_override_cannot_exceed_hard_cap(self) -> None:
        decision = calculate_order_quantity(
            price=120_000,
            base_budget=100_000,
            hard_budget_cap=100_000,
            cash_available=1_000_000,
            min_order=100_000,
            size_intent="normal",
        )

        self.assertEqual(decision.qty, 0)
        self.assertEqual(decision.blocker, "high_price_one_share_blocked")

    def test_high_price_one_share_reason_is_not_qty_zero(self) -> None:
        decision = calculate_order_quantity(
            price=300_000,
            base_budget=200_000,
            hard_budget_cap=200_000,
            cash_available=1_000_000,
            min_order=0,
            size_intent="normal",
        )

        self.assertEqual(decision.blocker, "high_price_one_share_blocked")

        decision = calculate_order_quantity(
            price=300_000,
            base_budget=200_000,
            hard_budget_cap=200_000,
            cash_available=1_000_000,
            min_order=200_000,
            size_intent="normal",
        )

        self.assertEqual(decision.blocker, "high_price_one_share_blocked")

    def test_one_share_over_budget_can_be_limited_by_account_pct(self) -> None:
        decision = calculate_order_quantity(
            price=300_000,
            base_budget=200_000,
            hard_budget_cap=200_000,
            cash_available=1_000_000,
            min_order=200_000,
            allow_one_share_over_budget=True,
            one_share_max_account_pct=7.0,
            total_equity=10_000_000,
        )

        self.assertEqual(decision.qty, 1)
        self.assertIn("one_share_over_budget_allowed", decision.warnings)

    def test_size_cap_applies_to_budget_once(self) -> None:
        decision = calculate_order_quantity(
            price=10_000,
            base_budget=200_000,
            hard_budget_cap=200_000,
            cash_available=1_000_000,
            size_intent="normal",
            size_cap_pct=50,
        )

        self.assertEqual(decision.qty, 10)
        self.assertEqual(decision.effective_budget, 100_000)

    def test_probe_stop_weight_is_fractional(self) -> None:
        self.assertEqual(probe_stop_weight(order_notional=30_000, normal_order_notional=100_000), 0.3)
        self.assertEqual(probe_stop_weight(order_notional=120_000, normal_order_notional=100_000), 1.0)


if __name__ == "__main__":
    unittest.main()
