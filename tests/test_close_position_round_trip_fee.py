"""청산 손익은 **왕복 비용**을 뺀 net이다 (2026-08-23, Codex 리뷰 P2-7).

결함: `open_position`은 매수 수수료를 현금(self.cash)과 daily_pnl에서 빼지만 `entry`에는
원가만 담는다. 그런데 `close_position`의 `pnl`/`pnl_pct`는 **매도 수수료만** 뺐고, 그 값이
그대로 `pnl_pct_net` / `broker_realized_krw`로 인증됐다. US는 건당 약 0.25%p, KR은 약
0.015%p씩 net이 과대계상돼 30건 판정의 평균·PF를 움직인다.

같이 지키는 계약: cash / daily_pnl / total_fee는 **이중 차감되면 안 된다** —
매수 수수료는 진입 때 이미 반영됐다.
"""

from __future__ import annotations

import unittest

from risk_manager import FEE_RATES, RiskManager


def _manager(market: str) -> RiskManager:
    return RiskManager(init_cash=10_000_000.0, market=market)


class RoundTripFeeTests(unittest.TestCase):
    def _open(self, risk: RiskManager, ticker: str, price: float, qty: int) -> None:
        risk.open_position(
            ticker=ticker, price=price, qty=qty, strategy="micro_probe",
            tp_pct=0.12, sl_pct=0.25, max_hold=5,
        )

    def test_us_close_subtracts_both_sides(self) -> None:
        risk = _manager("US")
        self._open(risk, "MXL", 100_000.0, 10)
        closed = risk.close_position("MXL", 110_000.0, "strategy_fixed_take_profit")

        rate = FEE_RATES["US"]["buy"]
        cost_basis = 100_000.0 * 10
        expected = (110_000.0 - 100_000.0) * 10 - 110_000.0 * 10 * rate - cost_basis * rate
        self.assertAlmostEqual(closed["pnl"], expected, places=6)
        self.assertAlmostEqual(closed["pnl_pct"], expected / cost_basis * 100, places=9)
        # gross +10.00%에서 왕복 비용만큼 깎인다 — 매도측만 빼던 값보다 작아야 한다.
        sell_only = ((110_000.0 - 100_000.0) * 10 - 110_000.0 * 10 * rate) / cost_basis * 100
        self.assertLess(closed["pnl_pct"], sell_only)
        self.assertAlmostEqual(sell_only - closed["pnl_pct"], rate * 100, places=9)

    def test_fee_breakdown_is_reported(self) -> None:
        """분해값을 함께 남긴다 — 어느 건이 어떤 규약으로 계산됐는지 사후 확인용."""
        risk = _manager("US")
        self._open(risk, "MXL", 100_000.0, 10)
        closed = risk.close_position("MXL", 110_000.0, "strategy_fixed_take_profit")
        self.assertGreater(closed["buy_fee_krw"], 0)
        self.assertGreater(closed["sell_fee_krw"], 0)
        self.assertAlmostEqual(
            closed["fee_pct_round_trip"],
            (closed["buy_fee_krw"] + closed["sell_fee_krw"]) / (100_000.0 * 10) * 100,
            places=9,
        )

    def test_cash_and_daily_pnl_are_not_double_charged(self) -> None:
        risk = _manager("KR")
        start_cash = risk.cash
        self._open(risk, "031330", 10_000.0, 20)
        buy_fee = 10_000.0 * 20 * FEE_RATES["KR"]["buy"]
        risk.close_position("031330", 11_000.0, "strategy_horizon_exit")

        sell_fee = 11_000.0 * 20 * FEE_RATES["KR"]["sell"]
        gross = (11_000.0 - 10_000.0) * 20
        # 현금: 매수 수수료 1회 + 매도 수수료 1회만 빠진다.
        self.assertAlmostEqual(risk.cash, start_cash + gross - buy_fee - sell_fee, places=6)
        # daily_pnl: open에서 -buy_fee, close에서 +(gross - sell_fee) = 왕복 1회씩.
        self.assertAlmostEqual(risk.daily_pnl, gross - buy_fee - sell_fee, places=6)

    def test_partial_close_uses_same_rule(self) -> None:
        risk = _manager("US")
        self._open(risk, "MXL", 100_000.0, 10)
        closed = risk.close_position_qty("MXL", 110_000.0, 4, "strategy_fixed_take_profit")

        rate = FEE_RATES["US"]["buy"]
        cost_basis = 100_000.0 * 4
        expected = (110_000.0 - 100_000.0) * 4 - 110_000.0 * 4 * rate - cost_basis * rate
        self.assertAlmostEqual(closed["pnl"], expected, places=6)
        self.assertEqual(closed["remaining_qty"], 6)


if __name__ == "__main__":
    unittest.main()
