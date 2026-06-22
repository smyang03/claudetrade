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

    def _save_us_state(self, *, mode: str = "live", alpha_name: str = "Alpha") -> None:
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
                        "name": alpha_name,
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
            mode=mode,
        )

    def _save_kr_state(self, *, mode: str = "live") -> None:
        captured_at = datetime.now(KST).isoformat(timespec="seconds")
        save_preopen_state(
            "KR",
            {
                "market": "KR",
                "session_date": "2026-06-05",
                "captured_at": captured_at,
                "collector_status": "ok",
                "provider": "kis_volume_rank",
                "data_quality": "kis_volume_rank",
                "candidates": [
                    {
                        "ticker": "005930",
                        "name": "Samsung",
                        "market": "KR",
                        "session_date": "2026-06-05",
                        "source": "kis_screen_market_kr",
                        "shadow_preopen_rank": 5,
                        "extended_change_pct": 4.2,
                        "extended_price": 70000.0,
                        "extended_volume": 100000,
                        "prior_day_traded_value": 7_000_000_000,
                        "extended_dollar_volume": 7_000_000_000,
                        "regular_open_price": 71000.0,
                        "news_or_earnings_flag": True,
                        "risk_tags": ["open_auction"],
                        "actual_selected": True,
                        "actual_selection_rank": 3,
                    },
                    {
                        "ticker": "000020",
                        "name": "Low Liquidity",
                        "market": "KR",
                        "session_date": "2026-06-05",
                        "source": "kis_screen_market_kr",
                        "shadow_preopen_rank": 8,
                        "extended_change_pct": 2.0,
                        "extended_price": 5000.0,
                        "extended_volume": 10000,
                        "prior_day_traded_value": 500_000_000,
                        "extended_dollar_volume": 500_000_000,
                        "regular_open_price": 5050.0,
                    },
                ],
            },
            session_date="2026-06-05",
            mode=mode,
        )

    def _save_outcome(self, *, mode: str = "live", price: float = 11.0) -> None:
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
                "price": price,
                "high": max(12.0, price),
                "low": 9.5,
                "volume": 1_500_000,
                "post_open_return_pct": 22.2222,
                "post_open_30m_return_pct": (price - 9.0) / 9.0 * 100.0,
                "price_source": "test",
            },
            mode=mode,
        )

    def _save_dense_outcome(self, *, mode: str = "live") -> None:
        samples = [
            {
                "offset_min": 5,
                "price": 10.5,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "return_pct": 16.6667,
                "high": 10.7,
                "low": 9.9,
                "volume": 500_000,
                "price_source": "test",
            },
            {
                "offset_min": 10,
                "price": 10.2,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "return_pct": 13.3333,
                "high": 10.8,
                "low": 9.8,
                "volume": 700_000,
                "price_source": "test",
            },
            {
                "offset_min": 15,
                "price": 10.8,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "return_pct": 20.0,
                "high": 11.0,
                "low": 9.7,
                "volume": 900_000,
                "price_source": "test",
            },
        ]
        save_outcome_record(
            "US",
            "2026-06-04",
            {
                "ticker": "AAA",
                "name": "Alpha",
                "offset_min": 15,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "outcome_status": "WIN",
                "token_status": "ok",
                "anchor_price": 9.0,
                "regular_open_price": 10.0,
                "price": 10.8,
                "high": 11.0,
                "low": 9.7,
                "volume": 900_000,
                "post_open_return_pct": 20.0,
                "post_open_15m_return_pct": 20.0,
                "price_source": "test",
                "outcome_samples": samples,
            },
            mode=mode,
        )

    def _save_kr_outcome(self, *, mode: str = "live", price: float = 72420.0) -> None:
        save_outcome_record(
            "KR",
            "2026-06-05",
            {
                "ticker": "005930",
                "name": "Samsung",
                "offset_min": 30,
                "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                "outcome_status": "WIN",
                "anchor_price": 70000.0,
                "regular_open_price": 71000.0,
                "price": price,
                "high": max(73000.0, price),
                "low": 70400.0,
                "volume": 250000,
                "post_open_30m_return_pct": (price - 71000.0) / 71000.0 * 100.0,
                "price_source": "test",
            },
            mode=mode,
        )

    def test_unsupported_market_guard(self) -> None:
        with self.assertRaisesRegex(cs.ContinuationShadowError, "preopen_continuation_shadow_supports_kr_us_only"):
            cs.collect_candidates("JP", session_date="2026-06-04", db_path=self.db_path)

    def test_collect_kr_candidates_uses_kr_eligibility_rules(self) -> None:
        self._save_kr_state()

        result = cs.collect_candidates("KR", session_date="2026-06-05", db_path=self.db_path)

        self.assertEqual(result["written"], 2)
        self.assertEqual(result["eligible_count"], 1)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = {
                row["ticker"]: row
                for row in conn.execute(
                    "SELECT ticker, eligible, exclusion_reason, eligible_components_json FROM preopen_candidates"
                ).fetchall()
            }

        self.assertEqual(rows["005930"]["eligible"], 1)
        self.assertEqual(rows["000020"]["eligible"], 0)
        self.assertIn("indicative_3_20", rows["000020"]["exclusion_reason"])
        self.assertIn("kr_traded_value_3b", rows["000020"]["exclusion_reason"])

    def test_kr_feature_snapshot_builds_eval_case(self) -> None:
        self._save_kr_state()
        self._save_kr_outcome()
        cs.collect_candidates("KR", session_date="2026-06-05", db_path=self.db_path)
        cs.record_feature_snapshots("KR", session_date="2026-06-05", offset_min=30, db_path=self.db_path)

        cases, readiness = cs.build_eval_cases(
            self.db_path,
            session_date="2026-06-05",
            market="KR",
            offset_min=30,
        )

        self.assertEqual(readiness, "ready")
        self.assertEqual(len(cases), 1)
        self.assertEqual(cases[0].ticker, "005930")
        self.assertEqual(cases[0].payload["market"], "KR")
        self.assertIsNone(cases[0].payload["dv_bucket"])
        self.assertEqual(cases[0].payload["liquidity_bucket"], "KRW_3B_10B")
        prompt = cs.build_prompt(cases)
        self.assertIn("Judge KR preopen continuation candidates", prompt)
        self.assertIn("KRW traded-value bucket", prompt)

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

    def test_shadow_db_separates_live_and_paper_candidates_outcomes_and_checks(self) -> None:
        self._save_us_state(mode="live", alpha_name="Live Alpha")
        self._save_us_state(mode="paper", alpha_name="Paper Alpha")
        self._save_outcome(mode="live", price=11.0)
        self._save_outcome(mode="paper", price=12.0)

        cs.collect_candidates("US", session_date="2026-06-04", mode="live", db_path=self.db_path)
        cs.collect_candidates("US", session_date="2026-06-04", mode="paper", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", mode="live", offset_min=30, db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", mode="paper", offset_min=30, db_path=self.db_path)
        live_eval = cs.run_eval("US", session_date="2026-06-04", mode="live", db_path=self.db_path, no_claude=True)
        paper_eval = cs.run_eval("US", session_date="2026-06-04", mode="paper", db_path=self.db_path, no_claude=True)

        self.assertEqual(live_eval["status"], "skipped")
        self.assertEqual(paper_eval["status"], "skipped")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            candidates = conn.execute(
                "SELECT runtime_mode, name FROM preopen_candidates WHERE ticker='AAA' ORDER BY runtime_mode"
            ).fetchall()
            outcomes = conn.execute(
                "SELECT runtime_mode, ret_30m FROM preopen_outcomes WHERE ticker='AAA' ORDER BY runtime_mode"
            ).fetchall()
            checks = conn.execute(
                "SELECT runtime_mode, attempt_no FROM preopen_claude_checks WHERE skip_reason='no_claude' ORDER BY runtime_mode"
            ).fetchall()

        self.assertEqual([(row["runtime_mode"], row["name"]) for row in candidates], [("live", "Live Alpha"), ("paper", "Paper Alpha")])
        self.assertEqual([row["runtime_mode"] for row in outcomes], ["live", "paper"])
        self.assertNotEqual(outcomes[0]["ret_30m"], outcomes[1]["ret_30m"])
        self.assertEqual([(row["runtime_mode"], row["attempt_no"]) for row in checks], [("live", 1), ("paper", 1)])

    def test_shadow_db_separates_kr_and_us_candidates_and_outcomes(self) -> None:
        self._save_us_state()
        self._save_kr_state()
        self._save_outcome()
        self._save_kr_outcome()

        cs.collect_candidates("US", session_date="2026-06-04", db_path=self.db_path)
        cs.collect_candidates("KR", session_date="2026-06-05", db_path=self.db_path)
        cs.record_feature_snapshots("US", session_date="2026-06-04", offset_min=30, db_path=self.db_path)
        cs.record_feature_snapshots("KR", session_date="2026-06-05", offset_min=30, db_path=self.db_path)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            candidates = conn.execute(
                "SELECT market, COUNT(*) AS c FROM preopen_candidates GROUP BY market ORDER BY market"
            ).fetchall()
            outcomes = conn.execute(
                "SELECT market, COUNT(*) AS c FROM preopen_outcomes GROUP BY market ORDER BY market"
            ).fetchall()

        self.assertEqual([(row["market"], row["c"]) for row in candidates], [("KR", 2), ("US", 2)])
        self.assertEqual([(row["market"], row["c"]) for row in outcomes], [("KR", 2), ("US", 2)])

    def test_outcome_latest_decision_is_filtered_by_runtime_mode(self) -> None:
        self._save_us_state(mode="live", alpha_name="Live Alpha")
        self._save_us_state(mode="paper", alpha_name="Paper Alpha")
        self._save_outcome(mode="live", price=11.0)
        self._save_outcome(mode="paper", price=12.0)

        for mode in ("live", "paper"):
            cs.collect_candidates("US", session_date="2026-06-04", mode=mode, db_path=self.db_path)
            cs.record_feature_snapshots("US", session_date="2026-06-04", mode=mode, offset_min=30, db_path=self.db_path)

        responses = [
            ('{"cases":[["C01","PROMOTE",0.8,"LIVE_OK"]]}', 40, 10, 6),
            ('{"cases":[["C01","DROP",0.7,"PAPER_DROP"]]}', 40, 10, 6),
        ]
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}, clear=False), patch(
            "preopen.continuation_shadow._call_claude", side_effect=responses
        ), patch("preopen.continuation_shadow._save_eval_raw_call", side_effect=["live.json", "paper.json"]), patch(
            "preopen.continuation_shadow._record_eval_credit"
        ):
            live_eval = cs.run_eval("US", session_date="2026-06-04", mode="live", db_path=self.db_path)
            paper_eval = cs.run_eval("US", session_date="2026-06-04", mode="paper", db_path=self.db_path)

        self.assertEqual(live_eval["status"], "called")
        self.assertEqual(paper_eval["status"], "called")
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT o.runtime_mode, d.decision
                FROM preopen_outcomes o
                LEFT JOIN preopen_claude_decisions d ON d.id=o.latest_decision_id
                WHERE o.ticker='AAA'
                ORDER BY o.runtime_mode
                """
            ).fetchall()

        self.assertEqual([(row["runtime_mode"], row["decision"]) for row in rows], [("live", "PROMOTE"), ("paper", "DROP")])

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

    def test_dense_feature_snapshot_range_writes_5m_offsets_to_close(self) -> None:
        self._save_us_state()
        self._save_dense_outcome()

        with patch("preopen.continuation_shadow._close_offset_min", return_value=15):
            result = cs.record_feature_snapshot_range(
                "US",
                session_date="2026-06-04",
                interval_min=5,
                db_path=self.db_path,
            )

        self.assertEqual(result["offsets"], [5, 10, 15])
        self.assertEqual(result["offset_count"], 3)
        self.assertEqual(result["snapshots"], 6)
        self.assertEqual(result["sampled"], 3)
        self.assertEqual(result["missing"], 3)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            features = conn.execute(
                "SELECT ticker, offset_min, snapshot_status, return_from_open_pct FROM preopen_feature_snapshots ORDER BY ticker, offset_min"
            ).fetchall()
            outcome = conn.execute("SELECT * FROM preopen_outcomes WHERE ticker='AAA'").fetchone()

        aaa = [row for row in features if row["ticker"] == "AAA"]
        bbb = [row for row in features if row["ticker"] == "BBB"]
        self.assertEqual([row["offset_min"] for row in aaa], [5, 10, 15])
        self.assertEqual([row["snapshot_status"] for row in aaa], ["sampled", "sampled", "sampled"])
        self.assertEqual([row["snapshot_status"] for row in bbb], ["missing", "missing", "missing"])
        self.assertEqual(aaa[0]["return_from_open_pct"], 5.0)
        self.assertEqual(outcome["ret_5m"], 5.0)
        self.assertEqual(outcome["ret_close"], 8.0)
        self.assertEqual(outcome["outcome_status"], "complete")

    def test_dense_report_payload_summarizes_5m_curve(self) -> None:
        self._save_us_state()
        self._save_dense_outcome()
        with patch("preopen.continuation_shadow._close_offset_min", return_value=15):
            cs.record_feature_snapshot_range(
                "US",
                session_date="2026-06-04",
                interval_min=5,
                db_path=self.db_path,
            )

        payload = cs.build_report_payload(self.db_path, market="US", date_from="2026-06-04", date_to="2026-06-04")
        dense = payload["dense_curve"]
        markdown = cs.render_report_markdown(payload)

        self.assertEqual(dense["offset_count"], 3)
        self.assertEqual(dense["sampled_offset_count"], 3)
        self.assertEqual(dense["sampled_snapshot_count"], 3)
        self.assertEqual(dense["missing_snapshot_count"], 3)
        self.assertEqual(dense["avg_time_to_peak_min"], 15.0)
        self.assertEqual(dense["avg_time_to_trough_min"], 10.0)
        self.assertIn("## Dense Curve", markdown)

    def test_dense_feature_offsets_include_close_when_interval_does_not_divide_session(self) -> None:
        with patch("preopen.continuation_shadow._close_offset_min", return_value=17):
            offsets = cs.dense_feature_offsets_min("US", "2026-06-04", interval_min=5)

        self.assertEqual(offsets, (5, 10, 15, 17))

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
        payload = cs.build_report_payload(
            self.db_path,
            market="US",
            mode="live",
            date_from="2026-06-04",
            date_to="2026-06-04",
        )
        self.assertEqual(payload["claude"]["called"], 2)
        self.assertEqual(payload["claude"]["parse_success_rate"], 50.0)

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

    def test_readonly_source_connection_reads_live_wal_rows(self) -> None:
        source_db = self.root / "source" / "wal_source.db"
        source_db.parent.mkdir(parents=True, exist_ok=True)
        writer = sqlite3.connect(source_db)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("CREATE TABLE sample (id INTEGER)")
            writer.commit()
            writer.execute("INSERT INTO sample (id) VALUES (7)")
            writer.commit()

            with cs._readonly_db(source_db) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM sample").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT id FROM sample").fetchone()[0], 7)
        finally:
            writer.close()

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
