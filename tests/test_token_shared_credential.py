"""KR/US 공유 KIS 자격증명 토큰 일원화 테스트.

US 키 미설정(KR fallback) 시 KIS 토큰 발급은 같은 app_key에 1분 1회로 제한(EGW00133)된다.
US 토큰 요청을 KR 토큰으로 일원화해 자정/장초반 rate limit 충돌을 막는다.
"""

import types
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class SharedTokenTests(unittest.TestCase):
    def _bot(self):
        bot = TradingBot.__new__(TradingBot)
        bot.tokens = {}
        bot.token = ""
        bot.enabled_markets = {"KR", "US"}
        bot._market_enabled = lambda m: True
        return bot

    @patch("trading_bot.get_kis_market_profile")
    @patch("trading_bot.get_access_token")
    def test_us_reuses_kr_token_when_shared_no_extra_issue(self, mock_token, mock_profile):
        # 공유 자격증명: US 토큰 요청은 KR 토큰을 재사용해야 한다(별도 발급 금지).
        mock_profile.return_value = types.SimpleNamespace(shared_with_kr=True)
        mock_token.return_value = "TOK_KR"
        bot = self._bot()
        kr = bot._token_for_market("KR", force_refresh=True)   # 발급 1회
        us = bot._token_for_market("US", force_refresh=False)  # KR 재사용
        self.assertEqual(kr, "TOK_KR")
        self.assertEqual(us, "TOK_KR")
        self.assertEqual(bot.tokens["US"], "TOK_KR")
        self.assertEqual(mock_token.call_count, 1)

    @patch("trading_bot.get_kis_market_profile")
    @patch("trading_bot.get_access_token")
    def test_us_issues_separately_when_not_shared(self, mock_token, mock_profile):
        # 별도 자격증명(US 전용 키): 각 시장이 따로 발급한다(기존 동작 유지).
        mock_profile.return_value = types.SimpleNamespace(shared_with_kr=False)
        mock_token.side_effect = ["TOK_KR", "TOK_US"]
        bot = self._bot()
        bot._token_for_market("KR", force_refresh=True)
        us = bot._token_for_market("US", force_refresh=True)
        self.assertEqual(us, "TOK_US")
        self.assertEqual(mock_token.call_count, 2)

    @patch("trading_bot.get_kis_market_profile")
    @patch("trading_bot.get_access_token")
    def test_midnight_pattern_issues_kr_once_when_shared(self, mock_token, mock_profile):
        # 자정 갱신 패턴 모사: KR force + US non-force(공유 재사용) → 발급 1회.
        mock_profile.return_value = types.SimpleNamespace(shared_with_kr=True)
        mock_token.return_value = "TOK_KR"
        bot = self._bot()
        shared_us = bool(getattr(mock_profile.return_value, "shared_with_kr", False))
        for mkt in sorted(bot.enabled_markets):
            force = not (shared_us and mkt == "US")
            bot._token_for_market(mkt, force_refresh=force)
        self.assertEqual(mock_token.call_count, 1)
        self.assertEqual(bot.tokens["KR"], "TOK_KR")
        self.assertEqual(bot.tokens["US"], "TOK_KR")


if __name__ == "__main__":
    unittest.main()
