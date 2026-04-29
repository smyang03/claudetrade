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


if __name__ == "__main__":
    unittest.main()
