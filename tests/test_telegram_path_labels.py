from __future__ import annotations

import unittest
from unittest.mock import patch

from telegram_reporter import buy_order_alert, decision_event_alert, fill_confirm_alert, pnl_alert, trade_alert


class TelegramPathLabelTests(unittest.TestCase):
    def test_buy_and_fill_alerts_include_path_labels(self) -> None:
        with patch("telegram_reporter.send", return_value=True):
            b_order = buy_order_alert("KR", "005930", 1, order_no="1", buy_path="path_b")
            a_fill = fill_confirm_alert("KR", "005930", 1, order_no="1", price=70000, buy_path="path_a")

        self.assertIn("B플랜 | Claude 지정가", b_order)
        self.assertIn("A플랜 | Timing Adapter", a_fill)

    def test_sell_and_pnl_alerts_include_path_labels(self) -> None:
        with patch("telegram_reporter.send", return_value=True):
            b_sell = trade_alert("sell", "005930", 1, 71000, "claude_price", 0, 0, reason="target", buy_path="path_b")
            a_pnl = pnl_alert("005930", 1.2, 1200, "trail_stop", market="KR", buy_path="path_a")

        self.assertIn("B플랜 | Claude 지정가", b_sell)
        self.assertIn("A플랜 | Timing Adapter", a_pnl)

    def test_decision_event_alert_uses_action_and_native_price(self) -> None:
        event = {
            "action": "sell_filled",
            "market": "US",
            "ticker": "BE",
            "price_native": 6.85,
            "price_krw": 9247.5,
        }
        with patch("telegram_reporter.send", return_value=True):
            text = decision_event_alert(event)

        self.assertNotIn("<b>[-]</b>", text)
        self.assertIn("$6.8500", text)

    def test_decision_event_alert_includes_affordability_detail(self) -> None:
        event = {
            "action": "buy_skipped",
            "market": "KR",
            "ticker": "003670",
            "price_krw": 287_000,
            "reason": "qty_zero",
            "detail": "unaffordable_high_price: price_krw=287,000 budget_krw=110,000 shortfall_krw=177,000",
        }
        with patch("telegram_reporter.send", return_value=True):
            text = decision_event_alert(event)

        self.assertIn("unaffordable_high_price", text)
        self.assertIn("budget_krw=110,000", text)
        self.assertIn("shortfall_krw=177,000", text)

    def test_us_trade_alert_preserves_decimal_price(self) -> None:
        with patch("telegram_reporter.send", return_value=True):
            text = trade_alert("sell", "BE", 2, 6.85, "claude_price", 0, 0, reason="target", market="US")

        self.assertIn("$6.85", text)
        self.assertNotIn("$6.00", text)


if __name__ == "__main__":
    unittest.main()
