from __future__ import annotations

import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tools.analyze_prompt_reorder_shadow import generate_report


class AnalyzePromptReorderShadowTests(unittest.TestCase):
    def _write_snapshot(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True)
        payload = {
            "market": "US",
            "selection_trace_id": "trace-1",
            "prompt_pool_shadow_reorder": {
                "active_top": ["OLD", "KEEP"],
                "trainer_top": ["NEW", "KEEP"],
                "trainer_top_new_tickers": ["NEW"],
                "active_top_displaced_tickers": ["OLD"],
                "top_overlap_ratio": 0.5,
            },
        }
        (log_dir / "candidate_funnel_snapshot_20260721_US.jsonl").write_text(
            json.dumps(payload, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _write_audit_db(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True)
        conn = sqlite3.connect(str(db_path))
        try:
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    candidate_key TEXT,
                    market TEXT,
                    session_date TEXT,
                    ticker TEXT,
                    prompt_rank INTEGER
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE audit_candidate_outcomes (
                    candidate_key TEXT,
                    horizon_min INTEGER,
                    status TEXT,
                    return_pct REAL,
                    max_runup_pct REAL,
                    max_drawdown_pct REAL,
                    observed_at TEXT,
                    source TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO audit_candidate_rows VALUES (?, ?, ?, ?, ?)",
                [
                    ("cand_new", "US", "2026-07-21", "NEW", 10),
                    ("cand_old", "US", "2026-07-21", "OLD", 1),
                ],
            )
            conn.executemany(
                "INSERT INTO audit_candidate_outcomes VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    ("cand_new", 60, "ok", 2.5, 3.1, -0.4, "2026-07-21T15:00:00Z", "unit"),
                    ("cand_old", 60, "ok", -0.8, 0.5, -1.2, "2026-07-21T15:00:00Z", "unit"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def test_report_compares_trainer_new_against_displaced(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs" / "funnel"
            audit_db = root / "data" / "audit" / "candidate_audit.db"
            self._write_snapshot(log_dir)
            self._write_audit_db(audit_db)

            report = generate_report(
                log_dir=log_dir,
                audit_db=audit_db,
                market="US",
                horizon_min=60,
                min_labeled=2,
            )

        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["observation_count"], 2)
        self.assertEqual(
            report["summary_by_bucket"]["trainer_top_new"]["mean_return_pct"],
            2.5,
        )
        self.assertEqual(
            report["summary_by_bucket"]["active_top_displaced"]["mean_return_pct"],
            -0.8,
        )
        self.assertEqual(
            report["recommendation"],
            "trainer_shadow_outperforms_displaced_consider_tail_reorder_shadow",
        )

    def test_missing_audit_db_becomes_backfill_recommendation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            log_dir = root / "logs" / "funnel"
            self._write_snapshot(log_dir)

            report = generate_report(
                log_dir=log_dir,
                audit_db=root / "missing.db",
                market="US",
                horizon_min=60,
            )

        self.assertEqual(report["event_count"], 1)
        self.assertEqual(report["summary_by_bucket"]["trainer_top_new"]["missing_outcome_count"], 1)
        self.assertEqual(report["recommendation"], "backfill_outcomes_or_attach_valid_external_history")
        self.assertEqual(
            report["external_data_policy"]["status"],
            "required_when_internal_labels_are_missing_or_sparse",
        )


if __name__ == "__main__":
    unittest.main()
