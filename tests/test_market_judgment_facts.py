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

        self.assertEqual(row["market_key"], "live:2026-05-20:US")
        self.assertEqual(row["runtime_mode"], "live")
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
        self.assertEqual(rows[0]["market_key"], "live:2026-05-20:US")
        self.assertEqual(rows[0]["runtime_mode"], "live")
        self.assertEqual(rows[0]["hit"], 0)

    def test_build_market_judgment_facts_keeps_live_and_paper_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            db = root / "facts.db"
            _write_log(
                logs / "live_20260520_US.json",
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "MILD_BULL", "unanimous_direction": "bull"},
                    "actual_result": {"market_change": 1.25},
                },
            )
            _write_log(
                logs / "paper_20260520_US.json",
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "DEFENSIVE", "unanimous_direction": "bear"},
                    "actual_result": {"market_change": -0.5},
                },
            )

            live = build_market_judgment_facts(
                db_path=db,
                logs_dir=logs,
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )
            paper = build_market_judgment_facts(
                db_path=db,
                logs_dir=logs,
                date="2026-05-20",
                market="US",
                runtime_mode="paper",
            )

            with closing(_connect(db)) as conn:
                rows = conn.execute(
                    "SELECT market_key, runtime_mode, consensus_mode FROM fact_market_judgment ORDER BY runtime_mode"
                ).fetchall()

        self.assertEqual(live["facts_written"], 1)
        self.assertEqual(paper["facts_written"], 1)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["market_key"] for row in rows},
            {"live:2026-05-20:US", "paper:2026-05-20:US"},
        )
        self.assertEqual({row["runtime_mode"] for row in rows}, {"live", "paper"})

    def test_build_market_judgment_facts_does_not_fallback_to_other_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            db = root / "facts.db"
            _write_log(
                logs / "live_20260520_US.json",
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "MILD_BULL", "unanimous_direction": "bull"},
                    "actual_result": {"market_change": 1.25},
                },
            )

            summary = build_market_judgment_facts(
                db_path=db,
                logs_dir=logs,
                date="2026-05-20",
                market="US",
                runtime_mode="paper",
                dry_run=True,
            )

        self.assertEqual(summary["runtime_mode"], "paper")
        self.assertEqual(summary["files_seen"], 0)
        self.assertEqual(summary["facts_generated"], 0)

    def test_init_schema_adds_runtime_mode_to_legacy_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            with closing(_connect(db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_market_judgment (
                        market_key TEXT PRIMARY KEY,
                        session_date TEXT NOT NULL,
                        market TEXT NOT NULL,
                        parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source_quality TEXT NOT NULL DEFAULT 'unknown',
                        source_refs_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO fact_market_judgment (
                        market_key, session_date, market, parse_status,
                        created_at, updated_at, source_quality, source_refs_json
                    )
                    VALUES ('2026-05-20:US', '2026-05-20', 'US', 'OK', 't0', 't0', 'complete', '{}')
                    """
                )
                conn.commit()

            init_schema(db)

            with closing(_connect(db)) as conn:
                columns = {row["name"] for row in conn.execute("PRAGMA table_info(fact_market_judgment)").fetchall()}
                row = conn.execute("SELECT runtime_mode, market_key FROM fact_market_judgment").fetchone()

        self.assertIn("runtime_mode", columns)
        self.assertIn("consensus_mode", columns)
        self.assertEqual(row["runtime_mode"], "live")
        self.assertEqual(row["market_key"], "live:2026-05-20:US")

    def test_build_market_judgment_facts_migrates_legacy_key_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logs = root / "logs"
            logs.mkdir()
            db = root / "facts.db"
            with closing(_connect(db)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_market_judgment (
                        market_key TEXT PRIMARY KEY,
                        session_date TEXT NOT NULL,
                        market TEXT NOT NULL,
                        consensus_mode TEXT,
                        consensus_dir TEXT,
                        actual_dir TEXT,
                        market_change_pct REAL,
                        hit INTEGER,
                        parse_status TEXT NOT NULL DEFAULT 'UNKNOWN',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        source_quality TEXT NOT NULL DEFAULT 'unknown',
                        source_refs_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO fact_market_judgment (
                        market_key, session_date, market, consensus_mode,
                        consensus_dir, actual_dir, market_change_pct, hit,
                        parse_status, created_at, updated_at, source_quality, source_refs_json
                    )
                    VALUES (
                        '2026-05-20:US', '2026-05-20', 'US', 'OLD',
                        'bull', 'bull', 1.0, 1,
                        'OK', 't0', 't0', 'complete', '{}'
                    )
                    """
                )
                conn.commit()
            _write_log(
                logs / "live_20260520_US.json",
                {
                    "date": "2026-05-20",
                    "market": "US",
                    "consensus": {"mode": "MILD_BULL", "unanimous_direction": "bull"},
                    "actual_result": {"market_change": 1.25},
                },
            )

            summary = build_market_judgment_facts(
                db_path=db,
                logs_dir=logs,
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            with closing(_connect(db)) as conn:
                rows = conn.execute(
                    """
                    SELECT market_key, runtime_mode, session_date, market, consensus_mode
                    FROM fact_market_judgment
                    ORDER BY market_key
                    """
                ).fetchall()

        self.assertEqual(summary["facts_written"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market_key"], "live:2026-05-20:US")
        self.assertEqual(rows[0]["runtime_mode"], "live")
        self.assertEqual(rows[0]["consensus_mode"], "MILD_BULL")


if __name__ == "__main__":
    unittest.main()
