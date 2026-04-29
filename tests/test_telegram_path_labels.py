from __future__ import annotations

import unittest
from unittest.mock import patch

from telegram_reporter import buy_order_alert, fill_confirm_alert, pnl_alert, trade_alert


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


if __name__ == "__main__":
    unittest.main()
