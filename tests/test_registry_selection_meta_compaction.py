from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from decision.registry import DecisionRegistry, _compact_selection_meta
from lifecycle.event_store import EventStore


class SelectionMetaCompactionTests(unittest.TestCase):
    def test_compact_keeps_only_consumed_per_ticker_projection(self) -> None:
        meta = {
            "trade_ready": ["A"],
            "watchlist": ["B"],
            "consensus_mode": "BEAR",
            "price_targets": {"A": {"target": 101}, "B": {"target": 202}},
            "_final_prompt_pool": [{
                "ticker": "A",
                "candidate_pool_role": "DISCOVERY",
                "discovery_reason": "sector wave",
                "huge_unused": "p" * 1000,
            }],
            "_live_evidence": {"e": 1},
            "_adaptive_live_condition": {"a": 1},
            "candidate_actions": [{"ticker": "A", "blob": "c" * 1000}],
            "_candidate_action_routes": [{"ticker": "A", "blob": "r" * 1000}],
            "_post_open_features_by_ticker": {"A": {"big": "x" * 100}},
            "_shadow_overlay_prompt_pool": ["y" * 100],
            "_strategy_feasibility_by_ticker": {"A": "z" * 100},
            "_excluded_from_prompt": [{"candidate": {"ticker": "Z"}}],
        }
        out = _compact_selection_meta(meta, ticker="A", market="US")
        self.assertEqual(out["trade_ready"], ["A"])
        self.assertNotIn("watchlist", out)
        self.assertEqual(out["price_targets"], {"A": {"target": 101}})
        self.assertEqual(out["_final_prompt_pool"], [{
            "ticker": "A",
            "candidate_pool_role": "DISCOVERY",
            "discovery_reason": "sector wave",
        }])
        for removed in (
            "_live_evidence", "_adaptive_live_condition", "candidate_actions",
            "_candidate_action_routes", "_post_open_features_by_ticker",
            "_shadow_overlay_prompt_pool", "_strategy_feasibility_by_ticker",
            "_excluded_from_prompt",
        ):
            self.assertNotIn(removed, out)

    def test_compact_does_not_mutate_original(self) -> None:
        meta = {"trade_ready": ["A"], "_post_open_features_by_ticker": {"A": 1}}
        _compact_selection_meta(meta)
        self.assertIn("_post_open_features_by_ticker", meta)

    def test_compact_always_returns_detached_projection(self) -> None:
        meta = {"trade_ready": ["A"]}
        self.assertIsNot(_compact_selection_meta(meta), meta)

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
            self.assertEqual(stored_meta["recommended_strategy"], {"005930": "orp"})
            self.assertLess(len(json.dumps(ready[0]["payload"])), 8192)
            # 원본 meta는 훼손되지 않아야 한다
            self.assertIn("_post_open_features_by_ticker", meta)


if __name__ == "__main__":
    unittest.main()
