from __future__ import annotations

from contextlib import contextmanager
import json
import unittest
from unittest.mock import patch

from dashboard import dashboard_server


class DashboardKisProfileTests(unittest.TestCase):
    def test_kis_runtime_sets_us_profile_fields(self) -> None:
        env = {
            "KIS_APP_KEY": "kr-key",
            "KIS_APP_SECRET": "kr-secret",
            "KIS_ACCOUNT_NO": "11111111-01",
            "KIS_IS_PAPER": "false",
            "KIS_ACCOUNT_NO_US": "22222222-01",
            "KIS_APP_KEY_US": "us-key",
            "KIS_APP_SECRET_US": "us-secret",
            "KIS_IS_PAPER_US": "false",
            "KIS_BASE_URL": "https://kr.example",
            "KIS_BASE_URL_US": "https://us.example",
            "KIS_WS_URL_US": "ws://us.example",
        }

        with patch.object(dashboard_server, "_runtime_env", return_value=env):
            with dashboard_server._kis_runtime("live"):
                profile = dashboard_server.get_kis_profile_summary()
                self.assertEqual(dashboard_server._kis_api_module.BASE_URL_US, "https://us.example")
                self.assertEqual(dashboard_server._kis_api_module.WS_URL_US, "ws://us.example")
                self.assertEqual(profile["US"]["credential_mode"], "separate_us")
                self.assertFalse(profile["US"]["shared_with_kr"])

    def test_broker_snapshot_uses_market_tokens_and_exposes_profile(self) -> None:
        token_calls: list[str] = []

        @contextmanager
        def fake_runtime(_mode: str):
            yield

        def fake_token(*, market: str = "KR") -> str:
            token_calls.append(market)
            return f"token-{market}"

        def fake_balance(token: str, *, market: str, force_refresh: bool = False) -> dict:
            self.assertEqual(token, f"token-{market}")
            if market == "US":
                return {"cash": 2.0, "total_eval": 3.0, "stocks": [], "currency": "USD"}
            return {"cash": 1000.0, "total_eval": 2000.0, "stocks": [], "currency": "KRW"}

        profile = {
            "KR": {"credential_mode": "primary"},
            "US": {"credential_mode": "separate_us", "shared_with_kr": False},
        }

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "get_balance", side_effect=fake_balance
        ), patch.object(
            dashboard_server, "get_kis_profile_summary", return_value=profile
        ), patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1300.0
        ):
            snapshot = dashboard_server._broker_snapshot("live")

        self.assertEqual(token_calls, ["KR", "US"])
        self.assertEqual(snapshot["kis_profile"]["US"]["credential_mode"], "separate_us")
        self.assertEqual(snapshot["cumulative"], 1000.0 + 2000.0 + (2.0 + 3.0) * 1300.0)

    def test_broker_snapshot_kis_profile_survives_json_response(self) -> None:
        profile = {
            "KR": {"credential_mode": "primary", "shared_with_kr": True},
            "US": {"credential_mode": "fallback_shared_kr", "shared_with_kr": True},
        }
        snapshot = {
            "source": "broker",
            "kis_profile": profile,
            "cumulative": 1000.0,
        }

        encoded = json.loads(json.dumps(snapshot))
        self.assertEqual(encoded["kis_profile"]["US"]["credential_mode"], "fallback_shared_kr")

        with dashboard_server.app.app_context():
            payload = dashboard_server.jsonify(snapshot).get_json()

        self.assertEqual(payload["kis_profile"]["KR"]["credential_mode"], "primary")
        self.assertTrue(payload["kis_profile"]["US"]["shared_with_kr"])


if __name__ == "__main__":
    unittest.main()
