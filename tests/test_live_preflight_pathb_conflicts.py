from __future__ import annotations

import unittest

from tools import live_preflight


class LivePreflightPathBConflictTests(unittest.TestCase):
    def test_still_held_order_unknown_is_recoverable_not_start_blocking(self) -> None:
        run = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "status": "ORDER_UNKNOWN",
            "session_date": "2026-05-15",
            "plan": {"session_end_unresolved": True},
        }
        exposure = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "qty": 12,
            "local_position_qty": 12,
            "local_sell_order_id": "0032123235",
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "SOFI", "qty": 12}],
                    "open_orders": [],
                    "today_fills": [],
                    "last_success_at": "2026-05-16T01:00:00+00:00",
                }
            }
        }

        item = dict(run)
        live_preflight._attach_exposure_evidence(item, {"path_sofi": exposure}, broker_snapshot)
        conflicts = live_preflight._pathb_broker_truth_conflicts(
            "live",
            {"path_sofi": run},
            broker_snapshot=broker_snapshot,
            exposure_by_path={"path_sofi": exposure},
        )

        self.assertTrue(item["pathb_recoverable_still_held"])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["suggested_action"], "recover_still_held")
        self.assertFalse(conflicts[0]["do_not_start"])
        self.assertFalse(conflicts[0]["manual_reconciliation_required"])

    def test_qty_mismatch_conflict_remains_start_blocking(self) -> None:
        run = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "status": "ORDER_UNKNOWN",
            "session_date": "2026-05-15",
            "plan": {},
        }
        exposure = {
            "market": "US",
            "ticker": "SOFI",
            "path_run_id": "path_sofi",
            "qty": 12,
            "local_position_qty": 12,
            "local_sell_order_id": "0032123235",
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "SOFI", "qty": 7}],
                    "open_orders": [],
                    "today_fills": [],
                }
            }
        }

        conflicts = live_preflight._pathb_broker_truth_conflicts(
            "live",
            {"path_sofi": run},
            broker_snapshot=broker_snapshot,
            exposure_by_path={"path_sofi": exposure},
        )

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["suggested_action"], "manual_review")
        self.assertTrue(conflicts[0]["do_not_start"])
        self.assertTrue(conflicts[0]["manual_reconciliation_required"])

    def test_acked_entry_with_matching_local_and_broker_position_is_recoverable(self) -> None:
        item = {
            "market": "US",
            "ticker": "MSFT",
            "path_run_id": "path_msft",
            "status": "ORDER_ACKED",
        }
        exposure = {
            "market": "US",
            "ticker": "MSFT",
            "path_run_id": "path_msft",
            "qty": 1,
            "local_position_qty": 1,
            "sources": ["local_position"],
        }
        broker_snapshot = {
            "markets": {
                "US": {
                    "positions": [{"ticker": "MSFT", "qty": 1}],
                    "open_orders": [],
                    "today_fills": [],
                }
            }
        }

        live_preflight._attach_exposure_evidence(item, {"path_msft": exposure}, broker_snapshot)

        self.assertTrue(item["pathb_recoverable_entry_holding"])
        self.assertFalse(item["pathb_recoverable_still_held"])


if __name__ == "__main__":
    unittest.main()
