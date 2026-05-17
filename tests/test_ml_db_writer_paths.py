import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ml import db_writer


ROOT = Path(__file__).resolve().parents[1]
PROD_DB = (ROOT / "data" / "ml" / "decisions.db").resolve()
SCHEMA = ROOT / "ml" / "schema.sql"


def _create_schema(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()


class MLDbWriterPathTests(unittest.TestCase):
    def test_writer_uses_env_override_without_touching_prod_db(self):
        self.assertTrue(hasattr(db_writer, "_resolve_db_path"))

        before = PROD_DB.stat().st_mtime_ns if PROD_DB.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            temp_db = Path(tmp) / "nested" / "decisions_test.db"
            _create_schema(temp_db)

            with patch.dict(os.environ, {"ML_DECISIONS_DB_PATH": str(temp_db)}):
                self.assertEqual(db_writer._resolve_db_path(), temp_db.resolve())
                decision_id = db_writer.write_decision(
                    {
                        "market": "KR",
                        "ticker": "123456",
                        "session_date": "2026-05-13",
                        "mode": "NEUTRAL",
                        "decision": "NO_SIGNAL",
                        "data_source": "live",
                    }
                )

            self.assertGreater(decision_id, 0)
            conn = sqlite3.connect(str(temp_db))
            try:
                count = conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 1)

        after = PROD_DB.stat().st_mtime_ns if PROD_DB.exists() else None
        self.assertEqual(before, after)

    def test_resolve_db_path_expands_and_resolves_override(self):
        self.assertTrue(hasattr(db_writer, "_resolve_db_path"))
        with tempfile.TemporaryDirectory() as tmp:
            rel = Path(tmp) / ".." / Path(tmp).name / "decisions.db"
            with patch.dict(os.environ, {"ML_DECISIONS_DB_PATH": str(rel)}):
                self.assertEqual(db_writer._resolve_db_path(), rel.expanduser().resolve())

    def test_load_for_ml_defaults_to_live_non_sim_outside_known_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            temp_db = Path(tmp) / "decisions.db"
            _create_schema(temp_db)
            conn = sqlite3.connect(str(temp_db))
            try:
                rows = [
                    ("KR", "LIVE", "2026-05-12", "live", 0),
                    ("KR", "BACKFILL", "2026-05-12", "backfill", 0),
                    ("KR", "SIM", "2026-05-12", "live", 1),
                    ("KR", "GAP", "2026-04-10", "live", 0),
                    ("KR", "RECOVERY", "2026-04-10", "live_verified_recovery", 0),
                    ("KR", "RECOVERY_SIM", "2026-04-10", "live_verified_recovery", 1),
                ]
                for market, ticker, session_date, data_source, is_simulated in rows:
                    conn.execute(
                        """
                        INSERT INTO decisions (
                            ts, market, ticker, session_date, mode, decision, data_source, is_simulated
                        ) VALUES (
                            '2026-05-12T09:00:00', ?, ?, ?, 'NEUTRAL', 'NO_SIGNAL', ?, ?
                        )
                        """,
                        (market, ticker, session_date, data_source, is_simulated),
                    )
                conn.commit()
            finally:
                conn.close()

            with patch.dict(os.environ, {"ML_DECISIONS_DB_PATH": str(temp_db)}):
                safe = db_writer.load_for_ml(market="KR")
                all_rows = db_writer.load_for_ml(market="KR", live_only=False)

            self.assertEqual(safe["ticker"].tolist(), ["LIVE", "RECOVERY"])
            self.assertEqual(
                set(all_rows["ticker"]),
                {"LIVE", "BACKFILL", "SIM", "GAP", "RECOVERY", "RECOVERY_SIM"},
            )


if __name__ == "__main__":
    unittest.main()
