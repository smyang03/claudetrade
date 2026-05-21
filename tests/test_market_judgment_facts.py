from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.build_market_judgment_facts import (
    build_market_judgment_facts,
    init_schema,
    parse_market_judgment_file,
)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _write_log(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class MarketJudgmentFactTests(unittest.TestCase):
    def test_parse_log_with_actual_result_sets_direction_and_hit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_20260520_US.json"
            _write_log(
                path,
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "MILD_BULL", "unanimous_direction": "bull"},
                    "actual_result": {"market_change": 1.25},
                },
            )

            row = parse_market_judgment_file(path, now="2026-05-21T00:00:00+00:00")

        self.assertEqual(row["market_key"], "2026-05-20:US")
        self.assertEqual(row["consensus_dir"], "bull")
        self.assertEqual(row["actual_dir"], "bull")
        self.assertEqual(row["hit"], 1)
        self.assertEqual(row["parse_status"], "OK")

    def test_missing_actual_result_is_partial_not_inferred_from_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_20260520_KR.json"
            _write_log(
                path,
                {
                    "date": "2026-05-20",
                    "market": "KR",
                    "consensus": {"mode": "DEFENSIVE", "unanimous_direction": "bear"},
                    "digest_raw": {"context": {"kospi": {"change_pct": -2.5}}},
                },
            )

            row = parse_market_judgment_file(path, now="2026-05-21T00:00:00+00:00")

        self.assertEqual(row["consensus_dir"], "bear")
        self.assertEqual(row["actual_dir"], "")
        self.assertIsNone(row["hit"])
        self.assertEqual(row["parse_status"], "PARTIAL_ACTUAL_MISSING")

    def test_build_market_judgment_facts_writes_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            db = root / "facts.db"
            init_schema(db)
            _write_log(
                logs / "live_20260520_US.json",
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "MILD_BULL", "unanimous_direction": "bull"},
                    "actual_result": {"market_change": -0.5},
                },
            )

            first = build_market_judgment_facts(db_path=db, logs_dir=logs, date="2026-05-20", market="US")
            second = build_market_judgment_facts(db_path=db, logs_dir=logs, date="2026-05-20", market="US")

            with closing(_connect(db)) as conn:
                rows = conn.execute("SELECT * FROM fact_market_judgment").fetchall()

        self.assertEqual(first["facts_written"], 1)
        self.assertEqual(second["facts_written"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hit"], 0)


if __name__ == "__main__":
    unittest.main()
