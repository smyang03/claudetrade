from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime.candidate_prompt_pool import build_trainer_prompt_pool
from runtime.candidate_quality_trainer import score_candidate_for_trainer


class CandidateQualityTrainerTests(unittest.TestCase):
    def test_us_high_liquidity_momentum_scores_above_mid_unclassified(self) -> None:
        strong = score_candidate_for_trainer(
            {
                "ticker": "NVDA",
                "market": "US",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 6.0,
            },
            market="US",
        )
        weak = score_candidate_for_trainer(
            {
                "ticker": "XYZ",
                "market": "US",
                "primary_bucket": "unclassified",
                "liquidity_bucket": "mid",
                "change_pct": 1.0,
            },
            market="US",
        )

        self.assertGreater(strong["trainer_prompt_score"], weak["trainer_prompt_score"])
        self.assertEqual(strong["trainer_candidate_state"], "PLAN_A")

    def test_kr_kosdaq_early_candidate_beats_kospi_late_momentum(self) -> None:
        kosdaq = score_candidate_for_trainer(
            {
                "ticker": "123456",
                "market": "KR",
                "market_type": "KOSDAQ",
                "primary_bucket": "liquidity_leader",
                "liquidity_bucket": "mid",
                "change_pct": 4.2,
            },
            market="KR",
        )
        kospi_chase = score_candidate_for_trainer(
            {
                "ticker": "005930",
                "market": "KR",
                "market_type": "KOSPI",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 16.0,
                "from_high_bucket": "at_high",
            },
            market="KR",
        )

        self.assertGreater(kosdaq["trainer_prompt_score"], kospi_chase["trainer_prompt_score"])
        self.assertGreater(kospi_chase["trainer_risk_score"], kosdaq["trainer_risk_score"])

    def test_future_fields_are_ignored_and_reported(self) -> None:
        scored = score_candidate_for_trainer(
            {
                "ticker": "AAPL",
                "market": "US",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 5.0,
                "forward_1d": -99.0,
                "ret60": -99.0,
            },
            market="US",
        )

        ignored = scored["trainer_score_components"]["future_fields_ignored"]
        self.assertIn("forward_1d", ignored)
        self.assertIn("ret60", ignored)
        self.assertGreater(scored["trainer_prompt_score"], 0)

    def test_prompt_pool_reorders_and_excludes_with_reason(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {"ticker": "BAD", "market": "US", "data_quality": "bad", "primary_bucket": "momentum_now"},
                {"ticker": "MID", "market": "US", "primary_bucket": "unclassified", "liquidity_bucket": "mid"},
                {"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 6},
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["GOOD"])
        excluded = {row["ticker"]: row["reason"] for row in result["excluded_from_prompt"]}
        self.assertEqual(excluded["BAD"], "trainer_quarantine")
        self.assertEqual(excluded["MID"], "prompt_cap")

    def test_select_tickers_uses_live_trainer_prompt_order_when_enabled(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text='{"watchlist":["GOOD","BAD"],"trade_ready":[],"reasons":{"GOOD":"watch"},"veto":{}}'
                    )
                ],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "true",
            "CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_TARGET_US": "2",
            "CANDIDATE_PROMPT_POOL_HARD_CAP_US": "2",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }
        with patch.dict("os.environ", env, clear=False), patch.object(analysts.client.messages, "create", side_effect=fake_create):
            selected, _ = analysts.select_tickers(
                "US",
                "digest",
                "NEUTRAL",
                [
                    {"ticker": "BAD", "market": "US", "primary_bucket": "unclassified", "liquidity_bucket": "mid", "change_pct": 1.0},
                    {"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 5.0},
                ],
            )

        self.assertEqual(selected[:1], ["GOOD"])
        self.assertLess(captured["prompt"].find("GOOD"), captured["prompt"].find("BAD"))
        self.assertIn("trainer=PLAN_A", captured["prompt"])
        meta = analysts.get_last_selection_meta()
        self.assertTrue(meta["_candidate_quality_trainer_enabled"])
        self.assertEqual(meta["_prompt_pool_count"], 2)


if __name__ == "__main__":
    unittest.main()
