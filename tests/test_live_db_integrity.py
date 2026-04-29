from __future__ import annotations

from pathlib import Path
import shutil
import tempfile
import unittest

from lifecycle.event_store import EventStore
from tools.live_preflight import REQUIRED_TABLE_COLUMNS, _db_checks


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


if __name__ == "__main__":
    unittest.main()
