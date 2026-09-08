# -*- coding: utf-8 -*-
"""코어 ETF 북 쉐도우 — 순수 함수 테스트(12-1 모멘텀 창·목표 비중·정수주 사이징·주문 적용 세금/수수료)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import core_etf_book_shadow as m  # noqa: E402

T = ["A", "B"]


def _mends(n, a_series, b_series):
    return [(f"2025-{i + 1:02d}" if i < 12 else f"2026-{i - 11:02d}", {"A": a_series[i], "B": b_series[i]}) for i in range(n)]


class MomentumTest(unittest.TestCase):
    def test_needs_13_month_ends(self):
        me = _mends(12, [100 + i for i in range(12)], [100] * 12)
        self.assertEqual(m.absmom_signal(me, T), {"A": False, "B": False})

    def test_skips_last_month(self):
        # A: 12개월 상승 후 직전 달 급락 → 12-1은 직전 달을 건너뛰므로 여전히 True
        a = [100 + i * 2 for i in range(13)]; a[-1] = 50
        # B: 직전 달만 급등, 그 전 12개월 하락 → False
        b = [200 - i * 5 for i in range(13)]; b[-1] = 500
        me = _mends(13, a, b)
        self.assertEqual(m.absmom_signal(me, T), {"A": True, "B": False})

    def test_month_end_closes_excludes_decision_month(self):
        dates = ["2026-01-05", "2026-01-30", "2026-02-02", "2026-02-27", "2026-03-02"]
        closes = {d: {"A": float(i)} for i, d in enumerate(dates)}
        me = m.month_end_closes(dates, closes, "2026-03")
        self.assertEqual([x[0] for x in me], ["2026-01", "2026-02"])
        self.assertEqual(me[-1][1]["A"], 3.0)   # 2026-02-27 종가


class WeightsAndSizingTest(unittest.TestCase):
    def test_target_weights(self):
        self.assertEqual(m.target_weights("ew", {}, T), {"A": 0.5, "B": 0.5})
        self.assertEqual(m.target_weights("absmom", {"A": True, "B": False}, T), {"A": 1.0, "B": 0.0})
        self.assertEqual(m.target_weights("absmom", {"A": False, "B": False}, T), {"A": 0.0, "B": 0.0})

    def test_integer_sizing_error(self):
        tq, err = m.size_orders(1_000_000.0, {"A": 0.5, "B": 0.5}, {"A": 300_000.0, "B": 10_000.0}, {})
        self.assertEqual(tq, {"A": 1, "B": 50})
        # A는 50만 목표에 30만만 살 수 있어 20%p 오차
        self.assertAlmostEqual(err, 20.0, places=3)

    def test_apply_orders_tax_and_fee(self):
        holdings = {"A": {"qty": 10, "avg_cost": 100.0}}
        cash, h, orders = m.apply_orders(0.0, holdings, {"A": 0}, {"A": 150.0})
        self.assertEqual(h["A"]["qty"], 0)
        value = 1500.0; fee = value * m.FEE_SIDE; tax = 500.0 * m.TAX
        self.assertAlmostEqual(cash, value - fee - tax, places=6)
        self.assertEqual(orders[0]["realized"], 500)

    def test_apply_orders_tax_free_bench(self):
        holdings = {m.BENCH: {"qty": 1, "avg_cost": 100.0}}
        cash, h, orders = m.apply_orders(0.0, holdings, {m.BENCH: 0}, {m.BENCH: 200.0})
        self.assertEqual(orders[0]["tax"], 0.0)

    def test_buy_never_exceeds_cash(self):
        cash, h, orders = m.apply_orders(1000.0, {}, {"A": 100}, {"A": 300.0})
        self.assertEqual(h["A"]["qty"], 3)
        self.assertGreaterEqual(cash, 0.0)


if __name__ == "__main__":
    unittest.main()
