from __future__ import annotations

import unittest
from unittest.mock import patch

import kis_api


class _Resp:
    def __init__(self, status_code: int, body: dict):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class KisMarketProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        kis_api._TOKEN_MARKET.clear()
        kis_api._TOKEN_ALIAS.clear()

    def test_us_profile_falls_back_to_kr_credentials_when_us_env_empty(self) -> None:
        with patch.multiple(
            kis_api,
            ACCOUNT_NO="12345678-01",
            ACCOUNT_NO_US="12345678-01",
            APP_KEY="kr-key",
            APP_SECRET="kr-secret",
            APP_KEY_US="kr-key",
            APP_SECRET_US="kr-secret",
            IS_PAPER=True,
            IS_PAPER_US=True,
            BASE_URL="https://kr.example",
            BASE_URL_US="https://kr.example",
        ):
            profile = kis_api.get_kis_market_profile("US")
            headers = kis_api._headers("token", "TRID", market="US")

        self.assertEqual(profile.credential_mode, "fallback_shared_kr")
        self.assertTrue(profile.shared_with_kr)
        self.assertEqual(headers["appkey"], "kr-key")
        self.assertEqual(headers["appsecret"], "kr-secret")

    def test_us_profile_uses_us_credentials_when_present(self) -> None:
        with patch.multiple(
            kis_api,
            ACCOUNT_NO="12345678-01",
            ACCOUNT_NO_US="87654321-01",
            APP_KEY="kr-key",
            APP_SECRET="kr-secret",
            APP_KEY_US="us-key",
            APP_SECRET_US="us-secret",
            IS_PAPER=True,
            IS_PAPER_US=False,
            BASE_URL="https://kr.example",
            BASE_URL_US="https://us.example",
        ):
            profile = kis_api.get_kis_market_profile("US")
            headers = kis_api._headers("token-us", "TRID", market="US")
            token_file = kis_api._token_file_for_market("US")

        self.assertEqual(profile.credential_mode, "separate_us")
        self.assertFalse(profile.shared_with_kr)
        self.assertEqual(profile.account_no, "87654321-01")
        self.assertEqual(headers["appkey"], "us-key")
        self.assertEqual(headers["appsecret"], "us-secret")
        self.assertTrue(str(token_file).endswith("live_kis_token_us.json"))

    def test_expired_us_header_refreshes_us_token(self) -> None:
        calls: list[str] = []

        def fake_get(_url, *, headers, timeout, **_kwargs):
            calls.append(headers["authorization"])
            if len(calls) == 1:
                return _Resp(500, {"msg_cd": "EGW00123"})
            return _Resp(200, {"rt_cd": "0"})

        with patch.multiple(
            kis_api,
            ACCOUNT_NO="12345678-01",
            ACCOUNT_NO_US="87654321-01",
            APP_KEY="kr-key",
            APP_SECRET="kr-secret",
            APP_KEY_US="us-key",
            APP_SECRET_US="us-secret",
            IS_PAPER=True,
            IS_PAPER_US=False,
            BASE_URL="https://kr.example",
            BASE_URL_US="https://us.example",
        ):
            headers = kis_api._headers("expired-us", "TRID", market="US")
            with patch("kis_api.requests.get", side_effect=fake_get), patch(
                "kis_api.get_access_token",
                return_value="fresh-us",
            ) as token_mock:
                resp = kis_api._kis_get("https://example.invalid/uapi/test", headers=headers, timeout=1)

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, ["Bearer expired-us", "Bearer fresh-us"])
        token_mock.assert_called_once_with(force_refresh=True, market="US")


if __name__ == "__main__":
    unittest.main()
