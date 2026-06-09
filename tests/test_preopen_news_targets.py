from __future__ import annotations

import unittest
from unittest.mock import patch

from phase1_trainer.preopen_news_targets import load_preopen_news_targets


class PreopenNewsTargetsTests(unittest.TestCase):
    def test_loads_stale_state_with_zero_max_age_and_sorts_candidates(self) -> None:
        state = {
            "candidates": [
                {"ticker": "000003", "name": "Third", "provider_rank": 1, "preopen_score": 99},
                {"ticker": "000002", "name": "Second", "shadow_preopen_rank": 2},
                {"ticker": "000001", "name": "First", "shadow_preopen_rank": 1},
                {"ticker": "000001", "name": "Duplicate", "shadow_preopen_rank": 3},
            ],
        }
        captured = {}

        def fake_load(*args, **kwargs):
            captured.update(kwargs)
            return state

        with patch("phase1_trainer.preopen_news_targets.load_preopen_state", side_effect=fake_load):
            targets = load_preopen_news_targets("KR", "2026-05-15", limit=3, mode="live")

        self.assertEqual(captured["max_age_min"], 0)
        self.assertEqual(list(targets), ["000001", "000002", "000003"])
        self.assertEqual(targets["000001"], "First")

    def test_fallback_sort_uses_provider_rank_then_score(self) -> None:
        state = {
            "candidates": [
                {"ticker": "bbb", "provider_rank": 5, "preopen_score": 10},
                {"ticker": "aaa", "provider_rank": 2, "preopen_score": 1},
                {"ticker": "ccc", "preopen_score": 99},
            ],
        }
        with patch("phase1_trainer.preopen_news_targets.load_preopen_state", return_value=state):
            targets = load_preopen_news_targets("US", "2026-05-15", limit=3, max_age_min=480)

        self.assertEqual(list(targets), ["AAA", "BBB", "CCC"])

    def test_candidate_log_supplements_state_targets_after_current_state(self) -> None:
        state = {
            "candidates": [
                {"ticker": "005930", "name": "Samsung", "shadow_preopen_rank": 1},
                {"ticker": "000660", "name": "SK hynix", "shadow_preopen_rank": 2},
            ],
        }
        log_rows = [
            {"ticker": "005930", "name": "Old Samsung", "shadow_preopen_rank": 1},
            {"ticker": "035420", "name": "NAVER", "shadow_preopen_rank": 1},
            {"ticker": "035720", "name": "Kakao", "provider_rank": 2},
        ]

        with patch.dict("os.environ", {"PREOPEN_NEWS_INCLUDE_CANDIDATE_LOG": "true"}), \
             patch("phase1_trainer.preopen_news_targets.load_preopen_state", return_value=state), \
             patch("phase1_trainer.preopen_news_targets.read_jsonl_tail", return_value=log_rows):
            targets = load_preopen_news_targets("KR", "2026-05-15", limit=4, max_age_min=0)

        self.assertEqual(list(targets), ["005930", "000660", "035420", "035720"])
        self.assertEqual(targets["005930"], "Samsung")

    def test_kr_default_target_limit_expands_for_preopen_candidate_churn(self) -> None:
        state = {
            "candidates": [
                {"ticker": f"{idx:06d}", "name": f"Stock {idx}", "shadow_preopen_rank": idx}
                for idx in range(1, 101)
            ],
        }

        with patch.dict("os.environ", {}, clear=True), \
             patch("phase1_trainer.preopen_news_targets.load_preopen_state", return_value=state), \
             patch("phase1_trainer.preopen_news_targets.read_jsonl_tail", return_value=[]):
            targets = load_preopen_news_targets("KR", "2026-05-15", max_age_min=0)

        self.assertEqual(len(targets), 100)


if __name__ == "__main__":
    unittest.main()
