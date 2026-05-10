from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from tools.pathb_legacy_remediation import build_report


class PathBLegacyRemediationTests(unittest.TestCase):
    def test_report_lists_legacy_candidates_without_mutating_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            store = EventStore(db_path)
            store.create_path_run(
                path_run_id="path_prev_unknown",
                decision_id="dec_prev_unknown",
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-09",
                ticker="005930",
                status="ORDER_UNKNOWN",
                plan={"order_unknown_resolution": "session_end_unresolved"},
            )
            store.create_path_run(
                path_run_id="path_current_unknown",
                decision_id="dec_current_unknown",
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-10",
                ticker="000660",
                status="ORDER_UNKNOWN",
                plan={},
            )
            store.create_path_run(
                path_run_id="path_prev_filled",
                decision_id="dec_prev_filled",
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-09",
                ticker="NVDA",
                status="FILLED",
                plan={},
            )
            store.create_path_run(
                path_run_id="path_prev_closed",
                decision_id="dec_prev_closed",
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-09",
                ticker="AMD",
                status="CLOSED",
                plan={},
            )
            store.append(
                LifecycleEvent(
                    event_type="CLAUDE_PRICE_PLAN_GATE_WARNING",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-09",
                    ticker="AMD",
                    decision_id="dec_prev_closed",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "claude_price"},
                )
            )

            report = build_report(
                db_path=db_path,
                mode="live",
                current_sessions={"KR": "2026-05-10", "US": "2026-05-10"},
            )

            self.assertTrue(report["dry_run"])
            self.assertFalse(report["write_supported"])
            self.assertEqual(report["order_unknown"]["current_count"], 1)
            self.assertEqual(report["order_unknown"]["previous_count"], 1)
            self.assertEqual(report["stale_active"]["count"], 2)
            self.assertEqual(report["stale_active"]["by_status"]["FILLED"], 1)
            self.assertEqual(report["stale_active"]["by_status"]["ORDER_UNKNOWN"], 1)
            self.assertGreaterEqual(report["lifecycle_consistency"]["missing_events_count"], 2)
            self.assertEqual(report["lifecycle_consistency"]["events_missing_path_run_id_count"], 1)
            plan = report["remediation_plan"]
            self.assertTrue(plan["dry_run_only"])
            self.assertFalse(plan["production_writes_supported"])
            self.assertTrue(plan["requires_broker_truth"])
            self.assertEqual(plan["summary"]["order_unknown_items"], 2)
            self.assertEqual(plan["summary"]["stale_active_items"], 2)
            self.assertGreaterEqual(plan["summary"]["missing_lifecycle_event_items"], 2)
            self.assertEqual(plan["summary"]["event_payload_link_items"], 1)
            self.assertFalse(plan["order_unknown"][0]["production_write"])
            self.assertEqual(store.find_path_run("path_prev_unknown")["status"], "ORDER_UNKNOWN")
            self.assertEqual(store.find_path_run("path_prev_filled")["status"], "FILLED")


if __name__ == "__main__":
    unittest.main()
