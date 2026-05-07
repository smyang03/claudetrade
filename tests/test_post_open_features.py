from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.post_open_features import (
    append_feature_snapshot,
    build_post_open_snapshot,
    feature_known_at_allowed,
    infer_momentum_state,
    returns_from_price_history,
)


class PostOpenFeatureTests(unittest.TestCase):
    def test_known_at_blocks_future_returns(self) -> None:
        snapshot = build_post_open_snapshot(
            market="KR",
            ticker="001440",
            known_at="2026-05-06T09:05:00",
            anchor_at="2026-05-06T09:00:00",
            anchor_price=1000,
            current_price=1020,
            returns={"ret_5m_pct": 2.0, "ret_30m_pct": 8.0},
        )

        self.assertEqual(snapshot.ret_5m_pct, 2.0)
        self.assertIsNone(snapshot.ret_30m_pct)
        self.assertEqual(snapshot.momentum_state, "early_strength")

    def test_3m_snapshot_is_probe_only_state(self) -> None:
        snapshot = build_post_open_snapshot(
            market="US",
            ticker="INTC",
            known_at="2026-05-06T22:33:00",
            anchor_at="2026-05-06T22:30:00",
            anchor_price=100,
            current_price=100.7,
            returns={"ret_3m_pct": 0.7, "ret_5m_pct": 1.2},
        )

        self.assertEqual(snapshot.ret_3m_pct, 0.7)
        self.assertIsNone(snapshot.ret_5m_pct)
        self.assertEqual(snapshot.momentum_state, "early_probe_only")

    def test_overextended_threshold_is_market_specific(self) -> None:
        self.assertEqual(
            infer_momentum_state(market="KR", ret_5m_pct=5.2, ret_30m_pct=None),
            "early_strength",
        )
        self.assertEqual(
            infer_momentum_state(market="US", ret_5m_pct=3.2, ret_30m_pct=None),
            "overextended",
        )

    def test_feature_known_at_allowed(self) -> None:
        self.assertTrue(
            feature_known_at_allowed(
                known_at="2026-05-06T09:30:00",
                anchor_at="2026-05-06T09:00:00",
                offset_min=30,
            )
        )
        self.assertFalse(
            feature_known_at_allowed(
                known_at="2026-05-06T09:29:59",
                anchor_at="2026-05-06T09:00:00",
                offset_min=30,
            )
        )

    def test_append_feature_snapshot_writes_utf8_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                snapshot = build_post_open_snapshot(
                    market="KR",
                    ticker="001440",
                    known_at="2026-05-06T09:05:00",
                    anchor_at="2026-05-06T09:00:00",
                    anchor_price=1000,
                    current_price=1020,
                    returns={"ret_5m_pct": 2.0},
                )
                append_feature_snapshot(snapshot)
                path = Path(tmpdir) / "logs" / "funnel" / "post_open_features_20260506_KR.jsonl"
                payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
                self.assertEqual(payload["ticker"], "001440")

    def test_returns_from_history_are_future_blind_and_lag_limited(self) -> None:
        returns = returns_from_price_history(
            [
                {"ts": "2026-05-06T09:05:40", "price": 103.0},
                {"ts": "2026-05-06T09:31:00", "price": 110.0},
            ],
            anchor_at="2026-05-06T09:00:00",
            anchor_price=100.0,
            known_at="2026-05-06T09:06:00",
            max_lag_sec=180,
        )

        self.assertAlmostEqual(returns["ret_5m_pct"], 3.0)
        self.assertIsNone(returns["ret_30m_pct"])

    def test_returns_from_history_rejects_stale_target_sample(self) -> None:
        returns = returns_from_price_history(
            [{"ts": "2026-05-06T09:20:00", "price": 103.0}],
            anchor_at="2026-05-06T09:00:00",
            anchor_price=100.0,
            known_at="2026-05-06T09:20:00",
            max_lag_sec=180,
        )

        self.assertIsNone(returns["ret_5m_pct"])


if __name__ == "__main__":
    unittest.main()
