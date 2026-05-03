from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import time
import unittest
from unittest.mock import patch

from dashboard import dashboard_server


class DashboardKisProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        dashboard_server._BROKER_SNAPSHOT_CACHE.clear()
        dashboard_server._BROKER_SNAPSHOT_STATUS.clear()
        dashboard_server._BROKER_POSITIONS_CACHE.clear()
        dashboard_server._BROKER_POSITIONS_STATUS.clear()

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

    def test_broker_snapshot_rechecks_cache_under_lock_for_parallel_calls(self) -> None:
        balance_calls: list[str] = []

        @contextmanager
        def fake_runtime(_mode: str):
            yield

        def fake_token(*, market: str = "KR") -> str:
            return f"token-{market}"

        def fake_balance(token: str, *, market: str, force_refresh: bool = False) -> dict:
            time.sleep(0.03)
            balance_calls.append(market)
            if market == "US":
                return {"cash": 1.0, "total_eval": 1.0, "stocks": [], "currency": "USD"}
            return {"cash": 1000.0, "total_eval": 0.0, "stocks": [], "currency": "KRW"}

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "get_balance", side_effect=fake_balance
        ), patch.object(
            dashboard_server, "get_kis_profile_summary", return_value={}
        ), patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1300.0
        ):
            with ThreadPoolExecutor(max_workers=2) as pool:
                snapshots = list(pool.map(lambda _: dashboard_server._broker_snapshot("live"), range(2)))

        self.assertEqual(balance_calls, ["KR", "US"])
        self.assertEqual(snapshots[0]["cumulative"], snapshots[1]["cumulative"])
        self.assertTrue(any(s["cache"]["hit"] for s in snapshots))
        self.assertFalse(any(s["cache"]["stale"] for s in snapshots))

    def test_broker_positions_cache_avoids_repeated_force_refresh(self) -> None:
        balance_calls: list[str] = []

        @contextmanager
        def fake_runtime(_mode: str):
            yield

        def fake_token(*, market: str = "KR") -> str:
            return f"token-{market}"

        def fake_balance(token: str, *, market: str, force_refresh: bool = False) -> dict:
            balance_calls.append(market)
            return {
                "stocks": [
                    {"ticker": "AAPL", "qty": 2, "avg_price": 100.0, "eval_price": 110.0, "profit_rate": 10.0}
                ]
            }

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "get_balance", side_effect=fake_balance
        ):
            first = dashboard_server._load_broker_positions("US", mode="live")
            second = dashboard_server._load_broker_positions("US", mode="live")

        self.assertEqual(balance_calls, ["US"])
        self.assertEqual(first, second)
        self.assertEqual(first[0]["currency"], "USD")
        self.assertTrue(dashboard_server._broker_positions_status("live", "US")["hit"])

    def test_broker_snapshot_returns_stale_cache_metadata_on_refresh_error(self) -> None:
        @contextmanager
        def fake_runtime(_mode: str):
            yield

        def fake_token(*, market: str = "KR") -> str:
            return f"token-{market}"

        def fake_balance(token: str, *, market: str, force_refresh: bool = False) -> dict:
            if market == "US":
                return {"cash": 1.0, "total_eval": 1.0, "stocks": [], "currency": "USD"}
            return {"cash": 1000.0, "total_eval": 0.0, "stocks": [], "currency": "KRW"}

        profile_calls = 0

        def fake_profile() -> dict:
            nonlocal profile_calls
            profile_calls += 1
            if profile_calls > 1:
                raise RuntimeError("snapshot boom")
            return {}

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "get_balance", side_effect=fake_balance
        ), patch.object(
            dashboard_server, "get_kis_profile_summary", side_effect=fake_profile
        ), patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1300.0
        ):
            first = dashboard_server._broker_snapshot("live")
            dashboard_server._BROKER_SNAPSHOT_CACHE["live"]["ts"] = 1
            second = dashboard_server._broker_snapshot("live")

        self.assertEqual(second["cumulative"], first["cumulative"])
        self.assertTrue(second["cache"]["stale"])
        self.assertEqual(second["cache"]["source"], "stale_cache")
        self.assertIn("snapshot boom", second["cache"]["last_error"])
        self.assertTrue(dashboard_server._broker_snapshot_status("live")["stale"])

    def test_broker_positions_returns_stale_cache_metadata_on_refresh_error(self) -> None:
        @contextmanager
        def fake_runtime(_mode: str):
            yield

        fail_balance = False

        def fake_token(*, market: str = "KR") -> str:
            return f"token-{market}"

        def fake_balance(token: str, *, market: str, force_refresh: bool = False) -> dict:
            if fail_balance:
                raise RuntimeError("balance boom")
            return {
                "stocks": [
                    {"ticker": "AAPL", "qty": 2, "avg_price": 100.0, "eval_price": 110.0, "profit_rate": 10.0}
                ]
            }

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "get_balance", side_effect=fake_balance
        ):
            first = dashboard_server._load_broker_positions("US", mode="live")
            dashboard_server._BROKER_POSITIONS_CACHE[("live", "US")]["ts"] = 1
            fail_balance = True
            second = dashboard_server._load_broker_positions("US", mode="live")

        status = dashboard_server._broker_positions_status("live", "US")
        self.assertEqual(second, first)
        self.assertTrue(status["stale"])
        self.assertEqual(status["source"], "stale_cache")
        self.assertIn("balance boom", status["last_error"])

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
