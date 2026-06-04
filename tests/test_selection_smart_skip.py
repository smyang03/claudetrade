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


if __name__ == "__main__":
    unittest.main()
