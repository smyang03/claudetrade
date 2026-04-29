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

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class KisTokenAutoRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        kis_api._TOKEN_ALIAS.clear()

    def test_kis_get_refreshes_once_on_expired_token_and_retries_request(self) -> None:
        calls: list[str] = []

        def fake_get(_url, *, headers, timeout, **_kwargs):
            calls.append(headers["authorization"])
            if len(calls) == 1:
                return _Resp(500, {"msg_cd": "EGW00123", "msg1": "expired"})
            return _Resp(200, {"rt_cd": "0"})

        with patch("kis_api.requests.get", side_effect=fake_get), patch(
            "kis_api.get_access_token",
            return_value="fresh_token",
        ) as token_mock:
            resp = kis_api._kis_get(
                "https://example.invalid/uapi/test",
                headers={"authorization": "Bearer expired_token"},
                timeout=1,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, ["Bearer expired_token", "Bearer fresh_token"])
        token_mock.assert_called_once_with(force_refresh=True)

    def test_known_expired_token_is_aliased_without_refetching_token(self) -> None:
        kis_api._TOKEN_ALIAS["expired_token"] = "fresh_token"
        calls: list[str] = []

        def fake_get(_url, *, headers, timeout, **_kwargs):
            calls.append(headers["authorization"])
            return _Resp(200, {"rt_cd": "0"})

        with patch("kis_api.requests.get", side_effect=fake_get), patch("kis_api.get_access_token") as token_mock:
            resp = kis_api._kis_get(
                "https://example.invalid/uapi/test",
                headers={"authorization": "Bearer expired_token"},
                timeout=1,
            )

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(calls, ["Bearer fresh_token"])
        token_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
