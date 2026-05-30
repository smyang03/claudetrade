from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
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


def _insert_unknown(path: Path, path_run_id: str = "run-us-1", path_type: str = "claude_price") -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT INTO v2_path_runs (
                path_run_id, decision_id, path_type, market, runtime_mode,
                session_date, ticker, status, plan_json, created_at, updated_at
            ) VALUES (?, 'd1', ?, 'US', 'live', '2026-05-28',
                'SMCI', 'ORDER_UNKNOWN', '{}', '2026-05-28T01:00:00Z', '2026-05-28T01:00:00Z')
            """,
            (path_run_id, path_type),
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
            self.assertEqual(evaluated[0]["path_type"], "claude_price")
            self.assertTrue(evaluated[0]["broker_truth_market_present"])
            self.assertEqual(evaluated[0]["broker_open_order_count"], 0)
            self.assertEqual(evaluated[0]["broker_fill_count"], 0)
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

    def test_scan_and_apply_are_restricted_to_claude_price_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            _create_event_db(db_path)
            _insert_unknown(db_path, "run-pathb", "claude_price")
            _insert_unknown(db_path, "run-timing", "timing_adapter")

            rows = order_unknown_remediation._scan_rows(
                db_path,
                mode="live",
                market="US",
                session_before="2026-05-29",
            )
            self.assertEqual([row["path_run_id"] for row in rows], ["run-pathb"])
            self.assertEqual(rows[0]["path_type"], "claude_price")

            applied = order_unknown_remediation.apply_remediation(
                db_path,
                [
                    {
                        "path_run_id": "run-timing",
                        "path_type": "timing_adapter",
                        "market": "US",
                        "ticker": "SMCI",
                        "session_date": "2026-05-28",
                        "remediation_allowed": True,
                    }
                ],
            )
            self.assertEqual(applied, [])
            conn = sqlite3.connect(str(db_path))
            try:
                status = conn.execute(
                    "SELECT status FROM v2_path_runs WHERE path_run_id='run-timing'"
                ).fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(status, "ORDER_UNKNOWN")

    def test_non_pathb_row_is_blocked_even_if_evaluated_directly(self) -> None:
        evaluated = order_unknown_remediation.evaluate_rows(
            [
                {
                    "market": "US",
                    "runtime_mode": "live",
                    "session_date": "2026-05-28",
                    "ticker": "SMCI",
                    "path_run_id": "run-timing",
                    "path_type": "timing_adapter",
                    "status": "ORDER_UNKNOWN",
                    "plan_json": "{}",
                }
            ],
            mode="live",
            session_before="2026-05-29",
            exposure_by_path={},
            broker_snapshot={
                "markets": {
                    "US": {
                        "positions": [],
                        "open_orders": [],
                        "today_fills": [],
                        "last_success_at": "2026-05-29T00:00:00Z",
                    }
                }
            },
        )

        self.assertFalse(evaluated[0]["remediation_allowed"])
        self.assertIn("not_pathb_claude_price", evaluated[0]["remediation_blockers"])

    def test_missing_or_stale_broker_truth_blocks_remediation(self) -> None:
        rows = [
            {
                "market": "US",
                "runtime_mode": "live",
                "session_date": "2026-05-28",
                "ticker": "SMCI",
                "path_run_id": "run-us-1",
                "path_type": "claude_price",
                "status": "ORDER_UNKNOWN",
                "plan_json": "{}",
            }
        ]
        for broker_snapshot in (
            {"markets": {}},
            {"markets": {"US": {"stale": True, "last_success_at": "2026-05-29T00:00:00Z"}}},
            {"markets": {"US": {"error": "refresh_failed", "last_success_at": "2026-05-29T00:00:00Z"}}},
        ):
            evaluated = order_unknown_remediation.evaluate_rows(
                rows,
                mode="live",
                session_before="2026-05-29",
                exposure_by_path={},
                broker_snapshot=broker_snapshot,
            )
            self.assertFalse(evaluated[0]["remediation_allowed"])
            self.assertTrue(evaluated[0]["broker_truth_unavailable"])
            self.assertIn("broker_truth_unavailable", evaluated[0]["remediation_blockers"])

    def test_broker_truth_without_timestamp_blocks_remediation(self) -> None:
        evaluated = order_unknown_remediation.evaluate_rows(
            [
                {
                    "market": "US",
                    "runtime_mode": "live",
                    "session_date": "2026-05-28",
                    "ticker": "SMCI",
                    "path_run_id": "run-us-1",
                    "path_type": "claude_price",
                    "status": "ORDER_UNKNOWN",
                    "plan_json": "{}",
                }
            ],
            mode="live",
            session_before="2026-05-29",
            exposure_by_path={},
            broker_snapshot={
                "markets": {
                    "US": {
                        "positions": [],
                        "open_orders": [],
                        "today_fills": [],
                    }
                }
            },
        )

        self.assertFalse(evaluated[0]["remediation_allowed"])
        self.assertFalse(evaluated[0]["broker_truth_unavailable"])
        self.assertIn("broker_truth_timestamp_missing", evaluated[0]["remediation_blockers"])

    def test_buy_side_broker_evidence_blocks_remediation(self) -> None:
        rows = [
            {
                "market": "US",
                "runtime_mode": "live",
                "session_date": "2026-05-28",
                "ticker": "SMCI",
                "path_run_id": "run-us-1",
                "path_type": "claude_price",
                "status": "ORDER_UNKNOWN",
                "plan_json": "{}",
            }
        ]

        evaluated = order_unknown_remediation.evaluate_rows(
            rows,
            mode="live",
            session_before="2026-05-29",
            exposure_by_path={},
            broker_snapshot={
                "markets": {
                    "US": {
                        "positions": [],
                        "open_orders": [{"ticker": "SMCI", "side": "buy", "remaining_qty": 1}],
                        "today_fills": [{"ticker": "SMCI", "side": "buy", "filled_qty": 1}],
                        "last_success_at": "2026-05-29T00:00:00Z",
                    }
                }
            },
        )

        self.assertFalse(evaluated[0]["remediation_allowed"])
        self.assertTrue(evaluated[0]["broker_any_open_order_evidence"])
        self.assertTrue(evaluated[0]["broker_any_fill_evidence"])
        self.assertEqual(evaluated[0]["broker_open_order_count"], 1)
        self.assertEqual(evaluated[0]["broker_fill_count"], 1)
        self.assertIn("broker_open_order_present", evaluated[0]["remediation_blockers"])
        self.assertIn("broker_fill_present", evaluated[0]["remediation_blockers"])

    def test_local_or_current_session_evidence_blocks_remediation(self) -> None:
        item = {
            "market": "US",
            "runtime_mode": "live",
            "session_date": "2026-05-29",
            "ticker": "SMCI",
            "path_run_id": "run-us-2",
            "path_type": "claude_price",
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

    def test_remediation_import_does_not_load_live_preflight_helpers(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import tools.order_unknown_remediation; print('tools.live_preflight' in sys.modules)",
            ],
            cwd=Path(__file__).resolve().parents[1],
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(result.stdout.strip(), "False")


if __name__ == "__main__":
    unittest.main()
