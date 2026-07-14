from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from decision.registry import DecisionRegistry, _compact_selection_meta
from lifecycle.event_store import EventStore


class SelectionMetaCompactionTests(unittest.TestCase):
    def test_compact_strips_only_heavy_unused_keys(self) -> None:
        meta = {
            "trade_ready": ["A"],
            "watchlist": ["B"],
            "consensus_mode": "BEAR",
            "_final_prompt_pool": [{"x": 1}],
            "_live_evidence": {"e": 1},
            "_adaptive_live_condition": {"a": 1},
            "_post_open_features_by_ticker": {"A": {"big": "x" * 100}},
            "_shadow_overlay_prompt_pool": ["y" * 100],
            "_strategy_feasibility_by_ticker": {"A": "z" * 100},
            "_excluded_from_prompt": [{"candidate": {"ticker": "Z"}}],
        }
        out = _compact_selection_meta(meta)
        self.assertEqual(
            set(meta) - set(out),
            {
                "_post_open_features_by_ticker",
                "_shadow_overlay_prompt_pool",
                "_strategy_feasibility_by_ticker",
                "_excluded_from_prompt",
            },
        )
        for keep in ("trade_ready", "watchlist", "consensus_mode", "_final_prompt_pool", "_live_evidence", "_adaptive_live_condition"):
            self.assertIn(keep, out)

    def test_compact_does_not_mutate_original(self) -> None:
        meta = {"trade_ready": ["A"], "_post_open_features_by_ticker": {"A": 1}}
        _compact_selection_meta(meta)
        self.assertIn("_post_open_features_by_ticker", meta)

    def test_compact_returns_same_object_when_no_heavy_keys(self) -> None:
        meta = {"trade_ready": ["A"]}
        self.assertIs(_compact_selection_meta(meta), meta)

    def test_registered_event_payload_excludes_heavy_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            reg = DecisionRegistry(store=store)
            meta = {
                "trade_ready": ["005930"],
                "recommended_strategy": {"005930": "orp"},
                "consensus_mode": "BEAR",
                "_post_open_features_by_ticker": {"005930": {"big": "x" * 500}},
                "_excluded_from_prompt": [{"candidate": {"ticker": "000660"}}],
            }
            reg.register_trade_ready_batch(
                market="KR",
                runtime_mode="live",
                session_date="2026-07-14",
                tickers=["005930"],
                prompt_version="v1",
                brain_snapshot_id="snap",
                selection_meta=meta,
            )
            events = store.events_for_session(market="KR", runtime_mode="live", session_date="2026-07-14")
            ready = [e for e in events if e.get("event_type") == "CLAUDE_TRADE_READY"]
            self.assertEqual(len(ready), 1)
            stored_meta = ready[0]["payload"]["selection_meta"]
            self.assertNotIn("_post_open_features_by_ticker", stored_meta)
            self.assertNotIn("_excluded_from_prompt", stored_meta)
            self.assertIn("trade_ready", stored_meta)
            self.assertIn("consensus_mode", stored_meta)
            # 원본 meta는 훼손되지 않아야 한다
            self.assertIn("_post_open_features_by_ticker", meta)


if __name__ == "__main__":
    unittest.main()
