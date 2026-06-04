from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from runtime import sub_screener


def _row(ticker: str, state: str, score: float) -> dict:
    return {
        "ticker": ticker,
        "trainer_candidate_state": state,
        "trainer_prompt_score": score,
    }


class SubScreenerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env_patch = patch.dict(os.environ, {"SUB_SCREENER_STATE_DIR": self.tmp.name}, clear=False)
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_no_trigger_when_all_in_watchlist(self) -> None:
        result = sub_screener.scan_new_candidates(
            "US",
            {"GOOD"},
            [{"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 6.0}],
        )

        self.assertFalse(result.should_trigger)
        self.assertEqual(result.trigger_reason, "no_trigger")

    def test_trigger_on_new_plan_a(self) -> None:
        result = sub_screener.scan_new_candidates(
            "US",
            set(),
            [{"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 6.0}],
        )

        self.assertTrue(result.should_trigger)
        self.assertEqual(result.trigger_reason, "new_plan_a:1")
        self.assertEqual([row["ticker"] for row in result.new_plan_a], ["GOOD"])

    def test_no_trigger_plan_b_below_threshold(self) -> None:
        with patch(
            "runtime.sub_screener.build_trainer_prompt_pool",
            return_value={"scored_pool": [_row("A", "PLAN_B", 66.0)]},
        ):
            result = sub_screener.scan_new_candidates("US", set(), [{"ticker": "A"}])

        self.assertFalse(result.should_trigger)
        self.assertEqual(result.trigger_reason, "no_trigger")

    def test_trigger_on_plan_b_high_score(self) -> None:
        with patch(
            "runtime.sub_screener.build_trainer_prompt_pool",
            return_value={"scored_pool": [_row("A", "PLAN_B", 66.0), _row("B", "PLAN_B", 65.0)]},
        ):
            result = sub_screener.scan_new_candidates("US", set(), [{"ticker": "A"}, {"ticker": "B"}])

        self.assertTrue(result.should_trigger)
        self.assertEqual(result.trigger_reason, "new_plan_b_high:2")

    def test_exclude_set_respected(self) -> None:
        with patch(
            "runtime.sub_screener.build_trainer_prompt_pool",
            return_value={"scored_pool": [_row("A", "PLAN_A", 90.0), _row("B", "PLAN_B", 66.0)]},
        ):
            result = sub_screener.scan_new_candidates("US", {"a"}, [{"ticker": "A"}, {"ticker": "B"}])

        self.assertFalse(result.should_trigger)
        self.assertEqual([row["ticker"] for row in result.all_new_scored], ["B"])

    def test_scored_pool_not_prompt_pool(self) -> None:
        with patch(
            "runtime.sub_screener.build_trainer_prompt_pool",
            return_value={"prompt_pool": [], "scored_pool": [_row("CAPOUT", "PLAN_A", 90.0)]},
        ):
            result = sub_screener.scan_new_candidates("US", set(), [{"ticker": "CAPOUT"}])

        self.assertTrue(result.should_trigger)
        self.assertEqual([row["ticker"] for row in result.new_plan_a], ["CAPOUT"])

    def test_rate_limit_blocks_after_max(self) -> None:
        trigger = sub_screener.SubScanResult(True, [_row("A", "PLAN_A", 90.0)], [], [], "new_plan_a:1")
        for _ in range(5):
            sub_screener.record_attempt("US", "2026-05-22", trigger)

        self.assertTrue(
            sub_screener.is_rate_limited("US", "2026-05-22", max_per_session=5, min_interval_sec=0)
        )

    def test_rate_limit_min_interval(self) -> None:
        trigger = sub_screener.SubScanResult(True, [_row("A", "PLAN_A", 90.0)], [], [], "new_plan_a:1")
        sub_screener.record_attempt("US", "2026-05-22", trigger)

        self.assertTrue(
            sub_screener.is_rate_limited("US", "2026-05-22", max_per_session=5, min_interval_sec=900)
        )

    def test_record_scan_tracks_detection_in_shadow(self) -> None:
        no_trigger = sub_screener.SubScanResult(False, [], [], [_row("A", "WATCH", 45.0)], "no_trigger")
        trigger = sub_screener.SubScanResult(True, [_row("B", "PLAN_A", 90.0)], [], [], "new_plan_a:1")

        sub_screener.record_scan("US", "2026-05-22", no_trigger)
        sub_screener.record_scan("US", "2026-05-22", trigger)
        state = sub_screener.load_session_counter("US", "2026-05-22")

        self.assertEqual(state["scan_count"], 2)
        self.assertEqual(state["detection_count"], 1)
        self.assertEqual(state["attempt_count"], 0)
        self.assertEqual(state["last_detection"]["new_tickers"], ["B"])

    def test_state_file_scan_attempt_success_separate(self) -> None:
        trigger = sub_screener.SubScanResult(True, [_row("A", "PLAN_A", 90.0)], [], [], "new_plan_a:1")

        sub_screener.record_scan("US", "2026-05-22", trigger)
        sub_screener.record_attempt("US", "2026-05-22", trigger)
        sub_screener.record_success("US", "2026-05-22")
        state = sub_screener.load_session_counter("US", "2026-05-22")

        self.assertEqual(state["scan_count"], 1)
        self.assertEqual(state["detection_count"], 1)
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["success_count"], 1)
        self.assertTrue(state["attempts"][-1]["success"])

    def test_state_file_persisted_across_reload(self) -> None:
        trigger = sub_screener.SubScanResult(True, [_row("A", "PLAN_A", 90.0)], [], [], "new_plan_a:1")
        sub_screener.record_attempt("KR", "2026-05-22", trigger)

        state = sub_screener.load_session_counter("KR", "2026-05-22")

        self.assertEqual(state["market"], "KR")
        self.assertEqual(state["date"], "2026-05-22")
        self.assertEqual(state["attempt_count"], 1)

    def test_duplicate_trigger_suppression_tracks_exact_ticker_set(self) -> None:
        trigger = sub_screener.SubScanResult(
            True,
            [_row("B", "PLAN_A", 90.0), _row("A", "PLAN_A", 88.0)],
            [],
            [],
            "new_plan_a:2",
        )
        same_set = sub_screener.SubScanResult(
            True,
            [_row("A", "PLAN_A", 91.0), _row("B", "PLAN_A", 89.0)],
            [],
            [],
            "new_plan_a:2",
        )
        new_set = sub_screener.SubScanResult(True, [_row("C", "PLAN_A", 90.0)], [], [], "new_plan_a:1")

        sub_screener.record_attempt("US", "2026-05-22", trigger)

        self.assertTrue(sub_screener.is_duplicate_trigger("US", "2026-05-22", same_set, ttl_sec=3600))
        self.assertFalse(sub_screener.is_duplicate_trigger("US", "2026-05-22", new_set, ttl_sec=3600))

        sub_screener.record_dedupe_suppressed("US", "2026-05-22", same_set, ttl_sec=3600)
        state = sub_screener.load_session_counter("US", "2026-05-22")

        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["dedupe_suppressed_count"], 1)
        self.assertEqual(state["last_dedupe_suppressed"]["fingerprint"], "A|B")

    def test_triage_candidates_and_success_state(self) -> None:
        trigger = sub_screener.SubScanResult(
            True,
            [_row("A", "PLAN_A", 90.0)],
            [_row("B", "PLAN_B", 70.0)],
            [_row("C", "PLAN_B", 66.0)],
            "new_plan_a:1",
        )

        rows = sub_screener.triage_candidates(trigger, max_add=2)
        self.assertEqual([row["ticker"] for row in rows], ["A", "B"])

        sub_screener.record_attempt("US", "2026-05-22", trigger)
        sub_screener.record_triage_success(
            "US",
            "2026-05-22",
            trigger,
            added_tickers=["A"],
            skipped_tickers=["B"],
        )
        state = sub_screener.load_session_counter("US", "2026-05-22")

        self.assertEqual(state["triage_success_count"], 1)
        self.assertEqual(state["success_count"], 1)
        self.assertTrue(state["attempts"][-1]["triage"])
        self.assertEqual(state["last_triage"]["added_tickers"], ["A"])


if __name__ == "__main__":
    unittest.main()
