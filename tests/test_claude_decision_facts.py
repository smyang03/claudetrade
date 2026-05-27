from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.build_claude_decision_facts import build_claude_decision_facts, init_schema


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _create_audit_db(path: Path) -> None:
    with closing(_connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE audit_candidate_rows (
                candidate_key TEXT PRIMARY KEY,
                call_id TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                market TEXT NOT NULL,
                session_date TEXT NOT NULL,
                known_at TEXT,
                ticker TEXT NOT NULL,
                source_file TEXT,
                prompt_rank INTEGER,
                in_prompt INTEGER DEFAULT 0,
                input_to_claude_reported INTEGER DEFAULT 0,
                final_prompt_included INTEGER,
                raw_rank INTEGER,
                trainer_score_rank INTEGER,
                prompt_excluded_reason TEXT,
                classification TEXT,
                claude_action TEXT,
                claude_reason TEXT,
                claude_veto_reason TEXT,
                claude_watchlist INTEGER DEFAULT 0,
                claude_trade_ready INTEGER DEFAULT 0,
                recommended_strategy TEXT,
                risk_tags_json TEXT NOT NULL DEFAULT '[]',
                hard_blocks TEXT,
                soft_gates TEXT,
                data_quality_flags_json TEXT,
                data_quality TEXT,
                evidence_data_state TEXT,
                trainer_candidate_state TEXT,
                max_position_pct REAL,
                route_original_action TEXT,
                route_final_action TEXT,
                route_route TEXT,
                route_reason TEXT,
                route_demoted_to TEXT,
                route_runtime_gate_reason TEXT,
                liquidity_bucket TEXT,
                market_type TEXT,
                primary_bucket TEXT,
                change_pct REAL,
                gap_pct REAL,
                from_high_pct REAL,
                volume_ratio REAL,
                turnover REAL,
                execution_decision_id TEXT,
                execution_link_source TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '2026-05-20T00:00:00+00:00',
                updated_at TEXT NOT NULL DEFAULT '2026-05-20T00:00:00+00:00'
            );
            CREATE TABLE audit_candidate_outcomes (
                candidate_key TEXT NOT NULL,
                horizon_min INTEGER NOT NULL,
                target_at TEXT,
                observed_at TEXT,
                observed_price REAL,
                return_pct REAL,
                max_runup_pct REAL,
                max_drawdown_pct REAL,
                status TEXT NOT NULL,
                source TEXT,
                label_generated_at TEXT NOT NULL DEFAULT '2026-05-20T00:00:00+00:00',
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT '2026-05-20T00:00:00+00:00',
                UNIQUE(candidate_key, horizon_min)
            );
            """
        )
        conn.commit()


def _create_selection_db(path: Path) -> None:
    with closing(_connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE ticker_selection_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_mode TEXT NOT NULL DEFAULT 'paper',
                date TEXT NOT NULL,
                market TEXT NOT NULL,
                ticker TEXT NOT NULL,
                consensus_mode TEXT,
                selection_rank INTEGER,
                watchlist_rank INTEGER,
                source_type TEXT,
                selection_batch_id TEXT,
                selected_reason TEXT,
                veto_reason TEXT,
                selected_reason_tag TEXT,
                selected_at TEXT,
                change_pct REAL,
                vol_ratio REAL,
                gap_pct REAL,
                from_high_pct REAL,
                market_type TEXT,
                category TEXT,
                sector TEXT,
                liquidity_bucket TEXT,
                from_high_bucket TEXT,
                trade_ready INTEGER DEFAULT 0,
                risk_tags TEXT,
                recommended_strategy TEXT,
                execution_decision_id TEXT,
                execution_strategy TEXT,
                execution_reason TEXT,
                pnl_pct REAL,
                exit_reason TEXT,
                forward_1d REAL,
                forward_3d REAL,
                forward_5d REAL,
                max_runup_3d REAL,
                max_drawdown_3d REAL,
                max_runup_5d REAL,
                max_drawdown_5d REAL,
                created_at TEXT DEFAULT '2026-05-20T00:00:00+00:00'
            );
            """
        )
        conn.commit()


