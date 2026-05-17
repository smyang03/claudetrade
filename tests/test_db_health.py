import sqlite3
import tempfile
import unittest
from pathlib import Path

from ml import db_health


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


class MLDbHealthTests(unittest.TestCase):
    def test_detects_fixture_contamination_and_sequence_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            _create_schema(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    """
                    INSERT INTO decisions (
                        id, ts, market, ticker, session_date, mode, decision,
                        forward_1d, forward_3d, forward_5d, data_source, is_simulated
                    ) VALUES (
                        90000, '2026-05-13T09:00:00', 'KR', '005930', '2026-05-12',
                        'NEUTRAL', 'NO_SIGNAL', 1.8, 3.2, 5.1, 'live', 0
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = db_health.check_db_health(db_path, read_only=True)

            self.assertFalse(result["ok"])
            self.assertEqual(result["total_rows"], 1)
            self.assertEqual(result["contamination"]["fixture_rows"], 1)
            self.assertTrue(result["suspicious_sequence_gap"])

    def test_known_gap_metadata_persists_when_rows_exist_inside_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            _create_schema(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    """
                    INSERT INTO decisions (
                        ts, market, ticker, session_date, mode, decision, data_source, is_simulated
                    ) VALUES (
                        '2026-04-10T09:00:00', 'KR', '123456', '2026-04-10',
                        'NEUTRAL', 'NO_SIGNAL', 'live', 0
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = db_health.check_db_health(db_path, read_only=True)

            self.assertTrue(result["gaps"]["known_unrecoverable_ranges"])
            self.assertEqual(result["gaps"]["rows_inside_known_gap"], 1)
            self.assertEqual(result["gaps"]["verified_recovery_rows_inside_known_gap"], 0)
            self.assertEqual(result["gaps"]["unexpected_rows_inside_known_gap"], 1)
            self.assertIn("accepted_known_gap", result["warnings"])
            self.assertIn("unexpected_rows_inside_known_gap", result["warnings"])

    def test_known_gap_verified_recovery_rows_are_not_unexpected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "decisions.db"
            _create_schema(db_path)
            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute(
                    """
                    INSERT INTO decisions (
                        ts, market, ticker, session_date, mode, decision, data_source, is_simulated
                    ) VALUES (
                        '2026-04-10T09:00:00', 'KR', '123456', '2026-04-10',
                        'NEUTRAL', 'NO_SIGNAL', 'live_verified_recovery', 0
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = db_health.check_db_health(db_path, read_only=True)

            self.assertEqual(result["gaps"]["rows_inside_known_gap"], 1)
            self.assertEqual(result["gaps"]["verified_recovery_rows_inside_known_gap"], 1)
            self.assertEqual(result["gaps"]["unexpected_rows_inside_known_gap"], 0)
            self.assertIn("accepted_known_gap", result["warnings"])
            self.assertNotIn("unexpected_rows_inside_known_gap", result["warnings"])


if __name__ == "__main__":
    unittest.main()
