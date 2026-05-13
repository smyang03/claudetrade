from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import recover_decisions_db


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "ml" / "schema.sql"


def _create_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


def _insert_row(
    path: Path,
    *,
    row_id: int,
    ticker: str,
    session_date: str,
    forward_1d: float | None = None,
    forward_3d: float | None = None,
    forward_5d: float | None = None,
    data_source: str = "live",
    is_simulated: int = 0,
) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            INSERT INTO decisions (
                id, ts, market, ticker, session_date, mode, decision,
                forward_1d, forward_3d, forward_5d, data_source, is_simulated
            ) VALUES (
                ?, '2026-05-13T09:00:00', 'KR', ?, ?, 'NEUTRAL', 'NO_SIGNAL',
                ?, ?, ?, ?, ?
            )
            """,
            (row_id, ticker, session_date, forward_1d, forward_3d, forward_5d, data_source, is_simulated),
        )
        conn.commit()
    finally:
        conn.close()


class RecoverDecisionsDbTests(unittest.TestCase):
    def test_apply_to_output_path_merges_only_verified_rows_and_keeps_prod_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "recovered.db"
            _create_schema(backup)
            _create_schema(current)

            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=86370, ticker="005930", session_date="2026-05-12", forward_1d=1.8, forward_3d=3.2, forward_5d=5.1)
            _insert_row(current, row_id=86371, ticker="LIVE", session_date="2026-05-12")
            _insert_row(current, row_id=86372, ticker="SIM", session_date="2026-05-12", is_simulated=1)

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=True,
                skip_forward_update=True,
            )

            self.assertTrue(output.exists())
            self.assertEqual(report["apply_mode"], "output_path")
            self.assertEqual(report["backup_rows_copied"], 1)
            self.assertEqual(report["current_rows_merged"], 1)
            self.assertEqual(report["fixture_rows_removed"], 0)

            conn = sqlite3.connect(str(output))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, session_date FROM decisions ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [(26, "BACK", "2026-04-03"), (86371, "LIVE", "2026-05-12")])

    def test_dry_run_does_not_create_output_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            output = temp / "dry_recovered.db"
            _create_schema(backup)
            _create_schema(current)
            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                output_path=output,
                apply=False,
                skip_forward_update=True,
            )

            self.assertFalse(output.exists())
            self.assertEqual(report["apply_mode"], "dry_run")
            self.assertEqual(report["backup_rows_copied"], 1)

    def test_apply_without_output_path_replaces_current_and_preserves_contaminated_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            backup = temp / "backup.db"
            current = temp / "current.db"
            _create_schema(backup)
            _create_schema(current)

            _insert_row(backup, row_id=26, ticker="BACK", session_date="2026-04-03")
            _insert_row(current, row_id=86370, ticker="005930", session_date="2026-05-12", forward_1d=1.8, forward_3d=3.2, forward_5d=5.1)
            _insert_row(current, row_id=86371, ticker="LIVE", session_date="2026-05-12")

            report = recover_decisions_db.recover(
                backup_path=backup,
                current_path=current,
                apply=True,
                skip_forward_update=True,
            )

            self.assertEqual(report["apply_mode"], "production_replace")
            self.assertEqual(report["output_path"], str(current.resolve()))
            self.assertTrue(report["preserved_current_files"])
            self.assertTrue(Path(report["preserved_current_files"][0]).exists())

            conn = sqlite3.connect(str(current))
            try:
                rows = conn.execute(
                    "SELECT id, ticker, session_date FROM decisions ORDER BY id"
                ).fetchall()
            finally:
                conn.close()
            self.assertEqual(rows, [(26, "BACK", "2026-04-03"), (86371, "LIVE", "2026-05-12")])


if __name__ == "__main__":
    unittest.main()
