from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.build_claude_decision_facts import init_schema as init_fact_schema
from tools.label_claude_judgments import label_claude_judgments


NOW = "2026-05-20T00:00:00+00:00"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_selection(
    conn: sqlite3.Connection,
    key: str,
    *,
    market: str = "US",
    ticker: str = "AAA",
    action: str = "WATCH",
    classification: str = "watch_only",
    risk_tags: list[str] | None = None,
    hard_blocks: list[str] | None = None,
    gap_pct: float | None = None,
    from_high_pct: float | None = None,
) -> None:
    trade_ready = 1 if action in {"BUY_READY", "TRADE_READY", "PROBE_READY", "ADD_READY"} else 0
    conn.execute(
        """
        INSERT INTO fact_selection (
            selection_key, runtime_mode, session_date, market, ticker, source,
            dedupe_key, prompt_included, input_to_claude_reported, classification,
            raw_action, normalized_action, final_action, claude_watchlist,
            claude_trade_ready, trade_ready, risk_tags_json, hard_blocks_json,
            soft_gates_json, data_quality_flags_json, gap_pct, from_high_pct,
            created_at, updated_at, source_quality, source_refs_json
        )
        VALUES (?, 'live', '2026-05-20', ?, ?, 'unit',
                ?, 1, 1, ?, ?, ?, ?, 1,
                ?, ?, ?, ?, '[]', '[]', ?, ?,
                ?, ?, 'partial', '{}')
        """,
        (
            key,
            market,
            ticker,
            f"live:{market}:2026-05-20:{ticker}",
            classification,
            action,
            action,
            action,
            trade_ready,
            trade_ready,
            json.dumps(risk_tags or []),
            json.dumps(hard_blocks or []),
            gap_pct,
            from_high_pct,
            NOW,
            NOW,
        ),
    )


def _insert_outcome(
    conn: sqlite3.Connection,
    key: str,
    *,
    market: str = "US",
    ticker: str = "AAA",
    f1: float | None = None,
    f3: float | None = None,
    runup3: float | None = None,
    drawdown3: float | None = None,
) -> None:
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


def _insert_execution(
    conn: sqlite3.Connection,
    key: str,
    *,
    market: str = "US",
    ticker: str = "AAA",
    pnl: float | None = None,
    quality_grade: str = "CLEAN",
    learning_allowed: int = 1,
    match_quality: str = "direct_decision_id",
    source_quality: str = "complete",
) -> None:
    conn.execute(
        """
        INSERT INTO fact_execution (
            execution_key, selection_key, runtime_mode, session_date, market,
            ticker, pnl_pct, quality_grade, learning_allowed, match_quality,
            created_at, updated_at, source_quality, source_refs_json
        )
        VALUES (?, ?, 'live', '2026-05-20', ?, ?, ?, ?, ?, ?,
                ?, ?, ?, '{}')
        """,
        (key, key, market, ticker, pnl, quality_grade, learning_allowed, match_quality, NOW, NOW, source_quality),
    )


