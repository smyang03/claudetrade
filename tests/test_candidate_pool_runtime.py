from __future__ import annotations

import unittest

from runtime.candidate_pool_runtime import build_candidate_pool, candidate_from_raw


class CandidatePoolRuntimeTests(unittest.TestCase):
    def test_merge_sources_keeps_single_ticker(self) -> None:
        result = build_candidate_pool(
            [
                {
                    "ticker": "076610",
                    "market": "KR",
                    "source": "preopen",
                    "shadow_preopen_rank": 44,
                    "preopen_score": 0.55,
                    "preopen_grade": "C",
                    "post_open_5m_return_pct": 5.6,
                },
                {
                    "ticker": "076610",
                    "market": "KR",
                    "source": "opening_fresh",
                    "source_rank": 3,
                    "source_score": 0.8,
                    "preopen_grade": "B",
                },
            ],
            market="KR",
            prompt_cap=30,
        )

        self.assertEqual(len(result.full_pool), 1)
        record = result.full_pool[0]
        self.assertEqual(record.sources, ["preopen", "opening_fresh"])
        self.assertEqual(record.grade, "B")
        self.assertGreater(record.prompt_score, 0)

    def test_prompt_cap_records_exclusion_reason(self) -> None:
        result = build_candidate_pool(
            [
                {"ticker": "A", "market": "US", "source": "opening_fresh"},
                {"ticker": "B", "market": "US", "source": "soft_pin"},
                {"ticker": "C", "market": "US", "source": "base_universe"},
            ],
            market="US",
            prompt_cap=2,
        )

        self.assertEqual(len(result.prompt_pool), 2)
        self.assertEqual(result.excluded_from_prompt[0]["reason"], "prompt_cap")

    def test_soft_pin_stays_in_full_pool(self) -> None:
        result = build_candidate_pool(
            [{"ticker": "005930", "market": "KR", "source": "soft_pin"}],
            market="KR",
            prompt_cap=0,
        )

        self.assertEqual(len(result.full_pool), 1)
        self.assertEqual(result.full_pool[0].sources, ["soft_pin"])

    def test_deferred_source_does_not_add_prompt_bonus(self) -> None:
        record = candidate_from_raw(
            {"ticker": "LATE", "market": "US", "source": "late_mover"},
            market="US",
        )
        result = build_candidate_pool([record], market="US", prompt_cap=1)

        self.assertEqual(result.full_pool[0].prompt_score, 0)
        self.assertEqual(result.deferred_sources, ["late_mover"])

    def test_prompt_score_is_clamped_to_100(self) -> None:
        result = build_candidate_pool(
            [
                {
                    "ticker": "HOT",
                    "market": "US",
                    "source": "preopen",
                    "policy_tags": ["hard_pin", "soft_pin"],
                    "post_open_5m_return_pct": 5.0,
                },
                {
                    "ticker": "HOT",
                    "market": "US",
                    "source": "opening_fresh",
                },
            ],
            market="US",
            prompt_cap=1,
        )

        self.assertLessEqual(result.full_pool[0].prompt_score, 100)


if __name__ == "__main__":
    unittest.main()
