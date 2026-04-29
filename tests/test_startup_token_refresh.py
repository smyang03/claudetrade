from __future__ import annotations

import unittest
from unittest.mock import patch

from kis_api import KISTokenExpiredError
from trading_bot import TradingBot


class StartupTokenRefreshTests(unittest.TestCase):
    def test_startup_balance_refreshes_bot_token_on_kis_expiry(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.token = "expired_token"

        with patch(
            "trading_bot.get_balance",
            side_effect=[
                KISTokenExpiredError("expired"),
                {"cash": 100_000, "total_eval": 0, "stocks": []},
            ],
        ) as get_balance_mock, patch(
            "trading_bot.get_access_token",
            return_value="fresh_token",
        ) as token_mock:
            balance = bot._get_balance_with_token_refresh("KR")

        self.assertEqual(balance["cash"], 100_000)
        self.assertEqual(bot.token, "fresh_token")
        token_mock.assert_called_once_with(force_refresh=True)
        self.assertEqual(get_balance_mock.call_count, 2)
        self.assertEqual(get_balance_mock.call_args_list[0].kwargs["market"], "KR")
        self.assertEqual(get_balance_mock.call_args_list[1].kwargs["market"], "KR")
        self.assertTrue(get_balance_mock.call_args_list[1].kwargs["force_refresh"])


if __name__ == "__main__":
    unittest.main()
