from __future__ import annotations

import unittest

from runtime.intraday_features import compute_intraday_features, normalize_intraday_candles


class IntradayFeatureTests(unittest.TestCase):
    def _candles(self):
        return [
            {"ts": "2026-05-13T09:00:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100},
            {"ts": "2026-05-13T09:01:00", "open": 100, "high": 102, "low": 100, "close": 101, "volume": 100},
            {"ts": "2026-05-13T09:02:00", "open": 101, "high": 103, "low": 101, "close": 102, "volume": 100},
            {"ts": "2026-05-13T09:03:00", "open": 102, "high": 104, "low": 102, "close": 103, "volume": 100},
            {"ts": "2026-05-13T09:04:00", "open": 103, "high": 105, "low": 103, "close": 104, "volume": 100},
            {"ts": "2026-05-13T09:05:00", "open": 104, "high": 106, "low": 104, "close": 105, "volume": 100},
            {"ts": "2026-05-13T09:06:00", "open": 105, "high": 107, "low": 105, "close": 107, "volume": 100},
        ]

    def test_normalize_accepts_kis_style_rows(self) -> None:
        rows = [
            {
                "stck_bsop_date": "20260513",
                "stck_cntg_hour": "090000",
                "stck_oprc": "100",
                "stck_hgpr": "102",
                "stck_lwpr": "99",
                "stck_prpr": "101",
                "cntg_vol": "123",
            }
        ]

        candles = normalize_intraday_candles(rows, market="KR", ticker="005930", source="kis")

        self.assertEqual(candles[0]["ts"], "2026-05-13T09:00:00")
        self.assertEqual(candles[0]["close"], 101.0)
        self.assertEqual(candles[0]["volume"], 123.0)

    def test_compute_returns_vwap_volume_and_opening_range(self) -> None:
        features = compute_intraday_features(
            self._candles(),
            market="KR",
            ticker="005930",
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume=39000,
            opening_range_min=3,
            source="unit",
        )

        self.assertAlmostEqual(features["ret_3m_pct"], 3.0)
        self.assertAlmostEqual(features["ret_5m_pct"], 5.0)
        self.assertEqual(features["opening_range_high"], 104.0)
        self.assertTrue(features["opening_range_break"])
        self.assertGreater(features["vwap"], 0)
        self.assertGreater(features["vwap_distance_pct"], 0)
        self.assertGreater(features["volume_ratio_open"], 1.0)
        self.assertEqual(features["data_quality"], "minute_complete")

    def test_does_not_use_candle_after_known_at(self) -> None:
        features = compute_intraday_features(
            [{"ts": "2026-05-13T09:05:40", "open": 100, "high": 102, "low": 99, "close": 102, "volume": 100}],
            market="KR",
            ticker="005930",
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:05:20",
            avg_daily_volume=39000,
        )

        self.assertIsNone(features["ret_5m_pct"])
        self.assertEqual(features["data_quality"], "minute_missing")

    def test_opening_range_is_none_before_completion(self) -> None:
        us_candles = [
            {"ts": "2026-05-13T22:30:00", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100},
            {"ts": "2026-05-13T22:31:00", "open": 100, "high": 102, "low": 100, "close": 101, "volume": 100},
            {"ts": "2026-05-13T22:32:00", "open": 101, "high": 103, "low": 101, "close": 102, "volume": 100},
            {"ts": "2026-05-13T22:33:00", "open": 102, "high": 104, "low": 102, "close": 103, "volume": 100},
        ]
        features = compute_intraday_features(
            us_candles,
            market="US",
            ticker="aapl",
            regular_open="2026-05-13T22:30:00",
            known_at="2026-05-13T22:33:00",
            avg_daily_volume=39000,
        )

        self.assertEqual(features["ticker"], "AAPL")
        self.assertIsNone(features["opening_range_break"])
        self.assertIn("opening_range_break", features["missing_fields"])
        self.assertEqual(features["data_quality"], "minute_partial")

    def test_zero_volume_leaves_vwap_and_volume_missing(self) -> None:
        candles = [
            {**row, "volume": 0}
            for row in self._candles()
        ]

        features = compute_intraday_features(
            candles,
            market="KR",
            ticker="005930",
            regular_open="2026-05-13T09:00:00",
            known_at="2026-05-13T09:06:00",
            avg_daily_volume=39000,
            opening_range_min=3,
        )

        self.assertIsNone(features["vwap"])
        self.assertIsNone(features["vwap_distance_pct"])
        self.assertIsNone(features["volume_ratio_open"])
        self.assertIn("vwap_distance_pct", features["missing_fields"])
        self.assertEqual(features["data_quality"], "minute_partial")


if __name__ == "__main__":
    unittest.main()
