from __future__ import annotations

import unittest

from tools.overnight_us_monitor import (
    _guardian_block_start_causes,
    _hold_advisor_cost_observation,
    _risk_axes,
)


class OvernightUsMonitorReportTests(unittest.TestCase):
    def test_guardian_block_start_causes_include_action_and_tool(self) -> None:
        causes = _guardian_block_start_causes(
            {
                "gate": "BLOCK_START",
                "findings": [
                    {"code": "db.pathb_stale_active_runs", "message": "stale PathB run"},
                    {"code": "broker_truth.us_stale_state", "message": "US broker truth stale"},
                ],
            },
            {},
            {},
        )

        by_code = {row["code"]: row for row in causes}
        self.assertEqual(by_code["db.pathb_stale_active_runs"]["risk_level"], "P1")
        self.assertTrue(by_code["broker_truth.us_stale_state"]["blocking"])
        self.assertIn("live_preflight", by_code["broker_truth.us_stale_state"]["remediation_tool"])

    def test_hold_advisor_cost_observation_separates_labels_and_bypass_contract(self) -> None:
        payload = _hold_advisor_cost_observation(
            {
                "by_label": {
                    "hold_advisor_bull": 3,
                    "hold_advisor_bear": 2,
                    "selection_rank": 1,
                }
            }
        )

        self.assertEqual(payload["observed_calls"], 5)
        self.assertEqual(payload["by_label"]["hold_advisor_bull"], 3)
        self.assertIn("pathb_auto_sell_hold_cooldown_guard", payload["safety_critical_cache_bypass"])

    def test_risk_axes_summarizes_manual_action_required(self) -> None:
        axes = _risk_axes(
            {
                "open_positions_count": 8,
                "protected_positions": [{"manual_reconciliation_required": True}, {}],
                "pending_sells": [{}],
                "order_unknown_event_count_us_total": 1,
                "guardian": {"gate": "BLOCK_START"},
                "broker_truth": {
                    "stale": True,
                    "missing": False,
                    "error": "",
                    "positions_count": 8,
                    "open_orders_count": 1,
                },
            }
        )

        self.assertEqual(axes["broker_positions"], 8)
        self.assertEqual(axes["broker_open_orders"], 1)
        self.assertEqual(axes["protected_positions"], 2)
        self.assertEqual(axes["manual_action_required"], 3)


if __name__ == "__main__":
    unittest.main()
