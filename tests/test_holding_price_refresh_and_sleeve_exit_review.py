"""보유 종목 청산 판정 입력 회귀 테스트 (2026-08-05 실측 사고).

사고 요약 — FRMI(us_swing_5d, TP12 계약)가 목표가를 넘겼는데 청산되지 않았다.
  1) 보유 종목이 스캔 목록에서 빠지면 price_cache가 session_open 값에 고정되고,
     RiskManager.update_prices가 그 종목을 건너뛰어(risk_manager.py:714)
     TP/SL 판정이 옛 가격으로 이뤄진다. FRMI는 $6.09에 멈춘 채 장중 실제
     고가 $6.35(TP선 $6.1824 초과)를 찍었으나 청산 후보가 생성되지 않았다.
  2) 가격을 고쳐도 CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true 아래에서는
     sleeve 계약 청산까지 Claude 리뷰 대상이 되어 목표가 초과분이 보류될 수 있었다.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class _Risk:
    def __init__(self, positions):
        self.positions = positions
        self.updated_with = None

    def update_prices(self, prices, raw_prices=None):
        self.updated_with = (dict(prices), dict(raw_prices or {}))
        for pos in self.positions:
            if pos["ticker"] not in prices:
                continue
            pos["current_price"] = prices[pos["ticker"]]
            if raw_prices and pos["ticker"] in raw_prices:
                pos["display_current_price"] = float(raw_prices[pos["ticker"]])


class _Bot:
    _refresh_holding_prices_for_exit = TradingBot._refresh_holding_prices_for_exit
    _ws_tick_silence_sec = TradingBot._ws_tick_silence_sec

    def __init__(self, positions):
        self.risk = _Risk(positions)
        self.price_cache = {}
        self.price_cache_raw = {}

    def _ticker_market(self, ticker):
        return "US" if str(ticker).isalpha() else "KR"

    def _token_for_market(self, _market):
        return "token"

    def _price_to_krw(self, raw, _market):
        return float(raw) * 1428.0


class HoldingPriceRefreshTests(unittest.TestCase):
    def test_holding_not_in_scan_list_still_gets_fresh_price(self) -> None:
        # 사고 재현: FRMI는 스캔 목록에 없어 price_cache가 비어 있는 상태.
        pos = {
            "ticker": "FRMI",
            "display_currency": "USD",
            "display_avg_price": 5.52,
            "display_current_price": 6.09,  # session_open 시점에 고정된 값
            "current_price": 8694.0,
        }
        bot = _Bot([pos])
        with patch("trading_bot.get_price", return_value={"price": 6.22}):
            result = bot._refresh_holding_prices_for_exit("US")
        self.assertEqual(result["updated"], ["FRMI"])
        self.assertEqual(bot.price_cache_raw["FRMI"], 6.22)
        # 리스크 엔진까지 반영되어야 TP 판정이 최신가로 이뤄진다.
        self.assertAlmostEqual(pos["display_current_price"], 6.22)
        self.assertGreaterEqual(pos["display_current_price"], 5.52 * 1.12)

    def test_other_market_holdings_are_untouched(self) -> None:
        kr = {"ticker": "275280", "display_currency": "KRW", "current_price": 39275.0}
        us = {"ticker": "FRMI", "display_currency": "USD", "display_current_price": 6.09}
        bot = _Bot([kr, us])
        with patch("trading_bot.get_price", return_value={"price": 6.22}):
            result = bot._refresh_holding_prices_for_exit("US")
        self.assertEqual(result["updated"], ["FRMI"])
        self.assertNotIn("275280", bot.price_cache)

    def test_quote_failure_does_not_corrupt_cache(self) -> None:
        pos = {"ticker": "FRMI", "display_currency": "USD", "display_current_price": 6.09}
        bot = _Bot([pos])
        with patch("trading_bot.get_price", side_effect=RuntimeError("quote down")):
            result = bot._refresh_holding_prices_for_exit("US")
        self.assertEqual(result["failed"], ["FRMI"])
        self.assertNotIn("FRMI", bot.price_cache)
        self.assertEqual(pos["display_current_price"], 6.09)

    def test_zero_price_is_rejected(self) -> None:
        pos = {"ticker": "FRMI", "display_currency": "USD", "display_current_price": 6.09}
        bot = _Bot([pos])
        with patch("trading_bot.get_price", return_value={"price": 0}):
            result = bot._refresh_holding_prices_for_exit("US")
        self.assertEqual(result["failed"], ["FRMI"])
        self.assertNotIn("FRMI", bot.price_cache)


class SleeveContractExitReviewTests(unittest.TestCase):
    def test_sleeve_contract_exits_skip_claude_review(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"}):
            self.assertFalse(TradingBot._auto_sell_review_required("strategy_fixed_take_profit"))
            self.assertFalse(TradingBot._auto_sell_review_required("strategy_catastrophe_stop"))

    def test_path_a_automated_sells_still_reviewed(self) -> None:
        # 이 플래그의 보호 대상(Path A 자동매도)은 그대로 리뷰를 거쳐야 한다.
        with patch.dict(os.environ, {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"}):
            for reason in ("loss_cap", "stop_loss", "trail_stop"):
                self.assertTrue(TradingBot._auto_sell_review_required(reason), reason)


if __name__ == "__main__":
    unittest.main()
