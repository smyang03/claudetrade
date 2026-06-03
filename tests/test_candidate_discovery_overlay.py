from __future__ import annotations

import unittest
from unittest.mock import patch

from runtime.candidate_discovery_overlay import apply_discovery_overlay, signal_family


class CandidateDiscoveryOverlayTests(unittest.TestCase):
    def test_disabled_returns_core_unchanged(self) -> None:
        core = [{"ticker": "AAPL", "prompt_rank": 1}]
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": "MSFT",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "trainer_score_rank": 2,
                }
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "false"}, clear=False):
            rows, out_meta = apply_discovery_overlay(core, meta, market="US")

        self.assertEqual(rows, core)
        self.assertFalse(out_meta["_discovery_enabled"])
        self.assertEqual(out_meta["_discovery_added"], 0)

    def test_appends_discovery_after_core_without_replacing_core(self) -> None:
        core = [{"ticker": "CORE1", "prompt_rank": 1}, {"ticker": "CORE2", "prompt_rank": 2}]
        meta = {
            "version": "trainer_prompt_pool_v1",
            "excluded_from_prompt": [
                {
                    "ticker": "DROP1",
                    "reason": "prompt_cap",
                    "primary_bucket": "unclassified",
                    "source_tags": ["US:unclassified"],
                    "trainer_score_rank": 3,
                    "trainer_prompt_score": 90,
                },
                {
                    "ticker": "ADD2",
                    "reason": "prompt_cap",
                    "primary_bucket": "momentum_now",
                    "source_tags": ["US:momentum_now"],
                    "trainer_score_rank": 5,
                    "trainer_prompt_score": 80,
                },
                {
                    "ticker": "ADD1",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["US:near_breakout"],
                    "trainer_score_rank": 4,
                    "trainer_prompt_score": 75,
                },
            ],
        }

        with patch.dict(
            "os.environ",
            {
                "DISCOVERY_PROMPT_ENABLED": "true",
                "DISCOVERY_MAX_SLOTS_US": "2",
                "DISCOVERY_EXCLUDE_UNCLASSIFIED_ONLY": "true",
                "DISCOVERY_EXCLUDE_PULLBACK_ONLY": "true",
            },
            clear=False,
        ):
            rows, out_meta = apply_discovery_overlay(core, meta, market="US")

        self.assertEqual([row["ticker"] for row in rows], ["CORE1", "CORE2", "ADD1", "ADD2"])
        self.assertEqual([row["prompt_rank"] for row in rows], [1, 2, 3, 4])
        self.assertEqual(rows[2]["candidate_pool_role"], "DISCOVERY")
        self.assertEqual(rows[2]["discovery_action_ceiling"], "WATCH")
        self.assertEqual(out_meta["_prompt_pool_core_count"], 2)
        self.assertEqual(out_meta["_prompt_pool_discovery_count"], 2)
        self.assertEqual(out_meta["_discovery_added_tickers"], ["ADD1", "ADD2"])
        self.assertEqual(out_meta["_discovery_role_by_ticker"]["ADD1"], "DISCOVERY")

    def test_kr_default_adds_at_most_five(self) -> None:
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": f"00{i}",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["KR:near_breakout"],
                    "trainer_score_rank": i,
                }
                for i in range(1, 8)
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "true"}, clear=False):
            rows, out_meta = apply_discovery_overlay([], meta, market="KR")

        self.assertEqual(len(rows), 5)
        self.assertEqual(out_meta["_discovery_added"], 5)

    def test_kr_strict_rule_excludes_volume_surge_only_and_extreme_chase(self) -> None:
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": "VOL",
                    "reason": "prompt_cap",
                    "trainer_candidate_state": "PLAN_B",
                    "primary_bucket": "volume_surge",
                    "source_tags": ["KR:volume_surge"],
                    "trainer_score_rank": 1,
                    "change_pct": 3.0,
                },
                {
                    "ticker": "CHASE",
                    "reason": "prompt_cap",
                    "trainer_candidate_state": "PLAN_A",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["KR:near_breakout"],
                    "trainer_score_rank": 2,
                    "change_pct": 16.0,
                },
                {
                    "ticker": "GOOD",
                    "reason": "prompt_cap",
                    "trainer_candidate_state": "PLAN_B",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["KR:near_breakout"],
                    "trainer_score_rank": 3,
                    "change_pct": 4.0,
                },
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "true"}, clear=False):
            rows, out_meta = apply_discovery_overlay([], meta, market="KR")

        self.assertEqual([row["ticker"] for row in rows], ["GOOD"])
        self.assertEqual(out_meta["_discovery_reject_counts"]["kr_volume_surge_only"], 1)
        self.assertEqual(out_meta["_discovery_reject_counts"]["kr_extreme_chase"], 1)

    def test_kr_plan_b_momentum_requires_change_below_threshold(self) -> None:
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": "FAST",
                    "reason": "prompt_cap",
                    "trainer_candidate_state": "PLAN_B",
                    "primary_bucket": "momentum_now",
                    "source_tags": ["KR:momentum_now"],
                    "trainer_score_rank": 1,
                    "change_pct": 8.0,
                },
                {
                    "ticker": "OK",
                    "reason": "prompt_cap",
                    "trainer_candidate_state": "PLAN_B",
                    "primary_bucket": "momentum_now",
                    "source_tags": ["KR:momentum_now"],
                    "trainer_score_rank": 2,
                    "change_pct": 6.0,
                },
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "true"}, clear=False):
            rows, out_meta = apply_discovery_overlay([], meta, market="KR")

        self.assertEqual([row["ticker"] for row in rows], ["OK"])
        self.assertEqual(out_meta["_discovery_reject_counts"]["kr_strict_rule"], 1)

    def test_signal_family_uses_primary_and_source_tags_not_secondary_only(self) -> None:
        row = {
            "primary_bucket": "unclassified",
            "source_tags": ["US:base_universe", "US:momentum_now", "US:near_high"],
            "secondary_buckets_json": "[]",
        }

        self.assertEqual(signal_family(row, market="US"), ["momentum_now"])

    def test_excludes_pullback_only_candidate(self) -> None:
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": "PB",
                    "reason": "prompt_cap",
                    "primary_bucket": "pullback_watch",
                    "source_tags": ["US:pullback_watch"],
                    "trainer_score_rank": 1,
                },
                {
                    "ticker": "NB",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["US:near_breakout"],
                    "trainer_score_rank": 2,
                },
            ]
        }

        with patch.dict(
            "os.environ",
            {
                "DISCOVERY_PROMPT_ENABLED": "true",
                "DISCOVERY_EXCLUDE_PULLBACK_ONLY": "true",
            },
            clear=False,
        ):
            rows, _out_meta = apply_discovery_overlay([], meta, market="US")

        self.assertEqual([row["ticker"] for row in rows], ["NB"])

    def test_us_defaults_to_five_slots_and_preserves_core_order_when_unconfigured(self) -> None:
        core = [{"ticker": "CORE1", "prompt_rank": 1}, {"ticker": "CORE2", "prompt_rank": 2}]
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": f"ADD{i}",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["US:near_breakout"],
                    "trainer_score_rank": i,
                    "trainer_prompt_score": 80 - i,
                }
                for i in range(1, 8)
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "true"}, clear=False):
            rows, out_meta = apply_discovery_overlay(core, meta, market="US")

        self.assertEqual([row["ticker"] for row in rows[:2]], ["CORE1", "CORE2"])
        self.assertEqual([row["prompt_rank"] for row in rows], list(range(1, 8)))
        self.assertEqual(len(rows), 7)
        self.assertEqual(out_meta["_prompt_pool_core_count"], 2)
        self.assertEqual(out_meta["_prompt_pool_discovery_count"], 5)
        self.assertEqual(out_meta["_discovery_added_tickers"], ["ADD1", "ADD2", "ADD3", "ADD4", "ADD5"])

    def test_us_strict_rule_excludes_low_liquidity_and_extreme_chase(self) -> None:
        meta = {
            "excluded_from_prompt": [
                {
                    "ticker": "LOW",
                    "reason": "prompt_cap",
                    "primary_bucket": "near_breakout",
                    "source_tags": ["US:near_breakout"],
                    "liquidity_bucket": "low",
                    "trainer_score_rank": 1,
                    "change_pct": 3.0,
                },
                {
                    "ticker": "CHASE",
                    "reason": "prompt_cap",
                    "primary_bucket": "momentum_now",
                    "source_tags": ["US:momentum_now"],
                    "liquidity_bucket": "high",
                    "trainer_score_rank": 2,
                    "change_pct": 26.0,
                },
                {
                    "ticker": "GOOD",
                    "reason": "prompt_cap",
                    "primary_bucket": "source_consensus",
                    "source_tags": ["US:source_consensus"],
                    "liquidity_bucket": "high",
                    "trainer_score_rank": 3,
                    "change_pct": 4.0,
                },
            ]
        }

        with patch.dict("os.environ", {"DISCOVERY_PROMPT_ENABLED": "true"}, clear=False):
            rows, out_meta = apply_discovery_overlay([], meta, market="US")

        self.assertEqual([row["ticker"] for row in rows], ["GOOD"])
        self.assertEqual(out_meta["_discovery_reject_counts"]["low_liquidity"], 1)
        self.assertEqual(out_meta["_discovery_reject_counts"]["us_extreme_chase"], 1)


if __name__ == "__main__":
    unittest.main()
