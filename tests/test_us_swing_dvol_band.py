# -*- coding: utf-8 -*-
"""거래대금 밴드 재선택 계약 테스트 (2026-08-20).

228세션 백테스트: 현행 rank1 무조건 +0.03%(t0.04) → 밴드 재선택 +3.67%(t4.80).
밴드 밖은 전 구간 음수. 아래는 그 규칙이 코드에서 정확히 집행되는지 고정한다.
"""
from __future__ import annotations

import sqlite3
import unittest

from runtime.us_swing_order_bridge import _apply_dollar_volume_band, _dollar_volume_by_ticker


class _Bot:
    def __init__(self, enabled=True, lo=100.0, hi=500.0):
        self._enabled, self._lo, self._hi = enabled, lo, hi

    def _runtime_bool(self, key, default=False):
        return self._enabled if key == "US_SWING_DVOL_BAND_ENABLED" else default

    def _runtime_float(self, key, default=0.0):
        if key == "US_SWING_DVOL_BAND_MIN_M":
            return self._lo
        if key == "US_SWING_DVOL_BAND_MAX_M":
            return self._hi
        return default


def _con(rows):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE candidate_pool_all (session_date TEXT, ticker TEXT, dollar_vol REAL)")
    con.executemany("INSERT INTO candidate_pool_all VALUES (?,?,?)", rows)
    return con


SD = "2026-08-20"


class DollarVolumeBandTests(unittest.TestCase):
    def test_picks_best_rank_inside_band_not_rank1(self):
        # rank1은 밴드 밖(1,300M — 뉴스 주도 재평가 구간), rank3이 밴드 안
        con = _con([(SD, "AXTI", 1_300e6), (SD, "FN", 900e6), (SD, "MXL", 200e6)])
        signals = [{"ticker": "AXTI", "rank": 1}, {"ticker": "FN", "rank": 2},
                   {"ticker": "MXL", "rank": 3}]
        kept, meta = _apply_dollar_volume_band(_Bot(), con, SD, signals)
        self.assertTrue(meta["applied"])
        self.assertEqual([s["ticker"] for s in kept], ["MXL"])

    def test_keeps_rank_order_within_band(self):
        con = _con([(SD, "A", 150e6), (SD, "B", 250e6), (SD, "C", 900e6)])
        signals = [{"ticker": "A", "rank": 1}, {"ticker": "B", "rank": 2}, {"ticker": "C", "rank": 3}]
        kept, _ = _apply_dollar_volume_band(_Bot(), con, SD, signals)
        self.assertEqual([s["ticker"] for s in kept], ["A", "B"])  # 랭크 순서 보존

    def test_no_band_candidate_returns_empty(self):
        # 전부 밴드 밖이면 그날은 사지 않는다(백테스트 ③과 동일 규약)
        con = _con([(SD, "A", 50e6), (SD, "B", 2_000e6)])
        signals = [{"ticker": "A", "rank": 1}, {"ticker": "B", "rank": 2}]
        kept, meta = _apply_dollar_volume_band(_Bot(), con, SD, signals)
        self.assertEqual(kept, [])
        self.assertTrue(meta["applied"])

    def test_fail_open_when_dollar_volume_missing(self):
        # 거래대금 원장이 비면 매매를 멈추지 않고 현행(rank1)으로 간다
        con = _con([])
        signals = [{"ticker": "A", "rank": 1}]
        kept, meta = _apply_dollar_volume_band(_Bot(), con, SD, signals)
        self.assertEqual([s["ticker"] for s in kept], ["A"])
        self.assertFalse(meta["applied"])
        self.assertEqual(meta["reason"], "dollar_volume_unavailable")

    def test_disabled_is_noop(self):
        con = _con([(SD, "A", 2_000e6)])
        signals = [{"ticker": "A", "rank": 1}]
        kept, meta = _apply_dollar_volume_band(_Bot(enabled=False), con, SD, signals)
        self.assertEqual(kept, signals)
        self.assertFalse(meta["applied"])

    def test_boundaries_are_inclusive_low_exclusive_high(self):
        con = _con([(SD, "LO", 100e6), (SD, "HI", 500e6)])
        signals = [{"ticker": "LO", "rank": 1}, {"ticker": "HI", "rank": 2}]
        kept, _ = _apply_dollar_volume_band(_Bot(), con, SD, signals)
        self.assertEqual([s["ticker"] for s in kept], ["LO"])

    def test_dollar_volume_loader_converts_to_millions(self):
        con = _con([(SD, "A", 250_000_000.0)])
        self.assertAlmostEqual(_dollar_volume_by_ticker(con, SD)["A"], 250.0)


if __name__ == "__main__":
    unittest.main()
