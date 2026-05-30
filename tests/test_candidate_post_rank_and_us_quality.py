from __future__ import annotations

import unittest

import pandas as pd

from runtime.candidate_post_rank import apply_candidate_post_rank
from runtime.us_candidate_quality import enrich_us_quality_shadow, enrich_us_runtime_quality_fallback


class CandidatePostRankAndUsQualityTests(unittest.TestCase):
    def test_kr_post_rank_shadow_annotates_without_reordering_by_default(self) -> None:
        rows = [
            {"ticker": "111111", "screen_score": 100, "candidate_quality_score": 40, "trainer_cohort_reliability": 0.2},
            {"ticker": "222222", "screen_score": 99, "candidate_quality_score": 85, "trainer_cohort_reliability": 0.9},
        ]

        ranked, meta = apply_candidate_post_rank(rows, market="KR", enforce=False)

        self.assertEqual([row["ticker"] for row in ranked], ["111111", "222222"])
        self.assertTrue(meta["enabled"])
        self.assertGreater(abs(ranked[0]["rank_delta"]), 0)
        self.assertEqual(ranked[0]["post_rank_version"], "candidate_post_rank_v1")

    def test_kr_post_rank_enforce_reorders_when_requested(self) -> None:
        rows = [
            {"ticker": "111111", "screen_score": 100, "candidate_quality_score": 40, "trainer_cohort_reliability": 0.2},
            {"ticker": "222222", "screen_score": 99, "candidate_quality_score": 85, "trainer_cohort_reliability": 0.9},
        ]

        ranked, meta = apply_candidate_post_rank(rows, market="KR", enforce=True)

        self.assertTrue(meta["enforce"])
        self.assertEqual(ranked[0]["ticker"], "222222")

    def test_us_quality_shadow_attaches_metadata_without_rank_fields(self) -> None:
        candles = pd.DataFrame(
            {
                "close": [100 + idx for idx in range(70)],
                "volume": [1_000_000 + idx * 1000 for idx in range(70)],
            }
        )

        row = enrich_us_quality_shadow(
            {"ticker": "NVDA", "market": "US", "price": 169, "liquidity_bucket": "high", "from_high_pct": -3.0},
            candles,
        )

        self.assertTrue(row["us_quality_shadow_only"])
        self.assertIn("us_quality_score_shadow", row)
        self.assertIn("us_rs20_shadow", row)
        self.assertGreater(row["candidate_quality_score"], 0)
        self.assertIn(row["candidate_quality_grade"], {"A", "B", "C", "D"})
        self.assertEqual(row["quality_source"], "us_runtime_quality_fallback:v1")
        self.assertNotIn("trainer_prompt_score", row)

    def test_us_quality_fallback_ignores_forward_outcome_fields(self) -> None:
        base = enrich_us_quality_shadow(
            {
                "ticker": "NVDA",
                "market": "US",
                "price": 169,
                "turnover": 200_000_000,
                "volume_ratio": 2.0,
                "primary_bucket": "momentum_now",
                "history_usable_rows": 80,
                "history_required_rows": 65,
                "ret_5m_pct": 1.2,
            }
        )
        leaked = enrich_us_quality_shadow(
            {
                **base,
                "candidate_quality_score": "",
                "forward_1d": -99,
                "forward_30m_from_bucket": -99,
                "ret30": -99,
                "pnl_pct": -99,
            }
        )

        self.assertEqual(base["candidate_quality_score"], leaked["candidate_quality_score"])
        self.assertIn("future_fields_ignored", leaked["candidate_quality_flags"])
        self.assertIn("forward_1d", leaked["candidate_quality_components"]["ignored_future_fields"])
        self.assertIn("forward_30m_from_bucket", leaked["candidate_quality_components"]["ignored_future_fields"])
        self.assertIn("ret30", leaked["candidate_quality_components"]["ignored_future_fields"])

    def test_us_runtime_quality_sets_required_history_rows_from_candles(self) -> None:
        candles = pd.DataFrame({"close": [100 + idx for idx in range(70)], "volume": [1000] * 70})

        row = enrich_us_runtime_quality_fallback(
            {
                "ticker": "NVDA",
                "market": "US",
                "price": 169,
                "turnover": 200_000_000,
                "volume_ratio": 2.0,
                "primary_bucket": "momentum_now",
            },
            candles,
        )

        self.assertEqual(row["history_usable_rows"], 70)
        self.assertEqual(row["history_required_rows"], 65)
        self.assertNotIn("history_required_rows_missing", row["quality_data_gaps"])

    def test_empty_candles_preserve_required_history_contract(self) -> None:
        row = enrich_us_runtime_quality_fallback({"ticker": "NVDA", "market": "US"}, pd.DataFrame())

        self.assertEqual(row["history_usable_rows"], 0)
        self.assertEqual(row["history_required_rows"], 65)
        self.assertIn("history_incomplete", row["quality_data_gaps"])


if __name__ == "__main__":
    unittest.main()
