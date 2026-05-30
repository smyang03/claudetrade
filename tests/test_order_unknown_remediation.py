from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import live_preflight
from tools import order_unknown_remediation


def _create_event_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE v2_path_runs (
                path_run_id TEXT PRIMARY KEY,
                decision_id TEXT,
                path_type TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                status TEXT,
                plan_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert_unknown(path: Path, path_run_id: str = "run-us-1") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT INTO v2_path_runs (
                path_run_id, decision_id, path_type, market, runtime_mode,
                session_date, ticker, status, plan_json, created_at, updated_at
            ) VALUES (?, 'd1', 'claude_price', 'US', 'live', '2026-05-28',
                'SMCI', 'ORDER_UNKNOWN', '{}', '2026-05-28T01:00:00Z', '2026-05-28T01:00:00Z')
            """,
            (path_run_id,),
        )
        conn.commit()
    finally:
        conn.close()


class OrderUnknownRemediationTests(unittest.TestCase):
    def test_previous_session_no_exposure_is_eligible_and_apply_cancels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            _create_event_db(db_path)
            _insert_unknown(db_path)
            rows = order_unknown_remediation._scan_rows(
                db_path,
                mode="live",
                market="US",
                session_before="2026-05-29",
            )
            broker_snapshot = {
                "markets": {
                    "US": {
                        "positions": [],
                        "open_orders": [],
                        "today_fills": [],
                        "last_success_at": "2026-05-29T00:00:00Z",
                    }
                }
            }

            evaluated = order_unknown_remediation.evaluate_rows(
                rows,
                mode="live",
                session_before="2026-05-29",
                exposure_by_path={},
                broker_snapshot=broker_snapshot,
            )
            self.assertEqual(len(evaluated), 1)
            self.assertTrue(evaluated[0]["remediation_allowed"])
            self.assertEqual(evaluated[0]["planned_status"], "CANCELLED")

            applied = order_unknown_remediation.apply_remediation(db_path, evaluated)
            self.assertEqual(len(applied), 1)
            conn = sqlite3.connect(str(db_path))
            try:
                status, plan_json = conn.execute(
                    "SELECT status, plan_json FROM v2_path_runs WHERE path_run_id='run-us-1'"
                ).fetchone()
            finally:
                conn.close()
            plan = json.loads(plan_json)
            self.assertEqual(status, "CANCELLED")
            self.assertTrue(plan["audited_remediation"])
            self.assertEqual(plan["order_unknown_resolution"], "audited_no_exposure_previous_session")

    def test_local_or_current_session_evidence_blocks_remediation(self) -> None:
        item = {
            "market": "US",
            "runtime_mode": "live",
            "session_date": "2026-05-29",
            "ticker": "SMCI",
            "path_run_id": "run-us-2",
            "status": "ORDER_UNKNOWN",
            "local_exposure": True,
            "local_position_qty": 1,
            "broker_truth_unavailable": False,
            "broker_position_qty": 0,
            "broker_open_order_evidence": False,
            "broker_sell_fill_evidence": False,
        }

        live_preflight._mark_order_unknown_remediation_hint(
            item,
            mode="live",
            previous_session=False,
            session_before="2026-05-29",
        )

        self.assertFalse(item["remediation_allowed"])
        self.assertIn("current_session_order_unknown", item["remediation_blockers"])
        self.assertIn("local_exposure_present", item["remediation_blockers"])

    def test_cli_rejects_ambiguous_apply_modes(self) -> None:
        self.assertEqual(
            order_unknown_remediation.main(["--mode", "live", "--market", "US", "--apply", "--dry-run"]),
            2,
        )
        self.assertEqual(
            order_unknown_remediation.main(["--mode", "live", "--market", "US", "--apply"]),
            2,
        )


if __name__ == "__main__":
    unittest.main()
