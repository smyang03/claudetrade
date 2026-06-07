from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from datetime import date, datetime
import unittest
from unittest.mock import patch

from dashboard import dashboard_server

app = dashboard_server.app


class DashboardPathBTests(unittest.TestCase):
    def test_known_kr_holiday_override_blocks_dashboard_trading_day(self) -> None:
        self.assertFalse(dashboard_server._is_trading_day("KR", date(2026, 7, 17)))

    def test_summary_daily_entry_cap_prefers_market_specific_config(self) -> None:
        overrides = {
            "V2_MAX_DAILY_ENTRIES": "20",
            "KR_DAILY_ENTRY_CAP": "1",
            "US_DAILY_ENTRY_CAP": "20",
        }
        with patch.object(dashboard_server, "_start_config_env_overrides", return_value=overrides), patch.object(
            dashboard_server, "_runtime_env", return_value={}
        ):
            self.assertEqual(dashboard_server._max_daily_entries_for_market("live", "KR"), 1)
            self.assertEqual(dashboard_server._max_daily_entries_for_market("live", "US"), 20)

    def test_summary_daily_entry_cap_falls_back_to_global_config(self) -> None:
        overrides = {"V2_MAX_DAILY_ENTRIES": "9", "KR_DAILY_ENTRY_CAP": "", "US_DAILY_ENTRY_CAP": ""}
        with patch.object(dashboard_server, "_start_config_env_overrides", return_value=overrides), patch.object(
            dashboard_server, "_runtime_env", return_value={}
        ), patch.dict(
            os.environ,
            {
                "KR_DAILY_ENTRY_CAP": "",
                "US_DAILY_ENTRY_CAP": "",
                "V2_MAX_DAILY_ENTRIES": "",
                "MAX_DAILY_ENTRIES": "",
            },
            clear=False,
        ):
            self.assertEqual(dashboard_server._max_daily_entries_for_market("live", "KR"), 9)
            self.assertEqual(dashboard_server._max_daily_entries_for_market("live", "US"), 9)

    def test_today_page_exposes_separate_today_and_lifetime_pnl_cards(self) -> None:
        res = app.test_client().get("/")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn('id="today-pnl"', body)
        self.assertIn('id="today-krw"', body)
        self.assertIn('id="buy-readiness-patha"', body)
        self.assertIn('id="buy-readiness-pathb"', body)
        self.assertIn("renderBuyReadiness", body)
        self.assertIn('id="bar-stop-cluster"', body)
        self.assertIn('requestStopClusterReset', body)
        self.assertIn('id="lifetime-pnl-total"', body)
        self.assertIn('id="lifetime-pnl-split"', body)
        self.assertIn('id="lifetime-pnl-basis"', body)
        self.assertNotIn('id="streak-val"', body)

    def test_pathb_page_exposes_preopen_recheck_column(self) -> None:
        res = app.test_client().get("/pathb")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("function pathbPreopenPolicyCell", body)
        self.assertIn("개장 재검증", body)
        self.assertIn("preopen_exit_policy_decision", body)

    def test_today_page_shows_pnl_source_badge_helpers(self) -> None:
        res = app.test_client().get("/")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn(".pnl-source-badge", body)
        self.assertIn("function pnlSourceLabel", body)
        self.assertIn("function pnlSourceClass", body)
        self.assertIn("broker_truth_confirmed_local_pnl: '브로커확인'", body)
        self.assertIn("live_status_or_metrics_fallback: 'fallback'", body)
        self.assertIn("kis_current_session_period_profit: '한투 기간손익'", body)
        self.assertIn("보유평가 변화", body)
        self.assertIn("현재 보유평가", body)
        self.assertIn("todayKrw.innerHTML", body)

    def test_today_page_defines_escape_html_before_judgment_rendering(self) -> None:
        res = app.test_client().get("/")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("function escapeHtml", body)
        self.assertIn("async function loadJudgments", body)
        self.assertLess(body.index("function escapeHtml"), body.index("async function loadJudgments"))
        self.assertLess(body.index("function escapeHtml"), body.index("basis.digest_built_at"))
        self.assertIn("${escapeHtml(basis.warning)}", body)

    def test_today_page_distinguishes_watch_only_fill_history_labels(self) -> None:
        res = app.test_client().get("/")

        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("displayEventLabel", body)
        self.assertIn("오늘 매수 이력", body)
        self.assertIn("오늘 매도 이력", body)

    def test_watch_only_fill_history_reason_mentions_current_buy_exclusion(self) -> None:
        buy_reason = dashboard_server._fallback_select_reason(
            "078150",
            "KR",
            "MODERATE_BULL",
            {"selection_status": "WATCH_ONLY", "last_event": "buy_filled"},
        )
        sell_reason = dashboard_server._fallback_select_reason(
            "006345",
            "KR",
            "MODERATE_BULL",
            {"selection_status": "WATCH_ONLY", "last_event": "sell_filled"},
        )

        self.assertIn("오늘 매수체결 이력 있음", buy_reason)
        self.assertIn("현재 신규매수 후보 아님", buy_reason)
        self.assertIn("오늘 매도체결 완료", sell_reason)
        self.assertIn("현재 재진입 후보 아님", sell_reason)

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
        self.assertEqual(dashboard_server._get_env_int("live", "KR_MAX_POSITIONS", 10), 20)
        self.assertEqual(dashboard_server._get_env_int("live", "US_MAX_POSITIONS", 10), 20)

    def test_dashboard_default_live_but_paper_mode_explicit(self) -> None:
        self.assertEqual(dashboard_server._normalize_mode(None), "live")
        self.assertEqual(dashboard_server._normalize_mode("paper"), "paper")

    def test_monitor_display_price_prefers_realtime_quote_over_old_trade(self) -> None:
        item = {
            "last_price": 108.29,
            "current_price": 0,
            "avg_price": 0,
            "held_qty": 0,
            "last_ts": "00:01",
        }

        dashboard_server._apply_monitor_display_price(
            item,
            "US",
            recent_trade={"date": "2026-04-27", "display_price": 83.84},
            quote={
                "price": 109.01,
                "ts": "2026-05-06T00:02:00+09:00",
                "source": "realtime_quote",
            },
        )

        self.assertEqual(item["display_price"], 109.01)
        self.assertEqual(item["current_price"], 109.01)
        self.assertEqual(item["last_price"], 109.01)
        self.assertEqual(item["price_source"], "realtime_quote")

    def test_monitor_display_price_uses_today_event_before_old_trade(self) -> None:
        item = {
            "last_price": 108.29,
            "current_price": 0,
            "avg_price": 0,
            "held_qty": 0,
            "last_ts": "00:01",
        }

        with patch.object(dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)):
            dashboard_server._apply_monitor_display_price(
                item,
                "US",
                recent_trade={"date": "2026-04-27", "display_price": 83.84},
                quote=None,
            )

        self.assertEqual(item["display_price"], 108.29)
        self.assertEqual(item["price_source"], "session_event_price")

    def test_realtime_quote_cache_uses_separate_cached_at_timestamp(self) -> None:
        key = ("live", "US", "INTC")
        dashboard_server._DASHBOARD_QUOTE_CACHE.clear()
        dashboard_server._DASHBOARD_QUOTE_CACHE[key] = {
            "cached_at": dashboard_server._time.time(),
            "ts": "2026-05-06T00:02:00+09:00",
            "ticker": "INTC",
            "price": 108.29,
            "source": "realtime_quote",
        }
        try:
            with patch.object(dashboard_server, "get_price", side_effect=AssertionError("cache should be used")):
                quotes = dashboard_server._dashboard_realtime_quotes("US", ["INTC"], "live")
        finally:
            dashboard_server._DASHBOARD_QUOTE_CACHE.clear()

        self.assertEqual(quotes["INTC"]["price"], 108.29)
        self.assertEqual(quotes["INTC"]["ts"], "2026-05-06T00:02:00+09:00")

    def test_realtime_quote_cache_accepts_pathb_ttl(self) -> None:
        key = ("live", "US", "INTC")
        dashboard_server._DASHBOARD_QUOTE_CACHE.clear()
        dashboard_server._DASHBOARD_QUOTE_CACHE[key] = {
            "cached_at": dashboard_server._time.time() - 30,
            "ts": "2026-05-06T00:02:00+09:00",
            "ticker": "INTC",
            "price": 108.29,
            "source": "realtime_quote",
        }
        try:
            with patch.object(dashboard_server, "get_price", side_effect=AssertionError("cache should be used")):
                quotes = dashboard_server._dashboard_realtime_quotes("US", ["INTC"], "live", ttl_sec=60)
        finally:
            dashboard_server._DASHBOARD_QUOTE_CACHE.clear()

        self.assertEqual(quotes["INTC"]["price"], 108.29)
        self.assertGreaterEqual(quotes["INTC"]["age_sec"], 29)

    def test_pathb_watch_price_enrichment_adds_realtime_price(self) -> None:
        summary = {
            "market": "US",
            "path_b_live": {
                "selection": {
                    "watch_rows": [
                        {"ticker": "INTC", "buy_zone_low": 100.0, "buy_zone_high": 110.0}
                    ]
                }
            },
        }

        with patch.object(dashboard_server, "_dashboard_pathb_quote_cache_sec", return_value=60), patch.object(
            dashboard_server,
            "_dashboard_realtime_quotes",
            return_value={
                "INTC": {
                    "ticker": "INTC",
                    "price": 105.25,
                    "ts": "2026-05-06T00:02:00+09:00",
                    "source": "realtime_quote",
                    "age_sec": 4,
                    "change_rate": 1.23,
                    "volume": 12345,
                }
            },
        ) as quotes, patch.object(dashboard_server, "_is_live_market", return_value=True):
            dashboard_server._enrich_pathb_watch_prices(summary, market="US", mode="live")

        quotes.assert_called_once_with("US", ["INTC"], "live", ttl_sec=60)
        selection = summary["path_b_live"]["selection"]
        row = selection["watch_rows"][0]
        self.assertEqual(selection["quote_refresh_interval_sec"], 60)
        self.assertEqual(selection["quote_updated_count"], 1)
        self.assertEqual(row["current_price"], 105.25)
        self.assertEqual(row["current_price_at"], "2026-05-06T00:02:00+09:00")
        self.assertEqual(row["current_change_rate"], 1.23)
        self.assertEqual(row["current_buy_zone_state"], "inside_buy_zone")

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
            "kr_eval": 200_000.0,
            "us_cash_krw": 1000.0,
            "us_eval_krw": 130_000.0,
            "unrealized_krw": {"KR": 10_000.0, "US": -5_000.0},
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
            dashboard_server,
            "_load_live_status",
            return_value={
                "mode": "NEUTRAL",
                "pending_orders": [],
                "broker": {},
                "stop_cluster": {
                    "daily_stop_count": 3,
                    "hard_block_count": 4,
                    "disaster_block_count": 6,
                    "blocked": False,
                    "reason": "",
                },
            },
        ), patch.object(
            dashboard_server, "_is_fresh_live_status", return_value=True
        ), patch.object(
            dashboard_server, "_record_metrics", return_value={"pnl_krw": 0, "pnl_pct": 0, "trades": 0, "win": False}
        ), patch.object(
            dashboard_server, "_broker_realized_pnl_krw", return_value=0
        ), patch.object(
            dashboard_server, "_load_broker_positions_fast", return_value=[]
        ), patch.object(
            dashboard_server, "_broker_positions_status", return_value=positions_cache
        ), patch.object(
            dashboard_server, "_broker_snapshot_fast", return_value=broker
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_ticker_name_map", return_value={}
        ), patch.object(
            dashboard_server, "_live_equity_payload_fast", return_value={}
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
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["basis"], "cached_state_fast_path")
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["source"], "summary_fast_no_broker_trade_refresh")
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["kr_pnl_krw"], 0.0)
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["us_pnl_krw"], 0.0)
        self.assertEqual(today["pnl_summary"]["lifetime_realized"]["total_pnl_krw"], 0.0)
        self.assertEqual(today["holding_unrealized_pnl_krw_kr"], 10000.0)
        self.assertEqual(today["holding_unrealized_pnl_krw_us"], -5000.0)
        self.assertEqual(today["holding_unrealized_total_pnl_krw"], 5000.0)
        self.assertAlmostEqual(today["holding_unrealized_total_pct"], 1.5385, places=4)
        self.assertEqual(today["stop_cluster"]["daily_stop_count"], 3)
        self.assertEqual(today["stop_cluster"]["hard_block_count"], 4)

    def test_summary_min_order_krw_tolerates_bad_env_values(self) -> None:
        def bad_us_env(_mode: str, key: str):
            return {"US_MIN_ORDER_KRW": "bad", "US_MIN_ORDER_USD": "bad"}.get(key)

        with patch.object(dashboard_server, "_get_env_raw", side_effect=bad_us_env):
            self.assertEqual(dashboard_server._summary_min_order_krw("US", {}, 1300.0), 39000.0)

        with patch.object(dashboard_server, "_get_env_raw", return_value="bad"):
            self.assertEqual(dashboard_server._summary_min_order_krw("KR", {}, 1300.0), 50000.0)

        with patch.object(dashboard_server, "_get_env_raw", return_value="bad"):
            self.assertEqual(
                dashboard_server._summary_min_order_krw("US", {"min_effective_order_krw": "42000"}, 1300.0),
                42000.0,
            )

    def test_summary_api_ignores_stale_previous_session_execution_warning(self) -> None:
        stale_rec = {
            "date": "2026-05-08",
            "market": "US",
            "actual_result": {
                "execution_contaminated": False,
                "execution_issues": ["broker_position_removed"],
                "cumulative": 1_000_000,
            },
            "consensus": {"mode": "STALE", "size": 50},
            "tickers": ["AMD", "INTC"],
            "universe_tickers": ["AMD", "INTC", "DKNG"],
        }
        signal_counts = {}

        def fake_signal_digest(market: str, selected_count: int = 0, universe_count: int = 0) -> dict:
            signal_counts["market"] = market
            signal_counts["selected_count"] = selected_count
            signal_counts["universe_count"] = universe_count
            return {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard_server, "load_records", return_value=[stale_rec]))
            stack.enter_context(patch.object(dashboard_server, "load_today", return_value=stale_rec))
            stack.enter_context(patch.object(dashboard_server, "_load_live_status", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_broker_realized_pnl_krw", return_value=0))
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_current_session_realized_pnl_status",
                    return_value={"available": True, "pnl_krw": 0, "source": "test"},
                )
            )
            stack.enter_context(patch.object(dashboard_server, "_load_broker_positions", return_value=[]))
            stack.enter_context(patch.object(dashboard_server, "_live_position_context_for_market", return_value=[]))
            stack.enter_context(patch.object(dashboard_server, "_broker_positions_status", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_broker_snapshot_status", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_broker_snapshot", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_persist_broker_equity_snapshot"))
            stack.enter_context(patch.object(dashboard_server, "_ticker_name_map", return_value={}))
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_live_asset_fallback",
                    return_value={"asset_krw": 1_000_000, "unrealized_krw": 0},
                )
            )
            stack.enter_context(patch.object(dashboard_server, "_live_equity_payload", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_today_signal_digest", side_effect=fake_signal_digest))
            stack.enter_context(patch.object(dashboard_server, "_ml_db_digest", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_adaptive_param_digest", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_count_today_entries", return_value=0))
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_current_session_trade_turnover",
                    return_value={"fill_count": 0, "buy_krw": 0, "sell_krw": 0, "total_krw": 0},
                )
            )
            stack.enter_context(patch.object(dashboard_server, "_lifetime_realized_pnl_summary", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_session_trade_date", return_value=date(2026, 5, 9)))
            stack.enter_context(patch.object(dashboard_server, "_session_status", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_current_risk_snapshot", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_load_claude_control", return_value={}))
            response = app.test_client().get("/api/summary?market=US&mode=live")

        self.assertEqual(response.status_code, 200)
        today = response.get_json()["today"]
        self.assertEqual(today["date"], "2026-05-09")
        self.assertFalse(today["execution_warning"])
        self.assertFalse(today["execution_contaminated"])
        self.assertEqual(today["execution_issues"], [])
        self.assertEqual(today["mode"], "-")
        self.assertEqual(signal_counts["selected_count"], 0)
        self.assertEqual(signal_counts["universe_count"], 0)

    def test_stop_cluster_reset_endpoint_queues_operator_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            control_path = Path(tmp) / "live_claude_control.json"
            with patch.object(dashboard_server, "_claude_control_path", return_value=control_path):
                response = app.test_client().post(
                    "/api/control/stop-cluster-reset",
                    json={"market": "US", "mode": "live"},
                )

                self.assertEqual(response.status_code, 200)
                payload = json.loads(control_path.read_text(encoding="utf-8"))

        pending = payload["pending_stop_cluster_reset"]
        self.assertEqual(pending["market"], "US")
        self.assertTrue(pending["keep_stopped_tickers"])
        self.assertEqual(payload["updated_by"], "dashboard")

    def test_claude_status_payload_marks_previous_day_error_stale(self) -> None:
        payload = dashboard_server._normalize_claude_status_payload(
            {
                "last_result_at": "2026-05-18T11:55:30+09:00",
                "last_result_status": "error",
                "last_error": "ticker missing",
                "pending_trigger": None,
                "pending_position_review": None,
                "pending_sell": None,
            },
            now_dt=datetime(2026, 5, 19, 11, 52, tzinfo=dashboard_server.KST),
        )

        self.assertEqual(payload["last_result_status"], "stale_error")
        self.assertEqual(payload["last_error"], "")
        self.assertTrue(payload["last_result_stale"])
        self.assertEqual(payload["stale_last_error"], "ticker missing")

    def test_claude_status_payload_keeps_current_day_error_visible(self) -> None:
        payload = dashboard_server._normalize_claude_status_payload(
            {
                "last_result_at": "2026-05-19T11:55:30+09:00",
                "last_result_status": "error",
                "last_error": "ticker missing",
                "pending_trigger": None,
            },
            now_dt=datetime(2026, 5, 19, 12, 1, tzinfo=dashboard_server.KST),
        )

        self.assertEqual(payload["last_result_status"], "error")
        self.assertEqual(payload["last_error"], "ticker missing")
        self.assertFalse(payload["last_result_stale"])

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

        with patch.object(dashboard_server, "_broker_period_profit_bucket", return_value=None), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", side_effect=fake_rows
        ), patch.object(
            dashboard_server, "_apply_current_session_realized_adjustment", return_value=None
        ):
            summary = dashboard_server._lifetime_realized_pnl_summary("live")

        self.assertEqual(summary["KR"]["pnl_krw"], -1200.0)
        self.assertEqual(summary["US"]["pnl_krw"], 4000.0)
        self.assertEqual(summary["kr_pnl_krw"], -1200.0)
        self.assertEqual(summary["us_pnl_krw"], 4000.0)
        self.assertEqual(summary["total_pnl_krw"], 2800.0)
        self.assertEqual(summary["known_sell_count"], 3)
        self.assertEqual(summary["sell_count"], 4)
        self.assertEqual(summary["unknown_cost_basis_count"], 1)

    def test_lifetime_realized_pnl_summary_adds_active_session_realized_adjustment(self) -> None:
        def fake_rows(market, period, start, end, mode="paper"):
            self.assertEqual(period, "all")
            if market == "US":
                return [
                    {"date": "2026-04-27", "side": "sell", "pnl_known": True, "pnl": -1000.0},
                ]
            return []

        def fake_live(market, mode="paper"):
            if market == "US":
                return {
                    "market": "US",
                    "session_active": True,
                    "trading_date": "2026-05-05",
                    "daily_pnl": -250.0,
                }
            return {"market": market, "session_active": False}

        with patch.object(dashboard_server, "_broker_period_profit_bucket", return_value=None), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", side_effect=fake_rows
        ), patch.object(
            dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_load_live_status", side_effect=fake_live
        ), patch.object(
            dashboard_server, "_is_fresh_live_status", side_effect=lambda live, today: bool(live.get("session_active"))
        ), patch.object(
            dashboard_server, "load_today", side_effect=lambda market: {"date": "2026-05-05", "market": market}
        ), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ):
            summary = dashboard_server._lifetime_realized_pnl_summary("live")

        self.assertEqual(summary["US"]["pnl_krw"], -1250.0)
        self.assertEqual(summary["US"]["current_session_adjustment_krw"], -250.0)
        self.assertEqual(summary["us_pnl_krw"], -1250.0)
        self.assertEqual(summary["total_pnl_krw"], -1250.0)

    def test_period_profit_bucket_includes_today_pnl_for_current_session(self) -> None:
        payload = {
            "today_date": "20260516",
            "rows": [
                {"trad_dt": "20260515", "rlzt_pfls": "1000"},
                {"trad_dt": "20260516", "rlzt_pfls": "-2500"},
            ],
            "summary": {"tot_rlzt_pfls": "-1500"},
            "source": "kis_period_trade_profit",
            "query_start": "20260101",
            "query_end": "20260516",
        }

        bucket = dashboard_server._period_profit_bucket_from_payload("KR", payload)

        self.assertEqual(bucket["pnl_krw"], -1500.0)
        self.assertEqual(bucket["today_date"], "20260516")
        self.assertEqual(bucket["today_pnl_krw"], -2500.0)

    def test_current_session_realized_pnl_prefers_kis_period_profit(self) -> None:
        dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()
        try:
            with patch.object(dashboard_server, "_session_trade_date", return_value=date(2026, 5, 22)), patch.object(
                dashboard_server,
                "_broker_period_profit_bucket",
                return_value={
                    "pnl_krw": 35_816.377,
                    "known_sell_count": 8,
                    "sell_count": 8,
                    "fee_krw": 11_050.725,
                    "query_start": "20260522",
                    "query_end": "20260522",
                },
            ), patch.object(
                dashboard_server,
                "_broker_today_fill_fifo_realized_pnl",
                side_effect=AssertionError("period profit should be preferred"),
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"market": "US", "trading_date": "2026-05-22", "market_realized_pnl_krw": 0.0},
                )
        finally:
            dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()

        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], 35_816.377)
        self.assertEqual(status["broker_sell_count"], 8)
        self.assertEqual(status["source"], "kis_current_session_period_profit")

    def test_current_session_realized_pnl_uses_previous_kis_date_for_closed_us_session(self) -> None:
        dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()
        calls = []

        def fake_period_bucket(_market, _mode, start_date, _end_date):
            calls.append(start_date)
            if start_date == date(2026, 5, 23):
                return {"pnl_krw": 0.0, "known_sell_count": 0, "sell_count": 0}
            return {
                "pnl_krw": 35_816.377,
                "known_sell_count": 8,
                "sell_count": 8,
                "query_start": "20260522",
                "query_end": "20260522",
            }

        try:
            with patch.object(dashboard_server, "_session_trade_date", return_value=date(2026, 5, 23)), patch.object(
                dashboard_server, "_is_trading_day", side_effect=lambda _market, day=None: day == date(2026, 5, 22)
            ), patch.object(
                dashboard_server, "_broker_period_profit_bucket", side_effect=fake_period_bucket
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"market": "US", "trading_date": "2026-05-23", "market_realized_pnl_krw": 0.0},
                )
        finally:
            dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()

        self.assertEqual(calls, [date(2026, 5, 23), date(2026, 5, 22)])
        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], 35_816.377)
        self.assertTrue(status["query_date_fallback"])
        self.assertEqual(status["session_date"], "2026-05-23")

    def test_current_session_realized_pnl_does_not_use_previous_kis_date_on_us_trading_day(self) -> None:
        dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()
        calls = []

        def fake_period_bucket(_market, _mode, start_date, _end_date):
            calls.append(start_date)
            if start_date == date(2026, 5, 28):
                return {"pnl_krw": 0.0, "known_sell_count": 0, "sell_count": 0}
            return {"pnl_krw": -54_548.0, "known_sell_count": 8, "sell_count": 8}

        try:
            with patch.object(dashboard_server, "_session_trade_date", return_value=date(2026, 5, 28)), patch.object(
                dashboard_server, "_is_trading_day", return_value=True
            ), patch.object(
                dashboard_server, "_broker_period_profit_bucket", side_effect=fake_period_bucket
            ), patch.object(
                dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
            ), patch.object(
                dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
            ), patch.object(
                dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"market": "US", "trading_date": "2026-05-28", "market_realized_pnl_krw": 0.0},
                )
        finally:
            dashboard_server._CURRENT_SESSION_PERIOD_PROFIT_CACHE.clear()

        self.assertEqual(calls, [date(2026, 5, 28)])
        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], 0.0)
        self.assertEqual(status["source"], "live_status_market_realized_pnl")

    def test_period_profit_direct_adds_missing_intraday_adjustment(self) -> None:
        direct = {
            "pnl_krw": 1000.0,
            "known_sell_count": 1,
            "sell_count": 1,
            "unknown_cost_basis_count": 0,
            "source": "kis_period_trade_profit",
            "today_pnl_krw": 0.0,
        }

        with patch.object(dashboard_server, "_broker_period_profit_bucket", side_effect=[direct, None]), patch.object(
            dashboard_server,
            "_current_session_realized_pnl_status",
            return_value={"available": True, "pnl_krw": 300.0, "source": "live_status_market_realized_pnl"},
        ), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 16)
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=[]
        ), patch.object(
            dashboard_server, "_apply_current_session_realized_adjustment", return_value=None
        ):
            summary = dashboard_server._lifetime_realized_pnl_summary("live")

        self.assertEqual(summary["KR"]["pnl_krw"], 1300.0)
        self.assertEqual(summary["KR"]["current_session_adjustment_krw"], 300.0)
        self.assertEqual(summary["kr_pnl_krw"], 1300.0)

    def test_period_profit_direct_skips_duplicate_intraday_adjustment(self) -> None:
        direct = {
            "pnl_krw": 1000.0,
            "known_sell_count": 1,
            "sell_count": 1,
            "unknown_cost_basis_count": 0,
            "source": "kis_overseas_period_profit",
            "today_pnl_krw": -250.0,
        }

        with patch.object(dashboard_server, "_broker_period_profit_bucket", side_effect=[None, direct]), patch.object(
            dashboard_server,
            "_current_session_realized_pnl_status",
            return_value={"available": True, "pnl_krw": -250.0, "source": "broker_today_fill_fifo"},
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=[]
        ), patch.object(
            dashboard_server, "_apply_current_session_realized_adjustment", return_value=None
        ):
            summary = dashboard_server._lifetime_realized_pnl_summary("live")

        self.assertEqual(summary["US"]["pnl_krw"], 1000.0)
        self.assertNotIn("current_session_adjustment_krw", summary["US"])
        self.assertEqual(summary["us_pnl_krw"], 1000.0)

    def test_period_profit_direct_does_not_reapply_period_profit_status(self) -> None:
        bucket = {
            "pnl_krw": 35_816.377,
            "known_sell_count": 8,
            "sell_count": 8,
            "unknown_cost_basis_count": 0,
            "source": "kis_overseas_period_profit",
            "today_pnl_krw": 0.0,
        }

        with patch.object(
            dashboard_server,
            "_current_session_period_profit_realized_pnl",
            side_effect=AssertionError("direct period bucket must not be adjusted by period profit again"),
        ), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_load_live_status", return_value={}
        ):
            dashboard_server._apply_current_session_period_profit_adjustment(bucket, "US", "live")

        self.assertEqual(bucket["pnl_krw"], 35_816.377)
        self.assertNotIn("current_session_adjustment_krw", bucket)

    def test_current_session_realized_pnl_prefers_broker_confirmed_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            now = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
            (state_dir / "live_broker_truth_snapshot.json").write_text(
                json.dumps(
                    {
                        "markets": {
                            "US": {
                                "missing": False,
                                "stale": False,
                                "last_success_at": now,
                                "ttl_sec": 3600,
                                "error": "",
                                "today_fills": [
                                    {
                                        "ticker": "CRCL",
                                        "side": "sell",
                                        "order_no": "0030651849",
                                        "filled_qty": 1,
                                    }
                                ],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "live_decisions.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "closed",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030650645",
                                "pnl_krw": -12372.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "CRCL",
                                "qty": 1,
                                "order_no": "0030651849",
                                "pnl_krw": 12414.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030699267",
                                "pnl_krw": -14724.0,
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            def fake_runtime_path(*parts, make_parents=True):
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None), patch.object(
                dashboard_server, "get_runtime_path", side_effect=fake_runtime_path
            ), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"session_active": True, "trading_date": "2026-05-05", "daily_pnl": -14682.0},
                )

        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], 12414.0)
        self.assertEqual(status["broker_sell_count"], 1)
        self.assertEqual(status["matched_local_count"], 1)
        self.assertEqual(status["source"], "broker_truth_confirmed_local_pnl")

    def test_current_session_realized_pnl_dedupes_same_ticker_local_closes_when_broker_fills_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            (state_dir / "live_decisions.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:30:20+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030650645",
                                "pnl_krw": -12372.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:30:55+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "CRCL",
                                "qty": 1,
                                "order_no": "0030651849",
                                "pnl_krw": 12414.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:58:47+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030699267",
                                "pnl_krw": -14724.0,
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            def fake_runtime_path(*parts, make_parents=True):
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None), patch.object(
                dashboard_server, "get_runtime_path", side_effect=fake_runtime_path
            ), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"session_active": True, "trading_date": "2026-05-05", "daily_pnl": -14682.0},
                )

        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], -2310.0)
        self.assertEqual(status["duplicate_tickers"], ["EAT"])
        self.assertEqual(status["local_closed_count"], 3)
        self.assertEqual(status["deduped_closed_count"], 2)
        self.assertEqual(status["source"], "local_decisions_duplicate_sell_deduped")

    def test_current_session_realized_pnl_dedupes_when_fresh_broker_fills_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir()
            now = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
            (state_dir / "live_broker_truth_snapshot.json").write_text(
                json.dumps(
                    {
                        "markets": {
                            "US": {
                                "missing": False,
                                "stale": False,
                                "last_success_at": now,
                                "ttl_sec": 3600,
                                "error": "",
                                "today_fills": [],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (state_dir / "live_decisions.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:30:20+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030650645",
                                "pnl_krw": -12372.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:30:55+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "CRCL",
                                "qty": 1,
                                "order_no": "0030651849",
                                "pnl_krw": 12414.0,
                            }
                        ),
                        json.dumps(
                            {
                                "type": "closed",
                                "timestamp": "2026-05-05T22:58:47+09:00",
                                "session_date": "2026-05-05",
                                "market": "US",
                                "ticker": "EAT",
                                "qty": 1,
                                "order_no": "0030699267",
                                "pnl_krw": -14724.0,
                            }
                        ),
                    ]
                ),
                encoding="utf-8",
            )

            def fake_runtime_path(*parts, make_parents=True):
                path = root.joinpath(*parts)
                if make_parents:
                    path.parent.mkdir(parents=True, exist_ok=True)
                return path

            with patch.object(dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None), patch.object(
                dashboard_server, "get_runtime_path", side_effect=fake_runtime_path
            ), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
            ):
                status = dashboard_server._current_session_realized_pnl_status(
                    "US",
                    "live",
                    live={"session_active": True, "trading_date": "2026-05-05", "daily_pnl": -14682.0},
                )

        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], -2310.0)
        self.assertEqual(status["duplicate_tickers"], ["EAT"])
        self.assertEqual(status["source"], "local_decisions_duplicate_sell_deduped")

    def test_current_session_realized_pnl_live_fallback_does_not_call_load_today(self) -> None:
        with patch.object(dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
        ), patch.object(
            dashboard_server, "load_today", side_effect=AssertionError("load_today should not be called")
        ):
            status = dashboard_server._current_session_realized_pnl_status(
                "US",
                "live",
                live={
                    "market": "US",
                    "session_active": True,
                    "trading_date": "2026-05-05",
                    "daily_pnl": -250.0,
                    "market_realized_pnl_krw": -125.0,
                },
            )

        self.assertTrue(status["available"])
        self.assertEqual(status["pnl_krw"], -125.0)
        self.assertEqual(status["source"], "live_status_market_realized_pnl")

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
            res = app.test_client().get("/api/history/equity?market=US&mode=live&refresh=1")

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
        ), patch.object(
            dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ):
            res = app.test_client().get(
                "/api/history/equity?market=US&mode=live&period=custom&start=2026-04-29&end=2026-04-30&refresh=1"
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

    def test_history_equity_live_exposes_account_total_series_for_chart(self) -> None:
        broker = {
            "source": "broker",
            "cumulative": 5_300_000.0,
            "kr_cash_effective": 2_000_000.0,
            "kr_cash": 2_000_000.0,
            "kr_eval": 0.0,
            "us_asset_krw": 3_300_000.0,
            "us_eval_krw": 300_000.0,
            "unrealized_krw": {"US": 10_000.0, "KR": 0.0},
        }

        def session_date(market: str, *_args, **_kwargs):
            return date(2026, 5, 14) if market == "US" else date(2026, 5, 15)

        with patch.object(
            dashboard_server, "_session_trade_date", side_effect=session_date
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value=broker
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=[]
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=[]
        ):
            res = app.test_client().get("/api/history/equity?market=US&mode=live&period=month&refresh=1")

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["equity"][-1], 3_300_000.0)
        self.assertEqual(data["account_equity_krw"][-1], 5_300_000.0)
        self.assertEqual(data["account_equity_scope"], "account_total")

    def test_history_equity_live_adds_active_session_realized_adjustment(self) -> None:
        snapshots = [
            {
                "market": "US",
                "date": "2026-05-05",
                "asset_krw": 1_000_000,
                "unrealized_krw": 4_000,
            }
        ]

        live = {
            "market": "US",
            "session_active": True,
            "trading_date": "2026-05-05",
            "daily_pnl": -14_000,
        }

        with patch.object(
            dashboard_server, "_session_trade_date", return_value=date(2026, 5, 5)
        ), patch.object(
            dashboard_server, "_broker_snapshot", return_value={}
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", return_value=[]
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=snapshots
        ), patch.object(
            dashboard_server, "_load_live_status", return_value=live
        ), patch.object(
            dashboard_server, "_is_fresh_live_status", return_value=True
        ), patch.object(
            dashboard_server, "load_today", return_value={"date": "2026-05-05", "market": "US"}
        ), patch.object(
            dashboard_server, "_current_session_period_profit_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ):
            res = app.test_client().get(
                "/api/history/equity?market=US&mode=live&period=custom&start=2026-05-05&end=2026-05-05&refresh=1"
            )

        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertEqual(data["realized_pnl_krw"], [-14_000.0])
        self.assertEqual(data["unrealized_today_delta_krw"], [4_000.0])
        self.assertEqual(data["trading_pnl_krw"], [-10_000.0])
        self.assertEqual(data["pnl"], [-0.9901])

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
            res = app.test_client().get("/api/chart/equity?market=KR&mode=live&period=3month&refresh=1")

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

    def test_runtime_events_exclude_same_kst_day_previous_us_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system_dir = Path(tmp)
            (system_dir / "live_trading_20260508.log").write_text(
                "2026-05-08 00:30:00 [INFO] bot | [PAPER BUY] AMD 1@120.0 | gap | prior\n"
                "2026-05-08 22:31:00 [INFO] bot | [PAPER BUY] AAPL 1@190.5 | gap | live\n",
                encoding="utf-8",
            )

            with patch.object(dashboard_server, "SYSTEM_LOG_DIR", system_dir), patch.object(
                dashboard_server, "_session_trade_date", return_value=date(2026, 5, 8)
            ):
                events = dashboard_server._parse_runtime_events("US", mode="live")

        self.assertEqual([event["ticker"] for event in events], ["AAPL"])

    def test_us_session_window_excludes_same_kst_day_previous_session(self) -> None:
        self.assertFalse(
            dashboard_server._log_ts_in_session_window("US", "2026-05-08T00:30:00", "2026-05-08")
        )
        self.assertTrue(
            dashboard_server._log_ts_in_session_window("US", "2026-05-08T22:31:00", "2026-05-08")
        )
        self.assertTrue(
            dashboard_server._log_ts_in_session_window("US", "2026-05-09T05:03:00", "2026-05-08")
        )

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
