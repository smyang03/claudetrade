import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ml import forward_updater


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


def _insert_decision(path: Path, *, ticker: str, session_date: str) -> int:
    conn = sqlite3.connect(str(path))
    try:
        cur = conn.execute(
            """
            INSERT INTO decisions (
                ts, market, ticker, session_date, mode, decision, data_source, is_simulated
            ) VALUES (
                '2026-05-13T09:00:00', 'KR', ?, ?, 'NEUTRAL', 'NO_SIGNAL', 'live', 0
            )
            """,
            (ticker, session_date),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        conn.close()


def _write_price_csv(price_root: Path, ticker: str, rows: list[tuple[str, float]]) -> None:
    target = price_root / "kr" / f"kr_{ticker}.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "volume": [1000 for _ in rows],
        }
    ).to_csv(target, index=False)


class ForwardUpdaterTests(unittest.TestCase):
    def test_env_path_skip_reasons_and_partial_updates(self):
        self.assertIn("_resolve_db_path", forward_updater._get_conn.__code__.co_names)

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            db_path = temp / "decisions.db"
            price_root = temp / "price"
            _create_schema(db_path)

            partial_id = _insert_decision(db_path, ticker="PART", session_date="2026-05-01")
            missing_id = _insert_decision(db_path, ticker="MISS", session_date="2026-05-06")
            bad_id = _insert_decision(db_path, ticker="BAD", session_date="2026-05-01")
            nan_id = _insert_decision(db_path, ticker="NAN", session_date="2026-05-01")

            _write_price_csv(
                price_root,
                "PART",
                [("2026-05-01", 100.0), ("2026-05-04", 110.0)],
            )
            _write_price_csv(
                price_root,
                "MISS",
                [("2026-05-01", 100.0), ("2026-05-04", 101.0)],
            )
            _write_price_csv(
                price_root,
                "BAD",
                [("2026-05-01", 0.0), ("2026-05-04", 100.0), ("2026-05-05", 101.0)],
            )
            _write_price_csv(
                price_root,
                "NAN",
                [("2026-05-01", 100.0), ("2026-05-04", float("nan"))],
            )

            forward_updater._price_cache.clear()
            with patch.dict(os.environ, {"ML_DECISIONS_DB_PATH": str(db_path)}), patch.object(
                forward_updater, "_PRICE_DIR", price_root
            ):
                summary = forward_updater.run(market="KR", dry_run=False, forward_days=(1, 3, 5))

            self.assertEqual(summary["updated"], 1)
            self.assertEqual(summary["partial_updated"], 1)
            self.assertEqual(summary["skipped"], 3)
            self.assertEqual(summary["missing_csv"], 0)
            self.assertEqual(summary["malformed_csv"], 2)
            self.assertEqual(summary["skip_by"]["stale_csv"], 1)
            self.assertEqual(summary["skip_by"]["malformed_csv"], 2)

            conn = sqlite3.connect(str(db_path))
            try:
                rows = {
                    row[0]: row[1:]
                    for row in conn.execute(
                        "SELECT id, forward_1d, forward_3d, forward_5d FROM decisions"
                    )
                }
            finally:
                conn.close()
            self.assertEqual(rows[partial_id], (10.0, None, None))
            self.assertEqual(rows[missing_id], (None, None, None))
            self.assertEqual(rows[bad_id], (None, None, None))
            self.assertEqual(rows[nan_id], (None, None, None))


if __name__ == "__main__":
    unittest.main()
