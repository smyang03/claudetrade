from __future__ import annotations

import unittest

import pandas as pd

from runtime.candidate_post_rank import apply_candidate_post_rank
from runtime.us_candidate_quality import enrich_us_quality_shadow


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
        self.assertNotIn("trainer_prompt_score", row)


if __name__ == "__main__":
    unittest.main()
