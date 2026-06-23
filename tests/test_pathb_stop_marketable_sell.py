"""US 손절 매도 marketable 지정가(②) + 미체결 매도 shadow 게이트 복구(①) 테스트.

② `_pathb_stop_marketable_sell_price`: US 손절성 close_reason만 트리거 아래로 호가, 토글/시장/익절 분기.
① `_log_unfilled_sell_shadow`: pathb_closing 의존 제거 → pending 지정가 있으면 캡처.
"""

import json
import os
import unittest
from unittest import mock

from execution.claude_price_sell_manager import ExitSignal
from runtime.pathb_runtime import PathBRuntime
import runtime.pathb_runtime as pathb_mod


def _sig(close_reason, price=100.0):
    return ExitSignal(True, close_reason.lower(), close_reason, price, "run-x")


class StopMarketableSellPriceTests(unittest.TestCase):
    def setUp(self):
        self.rt = PathBRuntime.__new__(PathBRuntime)

    def test_us_loss_cap_prices_below_trigger_when_enabled(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true",
                                          "US_PATHB_STOP_MARKETABLE_LIMIT_PCT": "0.5"}):
            px = self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_LOSS_CAP", 100.0))
        self.assertAlmostEqual(px, 99.5)  # 100 * (1 - 0.005)

    def test_us_hard_stop_and_claude_price_stop_included(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true",
                                          "US_PATHB_STOP_MARKETABLE_LIMIT_PCT": "1.0"}):
            self.assertAlmostEqual(
                self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_HARD_STOP", 200.0)), 198.0)
            self.assertAlmostEqual(
                self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_CLAUDE_PRICE_STOP", 50.0)), 49.5)

    def test_profit_exits_never_adjusted(self):
        # 익절 경로(target/ladder/claude_sell)는 가격 양보 금지 — 원가 그대로.
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true"}):
            for cr in ("CLOSED_CLAUDE_PRICE_TARGET", "CLOSED_PROFIT_LADDER", "CLOSED_CLAUDE_SELL"):
                self.assertAlmostEqual(
                    self.rt._pathb_stop_marketable_sell_price("US", _sig(cr, 100.0)), 100.0)

    def test_kr_never_adjusted_uses_market_order(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true"}):
            self.assertAlmostEqual(
                self.rt._pathb_stop_marketable_sell_price("KR", _sig("CLOSED_LOSS_CAP", 100.0)), 100.0)

    def test_disabled_toggle_returns_raw(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "false"}):
            self.assertAlmostEqual(
                self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_LOSS_CAP", 100.0)), 100.0)

    def test_pct_bounded_to_1_5(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true",
                                          "US_PATHB_STOP_MARKETABLE_LIMIT_PCT": "9.9"}):
            px = self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_LOSS_CAP", 100.0))
        self.assertAlmostEqual(px, 98.5)  # 1.5% 상한

    def test_invalid_price_passthrough(self):
        with mock.patch.dict(os.environ, {"US_PATHB_STOP_MARKETABLE_LIMIT_ENABLED": "true"}):
            self.assertEqual(self.rt._pathb_stop_marketable_sell_price("US", _sig("CLOSED_LOSS_CAP", 0.0)), 0.0)


class UnfilledSellShadowGateTests(unittest.TestCase):
    def setUp(self):
        self.rt = PathBRuntime.__new__(PathBRuntime)
        self.rt._current_native_price_for_exit = lambda *a, **k: 95.0
        self.rt._session_date = lambda *a, **k: "20260623"

    def _plan(self):
        return mock.Mock(ticker="AVGO", path_run_id="run-x")

    def test_logs_with_pending_sell_price_even_without_pathb_closing(self):
        # ① 핵심: pathb_closing이 없어도(클리어돼도) pending 지정가가 있으면 캡처돼야 한다.
        pos = {
            "ticker": "AVGO",
            "pathb_pending_sell_price": 100.0,   # 미체결 지정가
            "pathb_pending_sell_order_no": "0030275009",
            "pathb_pending_close_reason": "CLOSED_LOSS_CAP",
            "display_avg_price": 98.0,
            # pathb_closing 없음
        }
        written = []
        with mock.patch.object(pathb_mod, "get_runtime_path") as gp:
            import tempfile
            tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".jsonl")
            tmp.close()
            gp.return_value = tmp.name
            with mock.patch.dict(os.environ, {"PATHB_UNFILLED_SELL_SHADOW_ENABLED": "true"}):
                self.rt._log_unfilled_sell_shadow(pos, "US", self._plan())
            with open(tmp.name, encoding="utf-8") as f:
                written = [json.loads(line) for line in f if line.strip()]
            os.unlink(tmp.name)
        self.assertEqual(len(written), 1)
        rec = written[0]
        self.assertEqual(rec["order_price"], 100.0)
        self.assertEqual(rec["current"], 95.0)
        self.assertEqual(rec["gap_pct"], -5.0)  # 현재가가 지정가보다 5% 아래 = 미체결 페이드

    def test_no_log_without_pending_sell_price(self):
        pos = {"ticker": "AVGO", "pathb_closing": "2026-06-22T23:29:00+09:00"}  # pending price 없음
        with mock.patch.object(pathb_mod, "get_runtime_path") as gp:
            gp.side_effect = AssertionError("should not write file")
            with mock.patch.dict(os.environ, {"PATHB_UNFILLED_SELL_SHADOW_ENABLED": "true"}):
                self.rt._log_unfilled_sell_shadow(pos, "US", self._plan())  # 조용히 early-return


if __name__ == "__main__":
    unittest.main()
