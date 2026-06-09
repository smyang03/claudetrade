from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from runtime import selection_smart_skip


class SelectionSmartSkipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "SELECTION_SMART_SKIP_STATE_DIR": self.tmp.name,
                "SELECTION_SMART_SKIP_ENABLED": "true",
                "SELECTION_SMART_SKIP_MODE": "observe",
                "SELECTION_SMART_SKIP_TTL_MIN": "30",
                "SELECTION_SMART_SKIP_ALLOW_TRADE_READY_REUSE": "false",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_observe_mode_records_hit_without_reuse(self) -> None:
        prompt_hash = selection_smart_skip.sha256_text("same prompt")
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            selection_meta={"watchlist": ["AAPL"], "trade_ready": [], "reasons": {"AAPL": "ok"}},
            reasons={"AAPL": "ok"},
            session_date="2026-06-04",
        )

        decision = selection_smart_skip.maybe_reuse(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            session_date="2026-06-04",
        )
        state = selection_smart_skip.load_state("US", "2026-06-04")

        self.assertFalse(decision["reuse"])
        self.assertTrue(decision["would_reuse"])
        self.assertEqual(decision["reason"], "observe_only_cache_hit")
        self.assertEqual(state["observe_hit_count"], 1)
        self.assertEqual(state["reuse_count"], 0)

    def test_live_mode_reuses_exact_prompt_watch_only_result(self) -> None:
        prompt_hash = selection_smart_skip.sha256_text("same prompt")
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            selection_meta={"watchlist": ["AAPL"], "trade_ready": [], "reasons": {"AAPL": "ok"}},
            reasons={"AAPL": "ok"},
            session_date="2026-06-04",
        )

        with patch.dict(os.environ, {"SELECTION_SMART_SKIP_MODE": "live"}, clear=False):
            decision = selection_smart_skip.maybe_reuse(
                market="US",
                consensus_mode="BALANCED",
                execution_phase="intraday_live",
                prompt_hash=prompt_hash,
                prompt_candidate_count=3,
                session_date="2026-06-04",
            )
        state = selection_smart_skip.load_state("US", "2026-06-04")

        self.assertTrue(decision["reuse"])
        self.assertTrue(decision["full_claude_call_skipped"])
        self.assertEqual(decision["mode"], "live")
        self.assertEqual(decision["selection_meta"]["watchlist"], ["AAPL"])
        self.assertTrue(decision["selection_meta"]["_smart_skip_full_claude_call_skipped"])
        self.assertEqual(decision["selection_meta"]["_smart_skip_mode"], "live")
        self.assertEqual(state["reuse_count"], 1)
        self.assertTrue(state["last_reuse"]["full_claude_call_skipped"])

    def test_semantic_signature_ignores_price_noise(self) -> None:
        candidates = [
            {"ticker": "AAPL", "price": 100.0, "selection_evidence_action_ceiling": "WATCH", "trainer_candidate_state": "WATCH", "trainer_prompt_score": 71.2},
            {"ticker": "MSFT", "price": 200.0, "selection_evidence_action_ceiling": "WATCH", "trainer_candidate_state": "PLAN_B", "trainer_prompt_score": 67.9},
        ]
        first = selection_smart_skip.semantic_signature(
            market="US",
            session_date="2026-06-03",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_contract="selection_compact.v1",
            watch_cap=15,
            trade_cap=5,
            candidates=candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(candidates, market="US", start=1, end=25),
        )
        noisy_candidates = [dict(row) for row in candidates]
        noisy_candidates[0]["price"] = 99.5
        noisy_candidates[1]["price"] = 201.5
        second = selection_smart_skip.semantic_signature(
            market="US",
            session_date="2026-06-03",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_contract="selection_compact.v1",
            watch_cap=15,
            trade_cap=5,
            candidates=noisy_candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(noisy_candidates, market="US", start=1, end=25),
        )

        self.assertEqual(first, second)

    def test_semantic_signature_changes_on_context_hash(self) -> None:
        base = {
            "market": "US",
            "session_date": "2026-06-03",
            "consensus_mode": "BALANCED",
            "execution_phase": "intraday_live",
            "prompt_contract": "selection_compact.v1",
            "watch_cap": 15,
            "trade_cap": 5,
            "candidates": [{"ticker": "AAPL", "selection_evidence_action_ceiling": "WATCH"}],
        }
        normal = selection_smart_skip.semantic_signature(
            **base,
            market_context=selection_smart_skip.market_context_components(market_change_pct=0.2, consensus_mode="BALANCED"),
        )
        severe = selection_smart_skip.semantic_signature(
            **base,
            market_context=selection_smart_skip.market_context_components(market_change_pct=-3.2, consensus_mode="BALANCED"),
        )

        self.assertNotEqual(normal, severe)

    def test_semantic_signature_changes_on_core_rank_hash(self) -> None:
        first_candidates = [{"ticker": "AAPL"}, {"ticker": "MSFT"}]
        second_candidates = [{"ticker": "MSFT"}, {"ticker": "AAPL"}]
        base = {
            "market": "US",
            "session_date": "2026-06-03",
            "consensus_mode": "BALANCED",
            "execution_phase": "intraday_live",
            "prompt_contract": "selection_compact.v1",
            "watch_cap": 15,
            "trade_cap": 5,
        }
        first = selection_smart_skip.semantic_signature(
            **base,
            candidates=first_candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(first_candidates, market="US", start=1, end=25),
        )
        second = selection_smart_skip.semantic_signature(
            **base,
            candidates=second_candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(second_candidates, market="US", start=1, end=25),
        )

        self.assertNotEqual(first, second)

    def test_semantic_signature_ignores_tail_rank_hash_by_default(self) -> None:
        core = [{"ticker": f"C{i}"} for i in range(1, 26)]
        first_candidates = core + [{"ticker": "TAIL1"}, {"ticker": "TAIL2"}]
        second_candidates = core + [{"ticker": "TAIL2"}, {"ticker": "TAIL1"}]
        base = {
            "market": "US",
            "session_date": "2026-06-03",
            "consensus_mode": "BALANCED",
            "execution_phase": "intraday_live",
            "prompt_contract": "selection_compact.v1",
            "watch_cap": 15,
            "trade_cap": 5,
        }
        first = selection_smart_skip.semantic_signature(
            **base,
            candidates=first_candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(first_candidates, market="US", start=1, end=25),
            prompt_pool_tail_hash=selection_smart_skip.prompt_pool_rank_hash(first_candidates, market="US", start=26, end=40),
        )
        second = selection_smart_skip.semantic_signature(
            **base,
            candidates=second_candidates,
            prompt_pool_core_hash=selection_smart_skip.prompt_pool_rank_hash(second_candidates, market="US", start=1, end=25),
            prompt_pool_tail_hash=selection_smart_skip.prompt_pool_rank_hash(second_candidates, market="US", start=26, end=40),
        )

        self.assertEqual(first, second)

    def test_semantic_signature_changes_on_action_ceiling(self) -> None:
        base = {
            "market": "US",
            "session_date": "2026-06-03",
            "consensus_mode": "BALANCED",
            "execution_phase": "intraday_live",
            "prompt_contract": "selection_compact.v1",
            "watch_cap": 15,
            "trade_cap": 5,
        }
        watch = selection_smart_skip.semantic_signature(
            **base,
            candidates=[{"ticker": "AAPL", "selection_evidence_action_ceiling": "WATCH"}],
        )
        probe = selection_smart_skip.semantic_signature(
            **base,
            candidates=[{"ticker": "AAPL", "selection_evidence_action_ceiling": "PROBE_READY"}],
        )

        self.assertNotEqual(watch, probe)

    def test_live_mode_reuses_compact_watch_only_candidate_actions(self) -> None:
        prompt_hash = selection_smart_skip.sha256_text("same prompt")
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            selection_meta={
                "watchlist": ["AAPL", "MSFT"],
                "trade_ready": [],
                "candidate_actions": [
                    {"ticker": "AAPL", "action": "WATCH", "reason": "wait"},
                    {"ticker": "MSFT", "action": "WATCH", "price_targets": {}},
                ],
                "_candidate_action_routes": [
                    {"ticker": "AAPL", "final_action": "WATCH", "route": "watch"},
                    {"ticker": "MSFT", "final_action": "WATCH", "route": "watch"},
                ],
            },
            reasons={"AAPL": "wait", "MSFT": "wait"},
            session_date="2026-06-04",
        )

        with patch.dict(os.environ, {"SELECTION_SMART_SKIP_MODE": "live"}, clear=False):
            decision = selection_smart_skip.maybe_reuse(
                market="US",
                consensus_mode="BALANCED",
                execution_phase="intraday_live",
                prompt_hash=prompt_hash,
                prompt_candidate_count=3,
                session_date="2026-06-04",
            )

        self.assertTrue(decision["reuse"])
        self.assertEqual(decision["reason"], "prompt_cache_hit")
        self.assertEqual(decision["selection_meta"]["candidate_actions"][0]["action"], "WATCH")

    def test_fail_open_when_prompt_changes(self) -> None:
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=selection_smart_skip.sha256_text("old prompt"),
            prompt_candidate_count=3,
            selection_meta={"watchlist": ["AAPL"], "trade_ready": []},
            session_date="2026-06-04",
        )

        decision = selection_smart_skip.maybe_reuse(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=selection_smart_skip.sha256_text("new prompt"),
            prompt_candidate_count=3,
            session_date="2026-06-04",
        )
        state = selection_smart_skip.load_state("US", "2026-06-04")

        self.assertFalse(decision["reuse"])
        self.assertEqual(decision["reason"], "prompt_changed")
        self.assertEqual(state["fail_open_reasons"]["prompt_changed"], 1)

    def test_fail_open_for_cached_trade_ready_by_default(self) -> None:
        prompt_hash = selection_smart_skip.sha256_text("same prompt")
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            selection_meta={"watchlist": ["AAPL"], "trade_ready": ["AAPL"]},
            session_date="2026-06-04",
        )

        decision = selection_smart_skip.maybe_reuse(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            session_date="2026-06-04",
        )

        self.assertFalse(decision["reuse"])
        self.assertEqual(decision["reason"], "cached_entry_actionable")

    def test_fail_open_for_cached_pullback_wait_price_plan(self) -> None:
        prompt_hash = selection_smart_skip.sha256_text("same prompt")
        selection_smart_skip.record_full_call(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            selection_meta={
                "watchlist": ["AAPL"],
                "trade_ready": [],
                "candidate_actions": [
                    {
                        "ticker": "AAPL",
                        "action": "PULLBACK_WAIT",
                        "price_targets": {
                            "buy_zone_low": 100.0,
                            "buy_zone_high": 101.0,
                            "sell_target": 106.0,
                            "stop_loss": 98.0,
                            "hold_days": 1,
                            "confidence": 0.7,
                        },
                    }
                ],
            },
            session_date="2026-06-04",
        )

        decision = selection_smart_skip.maybe_reuse(
            market="US",
            consensus_mode="BALANCED",
            execution_phase="intraday_live",
            prompt_hash=prompt_hash,
            prompt_candidate_count=3,
            session_date="2026-06-04",
        )

        self.assertFalse(decision["reuse"])
        self.assertEqual(decision["reason"], "cached_entry_actionable")


if __name__ == "__main__":
    unittest.main()