def _create_ml_db(path: Path) -> None:
    with closing(_connect(path)) as conn:
        conn.executescript(
            """
            CREATE TABLE v2_canonical_performance (
                v2_decision_id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                session_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                status TEXT NOT NULL,
                route TEXT,
                path_type TEXT,
                path_run_id TEXT,
                strategy TEXT,
                origin_action TEXT,
                filled INTEGER NOT NULL DEFAULT 0,
                closed INTEGER NOT NULL DEFAULT 0,
                first_fill_event_id INTEGER,
                first_close_event_id INTEGER,
                last_close_event_id INTEGER,
                earliest_fill_at TEXT,
                first_closed_at TEXT,
                last_closed_at TEXT,
                entry_price REAL,
                first_exit_price REAL,
                last_exit_price REAL,
                pnl_pct REAL,
                mfe_pct REAL,
                mae_pct REAL,
                quality_grade TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
                learning_allowed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE v2_decision_fill_links (
                v2_decision_id TEXT PRIMARY KEY,
                canonical_key TEXT NOT NULL,
                legacy_decision_id INTEGER,
                market TEXT NOT NULL,
                runtime_mode TEXT NOT NULL,
                session_date TEXT NOT NULL,
                ticker TEXT NOT NULL,
                link_status TEXT NOT NULL,
                matched_by TEXT NOT NULL
            );
            """
        )
        conn.commit()


