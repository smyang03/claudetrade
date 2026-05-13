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


if __name__ == "__main__":
    unittest.main()
