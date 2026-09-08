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
        self.assertEqual(m.decide(-2.5, False, "OPEN"), (True, "signal"))
        self.assertEqual(m.decide(-2.49, False, "OPEN"), (False, "above_thr"))
        self.assertEqual(m.decide(-5.0, False, ""), (True, "signal"))

    def test_stale_and_closed_never_signal(self):
        self.assertEqual(m.decide(-6.0, True, "OPEN"), (False, "stale_quote"))
        self.assertEqual(m.decide(-6.0, False, "CLOSE"), (False, "market_close"))
        self.assertEqual(m.decide(None, False, "OPEN"), (False, "ratio_missing"))

    def test_quote_age(self):
        now = datetime(2026, 9, 9, 15, 19, 0, tzinfo=m.KST)
        self.assertAlmostEqual(m.quote_age_sec("2026-09-09T15:18:30+09:00", now), 30.0)
        self.assertEqual(m.quote_age_sec(None, now), float("inf"))
        self.assertGreater(m.quote_age_sec("2026-09-08T18:59:00+09:00", now), m.STALE_SEC)


class DivergenceTest(unittest.TestCase):
    def test_classes(self):
        self.assertEqual(m.classify_divergence(True, -2.6), "agree_signal")
        self.assertEqual(m.classify_divergence(True, -2.0), "false_positive_1519")
        self.assertEqual(m.classify_divergence(False, -2.6), "missed_by_1519")
        self.assertEqual(m.classify_divergence(False, -1.0), "agree_none")
        self.assertEqual(m.classify_divergence(True, None), "close_unknown")


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


if __name__ == "__main__":
    unittest.main()
