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

    def test_lifecycle_report_tracks_promotion_without_forward_label_gate(self) -> None:
        result = build_candidate_pool(
            [
                {
                    "ticker": "WATCHME",
                    "market": "US",
                    "source": "preopen",
                    "post_open_5m_return_pct": 1.2,
                    "previous_lifecycle_state": "PROBATION",
                    "forward_1d": -99.0,
                }
            ],
            market="US",
            prompt_cap=1,
        )

        record = result.full_pool[0]
        self.assertEqual(record.lifecycle_state, "WATCH")
        self.assertEqual(result.lifecycle_report["counts"]["WATCH"], 1)
        self.assertEqual(result.lifecycle_report["promotions"][0]["from"], "PROBATION")
        self.assertEqual(result.lifecycle_report["promotions"][0]["to"], "WATCH")
        self.assertIn("forward_return_fields", result.lifecycle_report["label_policy"])

    def test_bad_data_candidate_is_quarantined(self) -> None:
        result = build_candidate_pool(
            [{"ticker": "BAD", "market": "US", "source": "opening_fresh", "data_quality": "bad"}],
            market="US",
            prompt_cap=1,
        )

        self.assertEqual(result.full_pool[0].lifecycle_state, "QUARANTINE")
        self.assertEqual(result.lifecycle_report["counts"]["QUARANTINE"], 1)

    def test_existing_status_maps_to_lifecycle_state(self) -> None:
        result = build_candidate_pool(
            [
                {"ticker": "READY", "market": "US", "status": "TRADE_READY"},
                {"ticker": "WATCH", "market": "US", "status": "WATCH"},
                {"ticker": "BENCH", "market": "US", "status": "NOT_IN_PROMPT"},
            ],
            market="US",
            prompt_cap=0,
        )

        states = {record.ticker: record.lifecycle_state for record in result.full_pool}
        self.assertEqual(states["READY"], "CORE")
        self.assertEqual(states["WATCH"], "WATCH")
        self.assertEqual(states["BENCH"], "BENCH")


if __name__ == "__main__":
    unittest.main()
