# -*- coding: utf-8 -*-
"""KOSDAQ 급락일 지수 ETF 쉐도우 — 순수 함수 테스트(신호·stale 가드·어긋남 분류·정산 산수)."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import kr_index_etf_panic_shadow as m  # noqa: E402


class DecideTest(unittest.TestCase):
    def test_signal_at_threshold(self):
        self.assertEqual(m.decide(-2.5, False, "OPEN"), (True, "signal_close"))
        self.assertEqual(m.decide(-2.49, False, "OPEN"), (False, "above_thr"))
        self.assertEqual(m.decide(-5.0, False, ""), (True, "signal_close"))

    def test_gap_signal_v2(self):
        """v2 — 시가 갭 ≤−1%면 종가가 문턱 위여도 신호(11년 250건 t 3.40, 연도 11/11)."""
        self.assertEqual(m.decide(-0.5, False, "OPEN", gap_pct=-1.0), (True, "signal_gap"))
        self.assertEqual(m.decide(+1.2, False, "OPEN", gap_pct=-2.3), (True, "signal_gap"))
        self.assertEqual(m.decide(-3.0, False, "OPEN", gap_pct=-1.5), (True, "signal_both"))
        self.assertEqual(m.decide(-0.5, False, "OPEN", gap_pct=-0.99), (False, "above_thr"))
        # 갭 결측이면 갭 조건만 빠지고 종가 조건은 그대로 (fail-open 아님)
        self.assertEqual(m.decide(-0.5, False, "OPEN", gap_pct=None), (False, "above_thr"))
        self.assertEqual(m.decide(-3.0, False, "OPEN", gap_pct=None), (True, "signal_close"))
        # stale·휴장·결측은 갭이 있어도 신호 아님
        self.assertEqual(m.decide(-0.5, True, "OPEN", gap_pct=-3.0), (False, "stale_quote"))
        self.assertEqual(m.decide(-0.5, False, "CLOSE", gap_pct=-3.0), (False, "market_close"))
        self.assertEqual(m.decide(None, False, "OPEN", gap_pct=-3.0), (False, "ratio_missing"))

    def test_stale_and_closed_never_signal(self):
        self.assertEqual(m.decide(-6.0, True, "OPEN"), (False, "stale_quote"))
        self.assertEqual(m.decide(-6.0, False, "CLOSE"), (False, "market_close"))
        self.assertEqual(m.decide(None, False, "OPEN"), (False, "ratio_missing"))

    def test_quote_age(self):
        now = datetime(2026, 9, 9, 15, 19, 0, tzinfo=m.KST)
        self.assertAlmostEqual(m.quote_age_sec("2026-09-09T15:18:30+09:00", now), 30.0)
        self.assertEqual(m.quote_age_sec(None, now), float("inf"))
        self.assertGreater(m.quote_age_sec("2026-09-08T18:59:00+09:00", now), m.STALE_SEC)
        # 09-09 실측: 네이버 지수 localTradedAt은 분 단위라 15:18:00을 15:19:00에 읽으면 60s — stale이 아니어야 한다
        self.assertLessEqual(m.quote_age_sec("2026-09-09T15:18:00+09:00", now), m.STALE_SEC)
        self.assertEqual(m.decide(-3.0, m.quote_age_sec("2026-09-09T15:18:00+09:00", now) > m.STALE_SEC, "OPEN"), (True, "signal_close"))


class DivergenceTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(m.classify_divergence(True, -2.6), "agree_signal")
        self.assertEqual(m.classify_divergence(True, -2.0), "false_positive_1519")
        self.assertEqual(m.classify_divergence(False, -2.6), "missed_by_1519")
        self.assertEqual(m.classify_divergence(False, -1.0), "agree_none")
        self.assertEqual(m.classify_divergence(True, None), "close_unknown")

    def test_divergence_counts_gap_v2(self):
        """v2 — 갭 신호는 09:00에 확정이므로 종가 확정 신호에도 포함된다."""
        self.assertEqual(m.classify_divergence(True, -0.5, gap_pct=-1.5), "agree_signal")
        self.assertEqual(m.classify_divergence(False, -0.5, gap_pct=-1.5), "missed_by_1519")
        self.assertEqual(m.classify_divergence(True, -0.5, gap_pct=None), "false_positive_1519")
        self.assertEqual(m.classify_divergence(False, -0.5, gap_pct=-0.5), "agree_none")


class SettleTest(unittest.TestCase):
    def test_net_uses_close_entry_and_cost(self):
        r = m.settle_math(10000.0, 10100.0, 10050.0, entry_1519=10200.0, cost=0.05)
        self.assertAlmostEqual(r["net_pct"], 1.0 - 0.05, places=6)
        self.assertAlmostEqual(r["d1_pct"], 0.5 - 0.05, places=6)
        self.assertAlmostEqual(r["net_1519_pct"], (10100.0 / 10200.0 - 1) * 100 - 0.05, places=3)
        self.assertEqual(r["exit_price"], 10100.0)

    def test_no_1519_entry(self):
        r = m.settle_math(100.0, 99.0, None)
        self.assertNotIn("net_1519_pct", r)
        self.assertIsNone(r["d1_pct"])
        self.assertAlmostEqual(r["net_pct"], -1.0 - m.COST, places=6)


class TestFetchIndexGap(unittest.TestCase):
    """당일 시가 갭 파싱 — 네이버 지수 일별 price 응답 형태(2026-09-09 실측 payload)."""

    ROWS = [{"localTradedAt": "2026-09-09", "closePrice": "830.37", "compareToPreviousClosePrice": "18.49",
             "openPrice": "815.06", "highPrice": "830.87", "lowPrice": "814.79"},
            {"localTradedAt": "2026-09-08", "closePrice": "811.88", "compareToPreviousClosePrice": "-10.31",
             "openPrice": "825.03", "highPrice": "830.21", "lowPrice": "811.88"}]

    def _with(self, payload, today):
        orig = m._get_json
        m._get_json = lambda url, timeout=10.0: payload
        try:
            return m.fetch_index_gap(today)
        finally:
            m._get_json = orig

    def test_gap_from_prev_row_close(self):
        g = self._with(self.ROWS, "2026-09-09")
        self.assertEqual(g["gap_src"], "naver_price")
        self.assertAlmostEqual(g["gap"], (815.06 / 811.88 - 1) * 100, places=3)

    def test_gap_falls_back_to_compare_field(self):
        g = self._with(self.ROWS[:1], "2026-09-09")   # 이전 행 없음 → 종가−등락폭
        self.assertAlmostEqual(g["prev_close"], 830.37 - 18.49, places=6)
        self.assertEqual(g["gap_src"], "naver_price")

    def test_missing_today_row_is_not_a_signal_source(self):
        g = self._with(self.ROWS, "2026-09-10")
        self.assertIsNone(g["gap"]); self.assertEqual(g["gap_src"], "no_today_row")
        self.assertEqual(m.decide(-1.0, False, "OPEN", gap_pct=g["gap"]), (False, "above_thr"))

    def test_bad_payload(self):
        self.assertEqual(self._with({"error": 1}, "2026-09-09")["gap_src"], "bad_payload")
        self.assertIsNone(self._with([{"localTradedAt": "2026-09-09"}], "2026-09-09")["gap"])


if __name__ == "__main__":
    unittest.main()
