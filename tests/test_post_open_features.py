from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.post_open_features import (
    append_feature_snapshot,
    append_feature_snapshot_payload,
    build_post_open_snapshot,
    feature_known_at_allowed,
    infer_momentum_state,
    load_recent_feature_snapshots,
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

    def test_us_snapshot_uses_market_session_date_for_identity(self) -> None:
        snapshot = build_post_open_snapshot(
            market="US",
            ticker="AAPL",
            known_at="2026-06-04T01:10:00+09:00",
            anchor_at="2026-06-04T22:30:00+09:00",
            anchor_price=100,
            current_price=101,
            returns={},
            market_session_date="2026-06-03",
        )
        payload = snapshot.to_dict()

        self.assertEqual(payload["market_session_date"], "2026-06-03")
        self.assertEqual(payload["session_date"], "2026-06-03")
        self.assertTrue(payload["snapshot_id"].startswith("20260603|US|AAPL|"))

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
                self.assertEqual(payload["feature_surface"], "post_open_feature_builder")
                self.assertTrue(payload["runtime_gate_evidence_preferred"])

    def test_load_recent_feature_snapshots_filters_session_and_keeps_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "US",
                        "ticker": "AAPL",
                        "known_at": "2026-05-14T00:10:00",
                        "anchor_at": "2026-05-13T22:30:00",
                        "anchor_price": 100.0,
                        "current_price": 101.0,
                        "data_quality": "minute_partial",
                    }
                )
                append_feature_snapshot_payload(
                    {
                        "market": "US",
                        "ticker": "aapl",
                        "known_at": "2026-05-14T00:12:00",
                        "anchor_at": "2026-05-13T22:30:00",
                        "anchor_price": 100.0,
                        "current_price": 103.0,
                        "data_quality": "minute_complete",
                    }
                )
                append_feature_snapshot_payload(
                    {
                        "market": "US",
                        "ticker": "MSFT",
                        "known_at": "2026-05-14T00:13:00",
                        "anchor_at": "2026-05-12T22:30:00",
                        "anchor_price": 200.0,
                        "current_price": 201.0,
                        "data_quality": "minute_complete",
                    }
                )

                loaded = load_recent_feature_snapshots(market="US", session_date="2026-05-13")

                self.assertEqual(set(loaded), {"AAPL"})
                self.assertEqual(loaded["AAPL"]["current_price"], 103.0)
                self.assertEqual(loaded["AAPL"]["data_quality"], "minute_complete")
                self.assertEqual(load_recent_feature_snapshots(market="US", session_date="2026-05-14"), {})

    def test_us_feature_restore_uses_market_session_date_after_kst_midnight(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "US",
                        "ticker": "AAPL",
                        "known_at": "2026-06-04T01:10:00+09:00",
                        "anchor_at": "2026-06-04T22:30:00+09:00",
                        "market_session_date": "2026-06-03",
                        "anchor_price": 100.0,
                        "current_price": 101.0,
                        "data_quality": "minute_complete",
                    }
                )

                loaded = load_recent_feature_snapshots(market="US", session_date="2026-06-03")

        self.assertEqual(set(loaded), {"AAPL"})
        self.assertEqual(loaded["AAPL"]["market_session_date"], "2026-06-03")

    def test_load_recent_feature_snapshots_prefers_higher_quality_over_later_low_quality(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:00:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 110.0,
                        "data_quality": "minute_complete",
                        "ret_3m_pct": 1.0,
                        "ret_5m_pct": 2.0,
                        "opening_range_break": True,
                        "vwap_distance_pct": 0.5,
                        "volume_ratio_open": 2.0,
                    }
                )
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:01:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 111.0,
                        "data_quality": "first_observed",
                    }
                )

                loaded = load_recent_feature_snapshots(market="KR", session_date="2026-05-13")

                self.assertEqual(loaded["005930"]["known_at"], "2026-05-13T15:00:00")
                self.assertEqual(loaded["005930"]["data_quality"], "minute_complete")
                self.assertEqual(loaded["005930"]["ret_5m_pct"], 2.0)

    def test_load_recent_feature_snapshots_prefers_later_fail_closed_over_stale_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:00:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 110.0,
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 2.0,
                    }
                )
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:06:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "data_quality": "minute_missing",
                        "fail_closed": True,
                        "evidence_status": "fail_closed",
                        "evidence_action_ceiling": "WATCH",
                    }
                )

                loaded = load_recent_feature_snapshots(market="KR", session_date="2026-05-13")

                self.assertEqual(loaded["005930"]["known_at"], "2026-05-13T15:06:00")
                self.assertEqual(loaded["005930"]["data_quality"], "minute_missing")
                self.assertTrue(loaded["005930"]["fail_closed"])
                self.assertEqual(loaded["005930"]["evidence_action_ceiling"], "WATCH")

    def test_load_recent_feature_snapshots_newer_complete_recovers_from_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:05:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "data_quality": "minute_missing",
                        "fail_closed": True,
                        "evidence_status": "fail_closed",
                        "evidence_action_ceiling": "WATCH",
                    }
                )
                append_feature_snapshot_payload(
                    {
                        "market": "KR",
                        "ticker": "005930",
                        "known_at": "2026-05-13T15:06:00",
                        "anchor_at": "2026-05-13T09:00:00",
                        "anchor_price": 100.0,
                        "current_price": 112.0,
                        "data_quality": "minute_complete",
                        "ret_5m_pct": 3.0,
                    }
                )

                loaded = load_recent_feature_snapshots(market="KR", session_date="2026-05-13")

                self.assertEqual(loaded["005930"]["known_at"], "2026-05-13T15:06:00")
                self.assertEqual(loaded["005930"]["data_quality"], "minute_complete")
                self.assertEqual(loaded["005930"]["ret_5m_pct"], 3.0)

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

    def test_returns_from_history_rejects_tick_after_known_at(self) -> None:
        # Only available tick is at 09:05:40, but known_at is 09:05:20.
        # The tick is after known_at → must not be selected (future leak).
        returns = returns_from_price_history(
            [{"ts": "2026-05-06T09:05:40", "price": 103.0}],
            anchor_at="2026-05-06T09:00:00",
            anchor_price=100.0,
            known_at="2026-05-06T09:05:20",
            max_lag_sec=180,
        )

        self.assertIsNone(returns["ret_5m_pct"])

    def test_returns_from_history_accepts_tick_at_known_boundary(self) -> None:
        # Tick exactly at known_at boundary should be accepted.
        returns = returns_from_price_history(
            [{"ts": "2026-05-06T09:05:10", "price": 102.0}],
            anchor_at="2026-05-06T09:00:00",
            anchor_price=100.0,
            known_at="2026-05-06T09:05:20",
            max_lag_sec=180,
        )

        self.assertAlmostEqual(returns["ret_5m_pct"], 2.0)


if __name__ == "__main__":
    unittest.main()
