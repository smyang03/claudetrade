from __future__ import annotations

import json
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from runtime.candidate_prompt_pool import build_trainer_prompt_pool
from runtime.candidate_quality_labels import FUTURE_LABEL_FIELDS
from runtime.candidate_quality_trainer import FUTURE_LABEL_FIELDS as TRAINER_FUTURE_LABEL_FIELDS
from runtime.candidate_quality_trainer import score_candidate_for_trainer
from runtime.us_candidate_quality import FUTURE_LABEL_FIELDS as RUNTIME_FUTURE_LABEL_FIELDS


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

    def test_runtime_and_trainer_share_future_label_fields(self) -> None:
        self.assertIs(TRAINER_FUTURE_LABEL_FIELDS, FUTURE_LABEL_FIELDS)
        self.assertIs(RUNTIME_FUTURE_LABEL_FIELDS, FUTURE_LABEL_FIELDS)
        self.assertIn("ret30", FUTURE_LABEL_FIELDS)
        self.assertIn("forward_30m_from_bucket", FUTURE_LABEL_FIELDS)

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

    def test_kr_board_prior_overrides_use_candidate_trainer_env_prefix(self) -> None:
        env = {
            "CANDIDATE_TRAINER_KR_KOSDAQ_PRIOR": "0",
            "CANDIDATE_TRAINER_KR_KOSPI_PENALTY": "0",
            "CANDIDATE_TRAINER_KR_HIGH_LIQUIDITY_CHASE_PENALTY": "0",
        }
        with patch.dict("os.environ", env, clear=False):
            kosdaq = score_candidate_for_trainer(
                {
                    "ticker": "123456",
                    "market": "KR",
                    "market_type": "KOSDAQ",
                    "primary_bucket": "liquidity_leader",
                    "liquidity_bucket": "high",
                    "change_pct": 4.0,
                },
                market="KR",
            )
            kospi = score_candidate_for_trainer(
                {
                    "ticker": "005930",
                    "market": "KR",
                    "market_type": "KOSPI",
                    "primary_bucket": "liquidity_leader",
                    "liquidity_bucket": "high",
                    "change_pct": 4.0,
                },
                market="KR",
            )
            us = score_candidate_for_trainer(
                {
                    "ticker": "NVDA",
                    "market": "US",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 4.0,
                },
                market="US",
            )

        self.assertEqual(kosdaq["trainer_score_components"]["prompt"]["kr_kosdaq_prior"], 0.0)
        self.assertEqual(kospi["trainer_score_components"]["prompt"]["kr_kospi_penalty"], 0.0)
        self.assertEqual(kospi["trainer_score_components"]["prompt"]["kr_high_liquidity_chase_penalty"], 0.0)
        self.assertNotIn("kr_kosdaq_prior", us["trainer_score_components"]["prompt"])
        self.assertIn("us_high_liquidity", us["trainer_score_components"]["prompt"])

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

    def test_kr_candidate_quality_score_bonus_is_default_on_env_gated_and_gap_adjusted(self) -> None:
        candidate = {
            "ticker": "123456",
            "market": "KR",
            "market_type": "KOSDAQ",
            "primary_bucket": "liquidity_leader",
            "liquidity_bucket": "mid",
            "change_pct": 4.0,
            "candidate_quality_score": 80,
        }
        default_enabled = score_candidate_for_trainer(candidate, market="KR")
        with patch.dict("os.environ", {"CANDIDATE_TRAINER_QUALITY_SCORE_ENABLED": "false"}, clear=False):
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

        self.assertEqual(default_enabled["trainer_score_components"]["prompt"]["kr_quality_score_bonus"], 9.0)
        self.assertNotIn("kr_quality_score_bonus", disabled["trainer_score_components"]["prompt"])
        self.assertEqual(enabled["trainer_score_components"]["prompt"]["kr_quality_score_bonus"], 9.0)
        self.assertEqual(partial["trainer_score_components"]["prompt"]["kr_quality_score_bonus"], 4.5)
        self.assertGreater(enabled["trainer_prompt_score"], disabled["trainer_prompt_score"])

    def test_us_candidate_quality_score_bonus_is_env_gated(self) -> None:
        candidate = {
            "ticker": "NVDA",
            "market": "US",
            "primary_bucket": "momentum_now",
            "liquidity_bucket": "high",
            "change_pct": 5.0,
            "candidate_quality_score": 80,
        }
        default_disabled = score_candidate_for_trainer(candidate, market="US")
        with patch.dict(
            "os.environ",
            {
                "CANDIDATE_TRAINER_US_QUALITY_SCORE_ENABLED": "true",
                "CANDIDATE_TRAINER_US_QUALITY_SCORE_WEIGHT": "0.2",
            },
            clear=False,
        ):
            enabled = score_candidate_for_trainer(candidate, market="US")
            partial = score_candidate_for_trainer(
                {**candidate, "quality_data_gaps": ["history_incomplete"]},
                market="US",
            )

        self.assertNotIn("us_quality_score_bonus", default_disabled["trainer_score_components"]["prompt"])
        self.assertEqual(enabled["trainer_score_components"]["prompt"]["us_quality_score_bonus"], 6.0)
        self.assertEqual(partial["trainer_score_components"]["prompt"]["us_quality_score_bonus"], 3.0)
        self.assertGreater(enabled["trainer_prompt_score"], default_disabled["trainer_prompt_score"])

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

    def test_prompt_pool_prioritizes_news_hard_pin_within_cap(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "GOOD",
                    "market": "US",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 6,
                },
                {
                    "ticker": "NEWS",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1,
                    "preopen_news_edge": True,
                    "preopen_news_policy": "strict_loss_filter_v1",
                    "preopen_news_edge_reason": "news_strict_catalyst",
                    "preopen_pinned": True,
                    "preopen_pin_tier": "HARD",
                    "preopen_pin_source": "news_strict_catalyst",
                    "preopen_pin_require_confirmation": True,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["NEWS"])
        self.assertEqual(result["prompt_pool"][0]["preopen_pin_tier"], "HARD")
        self.assertEqual(result["metrics"]["hard_preopen_pin_count"], 1)
        self.assertEqual(result["metrics"]["hard_preopen_pin_prompt_count"], 1)
        excluded = {row["ticker"]: row for row in result["excluded_from_prompt"]}
        self.assertEqual(excluded["GOOD"]["prompt_excluded_reason"], "hard_cap_cutoff")

    def test_prompt_pool_keeps_same_day_stopped_hard_pin_after_regular_candidates(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "STOP",
                    "market": "US",
                    "same_day_stopped": True,
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                    "preopen_news_edge": True,
                    "preopen_pinned": True,
                    "preopen_pin_tier": "HARD",
                    "preopen_pin_source": "news_strict_catalyst",
                },
                {
                    "ticker": "OK",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["OK"])
        excluded = {row["ticker"]: row for row in result["excluded_from_prompt"]}
        self.assertEqual(excluded["STOP"]["prompt_excluded_reason"], "hard_cap_cutoff")
        self.assertTrue(excluded["STOP"]["candidate"]["same_day_stopped"])

    def test_kr_prompt_pool_records_board_and_liquidity_mix_metrics(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "005930",
                    "market": "KR",
                    "market_type": "KOSPI",
                    "primary_bucket": "liquidity_leader",
                    "liquidity_bucket": "high",
                    "change_pct": 4.0,
                },
                {
                    "ticker": "000660",
                    "market": "KR",
                    "market_type": "KOSPI",
                    "primary_bucket": "liquidity_leader",
                    "liquidity_bucket": "high",
                    "change_pct": 3.0,
                },
                {
                    "ticker": "123456",
                    "market": "KR",
                    "market_type": "KOSDAQ",
                    "primary_bucket": "liquidity_leader",
                    "liquidity_bucket": "mid",
                    "change_pct": 3.0,
                },
            ],
            market="KR",
            target=2,
            hard_cap=2,
            reorder_enabled=False,
        )

        metrics = result["metrics"]
        self.assertEqual(metrics["prompt_pool_board_mix"], {"KOSPI": 2})
        self.assertEqual(metrics["prompt_pool_excluded_board_mix"], {"KOSDAQ": 1})
        self.assertEqual(metrics["prompt_pool_mix"]["liquidity_counts"], {"high": 2})
        self.assertEqual(metrics["full_pool_mix"]["high_liquidity_by_board"], {"KOSPI": 2})

    def test_prompt_pool_defers_same_day_stopped_after_trainer_reorder(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "STOP",
                    "market": "US",
                    "same_day_stopped": True,
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                },
                {
                    "ticker": "OK",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["OK"])

    def test_prompt_pool_includes_same_day_stopped_last_when_cap_has_room(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "STOP",
                    "market": "US",
                    "same_day_stopped": True,
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                },
                {
                    "ticker": "OK",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                },
            ],
            market="US",
            target=2,
            hard_cap=2,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["OK", "STOP"])
        self.assertTrue(result["prompt_pool"][-1]["same_day_stopped"])

    def test_prompt_pool_records_same_day_stopped_cap_exclusion(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "STOP",
                    "market": "US",
                    "same_day_stopped": True,
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                },
                {
                    "ticker": "OK",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        excluded = {row["ticker"]: row for row in result["excluded_from_prompt"]}
        self.assertEqual(excluded["STOP"]["prompt_excluded_reason"], "hard_cap_cutoff")
        self.assertTrue(excluded["STOP"]["candidate"]["same_day_stopped"])

    def test_merge_duplicate_preserves_same_day_stopped_marker(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "DUP",
                    "market": "US",
                    "same_day_stopped": True,
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                },
                {
                    "ticker": "DUP",
                    "market": "US",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["DUP"])
        self.assertTrue(result["prompt_pool"][0]["same_day_stopped"])

    def test_merge_duplicate_preserves_news_hard_pin_marker(self) -> None:
        result = build_trainer_prompt_pool(
            [
                {
                    "ticker": "DUP",
                    "market": "US",
                    "primary_bucket": "unclassified",
                    "liquidity_bucket": "mid",
                    "change_pct": 1.0,
                    "preopen_news_edge": True,
                    "preopen_news_policy": "strict_loss_filter_v1",
                    "preopen_news_edge_reason": "news_strict_catalyst",
                    "preopen_pinned": True,
                    "preopen_pin_tier": "HARD",
                    "preopen_pin_source": "news_strict_catalyst",
                },
                {
                    "ticker": "DUP",
                    "market": "US",
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "change_pct": 5.0,
                },
            ],
            market="US",
            target=1,
            hard_cap=1,
            reorder_enabled=True,
        )

        self.assertEqual([row["ticker"] for row in result["prompt_pool"]], ["DUP"])
        self.assertTrue(result["prompt_pool"][0]["preopen_news_edge"])
        self.assertEqual(result["prompt_pool"][0]["preopen_pin_tier"], "HARD")
        self.assertEqual(result["prompt_pool"][0]["preopen_pin_source"], "news_strict_catalyst")

    def test_kr_prompt_pool_default_and_config_cap_follow_policy_40(self) -> None:
        candidates = [
            {"ticker": f"{idx:06d}", "market": "KR", "primary_bucket": "liquidity_leader", "liquidity_bucket": "mid"}
            for idx in range(45)
        ]

        default_result = build_trainer_prompt_pool(candidates, market="KR", target=30, reorder_enabled=True)
        capped_result = build_trainer_prompt_pool(candidates, market="KR", target=30, hard_cap=40, reorder_enabled=True)

        self.assertEqual(default_result["hard_cap"], 40)
        self.assertEqual(len(default_result["prompt_pool"]), 40)
        self.assertEqual(capped_result["hard_cap"], 40)
        self.assertEqual(len(capped_result["prompt_pool"]), 40)

    def test_us_prompt_pool_default_cap_follows_policy_40(self) -> None:
        candidates = [
            {"ticker": f"T{idx}", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high"}
            for idx in range(45)
        ]

        result = build_trainer_prompt_pool(candidates, market="US", target=30, reorder_enabled=True)

        self.assertEqual(result["hard_cap"], 40)
        self.assertEqual(len(result["prompt_pool"]), 40)
        self.assertTrue(
            all(row["prompt_excluded_reason"] == "hard_cap_cutoff" for row in result["excluded_from_prompt"])
        )

    def test_legacy_kr_selection_candidate_cap_defaults_to_40(self) -> None:
        from minority_report import analysts

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(analysts._selection_candidate_cap("KR", watch_max=80, trade_max=5), 40)

    def test_legacy_us_selection_candidate_cap_defaults_to_40(self) -> None:
        from minority_report import analysts

        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(analysts._selection_candidate_cap("US", watch_max=80, trade_max=5), 40)
            self.assertEqual(analysts._trainer_prompt_hard_cap("KR", fallback=30), 40)
            self.assertEqual(analysts._trainer_prompt_hard_cap("US", fallback=30), 40)

    def test_live_config_caps_selection_full_evidence_at_sixteen(self) -> None:
        # 2026-06-12 B3: 중복 키 제거 후 16으로 단일화 (운영자 승인) — 5는 중복 키 시절 잔재
        config_path = Path(__file__).resolve().parents[1] / "config" / "v2_start_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["env_overrides"]["SELECTION_FULL_EVIDENCE_MAX"], "16")
        self.assertEqual(config["env_overrides"]["SELECTION_EVIDENCE_MAX_CHARS"], "3500")
        self.assertEqual(config["env_overrides"]["FINAL_PROMPT_EVIDENCE_ALIGNMENT_ENABLED"], "true")
        self.assertEqual(config["env_overrides"]["FINAL_PROMPT_EVIDENCE_ALIGNMENT_WARN_OVERLAP_MIN"], "0.80")
        self.assertEqual(config["env_overrides"]["FINAL_PROMPT_EVIDENCE_ALIGNMENT_WARN_EXEC_MISSING_MAX"], "0.50")

    def test_live_config_neutralizes_kr_board_prior_with_runtime_env_names(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "v2_start_config.json"
        config = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(config["env_overrides"]["CANDIDATE_TRAINER_KR_KOSDAQ_PRIOR"], "0")
        self.assertEqual(config["env_overrides"]["CANDIDATE_TRAINER_KR_KOSPI_PENALTY"], "0")
        self.assertEqual(config["env_overrides"]["CANDIDATE_TRAINER_KR_HIGH_LIQUIDITY_CHASE_PENALTY"], "0")

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

    def test_select_tickers_prompt_marks_selection_evidence_ceiling(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["AAPL"],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        prompt_rows = [
            {
                "ticker": "AAPL",
                "market": "US",
                "price": 100,
                "evidence_class": "COMPACT_ONLY",
                "selection_evidence_action_ceiling": "WATCH",
                "selection_evidence_missing_reason": "not_in_intraday_prefetch",
            }
        ]
        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers(
                "US",
                "digest",
                "NEUTRAL",
                prompt_rows,
                prompt_pool_override=prompt_rows,
                prompt_pool_meta_override={"prompt_pool": prompt_rows, "prompt_pool_count": 1},
            )

        self.assertIn("ev=COMPACT_ONLY,ceil=WATCH", captured["prompt"])
        self.assertIn("ev=COMPACT_ONLY or ev=MISSING_OR_STALE", captured["prompt"])

    def test_select_tickers_prompt_includes_candidate_official_name(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["454910"],"trade_ready":[],"reasons":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        prompt_rows = [
            {
                "ticker": "454910",
                "name": "Doosan Robotics",
                "market": "KR",
                "price": 127200,
                "volume": 1000,
                "change_rate": 0.0,
            }
        ]
        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers(
                "KR",
                "digest",
                "NEUTRAL",
                prompt_rows,
                prompt_pool_override=prompt_rows,
                prompt_pool_meta_override={"prompt_pool": prompt_rows, "prompt_pool_count": 1},
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        self.assertIn("454910 name=Doosan Robotics", captured["prompt"])

    def test_selection_retry_keeps_original_prompt_pool_and_risk_hints(self) -> None:
        from minority_report import analysts

        candidates = [
            {"ticker": f"T{idx}", "name": f"Name {idx}", "market": "US", "price": 10 + idx, "volume": 1000}
            for idx in range(35)
        ]
        candidates[34].update(
            {
                "ticker": "HPSP",
                "name": "HPSP Co",
                "evidence_class": "COMPACT_ONLY",
                "selection_evidence_action_ceiling": "WATCH",
                "news_quality": "weak",
                "news_signal_type": "weak_generic",
                "post_open_features": {"ret_3m_pct": -0.5, "ret_30m_pct": -1.5},
            }
        )

        with patch.dict("os.environ", {"SELECTION_RETRY_CANDIDATE_CAP": "40"}, clear=False):
            retry_candidates = analysts._pick_selection_retry_candidates(
                candidates,
                {"watchlist": ["T0"], "_fallback_mode": "selection_partial", "_parse_recovered": True},
                "US",
            )
            prompt = analysts._build_selection_retry_prompt("US", "NEUTRAL", retry_candidates)

        self.assertIn("HPSP", [row["ticker"] for row in retry_candidates])
        self.assertIn("HPSP name=HPSP Co", prompt)
        self.assertIn("ev=COMPACT_ONLY", prompt)
        self.assertIn("newsq=", prompt)
        self.assertIn("post_open=", prompt)
        self.assertNotIn(" p=", prompt)
        self.assertNotIn(" vol=", prompt)
        self.assertNotIn(" board=", prompt)
        self.assertNotIn(" category=", prompt)
        self.assertNotIn("price_targets", prompt)
        self.assertNotIn("recommended_strategy", prompt)

    def test_kr_reason_identity_warning_flags_mismatched_prefix(self) -> None:
        from minority_report import analysts

        warnings = analysts._selection_reason_identity_warnings(
            {"reasons": {"454910": "HD현대마린솔루션: robotics theme"}},
            [{"ticker": "454910", "name": "두산로보틱스"}],
            "KR",
        )

        self.assertEqual(warnings[0]["ticker"], "454910")
        self.assertEqual(warnings[0]["type"], "reason_name_mismatch")

    def test_select_tickers_compact_evidence_pack_keeps_multiple_items_under_char_cap(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["T0"],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        def fake_save_raw_call(**kwargs):
            captured["raw_extra"] = kwargs.get("extra") or {}

        candidates = [{"ticker": f"T{idx}", "market": "US", "price": 100 + idx} for idx in range(5)]
        evidence = {}
        for idx in range(5):
            evidence[f"T{idx}"] = {
                "ticker": f"T{idx}",
                "evidence_class": "FULL_PACK",
                "selection_evidence_action_ceiling": "BUY_READY",
                "live_evidence": {
                    "data_state": "confirmed",
                    "action_ceiling": "BUY_READY",
                    "missing_fields": [],
                    "post_open_confirmation": {
                        "ret_3m_pct": 0.1,
                        "ret_5m_pct": 0.2,
                        "ret_10m_pct": 0.3,
                        "ret_30m_pct": 0.4,
                        "opening_range_break": True,
                        "vwap_distance_pct": 0.5,
                        "volume_ratio_open": 1.2,
                        "momentum_state": "sustained",
                    },
                    "risk_control_view": {"hard_blocks": [], "soft_gates": [], "action_ceiling": "BUY_READY"},
                },
            }
        env = {
            "SELECTION_FULL_EVIDENCE_MAX": "5",
            "SELECTION_EVIDENCE_MAX_CHARS": "3500",
            "SELECTION_COMPACT_EVIDENCE_PACK_ENABLED": "true",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", side_effect=fake_save_raw_call), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers("US", "digest", "NEUTRAL", candidates, evidence_by_ticker=evidence)

        prompt = captured["prompt"]
        self.assertIn('"t":"T0"', prompt)
        self.assertIn('"t":"T4"', prompt)
        self.assertNotIn("post_open_confirmation", prompt)
        meta = analysts.get_last_selection_meta()
        self.assertTrue(meta["compact_evidence_pack_enabled"])
        self.assertEqual(meta["compact_evidence_pack_included_count"], 5)
        self.assertTrue(meta["compact_evidence_shadow_enabled"])
        self.assertEqual(meta["compact_evidence_shadow_count"], 5)
        self.assertEqual(meta["compact_evidence_shadow_tickers"], ["T0", "T1", "T2", "T3", "T4"])
        self.assertEqual(captured["raw_extra"]["evidence_version"], "selection_evidence.compact_v1")
        self.assertEqual(captured["raw_extra"]["evidence_tickers"], ["T0", "T1", "T2", "T3", "T4"])

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

    def test_prepare_selection_prompt_pool_matches_select_tickers_order(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["GOOD","BAD"],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        candidates = [
            {"ticker": "BAD", "market": "US", "primary_bucket": "unclassified", "liquidity_bucket": "mid", "change_pct": 1.0},
            {"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 5.0},
        ]
        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_TARGET_US": "2",
            "CANDIDATE_PROMPT_POOL_HARD_CAP_US": "2",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            prompt_rows, prompt_meta = analysts.prepare_selection_prompt_pool("US", candidates)
            analysts.select_tickers("US", "digest", "NEUTRAL", candidates)

        self.assertEqual([row["ticker"] for row in prompt_rows], ["GOOD", "BAD"])
        self.assertLess(captured["prompt"].find("GOOD"), captured["prompt"].find("BAD"))
        meta = analysts.get_last_selection_meta()
        self.assertEqual([row["ticker"] for row in meta["_final_prompt_pool"]], ["GOOD", "BAD"])
        self.assertEqual(prompt_meta["prompt_pool_count"], 2)

    def test_discovery_overlay_appends_after_trainer_prompt_pool(self) -> None:
        from minority_report import analysts

        candidates = [
            {"ticker": "CORE", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 5.0},
            {"ticker": "DISC", "market": "US", "primary_bucket": "near_breakout", "liquidity_bucket": "high", "change_pct": 4.0},
        ]
        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "false",
            "CANDIDATE_PROMPT_POOL_TARGET_US": "1",
            "CANDIDATE_PROMPT_POOL_HARD_CAP_US": "1",
            "DISCOVERY_PROMPT_ENABLED": "true",
            "DISCOVERY_MAX_SLOTS_US": "1",
        }

        with patch.dict("os.environ", env, clear=False):
            prompt_rows, prompt_meta = analysts.prepare_selection_prompt_pool("US", candidates)

        self.assertEqual([row["ticker"] for row in prompt_rows], ["CORE", "DISC"])
        self.assertEqual(prompt_rows[1]["candidate_pool_role"], "DISCOVERY")
        self.assertEqual(prompt_rows[1]["prompt_rank"], 2)
        self.assertEqual(prompt_meta["prompt_pool_count"], 2)
        self.assertEqual(prompt_meta["_discovery_added_tickers"], ["DISC"])

    def test_select_tickers_prompt_labels_discovery_candidate(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["CORE","DISC"],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        candidates = [
            {"ticker": "CORE", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high", "change_pct": 5.0},
            {"ticker": "DISC", "market": "US", "primary_bucket": "near_breakout", "liquidity_bucket": "high", "change_pct": 4.0},
        ]
        env = {
            "CANDIDATE_QUALITY_TRAINER_ENABLED": "true",
            "CANDIDATE_PROMPT_POOL_REORDER_ENABLED": "false",
            "CANDIDATE_PROMPT_POOL_TARGET_US": "1",
            "CANDIDATE_PROMPT_POOL_HARD_CAP_US": "1",
            "DISCOVERY_PROMPT_ENABLED": "true",
            "DISCOVERY_MAX_SLOTS_US": "1",
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers("US", "digest", "NEUTRAL", candidates)

        self.assertIn("role=DISCOVERY", captured["prompt"])
        meta = analysts.get_last_selection_meta()
        self.assertEqual(meta["_final_prompt_pool"][1]["candidate_pool_role"], "DISCOVERY")

    def test_select_tickers_override_skips_builder_and_filters_non_prompt_evidence(self) -> None:
        from minority_report import analysts

        captured: dict[str, str] = {}

        def fake_create(**kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"watchlist":["GOOD"],"trade_ready":[],"reasons":{},"veto":{}}')],
                usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                stop_reason="end_turn",
            )

        candidates = [
            {"ticker": "GOOD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high"},
            {"ticker": "BAD", "market": "US", "primary_bucket": "momentum_now", "liquidity_bucket": "high"},
        ]
        prompt_rows = [{"ticker": "GOOD", "market": "US", "prompt_rank": 1}]
        evidence = {
            "GOOD": {"ticker": "GOOD", "evidence_id": "evidence_good"},
            "BAD": {"ticker": "BAD", "evidence_id": "evidence_bad"},
        }
        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "false",
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS_SHADOW": "false",
            "SELECTION_FULL_EVIDENCE_MAX": "5",
        }

        with patch.dict("os.environ", env, clear=False), \
             patch.object(analysts, "_build_selection_prompt_pool", side_effect=AssertionError("builder should not run")), \
             patch.object(analysts.client.messages, "create", side_effect=fake_create), \
             patch.object(analysts, "save_raw_call", lambda **_kwargs: None), \
             patch.object(analysts, "build_active_lesson_context", return_value={"section": "", "metadata": {}}):
            analysts.select_tickers(
                "US",
                "digest",
                "NEUTRAL",
                candidates,
                evidence_by_ticker=evidence,
                prompt_pool_override=prompt_rows,
                prompt_pool_meta_override={"enabled": False, "prompt_pool": prompt_rows},
            )

        self.assertIn("evidence_good", captured["prompt"])
        self.assertNotIn("evidence_bad", captured["prompt"])
        meta = analysts.get_last_selection_meta()
        self.assertEqual([row["ticker"] for row in meta["_final_prompt_pool"]], ["GOOD"])
        self.assertEqual(meta["evidence_tickers"], ["GOOD"])

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
