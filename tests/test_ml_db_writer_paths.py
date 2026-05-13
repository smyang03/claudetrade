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


if __name__ == "__main__":
    unittest.main()
