from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from lifecycle.event_store import EventStore
from tools.live_preflight import REQUIRED_TABLE_COLUMNS, _db_checks, _pathb_broker_truth_conflicts


class LiveDbIntegrityTests(unittest.TestCase):
    def test_live_schema_check_passes(self) -> None:
        checks = [item for item in _db_checks() if item.name.startswith("db.schema.")]
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])

    def test_required_columns_exist_on_new_event_store(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            store = EventStore(Path(tmp) / "events.db")
            with store.connect() as conn:
                for table, required in REQUIRED_TABLE_COLUMNS.items():
                    found = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                    self.assertFalse(required - found, table)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_event_store_context_manager_closes_connection(self) -> None:
        tmp = tempfile.mkdtemp()
        try:
            store = EventStore(Path(tmp) / "events.db")
            with store.connect() as conn:
                conn.execute("SELECT 1").fetchone()

            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute("SELECT 1").fetchone()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_pathb_broker_truth_conflict_detects_missing_local_sell_order_evidence(self) -> None:
        conflicts = _pathb_broker_truth_conflicts(
            "live",
            {
                "path1": {
                    "market": "US",
                    "ticker": "SOFI",
                    "path_run_id": "path1",
                    "status": "ORDER_UNKNOWN",
                    "session_date": "2026-05-15",
                    "plan": {"session_end_unresolved": True},
                }
            },
            broker_snapshot={
                "markets": {
                    "US": {
                        "positions": [{"ticker": "SOFI", "qty": 12}],
                        "open_orders": [],
                        "today_fills": [],
                        "last_success_at": "2026-05-16T01:00:00+00:00",
                    }
                }
            },
            exposure_by_path={
                "path1": {
                    "market": "US",
                    "ticker": "SOFI",
                    "qty": 12,
                    "local_position_qty": 12,
                    "local_sell_order_id": "0032123235",
                }
            },
        )

        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["suggested_action"], "recover_still_held")
        self.assertFalse(conflicts[0]["do_not_start"])
        self.assertTrue(conflicts[0]["pathb_recoverable_still_held"])
        self.assertIn("local_sell_order_missing_from_broker_truth", conflicts[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
