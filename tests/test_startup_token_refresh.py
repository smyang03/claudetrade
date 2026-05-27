from __future__ import annotations

import unittest
from unittest.mock import patch

from kis_api import KISTokenExpiredError, KISTokenRateLimitError
from trading_bot import TradingBot


class StartupTokenRefreshTests(unittest.TestCase):
    def test_startup_balance_refreshes_bot_token_on_kis_expiry(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.token = "expired_token"
        bot.tokens = {"KR": "expired_token"}

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
        token_mock.assert_called_once_with(force_refresh=True, market="KR")
        self.assertEqual(get_balance_mock.call_count, 2)
        self.assertEqual(get_balance_mock.call_args_list[0].kwargs["market"], "KR")
        self.assertEqual(get_balance_mock.call_args_list[1].kwargs["market"], "KR")
        self.assertTrue(get_balance_mock.call_args_list[1].kwargs["force_refresh"])

    def test_startup_token_helper_retries_with_backoff(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.tokens = {}

        with patch.dict(
            "os.environ",
            {"STARTUP_TOKEN_ATTEMPTS": "2", "STARTUP_TOKEN_BACKOFF_SEC": "0.25"},
            clear=False,
        ), patch(
            "trading_bot.get_access_token",
            side_effect=[RuntimeError("network"), "fresh_token"],
        ) as token_mock, patch("trading_bot.time.sleep") as sleep_mock:
            token = bot._get_startup_token_with_backoff()

        self.assertEqual(token, "fresh_token")
        self.assertEqual(token_mock.call_count, 2)
        self.assertEqual(token_mock.call_args_list[0].kwargs["market"], "KR")
        self.assertEqual(token_mock.call_args_list[1].kwargs["market"], "KR")
        sleep_mock.assert_called_once_with(0.25)

    def test_startup_token_helper_stops_on_kis_rate_limit(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.tokens = {}

        with patch.dict(
            "os.environ",
            {"STARTUP_TOKEN_ATTEMPTS": "3", "STARTUP_TOKEN_BACKOFF_SEC": "0.25"},
            clear=False,
        ), patch(
            "trading_bot.get_access_token",
            side_effect=KISTokenRateLimitError(
                "rate limited",
                market="KR",
                retry_after_sec=60,
                cooldown_until="2099-01-01T00:00:00",
            ),
        ) as token_mock, patch("trading_bot.time.sleep") as sleep_mock:
            with self.assertRaises(KISTokenRateLimitError):
                bot._get_startup_token_with_backoff()

        token_mock.assert_called_once_with(market="KR")
        sleep_mock.assert_not_called()

    def test_disabled_market_balance_lookup_returns_empty_without_kis_call(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.enabled_markets = {"US"}
        bot.token = "token"
        bot.tokens = {"US": "token"}

        with patch("trading_bot.get_balance") as get_balance_mock:
            balance = bot._get_balance_with_token_refresh("KR")

        self.assertEqual(balance, {"cash": 0, "total_eval": 0, "stocks": []})
        get_balance_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
