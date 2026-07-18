from __future__ import annotations

import unittest

import pandas as pd

from bot.pool_quality_features import compute_pool_quality_features


def _frame(closes):
    return pd.DataFrame({"close": closes})


class PoolQualityFeaturesTests(unittest.TestCase):
    def test_big_spike_flags_level_12(self) -> None:
        closes = [100.0] * 20 + [113.0, 113.5, 114.0]  # +13% 단일일 급등
        out = compute_pool_quality_features(_frame(closes))
        self.assertGreaterEqual(out["max_daily_ret_21d"], 12.0)
        self.assertEqual(out["spike_chase_level"], 12)
        self.assertIn("realized_vol_21d", out)
        self.assertIn("ret_1m_pct", out)

    def test_moderate_spike_flags_level_8(self) -> None:
        closes = [100.0] * 20 + [109.0, 109.2, 109.5]  # +9% 급등
        out = compute_pool_quality_features(_frame(closes))
        self.assertEqual(out["spike_chase_level"], 8)

    def test_calm_flags_level_0(self) -> None:
        closes = [100.0 + i * 0.3 for i in range(25)]  # 완만
        out = compute_pool_quality_features(_frame(closes))
        self.assertEqual(out["spike_chase_level"], 0)
        self.assertLess(out["max_daily_ret_21d"], 8.0)

    def test_uppercase_close_column(self) -> None:
        df = pd.DataFrame({"Close": [100.0] * 20 + [115.0, 116.0, 117.0]})
        out = compute_pool_quality_features(df)
        self.assertEqual(out["spike_chase_level"], 12)

    def test_list_of_dict_input(self) -> None:
        rows = [{"close": 100.0} for _ in range(20)] + [{"close": 108.5}, {"close": 108.6}]
        out = compute_pool_quality_features(rows)
        self.assertEqual(out["spike_chase_level"], 8)

    def test_insufficient_data_returns_source_only(self) -> None:
        out = compute_pool_quality_features(_frame([100.0, 101.0]))
        self.assertEqual(out.get("pool_quality_source"), "pool_quality:v1")
        self.assertNotIn("spike_chase_level", out)

    def test_none_candles(self) -> None:
        out = compute_pool_quality_features(None)
        self.assertNotIn("spike_chase_level", out)


if __name__ == "__main__":
    unittest.main()
