from __future__ import annotations

import types
import unittest
from unittest.mock import patch

import trading_bot


class FakeSocket:
    instances: list["FakeSocket"] = []
    fail_start_for: set[str] = set()

    def __init__(self, token, tickers, on_tick=None, on_notice=None, market="KR"):
        self.token = token
        self.tickers = list(tickers or [])
        self.on_tick = on_tick
        self.on_notice = on_notice
        self.market = "US" if str(market or "").upper() == "US" else "KR"
        self.started = False
        self.stopped = False
        FakeSocket.instances.append(self)

    def start(self):
        if self.market in FakeSocket.fail_start_for:
            raise RuntimeError(f"start failed {self.market}")
        self.started = True

    def stop(self):
        self.stopped = True


def _bot_for_ws_tests():
    bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
    bot.tokens = {"KR": "kr-token", "US": "us-token"}
    bot.token = "kr-token"
    bot.ws_by_market = {"KR": None, "US": None}
    bot._on_tick = lambda payload: None
    bot._on_fill_notice = lambda event: None
    return bot


class TradingBotWebSocketLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeSocket.instances = []
        FakeSocket.fail_start_for = set()

    def test_start_kr_creates_only_kr_socket(self) -> None:
        bot = _bot_for_ws_tests()

        with patch.object(trading_bot, "KISWebSocket", FakeSocket):
            ws = bot._start_ws_for_market("KR", ["005930"])

        self.assertIs(ws, bot.ws_by_market["KR"])
        self.assertIsNone(bot.ws_by_market["US"])
        self.assertEqual(ws.market, "KR")
        self.assertEqual(ws.token, "kr-token")
        self.assertEqual(ws.tickers, ["005930"])
        self.assertTrue(ws.started)

    def test_start_us_after_kr_preserves_kr_socket(self) -> None:
        bot = _bot_for_ws_tests()

        # 별도 US 자격증명(US 전용 키)이면 US WS는 자기 토큰을 사용한다.
        with patch.object(trading_bot, "KISWebSocket", FakeSocket), patch.object(
            trading_bot, "get_kis_market_profile",
            return_value=types.SimpleNamespace(shared_with_kr=False),
        ):
            kr_ws = bot._start_ws_for_market("KR", ["005930"])
            us_ws = bot._start_ws_for_market("US", ["AAPL"])

        self.assertIs(bot.ws_by_market["KR"], kr_ws)
        self.assertIs(bot.ws_by_market["US"], us_ws)
        self.assertFalse(kr_ws.stopped)
        self.assertEqual(us_ws.token, "us-token")

    def test_start_us_uses_shared_kr_token_when_credentials_shared(self) -> None:
        bot = _bot_for_ws_tests()

        # 공유 자격증명(US 키 미설정 → KR fallback)이면 US WS는 KR 공유 토큰으로 일원화된다.
        # KIS 토큰 발급 1분 1회 제한(EGW00133) 충돌 방지.
        with patch.object(trading_bot, "KISWebSocket", FakeSocket), patch.object(
            trading_bot, "get_kis_market_profile",
            return_value=types.SimpleNamespace(shared_with_kr=True),
        ):
            bot._start_ws_for_market("KR", ["005930"])
            us_ws = bot._start_ws_for_market("US", ["AAPL"])

        self.assertEqual(us_ws.token, "kr-token")
        self.assertEqual(bot.tokens["US"], "kr-token")

    def test_same_market_restart_stops_and_replaces_old_socket(self) -> None:
        bot = _bot_for_ws_tests()

        with patch.object(trading_bot, "KISWebSocket", FakeSocket):
            old_ws = bot._start_ws_for_market("KR", ["005930"])
            new_ws = bot._start_ws_for_market("KR", ["000660"])

        self.assertTrue(old_ws.stopped)
        self.assertIs(bot.ws_by_market["KR"], new_ws)
        self.assertEqual(new_ws.tickers, ["000660"])

    def test_stop_one_market_leaves_other_market_running(self) -> None:
        bot = _bot_for_ws_tests()

        with patch.object(trading_bot, "KISWebSocket", FakeSocket):
            kr_ws = bot._start_ws_for_market("KR", ["005930"])
            us_ws = bot._start_ws_for_market("US", ["AAPL"])
            bot._stop_ws_for_market("KR")

        self.assertTrue(kr_ws.stopped)
        self.assertIsNone(bot.ws_by_market["KR"])
        self.assertIs(bot.ws_by_market["US"], us_ws)
        self.assertFalse(us_ws.stopped)

    def test_stop_all_ws_stops_both_markets(self) -> None:
        bot = _bot_for_ws_tests()

        with patch.object(trading_bot, "KISWebSocket", FakeSocket):
            kr_ws = bot._start_ws_for_market("KR", ["005930"])
            us_ws = bot._start_ws_for_market("US", ["AAPL"])
            bot._stop_all_ws()

        self.assertTrue(kr_ws.stopped)
        self.assertTrue(us_ws.stopped)
        self.assertIsNone(bot.ws_by_market["KR"])
        self.assertIsNone(bot.ws_by_market["US"])

    def test_start_failure_clears_slot_and_stops_failed_socket(self) -> None:
        bot = _bot_for_ws_tests()
        FakeSocket.fail_start_for = {"US"}

        with patch.object(trading_bot, "KISWebSocket", FakeSocket):
            with self.assertRaises(RuntimeError):
                bot._start_ws_for_market("US", ["AAPL"])

        failed_ws = FakeSocket.instances[-1]
        self.assertTrue(failed_ws.stopped)
        self.assertIsNone(bot.ws_by_market["US"])


if __name__ == "__main__":
    unittest.main()
