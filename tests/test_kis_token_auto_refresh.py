from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
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


class _HTTPErrorResp(_Resp):
    def __init__(self, status_code: int, body: dict, *, headers: dict | None = None):
        super().__init__(status_code, body)
        self.headers = headers or {}
        self.text = json.dumps(body, ensure_ascii=False)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            exc = kis_api.requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            exc.response = self
            raise exc


class KisTokenAutoRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        kis_api._TOKEN_ALIAS.clear()
        kis_api._TOKEN_MARKET.clear()

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
        token_mock.assert_called_once_with(force_refresh=True, market="KR")

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

    def test_force_refresh_failure_preserves_existing_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "live_kis_token.json"
            original = {
                "access_token": "old_token",
                "expires_at": "2099-01-01T00:00:00",
                "issued_at": "2099-01-01T00:00:00",
                "context": {"base_url": "https://example.invalid"},
            }
            token_path.write_text(json.dumps(original), encoding="utf-8")
            profile = kis_api.KISMarketProfile(
                market="KR",
                account_no="12345678-01",
                app_key="app-key",
                app_secret="app-secret",
                is_paper=False,
                base_url="https://example.invalid",
                ws_url="",
                token_file=str(token_path),
                credential_mode="primary",
                shared_with_kr=True,
            )

            with patch("kis_api.get_kis_market_profile", return_value=profile), patch(
                "kis_api._token_file_for_market",
                return_value=token_path,
            ), patch(
                "kis_api._kis_post",
                side_effect=kis_api.requests.exceptions.ConnectionError("blocked"),
            ):
                with self.assertRaises(RuntimeError):
                    kis_api.get_access_token(force_refresh=True, market="KR")

            self.assertTrue(token_path.exists())
            self.assertEqual(json.loads(token_path.read_text(encoding="utf-8")), original)

    def test_token_rate_limit_records_cooldown_and_preserves_existing_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "live_kis_token.json"
            marker_path = Path(tmp) / "live_kis_token_rate_limit_kr_test.json"
            original = {
                "access_token": "old_token",
                "expires_at": "2099-01-01T00:00:00",
                "issued_at": "2099-01-01T00:00:00",
                "context": {"base_url": "https://example.invalid"},
            }
            token_path.write_text(json.dumps(original), encoding="utf-8")
            profile = kis_api.KISMarketProfile(
                market="KR",
                account_no="12345678-01",
                app_key="app-key",
                app_secret="app-secret",
                is_paper=False,
                base_url="https://example.invalid",
                ws_url="",
                token_file=str(token_path),
                credential_mode="primary",
                shared_with_kr=True,
            )
            resp = _HTTPErrorResp(
                403,
                {"rt_cd": "1", "msg_cd": "EGW00133", "msg1": "token issue rate exceeded"},
                headers={"Retry-After": "42"},
            )

            with patch("kis_api.get_kis_market_profile", return_value=profile), patch(
                "kis_api._token_file_for_market",
                return_value=token_path,
            ), patch(
                "kis_api._token_rate_limit_path",
                return_value=marker_path,
            ), patch(
                "kis_api._kis_post",
                return_value=resp,
            ):
                with self.assertRaises(kis_api.KISTokenRateLimitError) as caught:
                    kis_api.get_access_token(force_refresh=True, market="KR")

            self.assertEqual(caught.exception.retry_after_sec, 42)
            self.assertTrue(marker_path.exists())
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
            self.assertEqual(marker["payload"]["msg_cd"], "EGW00133")
            self.assertEqual(json.loads(token_path.read_text(encoding="utf-8")), original)

    def test_active_token_rate_limit_cooldown_blocks_token_issue_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "live_kis_token.json"
            marker_path = Path(tmp) / "live_kis_token_rate_limit_kr_test.json"
            profile = kis_api.KISMarketProfile(
                market="KR",
                account_no="12345678-01",
                app_key="app-key",
                app_secret="app-secret",
                is_paper=False,
                base_url="https://example.invalid",
                ws_url="",
                token_file=str(token_path),
                credential_mode="primary",
                shared_with_kr=True,
            )

            with patch("kis_api.get_kis_market_profile", return_value=profile), patch(
                "kis_api._token_file_for_market",
                return_value=token_path,
            ), patch(
                "kis_api._token_rate_limit_path",
                return_value=marker_path,
            ):
                marker_path.write_text(
                    json.dumps(
                        {
                            "cooldown_until": "2099-01-01T00:00:00",
                            "cooldown_until_ts": time.time() + 120,
                            "context": kis_api._token_cache_context("KR"),
                            "payload": {"msg_cd": "EGW00133", "msg1": "cooldown"},
                            "status_code": 403,
                            "response_text": "EGW00133",
                        }
                    ),
                    encoding="utf-8",
                )
                with patch("kis_api._kis_post") as post_mock:
                    with self.assertRaises(kis_api.KISTokenRateLimitError):
                        kis_api.get_access_token(force_refresh=True, market="KR")

                post_mock.assert_not_called()

    def test_shared_kr_us_credentials_use_same_token_rate_limit_marker(self) -> None:
        kr_profile = kis_api.KISMarketProfile(
            market="KR",
            account_no="12345678-01",
            app_key="shared-app-key",
            app_secret="shared-app-secret",
            is_paper=False,
            base_url="https://example.invalid",
            ws_url="",
            token_file="",
            credential_mode="primary",
            shared_with_kr=True,
        )
        us_profile = kis_api.KISMarketProfile(
            market="US",
            account_no="12345678-01",
            app_key="shared-app-key",
            app_secret="shared-app-secret",
            is_paper=False,
            base_url="https://example.invalid",
            ws_url="",
            token_file="",
            credential_mode="fallback_shared_kr",
            shared_with_kr=True,
        )

        self.assertEqual(
            kis_api._token_rate_limit_path(kr_profile).name,
            kis_api._token_rate_limit_path(us_profile).name,
        )

    def test_valid_cached_token_is_used_during_active_rate_limit_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            token_path = Path(tmp) / "live_kis_token.json"
            marker_path = Path(tmp) / "live_kis_token_rate_limit_kr_test.json"
            profile = kis_api.KISMarketProfile(
                market="KR",
                account_no="12345678-01",
                app_key="app-key",
                app_secret="app-secret",
                is_paper=False,
                base_url="https://example.invalid",
                ws_url="",
                token_file=str(token_path),
                credential_mode="primary",
                shared_with_kr=True,
            )

            with patch("kis_api.get_kis_market_profile", return_value=profile), patch(
                "kis_api._token_file_for_market",
                return_value=token_path,
            ), patch(
                "kis_api._token_rate_limit_path",
                return_value=marker_path,
            ):
                token_path.write_text(
                    json.dumps(
                        {
                            "access_token": "cached_token",
                            "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
                            "issued_at": datetime.now().isoformat(),
                            "context": kis_api._token_cache_context("KR"),
                        }
                    ),
                    encoding="utf-8",
                )
                marker_path.write_text(
                    json.dumps(
                        {
                            "cooldown_until": "2099-01-01T00:00:00",
                            "cooldown_until_ts": time.time() + 120,
                            "context": kis_api._token_cache_context("KR"),
                            "payload": {"msg_cd": "EGW00133", "msg1": "cooldown"},
                        }
                    ),
                    encoding="utf-8",
                )
                with patch("kis_api._kis_post") as post_mock:
                    token = kis_api.get_access_token(force_refresh=False, market="KR")

                self.assertEqual(token, "cached_token")
                post_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