def _label_for(conn: sqlite3.Connection, key: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM decision_labels WHERE selection_key=?", (key,)).fetchone()
    assert row is not None
    return row


class ClaudeJudgmentLabelerTests(unittest.TestCase):
    def test_us_positive_action_with_good_forward_is_correct_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_good", market="US", ticker="AAPL", action="BUY_READY", classification="trade_ready")
                _insert_outcome(conn, "sel_good", market="US", ticker="AAPL", f3=4.0, runup3=5.5)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            with closing(_connect(db)) as conn:
                row = _label_for(conn, "sel_good")
            self.assertEqual(row["label"], "correct_positive")
            self.assertEqual(row["owner"], "none")

    def test_positive_bad_forward_is_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_bad", market="KR", ticker="005930", action="BUY_READY", classification="trade_ready")
                _insert_outcome(conn, "sel_bad", market="KR", ticker="005930", f1=-3.5, f3=-6.0, drawdown3=-7.0)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="KR", write=True)
            with closing(_connect(db)) as conn:
                row = _label_for(conn, "sel_bad")
            self.assertEqual(row["label"], "false_positive")
            self.assertEqual(row["owner"], "claude_selection")

    def test_watch_missed_runup_without_risk_is_false_negative(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_miss", market="KR", ticker="123456", action="WATCH")
                _insert_outcome(conn, "sel_miss", market="KR", ticker="123456", f3=2.0, runup3=8.0)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="KR", write=True)
            with closing(_connect(db)) as conn:
                row = _label_for(conn, "sel_miss")
            self.assertEqual(row["label"], "false_negative")
            self.assertEqual(row["owner"], "claude_selection")

    def test_watch_missed_runup_with_risk_is_risk_justified_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_risk", market="US", ticker="RISK", action="WATCH", risk_tags=["low_liquidity"])
                _insert_outcome(conn, "sel_risk", market="US", ticker="RISK", f3=3.0, runup3=9.0)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            with closing(_connect(db)) as conn:
                row = _label_for(conn, "sel_risk")
            self.assertEqual(row["label"], "risk_justified_miss")
            self.assertEqual(row["owner"], "risk_policy")

    def test_good_forward_bad_clean_pnl_is_execution_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_exec", market="US", ticker="EXEC", action="BUY_READY", classification="trade_ready")
                _insert_outcome(conn, "sel_exec", market="US", ticker="EXEC", f3=4.0, runup3=6.0)
                _insert_execution(conn, "sel_exec", market="US", ticker="EXEC", pnl=-2.0)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            with closing(_connect(db)) as conn:
                row = _label_for(conn, "sel_exec")
            self.assertEqual(row["label"], "execution_issue")
            self.assertEqual(row["owner"], "execution")

    def test_missing_outcome_or_ambiguous_execution_is_data_quality_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_missing", market="US", ticker="MISS", action="BUY_READY", classification="trade_ready")
                _insert_selection(conn, "sel_amb", market="US", ticker="AMB", action="BUY_READY", classification="trade_ready")
                _insert_outcome(conn, "sel_amb", market="US", ticker="AMB", f3=5.0, runup3=6.0)
                _insert_execution(
                    conn,
                    "sel_amb",
                    market="US",
                    ticker="AMB",
                    pnl=2.0,
                    match_quality="ambiguous_session_ticker",
                    source_quality="ambiguous_match",
                )
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            with closing(_connect(db)) as conn:
                missing = _label_for(conn, "sel_missing")
                ambiguous = _label_for(conn, "sel_amb")
            self.assertEqual(missing["label"], "data_quality_issue")
            self.assertEqual(ambiguous["label"], "data_quality_issue")
            self.assertEqual(missing["owner"], "data_quality")
            self.assertEqual(ambiguous["owner"], "data_quality")

    def test_label_write_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "facts.db"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_once", market="US", ticker="ONCE", action="WATCH")
                _insert_outcome(conn, "sel_once", market="US", ticker="ONCE", f3=-1.0, runup3=1.0)
                conn.commit()

            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            label_claude_judgments(db_path=db, date="2026-05-20", market="US", write=True)
            with closing(_connect(db)) as conn:
                count = conn.execute("SELECT COUNT(*) AS n FROM decision_labels").fetchone()["n"]
                row = _label_for(conn, "sel_once")
            self.assertEqual(count, 1)
            self.assertEqual(row["label"], "correct_negative")

    def test_lesson_candidate_proposal_only_uses_selection_owned_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = root / "facts.db"
            output = root / "lesson_proposals.json"
            init_fact_schema(db)
            with closing(_connect(db)) as conn:
                _insert_selection(conn, "sel_fn", market="US", ticker="FN", action="WATCH")
                _insert_outcome(conn, "sel_fn", market="US", ticker="FN", f3=2.0, runup3=8.0)
                _insert_selection(conn, "sel_risk", market="US", ticker="RISK", action="WATCH", risk_tags=["low_liquidity"])
                _insert_outcome(conn, "sel_risk", market="US", ticker="RISK", f3=2.0, runup3=8.0)
                _insert_selection(conn, "sel_dq", market="US", ticker="DQ", action="BUY_READY", classification="trade_ready")
                conn.commit()

            summary = label_claude_judgments(
                db_path=db,
                date="2026-05-20",
                market="US",
                write=True,
                write_lesson_candidates=True,
                lesson_output=output,
                lesson_min_sample=1,
            )

            payload = json.loads(output.read_text(encoding="utf-8"))
            proposals = payload["markets"]["US"]
            proposal_ids = {item["id"] for item in proposals}
            proposal_text = json.dumps(payload, ensure_ascii=False)
            self.assertEqual(summary["lesson_candidate_proposals"], 1)
            self.assertTrue(any("false_negative" in item_id for item_id in proposal_ids))
            self.assertNotIn("risk_justified_miss", proposal_text)
            self.assertNotIn("data_quality_issue", proposal_text)


if __name__ == "__main__":
    unittest.main()