class ClaudeDecisionFactsBuilderTests(unittest.TestCase):
    def test_schema_init_is_idempotent_and_missing_sources_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mart = root / "claude_decision_facts.db"

            init_schema(mart)
            init_schema(mart)
            summary = build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=root / "missing_audit.db",
                selection_db=root / "missing_selection.db",
                ml_db=root / "missing_ml.db",
                event_db=root / "missing_events.db",
                start_date="2026-05-20",
                end_date="2026-05-20",
                market="KR",
                runtime_mode="live",
            )

            self.assertEqual(summary["fact_selection_rows"], 0)
            self.assertIn("candidate_audit_db", summary["missing_sources"])
            self.assertIn("selection_db", summary["missing_sources"])
            with closing(_connect(mart)) as conn:
                tables = {
                    row["name"]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                execution_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(fact_execution)")
                }
                self.assertIn("fact_selection", tables)
                self.assertIn("fact_build_runs", tables)
                self.assertIn("first_fill_event_id", execution_columns)
                self.assertIn("first_close_event_id", execution_columns)
                self.assertIn("last_close_event_id", execution_columns)

    def test_schema_init_migrates_existing_execution_fact_event_id_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mart = Path(tmp) / "facts.db"
            with closing(_connect(mart)) as conn:
                conn.execute(
                    """
                    CREATE TABLE fact_execution (
                        execution_key TEXT PRIMARY KEY,
                        runtime_mode TEXT NOT NULL,
                        session_date TEXT NOT NULL,
                        market TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        match_quality TEXT NOT NULL DEFAULT 'unknown',
                        source_quality TEXT NOT NULL DEFAULT 'unknown'
                    )
                    """
                )
                conn.commit()

            init_schema(mart)

            with closing(_connect(mart)) as conn:
                execution_columns = {
                    row["name"]
                    for row in conn.execute("PRAGMA table_info(fact_execution)")
                }
            self.assertIn("first_fill_event_id", execution_columns)
            self.assertIn("first_close_event_id", execution_columns)
            self.assertIn("last_close_event_id", execution_columns)

    def test_builder_preserves_call_level_audit_rows_and_latest_rank(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "candidate_audit.db"
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_audit_db(audit_db)
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(audit_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, in_prompt, input_to_claude_reported,
                        final_prompt_included, claude_action, claude_trade_ready,
                        route_final_action, classification
                    )
                    VALUES (?, ?, 'live', 'US', '2026-05-20', ?, 'AAPL', 1, 1, 1, ?, ?, ?, ?)
                    """,
                    ("cand_old", "call_old", "2026-05-20T09:00:00+09:00", "BUY_READY", 1, "BUY_READY", "trade_ready"),
                )
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, in_prompt, input_to_claude_reported,
                        final_prompt_included, claude_action, claude_trade_ready,
                        route_final_action, classification
                    )
                    VALUES (?, ?, 'live', 'US', '2026-05-20', ?, 'AAPL', 1, 1, 1, ?, ?, ?, ?)
                    """,
                    ("cand_new", "call_new", "2026-05-20T09:05:00+09:00", "WATCH", 0, "WATCH", "watch_only"),
                )
                conn.commit()

            summary = build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                start_date="2026-05-20",
                end_date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            self.assertEqual(summary["fact_selection_rows"], 2)
            with closing(_connect(mart)) as conn:
                rows = conn.execute(
                    "SELECT selection_key, latest_rank, final_action FROM fact_selection ORDER BY known_at"
                ).fetchall()
            self.assertEqual([row["selection_key"] for row in rows], ["audit:cand_old", "audit:cand_new"])
            self.assertEqual([row["latest_rank"] for row in rows], [2, 1])
            self.assertEqual(rows[0]["final_action"], "BUY_READY")
            self.assertEqual(rows[1]["final_action"], "WATCH")

    def test_builder_uses_selection_log_fallback_when_audit_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(selection_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO ticker_selection_log (
                        bot_mode, date, market, ticker, selected_at, trade_ready,
                        selected_reason, veto_reason, risk_tags, recommended_strategy,
                        forward_1d, forward_3d, forward_5d, max_runup_3d, max_drawdown_3d
                    )
                    VALUES ('live', '2026-05-20', 'KR', '005930', '2026-05-20T09:10:00+09:00',
                            1, 'strong setup', '', '["gap"]', 'momentum', 1.0, 3.0, 5.0, 6.0, -1.0)
                    """
                )
                conn.commit()

            summary = build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=root / "missing_audit.db",
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="KR",
                runtime_mode="live",
            )

            self.assertEqual(summary["fact_selection_rows"], 1)
            with closing(_connect(mart)) as conn:
                selection = conn.execute("SELECT * FROM fact_selection").fetchone()
                outcome = conn.execute("SELECT * FROM fact_forward_outcome").fetchone()
            self.assertEqual(selection["selection_key"], "selection_log:1")
            self.assertEqual(selection["final_action"], "TRADE_READY")
            self.assertEqual(outcome["forward_3d_pct"], 3.0)
            self.assertEqual(outcome["max_runup_3d_pct"], 6.0)

    def test_builder_matches_execution_by_decision_id_and_keeps_sources_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "candidate_audit.db"
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_audit_db(audit_db)
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(audit_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, claude_action, route_final_action,
                        claude_trade_ready, classification, execution_decision_id
                    )
                    VALUES ('cand_exec', 'call1', 'live', 'US', '2026-05-20',
                            '2026-05-20T09:00:00+09:00', 'NVDA', 'BUY_READY',
                            'BUY_READY', 1, 'trade_ready', 'dec_1')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO audit_candidate_outcomes (
                        candidate_key, horizon_min, return_pct, max_runup_pct,
                        max_drawdown_pct, status, source
                    )
                    VALUES ('cand_exec', 4320, 4.5, 7.0, -1.2, 'OK', 'unit')
                    """
                )
                conn.commit()
            with closing(_connect(ml_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance (
                        v2_decision_id, canonical_key, market, runtime_mode,
                        session_date, ticker, status, path_type, path_run_id,
                        strategy, origin_action, filled, closed, first_fill_event_id,
                        first_close_event_id, last_close_event_id, earliest_fill_at,
                        first_closed_at, last_closed_at, entry_price,
                        first_exit_price, last_exit_price, pnl_pct, mfe_pct,
                        mae_pct, quality_grade, learning_allowed
                    )
                    VALUES ('dec_1', 'canon_1', 'US', 'live', '2026-05-20', 'NVDA',
                            'CLOSED', 'plan_a', '', 'momentum', 'BUY_READY', 1, 1,
                            101, 201, 202,
                            '2026-05-20T09:30:00+09:00', '2026-05-20T10:30:00+09:00',
                            '2026-05-20T10:30:00+09:00', 100.0, 103.0, 103.0,
                            3.0, 4.0, -0.5, 'CLEAN', 1)
                    """
                )
                conn.commit()
            before_count = sqlite3.connect(audit_db).execute("SELECT COUNT(*) FROM audit_candidate_rows").fetchone()[0]

            summary = build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            self.assertEqual(summary["fact_execution_rows"], 1)
            after_count = sqlite3.connect(audit_db).execute("SELECT COUNT(*) FROM audit_candidate_rows").fetchone()[0]
            self.assertEqual(before_count, after_count)
            with closing(_connect(mart)) as conn:
                row = conn.execute("SELECT * FROM fact_execution").fetchone()
                outcome = conn.execute("SELECT * FROM fact_forward_outcome").fetchone()
            self.assertEqual(row["v2_decision_id"], "dec_1")
            self.assertEqual(row["match_quality"], "direct_decision_id")
            self.assertEqual(row["first_fill_event_id"], 101)
            self.assertEqual(row["first_close_event_id"], 201)
            self.assertEqual(row["last_close_event_id"], 202)
            self.assertEqual(row["pnl_pct"], 3.0)
            self.assertEqual(outcome["forward_3d_pct"], 4.5)

    def test_builder_does_not_link_ambiguous_session_ticker_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "candidate_audit.db"
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_audit_db(audit_db)
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(audit_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, claude_action, route_final_action,
                        claude_trade_ready, classification
                    )
                    VALUES ('cand_amb', 'call1', 'live', 'US', '2026-05-20',
                            '2026-05-20T09:00:00+09:00', 'AMD', 'BUY_READY',
                            'BUY_READY', 1, 'trade_ready')
                    """
                )
                conn.commit()
            with closing(_connect(ml_db)) as conn:
                for decision_id in ("dec_a", "dec_b"):
                    conn.execute(
                        """
                        INSERT INTO v2_canonical_performance (
                            v2_decision_id, canonical_key, market, runtime_mode,
                            session_date, ticker, status, filled, closed,
                            quality_grade, learning_allowed
                        )
                        VALUES (?, ?, 'US', 'live', '2026-05-20', 'AMD',
                                'CLOSED', 1, 1, 'CLEAN', 1)
                        """,
                        (decision_id, f"canon_{decision_id}"),
                    )
                conn.commit()

            build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            with closing(_connect(mart)) as conn:
                row = conn.execute("SELECT * FROM fact_execution").fetchone()
            self.assertIsNone(row["v2_decision_id"])
            self.assertEqual(row["match_quality"], "ambiguous_session_ticker")
            self.assertEqual(row["source_quality"], "ambiguous_match")

    def test_builder_does_not_fallback_when_explicit_decision_id_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "candidate_audit.db"
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_audit_db(audit_db)
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(audit_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, call_id, runtime_mode, market, session_date,
                        known_at, ticker, claude_action, route_final_action,
                        claude_trade_ready, classification, execution_decision_id
                    )
                    VALUES ('cand_stale_link', 'call1', 'live', 'US', '2026-05-20',
                            '2026-05-20T09:00:00+09:00', 'TSLA', 'BUY_READY',
                            'BUY_READY', 1, 'trade_ready', 'dec_missing')
                    """
                )
                conn.commit()
            with closing(_connect(ml_db)) as conn:
                conn.execute(
                    """
                    INSERT INTO v2_canonical_performance (
                        v2_decision_id, canonical_key, market, runtime_mode,
                        session_date, ticker, status, filled, closed,
                        quality_grade, learning_allowed
                    )
                    VALUES ('dec_other', 'canon_other', 'US', 'live',
                            '2026-05-20', 'TSLA', 'CLOSED', 1, 1, 'CLEAN', 1)
                    """
                )
                conn.commit()

            build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            with closing(_connect(mart)) as conn:
                row = conn.execute("SELECT * FROM fact_execution").fetchone()
            self.assertIsNone(row["v2_decision_id"])
            self.assertEqual(row["execution_decision_id"], "dec_missing")
            self.assertEqual(row["match_quality"], "decision_id_not_found")
            self.assertEqual(row["source_quality"], "missing_execution")

    def test_builder_clears_scope_before_rebuild_to_avoid_stale_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit_db = root / "candidate_audit.db"
            selection_db = root / "ticker_selection_log.db"
            ml_db = root / "decisions.db"
            mart = root / "facts.db"
            _create_audit_db(audit_db)
            _create_selection_db(selection_db)
            _create_ml_db(ml_db)
            with closing(_connect(audit_db)) as conn:
                for ticker in ("AAPL", "MSFT"):
                    conn.execute(
                        """
                        INSERT INTO audit_candidate_rows (
                            candidate_key, call_id, runtime_mode, market, session_date,
                            known_at, ticker, claude_action, route_final_action,
                            claude_trade_ready, classification
                        )
                        VALUES (?, 'call1', 'live', 'US', '2026-05-20',
                                '2026-05-20T09:00:00+09:00', ?, 'WATCH',
                                'WATCH', 0, 'watch_only')
                        """,
                        (f"cand_{ticker}", ticker),
                    )
                conn.commit()

            build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )
            with closing(_connect(audit_db)) as conn:
                conn.execute("DELETE FROM audit_candidate_rows WHERE ticker='MSFT'")
                conn.commit()

            build_claude_decision_facts(
                db_path=mart,
                candidate_audit_db=audit_db,
                selection_db=selection_db,
                ml_db=ml_db,
                event_db=root / "events.db",
                date="2026-05-20",
                market="US",
                runtime_mode="live",
            )

            with closing(_connect(mart)) as conn:
                tickers = [
                    row["ticker"]
                    for row in conn.execute(
                        "SELECT ticker FROM fact_selection WHERE session_date='2026-05-20' ORDER BY ticker"
                    )
                ]
                build_runs = conn.execute("SELECT COUNT(*) AS n FROM fact_build_runs").fetchone()["n"]
            self.assertEqual(tickers, ["AAPL"])
            self.assertEqual(build_runs, 2)


if __name__ == "__main__":
    unittest.main()
