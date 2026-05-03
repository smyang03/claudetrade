from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import tempfile
from datetime import date
import unittest
from unittest.mock import patch

from dashboard import dashboard_server

app = dashboard_server.app


class DashboardPathBTests(unittest.TestCase):
    def test_today_page_exposes_separate_today_and_lifetime_pnl_cards(self) -> None:
        res = app.test_client().get("/")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn('id="today-pnl"', body)
        self.assertIn('id="today-krw"', body)
        self.assertIn('id="lifetime-pnl-total"', body)
        self.assertIn('id="lifetime-pnl-split"', body)
        self.assertIn('id="lifetime-pnl-basis"', body)
        self.assertNotIn('id="streak-val"', body)

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

    def test_preopen_page_keeps_current_session_dynamic_and_escapes_tables(self) -> None:
        res = app.test_client().get("/preopen")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertNotIn("getMarket()", body)
        self.assertIn("const market = MARKET || localStorage.getItem('market') || 'KR';", body)
        self.assertNotIn("dateInput.value = d.session_date", body)
        self.assertIn("renderPreopenSessions(d.recent_sessions || [], d.session_date || '', !!sessionDate);", body)
        self.assertIn("function preopenEscapeHtml", body)
        self.assertIn("function preopenTrustedHtml", body)
        self.assertIn("preopenTableCell(c)", body)

    def test_live_trades_pending_order_currency_does_not_reference_missing_market_key(self) -> None:
        live = {
            "session_active": True,
            "trading_date": "2026-05-04",
            "updated_at": "2026-05-04T22:40:00+09:00",
            "pending_orders": [
                {"ticker": "AAPL", "qty": 1, "raw_price": 100.5, "order_no": "1", "created_at": "2026-05-04T22:35:00+09:00"}
            ],
        }
        with app.test_request_context("/?mode=live"), patch.object(
            dashboard_server, "_load_live_status", return_value=live
        ), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 4)
        ):
            rows = dashboard_server._live_trades("US")

        self.assertEqual(rows[0]["currency"], "USD")

    def test_broker_trade_bundle_normalizes_market_for_currency_and_api(self) -> None:
        @contextmanager
        def fake_runtime(_mode: str):
            yield

        token_calls = []

        def fake_token(*, market: str = "KR") -> str:
            token_calls.append(market)
            return f"token-{market}"

        us_rows = [{
            "ticker": "AAPL",
            "filled_qty": 2,
            "fill_price": 188.5,
            "side": "buy",
            "order_time": "223501",
            "order_no": "US-1",
            "raw": {"ord_dt": "20260504"},
        }]
        kr_rows = [{
            "ticker": "005930",
            "filled_qty": 3,
            "fill_price": 70000,
            "side": "buy",
            "order_time": "093000",
            "order_no": "KR-1",
            "raw": {"ord_dt": "20260504"},
        }]

        with patch.object(dashboard_server, "_kis_runtime", fake_runtime), patch.object(
            dashboard_server, "get_access_token", side_effect=fake_token
        ), patch.object(
            dashboard_server, "inquire_ccnl_us", return_value=us_rows
        ) as us_mock, patch.object(
            dashboard_server, "inquire_daily_ccld_kr", return_value=kr_rows
        ) as kr_mock, patch.object(
            dashboard_server, "_ticker_name_map", return_value={}
        ), patch.dict(
            dashboard_server.os.environ, {"DASHBOARD_BROKER_TRADE_CACHE_SEC": "0"}
        ):
            us = dashboard_server._load_broker_trade_bundle("us", "custom", "2026-05-04", "2026-05-04", mode="live")
            kr = dashboard_server._load_broker_trade_bundle("KR", "custom", "2026-05-04", "2026-05-04", mode="live")

        self.assertEqual(token_calls, ["US", "KR"])
        us_mock.assert_called_once()
        kr_mock.assert_called_once()
        self.assertTrue(us["ok"])
        self.assertEqual(us["market"], "US")
        self.assertEqual(us["rows"][0]["currency"], "USD")
        self.assertTrue(kr["ok"])
        self.assertEqual(kr["market"], "KR")
        self.assertEqual(kr["rows"][0]["currency"], "KRW")

    def test_summary_api_exposes_broker_cache_stale_metadata(self) -> None:
        broker = {
            "source": "broker+stale_cache",
            "cache": {
                "hit": True,
                "stale": True,
                "age_sec": 42,
                "last_error": "snapshot boom",
                "source": "stale_cache",
            },
            "usd_krw": 1300.0,
            "kr_cash": 0.0,
            "kr_cash_effective": 0.0,
            "kr_eval": 0.0,
            "us_cash_krw": 1000.0,
            "us_eval_krw": 0.0,
            "unrealized_krw": {"US": 0.0},
        }
        positions_cache = {
            "hit": True,
            "stale": True,
            "age_sec": 55,
            "last_error": "positions boom",
            "source": "stale_cache",
        }
        lifetime = {
            "basis": "broker_fills_fifo_excluding_cash_flow",
            "KR": {"pnl_krw": -1000.0, "known_sell_count": 1, "sell_count": 1, "unknown_cost_basis_count": 0},
            "US": {"pnl_krw": 2500.0, "known_sell_count": 2, "sell_count": 2, "unknown_cost_basis_count": 0},
            "kr_pnl_krw": -1000.0,
            "us_pnl_krw": 2500.0,
            "total_pnl_krw": 1500.0,
            "known_sell_count": 3,
            "sell_count": 3,
            "unknown_cost_basis_count": 0,
            "errors": {},
        }

        with patch.object(
            dashboard_server, "load_records", return_value=[{"date": "2026-05-04", "actual_result": {"cumulative": 1000}}]
        ), patch.object(
            dashboard_server, "load_today", return_value={"date": "2026-05-04", "actual_result": {}, "consensus": {"mode": "NEUTRAL"}}
        ), patch.object(
            dashboard_server, "_load_live_status", return_value={"mode": "NEUTRAL", "pending_orders": [], "broker": {}}
        ), patch.object(
            dashboard_server, "_is_fresh_live_status", return_value=True
        ), patch.object(
            dashboard_server, "_record_metrics", return_value={"pnl_krw": 0, "pnl_pct": 0, "trades": 0, "win": False}
        ), patch.object(
            dashboard_server, "_broker_realized_pnl_krw", return_value=0
        ), patch.object(
            dashboard_server, "_load_broker_positions", return_value=[]
        ), patch.object(
            dashboard_server, "_broker_positions_status", return_value=positions_cache
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value=broker
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_ticker_name_map", return_value={}
        ), patch.object(
            dashboard_server, "_live_equity_payload", return_value={}
        ), patch.object(
            dashboard_server, "_today_signal_digest", return_value={}
        ), patch.object(
            dashboard_server, "_ml_db_digest", return_value={}
        ), patch.object(
            dashboard_server, "_adaptive_param_digest", return_value={}
        ), patch.object(
            dashboard_server, "_count_today_entries", return_value=0
        ), patch.object(
            dashboard_server, "_lifetime_realized_pnl_summary", return_value=lifetime
        ), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 4)
        ), patch.object(
            dashboard_server, "_session_status", return_value={}
        ), patch.object(
            dashboard_server, "_current_risk_snapshot", return_value={}
        ):
            response = app.test_client().get("/api/summary?market=US&mode=live")

        self.assertEqual(response.status_code, 200)
        today = response.get_json()["today"]
        self.assertTrue(today["broker_cache_stale"])
        self.assertEqual(today["broker_cache_age_sec"], 42)
        self.assertEqual(today["broker_cache_source"], "stale_cache")
        self.assertTrue(today["broker_positions_cache_stale"])
        self.assertEqual(today["broker_positions_cache_age_sec"], 55)
        self.assertEqual(today["broker_last_error"], "snapshot boom")
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["kr_pnl_krw"], -1000.0)
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["us_pnl_krw"], 2500.0)
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["total_pnl_krw"], 1500.0)

    def test_lifetime_realized_pnl_summary_splits_markets_and_excludes_unknown_cost_basis(self) -> None:
        def fake_rows(market, period, start, end, mode="paper"):
            self.assertEqual(period, "all")
            if market == "KR":
                return [
                    {"side": "buy", "pnl_known": True, "pnl": 0},
                    {"side": "sell", "pnl_known": True, "pnl": -1200.0},
                    {"side": "sell", "pnl_known": False, "pnl": 999999.0},
                ]
            return [
                {"side": "sell", "pnl_known": True, "pnl": 3400.0},
                {"side": "sell", "pnl_known": True, "pnl": 600.0},
            ]

        with patch.object(dashboard_server, "_broker_trade_rows_with_pnl", side_effect=fake_rows):
            summary = dashboard_server._lifetime_realized_pnl_summary("live")

        self.assertEqual(summary["KR"]["pnl_krw"], -1200.0)
        self.assertEqual(summary["US"]["pnl_krw"], 4000.0)
        self.assertEqual(summary["kr_pnl_krw"], -1200.0)
        self.assertEqual(summary["us_pnl_krw"], 4000.0)
        self.assertEqual(summary["total_pnl_krw"], 2800.0)
        self.assertEqual(summary["known_sell_count"], 3)
        self.assertEqual(summary["sell_count"], 4)
        self.assertEqual(summary["unknown_cost_basis_count"], 1)

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

    def test_history_equity_live_splits_trading_pnl_and_cash_flow(self) -> None:
        snapshots = [
            {
                "market": "US",
                "date": "2026-04-29",
                "asset_krw": 1_000_000,
                "unrealized_krw": 0,
            },
            {
                "market": "US",
                "date": "2026-04-30",
                "asset_krw": 1_200_000,
                "unrealized_krw": 10_000,
            },
        ]
        broker_rows = [
            {
                "side": "sell",
                "pnl_known": True,
                "date": "2026-04-30",
                "pnl": 5_000,
                "pnl_pct": 0.5,
            }
        ]

        with patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 4, 30)
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value={}
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=broker_rows
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=snapshots
        ):
            res = app.test_client().get(
                "/api/history/equity?market=US&mode=live&period=custom&start=2026-04-29&end=2026-04-30"
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2026-04-29", "2026-04-30"])
        self.assertEqual(data["equity"], [1_000_000, 1_200_000])
        self.assertEqual(data["trading_pnl_krw"], [0.0, 15_000.0])
        self.assertEqual(data["cumulative_trading_pnl_krw"], [0.0, 15_000.0])
        self.assertEqual(data["cash_flow_krw"], [0.0, 185_000.0])
        self.assertEqual(data["cumulative_cash_flow_krw"], [0.0, 185_000.0])
        self.assertEqual(data["pnl"], [0.0, 1.5])
        self.assertEqual(data["basis"], "broker_asset_reconstructed")
        self.assertEqual(data["reconciliation_basis"], "broker_asset_trading_pnl_cashflow")

    def test_chart_equity_live_uses_broker_payload_not_historical_records(self) -> None:
        snapshots = [
            {
                "market": "KR",
                "date": "2026-04-29",
                "asset_krw": 1_000_000,
                "unrealized_krw": 0,
            }
        ]

        with patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 4, 29)
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value={}
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=[]
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=snapshots
        ), patch.object(
            dashboard_server, "load_records_filtered", side_effect=AssertionError("historical records should not be used")
        ):
            res = app.test_client().get("/api/chart/equity?market=KR&mode=live&period=3month")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["labels"], ["2026-04-29"])
        self.assertEqual(data["equity"], [1_000_000])
        self.assertEqual(data["asset_basis"], "kis_broker_account")

    def test_judgment_candidates_do_not_let_legacy_hide_newer_live_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_dir = Path(tmp)
            (log_dir / "20260418_US.json").write_text(json.dumps({"date": "2026-04-18"}), encoding="utf-8")
            (log_dir / "live_20260501_US.json").write_text(json.dumps({"date": "2026-05-01"}), encoding="utf-8")
            (log_dir / "paper_20260503_US.json").write_text(json.dumps({"date": "2026-05-03"}), encoding="utf-8")

            with patch.object(dashboard_server, "LOG_DIR", log_dir):
                names = [path.name for path in dashboard_server._judgment_candidates("US", "live")]

        self.assertEqual(names[-1], "live_20260501_US.json")
        self.assertNotIn("paper_20260503_US.json", names)

    def test_preferred_analysis_log_uses_mode_prefixed_file_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_dir = root / "logs" / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "analysis_20260503.jsonl").write_text("{}\n", encoding="utf-8")
            (analysis_dir / "paper_analysis_20260503.jsonl").write_text("{}\n", encoding="utf-8")
            (analysis_dir / "live_analysis_20260503.jsonl").write_text("{}\n", encoding="utf-8")

            with patch.object(dashboard_server, "BASE_DIR", root):
                self.assertEqual(
                    dashboard_server._preferred_analysis_log_path("20260503", "paper").name,
                    "paper_analysis_20260503.jsonl",
                )
                self.assertEqual(
                    dashboard_server._preferred_analysis_log_path("20260503", "live").name,
                    "live_analysis_20260503.jsonl",
                )

    def test_runtime_events_read_mode_prefixed_trading_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system_dir = Path(tmp)
            (system_dir / "trading_20260502.log").write_text(
                "2026-05-02 23:00:00 [INFO] bot | [PAPER BUY] AAPL 1@9999 | gap | 주문번호=old\n",
                encoding="utf-8",
            )
            (system_dir / "live_trading_20260502.log").write_text(
                "2026-05-02 23:01:00 [INFO] bot | [PAPER BUY] AAPL 1@190.5 | gap | 주문번호=live\n",
                encoding="utf-8",
            )

            with patch.object(dashboard_server, "SYSTEM_LOG_DIR", system_dir), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 2)
            ):
                events = dashboard_server._parse_runtime_events("US", mode="live")

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["ticker"], "AAPL")
        self.assertEqual(events[0]["price"], 190.5)

    def test_position_chart_uses_entry_date_range_and_buy_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_dir = root / "data"
            db_dir.mkdir(parents=True)
            db_path = db_dir / "intraday_strategy_log.db"
            con = sqlite3.connect(db_path)
            try:
                con.execute(
                    """
                    CREATE TABLE intraday_strategy_log (
                        ts TEXT NOT NULL,
                        session_date TEXT NOT NULL,
                        market TEXT NOT NULL,
                        ticker TEXT NOT NULL,
                        strategy_name TEXT NOT NULL,
                        stage TEXT NOT NULL,
                        price REAL,
                        bot_mode TEXT NOT NULL DEFAULT 'paper'
                    )
                    """
                )
                con.executemany(
                    """
                    INSERT INTO intraday_strategy_log
                    (ts, session_date, market, ticker, strategy_name, stage, price, bot_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        ("2026-04-30T09:00:00", "2026-04-30", "KR", "005930", "gap", "probe", 100.0, "live"),
                        ("2026-04-30T09:31:00", "2026-04-30", "KR", "005930", "gap", "trade", 102.0, "live"),
                        ("2026-05-01T10:00:00", "2026-05-01", "KR", "005930", "gap", "probe", 108.0, "live"),
                    ],
                )
                con.commit()
            finally:
                con.close()

            with patch.object(dashboard_server, "BASE_DIR", root), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 1)
            ), patch.object(
                dashboard_server, "_saved_positions_for_market", return_value=[]
            ):
                res = app.test_client().get(
                    "/api/position/chart?market=KR&mode=live&ticker=005930&entry_date=2026-04-30&fill_time=093000&avg_price=101&current_price=109"
                )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["range_start"], "2026-04-30")
        self.assertEqual(data["range_end"], "2026-05-01")
        self.assertEqual(data["source"], "position_overview_strategy_samples")
        self.assertEqual(data["labels"], ["04-30 09:30", "04-30 09:31", "05-01 10:00", "현재"])
        self.assertEqual(data["point_kinds"], ["buy_fill", "strategy_sample", "strategy_sample", "current_position"])
        self.assertEqual(data["buy_markers"], [
            {
                "label": "04-30 09:30",
                "price": 101.0,
                "source": "position_entry",
                "timestamp": "2026-04-30T09:30:00",
            }
        ])


if __name__ == "__main__":
    unittest.main()
