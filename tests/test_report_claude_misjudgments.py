from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.build_claude_decision_facts import init_schema as init_fact_schema
from tools.label_claude_judgments import label_claude_judgments
from tools.report_claude_misjudgments import build_report_payload, to_markdown


NOW = "2026-05-20T00:00:00+00:00"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _selection(conn: sqlite3.Connection, key: str, market: str, ticker: str, action: str, *, risk: list[str] | None = None) -> None:
    trade_ready = 1 if action in {"BUY_READY", "TRADE_READY", "PROBE_READY", "ADD_READY"} else 0
    conn.execute(
        """
        INSERT INTO fact_selection (
            selection_key, runtime_mode, session_date, market, ticker, source,
            dedupe_key, prompt_included, input_to_claude_reported, classification,
            raw_action, normalized_action, final_action, claude_watchlist,
            claude_trade_ready, trade_ready, risk_tags_json, hard_blocks_json,
            soft_gates_json, data_quality_flags_json, created_at, updated_at,
            source_quality, source_refs_json
        )
        VALUES (?, 'live', '2026-05-20', ?, ?, 'unit',
                ?, 1, 1, ?, ?, ?, ?, 1,
                ?, ?, ?, '[]', '[]', '[]', ?, ?, 'partial', '{}')
        """,
        (
            key,
            market,
            ticker,
            f"live:{market}:2026-05-20:{ticker}",
            "trade_ready" if trade_ready else "watch_only",
            action,
            action,
            action,
            trade_ready,
            trade_ready,
            json.dumps(risk or []),
            NOW,
            NOW,
        ),
    )


def _outcome(conn: sqlite3.Connection, key: str, market: str, ticker: str, *, f1: float | None = None, f3: float | None = None, runup3: float | None = None, drawdown3: float | None = None) -> None:
    conn.execute(
        """
        INSERT INTO fact_forward_outcome (
            selection_key, runtime_mode, session_date, market, ticker,
            forward_1d_pct, forward_3d_pct, max_runup_3d_pct,
            max_drawdown_3d_pct, outcome_status, outcome_source,
            created_at, updated_at, source_quality, source_refs_json
        )
        VALUES (?, 'live', '2026-05-20', ?, ?, ?, ?, ?, ?, 'OK', 'unit',
                ?, ?, 'partial', '{}')
        """,
        (key, market, ticker, f1, f3, runup3, drawdown3, NOW, NOW),
    )


def _execution(conn: sqlite3.Connection, key: str, market: str, ticker: str, *, pnl: float) -> None:
    conn.execute(
        """
        INSERT INTO fact_execution (
            execution_key, selection_key, runtime_mode, session_date, market,
            ticker, pnl_pct, quality_grade, learning_allowed, match_quality,
            created_at, updated_at, source_quality, source_refs_json
        )
        VALUES (?, ?, 'live', '2026-05-20', ?, ?, ?, 'CLEAN', 1,
                'direct_decision_id', ?, ?, 'complete', '{}')
        """,
        (key, key, market, ticker, pnl, NOW, NOW),
    )


class ClaudeMisjudgmentReportTests(unittest.TestCase):
    def test_markdown_includes_market_label_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _selection(conn, "us_good", "US", "AAPL", "BUY_READY")
                _outcome(conn, "us_good", "US", "AAPL", f3=4.0, runup3=6.0)
                conn.commit()
            label_claude_judgments(db_path=db, date="2026-05-20", market="ALL", write=True)

            payload = build_report_payload(db_path=db, date="2026-05-20", market="ALL")
            markdown = to_markdown(payload)

        self.assertIn("## Market Label Distribution", markdown)
        self.assertIn("| US | correct_positive | 1 |", markdown)

    def test_risk_miss_is_not_counted_as_false_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _selection(conn, "fn", "US", "FN", "WATCH")
                _outcome(conn, "fn", "US", "FN", f3=2.0, runup3=8.0)
                _selection(conn, "risk", "US", "RISK", "WATCH", risk=["broker_untrusted"])
                _outcome(conn, "risk", "US", "RISK", f3=2.0, runup3=8.0)
                conn.commit()
            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)

            payload = build_report_payload(db_path=db, date="2026-05-20", market="US")
            summary = payload["summary"]

        self.assertEqual(summary["by_market_label"]["US"]["false_negative"], 1)
        self.assertEqual(summary["by_market_label"]["US"]["risk_justified_miss"], 1)
        self.assertEqual(len(summary["false_negative"]), 1)
        self.assertEqual(len(summary["risk_justified_miss"]), 1)

    def test_execution_issue_is_not_in_selection_improvement_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _selection(conn, "exec", "US", "EXEC", "BUY_READY")
                _outcome(conn, "exec", "US", "EXEC", f3=4.0, runup3=6.0)
                _execution(conn, "exec", "US", "EXEC", pnl=-2.0)
                conn.commit()
            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)

            payload = build_report_payload(db_path=db, date="2026-05-20", market="US")
            summary = payload["summary"]

        self.assertEqual(summary["by_market_label"]["US"]["execution_issue"], 1)
        self.assertEqual(summary["lesson_candidate_eligible_count"], 0)
        self.assertEqual(summary["execution_issue"][0]["ticker"], "EXEC")

    def test_data_quality_issue_is_not_lesson_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _selection(conn, "dq", "US", "DQ", "BUY_READY")
                conn.commit()
            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)

            payload = build_report_payload(db_path=db, date="2026-05-20", market="US")
            summary = payload["summary"]

        self.assertEqual(summary["by_market_label"]["US"]["data_quality_issue"], 1)
        self.assertEqual(summary["lesson_candidate_eligible_count"], 0)
        self.assertGreaterEqual(summary["lesson_candidate_blocked_count"], 1)


if __name__ == "__main__":
    unittest.main()
