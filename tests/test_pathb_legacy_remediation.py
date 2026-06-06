from __future__ import annotations

import json
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
            store.create_path_run(
                path_run_id="path_current_partial",
                decision_id="dec_current_partial",
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-10",
                ticker="035420",
                status="PARTIAL_FILLED",
                plan={},
            )
            store.create_path_run(
                path_run_id="timing_current_unknown",
                decision_id="dec_timing_current_unknown",
                path_type="timing_adapter",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-10",
                ticker="051910",
                status="ORDER_UNKNOWN",
                plan={},
            )
            store.create_path_run(
                path_run_id="timing_prev_active",
                decision_id="dec_timing_prev_active",
                path_type="timing_adapter",
                market="US",
                runtime_mode="live",
                session_date="2026-05-09",
                ticker="MSFT",
                status="FILLED",
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
            store.append(
                LifecycleEvent(
                    event_type="PARTIAL_FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-05-10",
                    ticker="035420",
                    decision_id="dec_current_partial",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "claude_price", "path_run_id": "path_current_partial"},
                )
            )
            truth_path = Path(tmp) / "broker_truth.json"
            truth_path.write_text(
                json.dumps(
                    {
                        "generated_at": "2026-05-10T01:00:00Z",
                        "markets": {
                            "KR": {
                                "fresh": True,
                                "trusted": True,
                                "positions": [{"ticker": "005930", "qty": 3}],
                                "open_orders": [{"ticker": "000660", "remaining_qty": 1}],
                                "today_fills": [{"ticker": "005930", "side": "buy"}],
                            },
                            "US": {
                                "fresh": True,
                                "trusted": True,
                                "positions": [],
                                "open_orders": [],
                                "today_fills": [],
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            report = build_report(
                db_path=db_path,
                mode="live",
                current_sessions={"KR": "2026-05-10", "US": "2026-05-10"},
                broker_truth_path=truth_path,
            )

            self.assertTrue(report["dry_run"])
            self.assertFalse(report["write_supported"])
            self.assertEqual(report["order_unknown"]["current_count"], 1)
            self.assertEqual(report["order_unknown"]["previous_count"], 1)
            self.assertEqual(report["stale_active"]["count"], 2)
            self.assertEqual(report["stale_active"]["by_status"]["FILLED"], 1)
            self.assertEqual(report["stale_active"]["by_status"]["ORDER_UNKNOWN"], 1)
            self.assertTrue(report["broker_truth"]["snapshot_loaded"])
            current_unknown = report["order_unknown"]["current_session"][0]
            previous_unknown = report["order_unknown"]["previous_session"][0]
            self.assertTrue(current_unknown["do_not_start"])
            self.assertEqual(current_unknown["broker_truth_evidence"]["open_order_count"], 1)
            self.assertEqual(previous_unknown["broker_truth_evidence"]["position_qty"], 3.0)
            self.assertEqual(previous_unknown["broker_truth_evidence"]["today_fill_count"], 1)
            self.assertIn("lifecycle_window_consistency", report)
            self.assertIn("lifecycle_full_consistency", report)
            self.assertGreaterEqual(report["lifecycle_consistency"]["missing_events_count"], 2)
            self.assertEqual(report["lifecycle_consistency"]["events_missing_path_run_id_count"], 1)
            self.assertEqual(report["lifecycle_window_consistency"]["pathb_pre_run_events_missing_path_run_id_count"], 1)
            self.assertEqual(report["lifecycle_full_consistency"]["missing_events_count"], 2)
            order_unknown_ids = {
                item["path_run_id"]
                for item in report["order_unknown"]["current_session"] + report["order_unknown"]["previous_session"]
            }
            stale_ids = {item["path_run_id"] for item in report["stale_active"]["rows"]}
            missing_event_ids = {item["path_run_id"] for item in report["lifecycle_consistency"]["missing_events"]}
            self.assertNotIn("timing_current_unknown", order_unknown_ids)
            self.assertNotIn("timing_prev_active", stale_ids)
            self.assertNotIn("path_current_partial", missing_event_ids)
            plan = report["remediation_plan"]
            self.assertTrue(plan["dry_run_only"])
            self.assertFalse(plan["production_writes_supported"])
            self.assertTrue(plan["requires_broker_truth"])
            self.assertEqual(plan["summary"]["order_unknown_items"], 2)
            self.assertEqual(plan["summary"]["stale_active_items"], 2)
            self.assertGreaterEqual(plan["summary"]["missing_lifecycle_event_items"], 2)
            self.assertEqual(plan["summary"]["event_payload_link_items"], 1)
            self.assertIn("apply_eligible_items", plan["summary"])
            self.assertFalse(plan["order_unknown"][0]["production_write"])
            by_path = {item["path_run_id"]: item for item in plan["order_unknown"] + plan["stale_active"]}
            self.assertFalse(by_path["path_current_unknown"]["apply_eligible"])
            self.assertEqual(
                by_path["path_current_unknown"]["apply_block_reason"],
                "current_session_operator_review_required",
            )
            self.assertFalse(by_path["path_prev_unknown"]["apply_eligible"])
            self.assertEqual(by_path["path_prev_unknown"]["apply_block_reason"], "broker_exposure_or_fill_found")
            self.assertTrue(by_path["path_prev_filled"]["apply_eligible"])
            self.assertEqual(store.find_path_run("path_prev_unknown")["status"], "ORDER_UNKNOWN")
            self.assertEqual(store.find_path_run("path_prev_filled")["status"], "FILLED")

    def test_report_flags_cross_run_closed_lifecycle_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            store = EventStore(db_path)
            store.create_path_run(
                path_run_id="path_previous",
                decision_id="dec_nbis",
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-28",
                ticker="NBIS",
                status="CLOSED",
                plan={},
            )
            store.create_path_run(
                path_run_id="path_current",
                decision_id="dec_nbis",
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-28",
                ticker="NBIS",
                status="CLOSED",
                plan={
                    "pathb_closed_lifecycle_evidence": {
                        "path_run_id": "path_previous",
                        "event_id": 3915,
                        "execution_id": "0031323229",
                    },
                    "exit_execution_id": "0031323229",
                    "close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
                },
            )

            report = build_report(
                db_path=db_path,
                mode="live",
                current_sessions={"KR": "2026-05-29", "US": "2026-05-29"},
            )

            self.assertEqual(report["cross_run_closed_lifecycle_evidence"]["count"], 1)
            row = report["cross_run_closed_lifecycle_evidence"]["rows"][0]
            self.assertEqual(row["path_run_id"], "path_current")
            self.assertEqual(row["evidence_path_run_id"], "path_previous")
            self.assertEqual(report["remediation_plan"]["summary"]["cross_run_closed_evidence_items"], 1)
            candidate = report["remediation_plan"]["cross_run_closed_evidence_candidates"][0]
            self.assertFalse(candidate["production_write"])
            self.assertIn("broker_fills", candidate["source_of_truth_required"])


if __name__ == "__main__":
    unittest.main()
