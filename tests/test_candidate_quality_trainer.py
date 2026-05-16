from __future__ import annotations

import json
from pathlib import Path
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

    def test_trainer_penalty_defaults_are_env_overridable_without_changing_default(self) -> None:
        base = score_candidate_for_trainer(
            {
                "ticker": "005930",
                "market": "KR",
                "market_type": "KOSPI",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 16.0,
            },
            market="KR",
        )
        with patch.dict("os.environ", {"CANDIDATE_TRAINER_KR_CHASE_CHANGE_PENALTY": "0"}, clear=False):
            overridden = score_candidate_for_trainer(
                {
                    "ticker": "005930",
                    "market": "KR",
                    "market_type": "KOSPI",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 16.0,
                },
                market="KR",
            )

        self.assertEqual(base["trainer_score_components"]["prompt"]["kr_chase_change_penalty"], -12.0)
        self.assertEqual(overridden["trainer_score_components"]["prompt"]["kr_chase_change_penalty"], 0.0)
        self.assertGreater(overridden["trainer_prompt_score"], base["trainer_prompt_score"])

    def test_trainer_threshold_and_kr_weak_bucket_config_follow_contract_names(self) -> None:
        candidate = {
            "ticker": "005930",
            "market": "KR",
            "market_type": "KOSPI",
            "primary_bucket": "momentum_now",
            "liquidity_bucket": "high",
            "change_pct": 4.0,
        }
        base = score_candidate_for_trainer(candidate, market="KR")

        env = {
            "TRAINER_KR_WEAK_IMMEDIATE_BUCKET_PENALTY": "0",
            "TRAINER_KR_WEAK_IMMEDIATE_BUCKET_RISK": "0",
            "TRAINER_PLAN_A_SCORE_MIN": "99",
            "TRAINER_PLAN_B_SCORE_MIN": "99",
        }
        with patch.dict("os.environ", env, clear=False):
            overridden = score_candidate_for_trainer(candidate, market="KR")

        self.assertEqual(base["trainer_score_components"]["config"]["kr_weak_immediate_bucket_penalty"], -8.0)
        self.assertEqual(overridden["trainer_score_components"]["prompt"]["kr_weak_immediate_bucket"], 0.0)
        self.assertEqual(overridden["trainer_score_components"]["risk"]["kr_weak_immediate_bucket_risk"], 0.0)
        self.assertEqual(overridden["trainer_score_components"]["config"]["plan_a_score_min"], 99.0)
        self.assertNotEqual(base["trainer_candidate_state"], overridden["trainer_candidate_state"])

    def test_stale_cycle_penalty_and_prompt_hint_use_failed_ready_count(self) -> None:
        from minority_report import analysts

        base = score_candidate_for_trainer(
            {
                "ticker": "GOOD",
                "market": "US",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 5.0,
            },
            market="US",
        )
        stale = score_candidate_for_trainer(
            {
                "ticker": "GOOD",
                "market": "US",
                "primary_bucket": "momentum_now",
                "liquidity_bucket": "high",
                "change_pct": 5.0,
                "stale_cycle_count": 3,
                "repeated_failed_ready_count": 3,
            },
            market="US",
        )

        self.assertEqual(stale["trainer_score_components"]["prompt"]["stale_cycle_penalty"], -8.0)
        self.assertLess(stale["trainer_prompt_score"], base["trainer_prompt_score"])
        self.assertIn("repeated_failed_ready_count=3", analysts._candidate_trainer_hint(stale))

    def test_kr_candidate_quality_score_bonus_is_env_gated_and_gap_adjusted(self) -> None:
        candidate = {
            "ticker": "123456",
            "market": "KR",
            "market_type": "KOSDAQ",
            "primary_bucket": "liquidity_leader",
            "liquidity_bucket": "mid",
            "change_pct": 4.0,
            "candidate_quality_score": 80,
        }
        disabled = score_candidate_for_trainer(candidate, market="KR")
        with patch.dict(
            "os.environ",
            {
                "CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED": "true",
                "CANDIDATE_TRAINER_QUALITY_SCORE_WEIGHT": "0.3",
            },
            clear=False,
        ):
            enabled = score_candidate_for_trainer(candidate, market="KR")
            partial = score_candidate_for_trainer(
                {**candidate, "quality_data_gaps": ["flow_missing"]},
                market="KR",
            )

        self.assertNotIn("kr_quality_score_bonus", disabled["trainer_score_components"]["prompt"])
        self.assertEqual(enabled["trainer_score_components"]["prompt"]["kr_quality_score_bonus"], 9.0)
        self.assertEqual(partial["trainer_score_components"]["prompt"]["kr_quality_score_bonus"], 4.5)
        self.assertGreater(enabled["trainer_prompt_score"], disabled["trainer_prompt_score"])

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

    def test_kr_prompt_pool_default_and_config_cap_follow_policy_28(self) -> None:
        candidates = [
            {"ticker": f"{idx:06d}", "market": "KR", "primary_bucket": "liquidity_leader", "liquidity_bucket": "mid"}
            for idx in range(40)
        ]

        default_result = build_trainer_prompt_pool(candidates, market="KR", target=30, reorder_enabled=True)
        capped_result = build_trainer_prompt_pool(candidates, market="KR", target=30, hard_cap=28, reorder_enabled=True)

        self.assertEqual(default_result["hard_cap"], 28)
        self.assertEqual(len(default_result["prompt_pool"]), 28)
        self.assertEqual(capped_result["hard_cap"], 28)
        self.assertEqual(len(capped_result["prompt_pool"]), 28)

    def test_us_prompt_pool_default_cap_follows_policy_24(self) -> None:
        candidates = [
            {"ticker": f"T{idx}", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high"}
            for idx in range(40)
        ]

        result = build_trainer_prompt_pool(candidates, market="US", target=30, reorder_enabled=True)

        self.assertEqual(result["hard_cap"], 24)
        self.assertEqual(len(result["prompt_pool"]), 24)
        self.assertTrue(
            all(row["prompt_excluded_reason"] == "hard_cap_cutoff" for row in result["excluded_from_prompt"])
        )

    def test_legacy_kr_selection_candidate_cap_defaults_to_28(self) -> None:
        from minority_report import analysts

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(analysts._selection_candidate_cap("KR", watch_max=80, trade_max=5), 28)

    def test_legacy_us_selection_candidate_cap_defaults_to_24(self) -> None:
        from minority_report import analysts

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(analysts._selection_candidate_cap("US", watch_max=80, trade_max=5), 24)
            self.assertEqual(analysts._trainer_prompt_hard_cap("KR", fallback=30), 28)
            self.assertEqual(analysts._trainer_prompt_hard_cap("US", fallback=30), 24)

    def test_live_config_caps_selection_full_evidence_at_five(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "v2_start_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["env_overrides"]["SELECTION_FULL_EVIDENCE_MAX"], "5")
        self.assertEqual(config["env_overrides"]["SELECTION_EVIDENCE_MAX_CHARS"], "3500")

    def test_select_tickers_evidence_pack_honors_item_count_cap(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["T0"],"trade_ready":[],"reasons":{"T0":"watch"},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        candidates = [
            {"ticker": f"T{idx}", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high"}
            for idx in range(7)
        ]
        evidence = {
            f"T{idx}": {"ticker": f"T{idx}", "evidence_id": f"evidence_{idx}", "facts": ["ok"]}
            for idx in range(7)
        }
        env = {
            "SELECTION_FULL_EVIDENCE_MAX": "5",
            "SELECTION_EVIDENCE_MAX_CHARS": "3500",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), patch.object(analysts.client.messages, "create", side_effect=fake_create):
            analysts.select_tickers(
                "US",
                "digest",
                "NEUTRAL",
                candidates,
                evidence_by_ticker=evidence,
            )

        prompt = captured["prompt"]
        for idx in range(5):
            self.assertIn(f"evidence_{idx}", prompt)
        self.assertNotIn("evidence_5", prompt)
        self.assertNotIn("evidence_6", prompt)

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

    def test_select_tickers_does_not_legacy_fallback_when_all_candidates_quarantined(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":[],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "true",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }
        candidates = [
            {
                "ticker": "BAD1",
                "market": "US",
                "data_quality": "bad",
                "status": "blocked",
                "hard_safety": True,
                "primary_bucket": "momentum_now",
            },
            {
                "ticker": "BAD2",
                "market": "US",
                "data_quality": "bad",
                "status": "blocked",
                "hard_safety": True,
                "primary_bucket": "momentum_now",
            },
        ]

        with patch.dict("os.environ", env, clear=False), patch.object(analysts.client.messages, "create", side_effect=fake_create):
            selected, reasons = analysts.select_tickers("US", "digest", "NEUTRAL", candidates)

        self.assertEqual(selected, [])
        self.assertEqual(reasons, {})
        self.assertNotIn("BAD1", captured["prompt"])
        self.assertNotIn("BAD2", captured["prompt"])
        meta = analysts.get_last_selection_meta()
        self.assertTrue(meta["_candidate_quality_trainer_enabled"])
        self.assertEqual(meta["_prompt_pool_count"], 0)
        self.assertTrue(meta["_safe_empty_prompt_pool"])
        self.assertEqual(meta["_prompt_pool_empty_reason"], "all_candidates_quarantined")
        self.assertTrue(meta["_trainer_all_quarantined"])
        excluded = {row["ticker"]: row["reason"] for row in meta["_excluded_from_prompt"]}
        self.assertEqual(excluded["BAD1"], "trainer_quarantine")
        self.assertEqual(excluded["BAD2"], "trainer_quarantine")


if __name__ == "__main__":
    unittest.main()
