from __future__ import annotations

import unittest
from unittest.mock import patch

from decision.claude_price_plan import parse_plan_from_claude


BASE_RAW = {
    "buy_zone_low": "52,000",
    "buy_zone_high": "52,500",
    "sell_target": "54,500",
    "stop_loss": "51,000",
    "hold_days": 1,
    "confidence": 0.7,
    "entry_rationale": "support pullback",
    "exit_rationale": "resistance",
    "entry_basis_tags": ["support"],
    "exit_basis_tags": ["resistance"],
}


class PathBPlanTests(unittest.TestCase):
    def test_parse_valid_string_prices(self) -> None:
        with patch.dict("os.environ", {"KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": "1.05"}, clear=False):
            plan, errors = parse_plan_from_claude(
                decision_id="dec1",
                ticker="005930",
                market="KR",
                session_date="2026-04-27",
                raw=BASE_RAW,
            )

        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        self.assertEqual(plan.buy_zone_low, 52000.0)
        self.assertTrue(plan.path_run_id.startswith("path_20260427_KR_005930_claude_price_"))
        self.assertEqual(plan.cancel_if_open_above, 55125.0)

    def test_parse_preserves_explicit_cancel_if_open_above(self) -> None:
        raw = {**BASE_RAW, "cancel_if_open_above": "53,500"}
        plan, errors = parse_plan_from_claude(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            raw=raw,
        )

        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        self.assertEqual(plan.cancel_if_open_above, 53500.0)

    def test_parse_uses_market_cancel_above_multiplier_override(self) -> None:
        with patch.dict("os.environ", {"KR_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": "1.10"}, clear=False):
            plan, errors = parse_plan_from_claude(
                decision_id="dec1",
                ticker="005930",
                market="KR",
                session_date="2026-04-27",
                raw=BASE_RAW,
            )

        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan.cancel_if_open_above, 57750.0)

    def test_rejects_reversed_stop(self) -> None:
        raw = {**BASE_RAW, "stop_loss": 53000}
        plan, errors = parse_plan_from_claude(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            raw=raw,
        )

        self.assertIsNone(plan)
        self.assertIn("stop_loss_not_below_buy_zone", errors)

    def test_rejects_target_below_zone(self) -> None:
        raw = {**BASE_RAW, "sell_target": 52400}
        plan, errors = parse_plan_from_claude(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            raw=raw,
        )

        self.assertIsNone(plan)
        self.assertIn("sell_target_not_above_buy_zone", errors)

    def test_rejects_low_confidence_and_missing_fields(self) -> None:
        raw = {**BASE_RAW, "confidence": 0.2}
        plan, errors = parse_plan_from_claude(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            raw=raw,
        )
        self.assertIsNone(plan)
        self.assertIn("confidence_below_minimum", errors)

        plan, errors = parse_plan_from_claude(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            raw={"buy_zone_low": 1},
        )
        self.assertIsNone(plan)
        self.assertIn("missing_buy_zone_high", errors)


if __name__ == "__main__":
    unittest.main()
