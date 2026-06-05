from __future__ import annotations

import json
import os
import sqlite3
import io
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from preopen.storage import save_outcome_record, save_preopen_state
from preopen import continuation_shadow as cs


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenContinuationShadowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "data" / "preopen_continuation.db"
        self.patches = [
            patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(self.root)),
            patch("preopen.continuation_shadow.get_runtime_path", side_effect=_runtime_path(self.root)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.tmp.cleanup)

    def _save_us_state(self) -> None:
        captured_at = datetime.now(KST).isoformat(timespec="seconds")
        save_preopen_state(
            "US",
            {
                "market": "US",
                "session_date": "2026-06-04",
                "captured_at": captured_at,
                "collector_status": "ok",
                "provider": "us_screen_market",
                "data_quality": "live_screen",
                "candidates": [
                    {
                        "ticker": "AAA",
                        "name": "Alpha",
                        "market": "US",
                        "session_date": "2026-06-04",
                        "source": "day_gainers",
                        "shadow_preopen_rank": 4,
                        "extended_change_pct": 6.2,
                        "extended_price": 10.0,
                        "extended_volume": 12_000_000,
                        "extended_dollar_volume": 120_000_000,
                        "regular_open_price": 10.0,
                        "news_or_earnings_flag": True,
                        "risk_tags": ["wide_spread"],
                        "actual_selected": True,
                        "actual_selection_rank": 2,
                    },
                    {
                        "ticker": "BBB",
                        "name": "Beta",
                        "market": "US",
                        "session_date": "2026-06-04",
                        "source": "day_gainers",
                        "shadow_preopen_rank": 7,
                        "extended_change_pct": 25.0,
                        "extended_price": 4.0,
                        "extended_volume": 1_000_000,
                        "extended_dollar_volume": 4_000_000,
                        "regular_open_price": 4.0,
                    },
                ],
            },
            session_date="2026-06-04",
            mode="live",
        )

    def _save_outcome(self) -> None:
        save_outcome_record(
            "US",
            "2026-06-04",
            {
                "ticker": "AAA",
                "name": "Alpha",
                "offset_min": 30,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "outcome_status": "WIN",
                "token_status": "ok",
                "anchor_price": 9.0,
                "regular_open_price": 10.0,
                "price": 11.0,
                "high": 12.0,
                "low": 9.5,
                "volume": 1_500_000,
                "post_open_return_pct": 22.2222,
                "post_open_30m_return_pct": 22.2222,
                "price_source": "test",
            },
            mode="live",
        )

    def test_us_only_guard(self) -> None:
        with self.assertRaisesRegex(cs.ContinuationShadowError, "preopen_continuation_shadow_supports_us_only"):
            cs.collect_candidates("KR", session_date="2026-06-04", db_path=self.db_path)

    def test_collect_candidates_is_idempotent_and_keeps_exclusions(self) -> None:
        self._save_us_state()

        first = cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        second = cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)

        self.assertEqual(first["written"], 2)
        self.assertEqual(second["written"], 2)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT ticker, eligible, exclusion_reason FROM preopen_candidates ORDER BY ticker").fetchall()
            run_count = conn.execute("SELECT COUNT(*) FROM preopen_shadow_runs WHERE step='collect'").fetchone()[0]

        self.assertEqual(len(rows), 2)
        self.assertEqual(run_count, 2)
        self.assertEqual(rows[0]["ticker"], "AAA")
        self.assertEqual(rows[0]["eligible"], 1)
        self.assertEqual(rows[1]["ticker"], "BBB")
        self.assertEqual(rows[1]["eligible"], 0)
        self.assertIn("gap_2_20", rows[1]["exclusion_reason"])

    def test_feature_snapshot_separates_open_and_anchor_basis(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)

        result = cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)

        self.assertEqual(result["sampled"], 1)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            feature = conn.execute(
                "SELECT * FROM preopen_feature_snapshots WHERE ticker='AAA' AND offset_min=30"
            ).fetchone()
            outcome = conn.execute("SELECT * FROM preopen_outcomes WHERE ticker='AAA'").fetchone()

        self.assertEqual(feature["snapshot_status"], "sampled")
        self.assertEqual(feature["return_from_open_pct"], 10.0)
        self.assertEqual(feature["anchor_return_pct"], 22.2222)
        self.assertEqual(feature["mfe_from_open_pct"], 20.0)
        self.assertEqual(feature["mae_from_open_pct"], -5.0)
        self.assertEqual(outcome["ret_30m"], 10.0)
        self.assertIsNone(outcome["ret_close"])
        self.assertEqual(outcome["outcome_status"], "partial")
        self.assertEqual(outcome["actual_selected"], 1)

    def test_close_offset_is_required_for_ret_close(self) -> None:
        self._save_us_state()
        self._save_outcome()
        save_outcome_record(
            "US",
            "2026-06-04",
            {
                "ticker": "AAA",
                "name": "Alpha",
                "offset_min": 390,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "outcome_status": "WIN",
                "anchor_price": 9.0,
                "regular_open_price": 10.0,
                "price": 12.5,
                "high": 13.0,
                "low": 9.0,
                "volume": 2_000_000,
                "post_open_return_pct": 38.8889,
                "post_open_390m_return_pct": 38.8889,
                "price_source": "test",
            },
            mode="live",
        )
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)

        with patch("preopen.continuation_shadow._close_offset_min", return_value=390):
            cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)
            cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=390, db_path=self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            outcome = conn.execute("SELECT * FROM preopen_outcomes WHERE ticker='AAA'").fetchone()

        self.assertEqual(outcome["ret_30m"], 10.0)
        self.assertEqual(outcome["ret_close"], 25.0)
        self.assertEqual(outcome["close_price"], 12.5)
        self.assertEqual(outcome["outcome_status"], "complete")

    def test_strict_claude_parser_rejects_non_contract_responses(self) -> None:
        parsed = cs.parse_claude_response('{"cases":[["C01","PROMOTE",0.72,"OK"]]}', ["C01"])
        self.assertEqual(parsed[0]["decision"], "PROMOTE")

        with self.assertRaisesRegex(cs.ParseError, "strict_json_required"):
            cs.parse_claude_response('```json\n{"cases":[]}\n```', ["C01"])
        with self.assertRaisesRegex(cs.ParseError, "unknown_case_id"):
            cs.parse_claude_response('{"cases":[["C02","DROP",0.5,"X"]]}', ["C01"])
        with self.assertRaisesRegex(cs.ParseError, "missing_case_id"):
            cs.parse_claude_response('{"cases":[["C01","DROP",0.5,"X"]]}', ["C01", "C02"])

    def test_eval_no_claude_records_skip_without_raw_call(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)

        result = cs.run_eval("US", session_date="2026-06-04", db_path=self.db_path, no_claude=True)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["skip_reason"], "no_claude")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            check = conn.execute("SELECT * FROM preopen_claude_checks").fetchone()

        self.assertEqual(check["smart_skip"], 1)
        self.assertEqual(check["raw_call_path"], None)

    def test_eval_dry_run_does_not_create_shadow_db(self) -> None:
        missing_db = self.root / "data" / "missing_shadow.db"

        result = cs.run_eval("US", session_date="2026-06-04", db_path=missing_db, dry_run=True)

        self.assertEqual(result["readiness"], "db_missing")
        self.assertFalse(missing_db.exists())

    def test_repeated_no_claude_eval_records_new_attempt_without_unique_conflict(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)

        first = cs.run_eval("US", session_date="2026-06-04", db_path=self.db_path, no_claude=True)
        second = cs.run_eval("US", session_date="2026-06-04", db_path=self.db_path, no_claude=True)

        self.assertEqual(first["status"], "skipped")
        self.assertEqual(second["status"], "skipped")
        with sqlite3.connect(self.db_path) as conn:
            attempts = [
                row[0]
                for row in conn.execute(
                    "SELECT attempt_no FROM preopen_claude_checks WHERE skip_reason='no_claude' ORDER BY id"
                ).fetchall()
            ]

        self.assertEqual(attempts, [1, 2])

    def test_eval_parse_failure_retries_once_with_compact_repair(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)
        responses = [
            ("not json", 100, 20, 12),
            ('{"cases":[["C01","KEEP",0.55,"REPAIRED"]]}', 30, 10, 8),
        ]

        with patch.dict(
            os.environ,
            {"ANTHROPIC_API_KEY": "test", "PREOPEN_CONTINUATION_CLAUDE_RETRY_MAX": "1"},
            clear=False,
        ), patch("preopen.continuation_shadow._call_claude", side_effect=responses), patch(
            "preopen.continuation_shadow._save_eval_raw_call",
            side_effect=["raw1.json", "raw2.json"],
        ), patch("preopen.continuation_shadow._record_eval_credit") as credit:
            result = cs.run_eval("US", session_date="2026-06-04", db_path=self.db_path)

        self.assertEqual(result["status"], "called")
        self.assertTrue(result["parse_ok"])
        self.assertIsNotNone(result["retry_of_check_id"])
        self.assertEqual(credit.call_count, 2)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            checks = conn.execute("SELECT status, attempt_no, retry_of_check_id, parse_ok FROM preopen_claude_checks ORDER BY id").fetchall()
            decision = conn.execute("SELECT decision, case_id FROM preopen_claude_decisions").fetchone()

        self.assertEqual(len(checks), 2)
        self.assertEqual(checks[0]["status"], "parse_failed")
        self.assertEqual(checks[0]["attempt_no"], 1)
        self.assertEqual(checks[1]["status"], "called")
        self.assertEqual(checks[1]["attempt_no"], 2)
        self.assertEqual(checks[1]["parse_ok"], 1)
        self.assertEqual(decision["decision"], "KEEP")

    def test_existing_selection_db_backfill_writes_only_shadow_db(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)
        selection_db = self.root / "source" / "ticker_selection_log.db"
        selection_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(selection_db) as conn:
            conn.execute(
                """
                CREATE TABLE ticker_selection_log (
                    id INTEGER PRIMARY KEY, date TEXT, market TEXT, ticker TEXT,
                    selection_rank INTEGER, watchlist_rank INTEGER,
                    trade_ready INTEGER, traded INTEGER, pnl_pct REAL,
                    execution_decision_id TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO ticker_selection_log
                (id, date, market, ticker, selection_rank, watchlist_rank, trade_ready, traded, pnl_pct, execution_decision_id)
                VALUES (7, '2026-06-04', 'US', 'AAA', 3, 3, 1, 1, 2.5, 'dec_1')
                """
            )

        before = selection_db.read_bytes()
        result = cs.backfill_outcomes(
            "US",
            session_date="2026-06-04",
            db_path=self.db_path,
            ticker_selection_db_path=selection_db,
            candidate_audit_db_path=self.root / "missing_audit.db",
        )
        after = selection_db.read_bytes()

        self.assertGreaterEqual(result["updated"], 1)
        self.assertEqual(before, after)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            outcome = conn.execute("SELECT * FROM preopen_outcomes WHERE ticker='AAA'").fetchone()

        self.assertEqual(outcome["ticker_selection_log_id"], 7)
        self.assertEqual(outcome["actual_trade_ready"], 1)
        self.assertEqual(outcome["actual_ordered"], 1)

    def test_existing_ml_decisions_db_backfill_writes_only_shadow_db(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)
        ml_db = self.root / "source" / "decisions.db"
        ml_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(ml_db) as conn:
            conn.execute(
                """
                CREATE TABLE v2_learning_performance (
                    v2_decision_id TEXT PRIMARY KEY, market TEXT, runtime_mode TEXT,
                    session_date TEXT, ticker TEXT, route TEXT, path_run_id TEXT,
                    filled INTEGER, closed INTEGER, entry_price REAL, pnl_pct REAL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO v2_learning_performance
                (v2_decision_id, market, runtime_mode, session_date, ticker, route,
                 path_run_id, filled, closed, entry_price, pnl_pct)
                VALUES ('v2_aaa', 'US', 'live', '2026-06-04', 'AAA', 'PathB',
                        'path_aaa', 1, 1, 10.25, 3.4)
                """
            )

        before = ml_db.read_bytes()
        result = cs.backfill_outcomes(
            "US",
            session_date="2026-06-04",
            db_path=self.db_path,
            ticker_selection_db_path=self.root / "missing_selection.db",
            candidate_audit_db_path=self.root / "missing_audit.db",
            ml_decisions_db_path=ml_db,
        )
        after = ml_db.read_bytes()

        self.assertGreaterEqual(result["updated"], 1)
        self.assertEqual(before, after)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            outcome = conn.execute("SELECT * FROM preopen_outcomes WHERE ticker='AAA'").fetchone()

        self.assertEqual(outcome["v2_decision_id"], "v2_aaa")
        self.assertEqual(outcome["path_run_id"], "path_aaa")
        self.assertEqual(outcome["actual_ordered"], 1)
        self.assertEqual(outcome["entry_price"], 10.25)
        self.assertEqual(outcome["pnl_pct"], 3.4)

    def test_source_db_connection_is_read_only(self) -> None:
        source_db = self.root / "source" / "readonly_source.db"
        source_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source_db) as conn:
            conn.execute("CREATE TABLE sample (id INTEGER)")

        with cs._readonly_db(source_db) as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO sample (id) VALUES (1)")

    def test_shadow_writer_refuses_protected_operating_db_paths(self) -> None:
        protected_paths = [
            self.root / "data" / "ticker_selection_log.db",
            self.root / "data" / "audit" / "candidate_audit.db",
            self.root / "data" / "ml" / "decisions.db",
            self.root / "data" / "v2_event_store.db",
            self.root / "data" / "intraday_strategy_log.db",
            self.root / "data" / "audit" / "agent_call_events.db",
            self.root / "scratch" / "decisions.db",
        ]

        for protected in protected_paths:
            with self.subTest(path=protected):
                with self.assertRaises(cs.ContinuationShadowError):
                    cs.init_schema(protected)
                self.assertFalse(protected.exists())

    def test_shadow_writer_allows_only_preopen_continuation_db_name(self) -> None:
        allowed = self.root / "scratch" / "preopen_continuation_sim.db"
        blocked = self.root / "scratch" / "shadow.db"

        cs.init_schema(allowed)

        self.assertTrue(allowed.exists())
        with self.assertRaises(cs.ContinuationShadowError):
            cs.init_schema(blocked)
        self.assertFalse(blocked.exists())

    def test_report_missing_db_is_read_only_and_does_not_create_file(self) -> None:
        missing_db = self.root / "data" / "missing_report.db"

        payload = cs.build_report_payload(missing_db, market="US", date_from="2026-06-04", date_to="2026-06-04")

        self.assertEqual(payload["recommendation"], "shadow_continue")
        self.assertEqual(payload["candidate_count"], 0)
        self.assertFalse(missing_db.exists())

    def test_wrong_schema_db_returns_safe_status_instead_of_raising(self) -> None:
        wrong_db = self.root / "data" / "wrong_schema.db"
        wrong_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(wrong_db) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")

        result = cs.run_eval("US", session_date="2026-06-04", db_path=wrong_db, dry_run=True)
        payload = cs.build_report_payload(wrong_db, market="US", date_from="2026-06-04", date_to="2026-06-04")

        self.assertEqual(result["readiness"], "db_schema_unavailable")
        self.assertEqual(payload["recommendation"], "shadow_continue")
        self.assertIn("schema_error", payload)

    def test_report_cli_missing_db_does_not_write_default_report(self) -> None:
        from tools import preopen_continuation_shadow_report as report_cli

        missing_db = self.root / "data" / "missing_report_cli.db"
        stdout = io.StringIO()
        with patch.object(sys, "argv", ["prog", "--market", "US", "--db-path", str(missing_db)]), patch(
            "sys.stdout",
            stdout,
        ):
            rc = report_cli.main()

        self.assertEqual(rc, 0)
        self.assertFalse(missing_db.exists())
        self.assertFalse((self.root / "docs" / "reports").exists())
        self.assertIn("missing_db", stdout.getvalue())

    def test_report_payload_uses_shadow_continue_until_minimum_sample(self) -> None:
        self._save_us_state()
        self._save_outcome()
        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)

        payload = cs.build_report_payload(self.db_path, market="US", date_from="2026-06-04", date_to="2026-06-04")

        self.assertEqual(payload["candidate_count"], 2)
        self.assertEqual(payload["outcome_complete_rate"], 0.0)
        self.assertEqual(payload["recommendation"], "shadow_continue")
        markdown = cs.render_report_markdown(payload)
        self.assertIn("recommendation: `shadow_continue`", markdown)


if __name__ == "__main__":
    unittest.main()
