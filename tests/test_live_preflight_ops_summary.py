from __future__ import annotations

import unittest
from unittest.mock import patch

from tools import live_preflight


class LivePreflightOpsSummaryTests(unittest.TestCase):
    def test_order_unknown_review_ignores_recovered_raw_lifecycle_events(self) -> None:
        def fake_summary(*, market: str, runtime_mode: str, session_date: str) -> dict:
            return {
                "path_b_live": {
                    "path_comparison": {},
                    "readiness": {},
                    "live_truth_verdict": {},
                    "execution_capacity": {},
                    "order_unknown": [],
                },
                "lifecycle": {
                    "order_unknown": [{"ticker": "SMCI", "event_type": "ORDER_UNKNOWN"}],
                    "closed": [{"ticker": "SMCI", "event_type": "CLOSED"}],
                },
                "broker_truth": {"markets": {market: {}}},
            }

        with patch("interface.v2_ops_summary.build_v2_ops_summary", side_effect=fake_summary):
            checks = live_preflight._ops_summary_checks("live")

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["us.today_order_unknown_review"].status, "PASS")
        self.assertEqual(by_name["kr.today_order_unknown_review"].status, "PASS")

    def test_order_unknown_review_warns_on_unresolved_pathb_rows(self) -> None:
        def fake_summary(*, market: str, runtime_mode: str, session_date: str) -> dict:
            unresolved = [{"ticker": "SMCI", "status": "ORDER_UNKNOWN"}] if market == "US" else []
            return {
                "path_b_live": {
                    "path_comparison": {},
                    "readiness": {},
                    "live_truth_verdict": {},
                    "execution_capacity": {},
                    "order_unknown": unresolved,
                },
                "lifecycle": {"order_unknown": [], "closed": []},
                "broker_truth": {"markets": {market: {}}},
            }

        with patch("interface.v2_ops_summary.build_v2_ops_summary", side_effect=fake_summary):
            checks = live_preflight._ops_summary_checks("live")

        by_name = {check.name: check for check in checks}
        self.assertEqual(by_name["us.today_order_unknown_review"].status, "WARN")
        self.assertEqual(by_name["us.today_order_unknown_review"].data["rows"], [{"ticker": "SMCI", "status": "ORDER_UNKNOWN"}])
        self.assertEqual(by_name["kr.today_order_unknown_review"].status, "PASS")


if __name__ == "__main__":
    unittest.main()
