from __future__ import annotations

from datetime import date
import unittest
from unittest.mock import patch

from dashboard import dashboard_server

app = dashboard_server.app


class DashboardPathBTests(unittest.TestCase):
    def test_pathb_page_loads_and_old_pages_redirect(self) -> None:
        client = app.test_client()

        pathb = client.get("/pathb")
        self.assertEqual(pathb.status_code, 200)
        body = pathb.get_data(as_text=True)
        self.assertIn("B플랜 실시간", body)
        self.assertIn("pathbPnlChart", body)
        self.assertIn("pathbOutcomeChart", body)
        self.assertIn("pathbStatusChart", body)
        self.assertIn("pathbCompareChart", body)
        self.assertIn("클로드 매수/매도 근거", body)
        self.assertNotIn('href="/history"', body)
        self.assertNotIn('href="/trades"', body)
        self.assertNotIn('href="/broker-trades"', body)

        for old_path in ("/history", "/trades", "/broker-trades"):
            res = client.get(old_path, follow_redirects=False)
            self.assertEqual(res.status_code, 302)
            self.assertEqual(res.headers["Location"], "/pathb")

    def test_pathb_ops_api_loads(self) -> None:
        client = app.test_client()
        res = client.get("/api/v2/ops?market=KR")
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["ok"])
        self.assertIn("path_b_live", data)
        self.assertIn("config", data["path_b_live"])
        self.assertIn("metrics", data["path_b_live"])
        self.assertIn("charts", data["path_b_live"])

    def test_live_limits_use_start_config_overrides(self) -> None:
        self.assertEqual(dashboard_server._get_env_int("live", "KR_MAX_POSITIONS", 10), 15)
        self.assertEqual(dashboard_server._get_env_int("live", "US_MAX_POSITIONS", 10), 10)

    def test_dashboard_default_live_but_paper_mode_explicit(self) -> None:
        self.assertEqual(dashboard_server._normalize_mode(None), "live")
        self.assertEqual(dashboard_server._normalize_mode("paper"), "paper")

    def test_v2_ops_market_uses_session_trade_date(self) -> None:
        captured = {}

        def fake_summary(**kwargs):
            captured.update(kwargs)
            return {"ok": True}

        with patch.object(dashboard_server, "build_v2_ops_summary", side_effect=fake_summary), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 4, 28)
        ):
            res = app.test_client().get("/api/v2/ops?market=us")

        self.assertEqual(res.status_code, 200)
        self.assertEqual(captured["market"], "US")
        self.assertEqual(captured["session_date"], "2026-04-28")

    def test_history_equity_live_us_uses_session_trade_date(self) -> None:
        class FakeDate(date):
            @classmethod
            def today(cls):
                return cls(2026, 5, 1)

        broker = {
            "us_cash_krw": 1_000_000,
            "us_eval_krw": 250_000,
        }
        broker_rows = [
            {
                "side": "sell",
                "pnl_known": True,
                "date": "2026-04-30",
                "pnl": 10_000,
                "pnl_pct": 1.25,
            }
        ]

        with patch.object(dashboard_server, "date", FakeDate), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 4, 30)
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value=broker
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=broker_rows
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=[]
        ):
            res = app.test_client().get("/api/history/equity?market=US&mode=live")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2026-04-30"])
        self.assertNotIn("2026-05-01", data["labels"])
        self.assertEqual(data["equity"], [1_250_000])


if __name__ == "__main__":
    unittest.main()
