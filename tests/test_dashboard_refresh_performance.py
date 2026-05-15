from __future__ import annotations

import json
import os
import tempfile
from contextlib import ExitStack
from datetime import date
from pathlib import Path
import unittest
from unittest.mock import patch

from dashboard import dashboard_server


class DashboardRefreshPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        dashboard_server.app.config["TESTING"] = True
        dashboard_server._BROKER_REFRESH_LAST_TS.clear()
        dashboard_server._BROKER_REFRESH_STATUS.clear()
        dashboard_server._BROKER_SNAPSHOT_CACHE.clear()
        dashboard_server._BROKER_SNAPSHOT_STATUS.clear()
        dashboard_server._BROKER_POSITIONS_CACHE.clear()
        dashboard_server._BROKER_POSITIONS_STATUS.clear()
        dashboard_server._BROKER_TRADE_BUNDLE_CACHE.clear()
        dashboard_server._STATE_JSON_CACHE.clear()

    def tearDown(self) -> None:
        dashboard_server._BROKER_REFRESH_LAST_TS.clear()
        dashboard_server._BROKER_REFRESH_STATUS.clear()
        dashboard_server._BROKER_SNAPSHOT_CACHE.clear()
        dashboard_server._BROKER_SNAPSHOT_STATUS.clear()
        dashboard_server._BROKER_POSITIONS_CACHE.clear()
        dashboard_server._BROKER_POSITIONS_STATUS.clear()
        dashboard_server._BROKER_TRADE_BUNDLE_CACHE.clear()
        dashboard_server._STATE_JSON_CACHE.clear()

    def test_broker_snapshot_fast_returns_no_cache_without_kis_call(self) -> None:
        with patch.object(dashboard_server, "_get_usd_krw_cached", return_value=1350.0), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value={}
        ), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=AssertionError("fast path must not call KIS")
        ):
            payload = dashboard_server._broker_snapshot_fast("live")

        self.assertEqual(payload["source"], "no_cache")
        self.assertFalse(payload["cache"]["hit"])
        self.assertTrue(payload["cache"]["stale"])
        self.assertEqual(payload["cumulative"], 0.0)

    def test_broker_snapshot_fast_rereads_truth_file_when_memory_cache_is_stale(self) -> None:
        old_ts = dashboard_server._time.time() - 120
        dashboard_server._BROKER_SNAPSHOT_CACHE["live"] = {
            "ts": old_ts,
            "value": {"source": "broker", "cumulative": 1_000.0, "kr_cash": 1_000.0, "cache": {}},
        }
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "account_summary": {"cash": 2_000.0, "orderable_cash": 2_000.0, "total_eval": 0.0, "total_profit": 0.0},
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": False,
                },
                "US": {
                    "account_summary": {"cash": 0.0, "orderable_cash": 0.0, "total_eval": 0.0, "total_profit": 0.0},
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": False,
                },
            },
        }

        with patch.dict(os.environ, {"DASHBOARD_BROKER_SNAPSHOT_CACHE_SEC": "20"}), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth
        ), patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1350.0
        ), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=AssertionError("fast stale reread must not call KIS")
        ):
            payload = dashboard_server._broker_snapshot_fast("live")

        self.assertEqual(payload["source"], "broker_truth_snapshot")
        self.assertEqual(payload["cumulative"], 2_000.0)
        self.assertEqual(dashboard_server._BROKER_SNAPSHOT_CACHE["live"]["value"]["cumulative"], 2_000.0)

    def test_broker_snapshot_fast_falls_back_to_stale_memory_when_truth_unavailable(self) -> None:
        old_ts = dashboard_server._time.time() - 120
        dashboard_server._BROKER_SNAPSHOT_CACHE["live"] = {
            "ts": old_ts,
            "value": {"source": "broker", "cumulative": 1_234.0, "kr_cash": 1_234.0, "cache": {}},
        }

        with patch.dict(os.environ, {"DASHBOARD_BROKER_SNAPSHOT_CACHE_SEC": "20"}), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value={}
        ), patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1350.0
        ), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=AssertionError("fast stale fallback must not call KIS")
        ):
            payload = dashboard_server._broker_snapshot_fast("live")

        self.assertEqual(payload["cumulative"], 1_234.0)
        self.assertTrue(payload["cache"]["stale"])
        self.assertEqual(payload["cache"]["source"], "stale_cache_file_refresh_failed")

    def test_broker_refresh_restores_stale_cache_when_refresh_raises(self) -> None:
        old_ts = dashboard_server._time.time() - 120
        dashboard_server._BROKER_SNAPSHOT_CACHE["live"] = {
            "ts": old_ts,
            "value": {"source": "broker", "cumulative": 1_234.0, "kr_cash": 1_234.0, "cache": {}},
            "meta": {"stale": False, "source": "broker"},
        }

        with patch.dict(
            os.environ,
            {
                "DASHBOARD_BROKER_REFRESH_COOLDOWN_SEC": "0",
                "DASHBOARD_BROKER_SNAPSHOT_CACHE_SEC": "20",
            },
        ), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=RuntimeError("kis down")
        ), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value={}
        ):
            result = dashboard_server._broker_snapshot_refresh("live", force=True)

        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "error")
        self.assertEqual(result["snapshot"]["cumulative"], 1_234.0)
        self.assertEqual(result["snapshot"]["cache"]["source"], "stale_cache_file_refresh_failed")
        self.assertEqual(dashboard_server._BROKER_SNAPSHOT_CACHE["live"]["value"]["cumulative"], 1_234.0)
        self.assertEqual(dashboard_server._BROKER_REFRESH_STATUS["live"]["error"], "kis down")

    def test_broker_snapshot_cache_hit_keeps_stale_truth_meta(self) -> None:
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "account_summary": {
                        "cash": 1_000.0,
                        "orderable_cash": 1_000.0,
                        "total_eval": 0.0,
                        "total_profit": 0.0,
                    },
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": True,
                    "error": "snapshot lag",
                },
                "US": {
                    "account_summary": {
                        "cash": 0.0,
                        "orderable_cash": 0.0,
                        "total_eval": 0.0,
                        "total_profit": 0.0,
                    },
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": False,
                },
            },
        }

        with patch.dict(os.environ, {"DASHBOARD_BROKER_SNAPSHOT_CACHE_SEC": "20"}), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth
        ) as load_truth, patch.object(
            dashboard_server, "_get_usd_krw_cached", return_value=1350.0
        ), patch.object(
            dashboard_server, "get_kis_profile_summary", side_effect=AssertionError("cache hit must not call KIS")
        ):
            first = dashboard_server._broker_snapshot_fast("live")
            second = dashboard_server._broker_snapshot_fast("live")
            third = dashboard_server._broker_snapshot("live")

        self.assertEqual(load_truth.call_count, 1)
        self.assertTrue(first["cache"]["stale"])
        self.assertEqual(first["cache"]["source"], "broker_truth_snapshot")
        self.assertTrue(second["cache"]["stale"])
        self.assertEqual(second["cache"]["source"], "cache")
        self.assertIn("KR: snapshot lag", second["cache"]["last_error"])
        self.assertTrue(third["cache"]["stale"])
        self.assertIn("KR: snapshot lag", third["cache"]["last_error"])
        status = dashboard_server._broker_snapshot_status("live")
        self.assertTrue(status["stale"])
        self.assertIn("KR: snapshot lag", status["last_error"])

    def test_broker_positions_fast_rereads_truth_file_when_memory_cache_is_stale(self) -> None:
        old_ts = dashboard_server._time.time() - 120
        dashboard_server._BROKER_POSITIONS_CACHE[("live", "US")] = {
            "ts": old_ts,
            "value": [{"ticker": "OLD", "qty": 1}],
            "meta": {"stale": True, "source": "stale_cache"},
        }
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "US": {
                    "account_summary": {"cash": 0.0, "orderable_cash": 0.0, "total_eval": 100.0, "total_profit": 0.0},
                    "last_success_at": now_iso,
                    "positions": [{"ticker": "NEW", "qty": 2, "avg_price": 10.0, "current_price": 11.0}],
                    "stale": False,
                }
            },
        }

        with patch.dict(os.environ, {"DASHBOARD_BROKER_POSITIONS_CACHE_SEC": "20"}), patch.object(
            dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth
        ), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=AssertionError("positions fast stale reread must not call KIS")
        ):
            positions = dashboard_server._load_broker_positions_fast("US", mode="live")

        self.assertEqual([row["ticker"] for row in positions], ["NEW"])
        status = dashboard_server._broker_positions_status("live", "US")
        self.assertEqual(status["source"], "broker_truth_snapshot")
        self.assertFalse(status["stale"])

    def test_broker_positions_fast_keeps_stale_truth_positions_on_refresh_error(self) -> None:
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "account_summary": {
                        "cash": 1_000_000.0,
                        "orderable_cash": 1_000_000.0,
                        "total_eval": 70_000.0,
                        "total_profit": 1_000.0,
                    },
                    "last_success_at": now_iso,
                    "positions": [{"ticker": "005930", "qty": 1, "avg_price": 69_000.0, "current_price": 70_000.0}],
                    "stale": True,
                    "error": "KIS outage",
                    "source": "broker_error_previous_snapshot",
                }
            },
        }

        with patch.object(dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth), patch.object(
            dashboard_server, "_broker_snapshot", side_effect=AssertionError("positions fast path must not call KIS")
        ):
            positions = dashboard_server._load_broker_positions_fast("KR", mode="live")

        self.assertEqual([row["ticker"] for row in positions], ["005930"])
        status = dashboard_server._broker_positions_status("live", "KR")
        self.assertEqual(status["source"], "broker_truth_snapshot")
        self.assertTrue(status["stale"])
        self.assertEqual(status["last_error"], "KIS outage")

    def test_broker_positions_fast_rejects_error_truth_without_payload(self) -> None:
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "missing": False,
                    "account_summary": {"cash": 0.0, "orderable_cash": 0.0, "total_eval": 0.0},
                    "last_success_at": "",
                    "positions": [],
                    "stale": True,
                    "error": "KIS outage",
                }
            },
        }

        with patch.object(dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth):
            positions = dashboard_server._load_broker_positions_fast("KR", mode="live")

        self.assertIsNone(positions)
        status = dashboard_server._broker_positions_status("live", "KR")
        self.assertFalse(status["hit"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["last_error"], "KIS outage")

    def test_broker_positions_fast_keeps_empty_last_success_truth_on_refresh_error(self) -> None:
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "missing": False,
                    "account_summary": {"cash": 0.0, "orderable_cash": 0.0, "total_eval": 0.0},
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": True,
                    "error": "KIS outage",
                }
            },
        }

        with patch.object(dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth):
            positions = dashboard_server._load_broker_positions_fast("KR", mode="live")

        self.assertEqual(positions, [])
        status = dashboard_server._broker_positions_status("live", "KR")
        self.assertTrue(status["hit"])
        self.assertTrue(status["stale"])
        self.assertEqual(status["last_error"], "KIS outage")

    def test_broker_truth_error_with_account_summary_is_usable_stale_payload(self) -> None:
        now_iso = dashboard_server.datetime.now(dashboard_server.KST).isoformat()
        truth = {
            "generated_at": now_iso,
            "markets": {
                "KR": {
                    "missing": False,
                    "account_summary": {"cash": 1_000_000.0, "orderable_cash": 900_000.0, "total_eval": 0.0},
                    "last_success_at": now_iso,
                    "positions": [],
                    "stale": True,
                    "error": "KIS outage",
                }
            },
        }

        with patch.object(dashboard_server, "_load_broker_truth_snapshot_cached", return_value=truth):
            data, meta = dashboard_server._broker_truth_market_with_meta("KR", "live")

        self.assertIsNotNone(data)
        self.assertTrue(meta["hit"])
        self.assertTrue(meta["stale"])
        self.assertEqual(meta["last_error"], "KIS outage")

    def test_broker_snapshot_has_account_rejects_all_zero_truth_snapshot(self) -> None:
        empty_truth = dashboard_server._empty_broker_snapshot("broker_truth_snapshot")
        positive_truth = {**empty_truth, "kr_cash": 1.0}

        self.assertFalse(dashboard_server._broker_snapshot_has_account(empty_truth))
        self.assertTrue(dashboard_server._broker_snapshot_has_account(positive_truth))

    def test_state_json_parse_failure_falls_back_to_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_status.json"
            path.write_text('{"ok": true}', encoding="utf-8")
            first = dashboard_server._read_json_state_cached(path, {}, category="test_state")

            path.write_text('{"ok": ', encoding="utf-8")
            second = dashboard_server._read_json_state_cached(path, {}, category="test_state")

            dashboard_server._STATE_JSON_CACHE.clear()
            third = dashboard_server._read_json_state_cached(path, {"fallback": True}, category="test_state")

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["_state_cache_stale"])
        self.assertEqual(second["_state_cache_source"], "state_parse_error_stale_cache")
        self.assertTrue(third["fallback"])
        self.assertEqual(third["_state_cache_source"], "state_parse_error_no_cache")

    def test_live_summary_uses_cached_read_path_without_blocking_broker_calls(self) -> None:
        session = date(2026, 5, 15)
        record = {
            "date": session.isoformat(),
            "market": "KR",
            "actual_result": {"pnl_krw": 123.0, "pnl_pct": 0.1, "cumulative": 1_000_000.0, "trades": 0},
            "consensus": {"mode": "TEST", "size": 0},
            "tickers": [],
            "universe_tickers": [],
        }
        live = {
            "updated_at": "2026-05-15T09:00:00+09:00",
            "mode": "TEST",
            "total_equity": 1_000_000.0,
            "cash": 1_000_000.0,
            "daily_pnl": 123.0,
            "daily_pnl_pct": 0.1,
            "positions": [],
            "pending_orders": [],
        }
        equity = {
            "trading_pnl_krw": [123.0],
            "pnl": [0.1],
            "unrealized_today_delta_krw": [0.0],
            "cumulative_trading_pnl_krw": [123.0],
            "cash_flow_krw": [0.0],
            "cumulative_cash_flow_krw": [0.0],
            "starting_asset_krw": 1_000_000.0,
            "starting_capital_krw": 1_000_000.0,
        }
        no_cache = dashboard_server._empty_broker_snapshot("no_cache")

        with ExitStack() as stack:
            stack.enter_context(patch.object(dashboard_server, "_session_trade_date", return_value=session))
            stack.enter_context(patch.object(dashboard_server, "load_records", return_value=[record]))
            stack.enter_context(patch.object(dashboard_server, "load_today", return_value=record))
            stack.enter_context(patch.object(dashboard_server, "_load_live_status", return_value=live))
            stack.enter_context(patch.object(dashboard_server, "_is_fresh_live_status", return_value=True))
            stack.enter_context(patch.object(dashboard_server, "_load_broker_positions_fast", return_value=[]))
            stack.enter_context(patch.object(dashboard_server, "_broker_snapshot_fast", return_value=no_cache))
            stack.enter_context(patch.object(dashboard_server, "_live_equity_payload_fast", return_value=equity))
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_current_session_trade_turnover",
                    return_value={"total_krw": 0, "buy_krw": 0, "sell_krw": 0, "fill_count": 0},
                )
            )
            stack.enter_context(patch.object(dashboard_server, "_current_risk_snapshot", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_load_claude_control", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_ticker_name_map", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_today_signal_digest", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_ml_db_digest", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_adaptive_param_digest", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_count_today_entries", return_value=0))
            stack.enter_context(patch.object(dashboard_server, "_max_daily_entries_for_market", return_value=40))
            stack.enter_context(patch.object(dashboard_server, "_session_status", return_value={}))
            stack.enter_context(patch.object(dashboard_server, "_live_position_context_for_market", return_value=[]))
            stack.enter_context(
                patch.object(dashboard_server, "_broker_snapshot", side_effect=AssertionError("summary live path must not call KIS"))
            )
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_load_broker_positions",
                    side_effect=AssertionError("summary live path must not block on broker positions"),
                )
            )
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_broker_realized_pnl_krw",
                    side_effect=AssertionError("summary live path must not fetch broker trades"),
                )
            )
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_broker_trade_rows_with_pnl",
                    side_effect=AssertionError("summary live path must not fetch broker trade rows"),
                )
            )
            stack.enter_context(
                patch.object(
                    dashboard_server,
                    "_lifetime_realized_pnl_summary",
                    side_effect=AssertionError("summary live path must not refresh lifetime broker trades"),
                )
            )
            response = dashboard_server.app.test_client().get("/api/summary?market=KR&mode=live")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["today"]["realized_pnl_source"], "live_status_cached")
        self.assertEqual(payload["today"]["performance_basis"], "cached_state")
        self.assertEqual(payload["today"]["broker_cache_source"], "no_cache")
        self.assertEqual(payload["today"]["account_asset_krw"], 1_000_000)
        self.assertEqual(payload["today"]["account_asset_source"], "internal_fallback")

    def test_lifetime_realized_endpoint_fetches_broker_summary_on_demand(self) -> None:
        summary = {
            "basis": "broker_fills_fifo_excluding_cash_flow",
            "source": "broker_trade_rows_with_pnl",
            "KR": {"pnl_krw": 1_000.0, "known_sell_count": 1, "sell_count": 1, "unknown_cost_basis_count": 0},
            "US": {"pnl_krw": 2_000.0, "known_sell_count": 1, "sell_count": 1, "unknown_cost_basis_count": 0},
            "kr_pnl_krw": 1_000.0,
            "us_pnl_krw": 2_000.0,
            "total_pnl_krw": 3_000.0,
            "known_sell_count": 2,
            "sell_count": 2,
            "unknown_cost_basis_count": 0,
            "errors": {},
        }
        with patch.object(dashboard_server, "_lifetime_realized_pnl_summary", return_value=dict(summary)) as fetch_summary:
            response = dashboard_server.app.test_client().post(
                "/api/pnl/lifetime-realized",
                json={"mode": "live"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        fetch_summary.assert_called_once_with("live")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["lifetime_realized"]["total_pnl_krw"], 3_000.0)
        self.assertEqual(payload["lifetime_realized"]["request_source"], "dashboard_on_demand")
        self.assertIn("fetched_at", payload["lifetime_realized"])

    def test_lifetime_realized_endpoint_force_clears_trade_cache_for_mode(self) -> None:
        dashboard_server._BROKER_TRADE_BUNDLE_CACHE[("live", "KR", "all", "", "")] = {"ts": 1, "value": {}}
        dashboard_server._BROKER_TRADE_BUNDLE_CACHE[("paper", "KR", "all", "", "")] = {"ts": 1, "value": {}}
        summary = dashboard_server._empty_lifetime_realized_pnl_summary()

        with patch.object(dashboard_server, "_lifetime_realized_pnl_summary", return_value=summary):
            response = dashboard_server.app.test_client().post(
                "/api/pnl/lifetime-realized",
                json={"mode": "live", "force": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn(("live", "KR", "all", "", ""), dashboard_server._BROKER_TRADE_BUNDLE_CACHE)
        self.assertIn(("paper", "KR", "all", "", ""), dashboard_server._BROKER_TRADE_BUNDLE_CACHE)

    def test_live_equity_endpoints_default_to_fast_payload(self) -> None:
        fast_payload = {
            "labels": ["2026-05-15"],
            "equity": [1_000_000.0],
            "pnl": [0.0],
            "wins": [False],
            "modes": [""],
            "performance_basis": "cached_state",
        }

        with patch.object(dashboard_server, "_live_equity_payload_fast", return_value=fast_payload) as fast, patch.object(
            dashboard_server, "_live_equity_payload", side_effect=AssertionError("default live equity path must not block")
        ):
            chart = dashboard_server.app.test_client().get("/api/chart/equity?market=KR&mode=live&period=3month")
            history = dashboard_server.app.test_client().get("/api/history/equity?market=KR&mode=live&period=3month")

        self.assertEqual(chart.status_code, 200)
        self.assertEqual(history.status_code, 200)
        self.assertEqual(fast.call_count, 2)
        self.assertEqual(chart.get_json()["performance_basis"], "cached_state")
        self.assertEqual(history.get_json()["performance_basis"], "cached_state")

    def test_live_equity_fast_includes_current_session_realized_pnl(self) -> None:
        session = date(2026, 5, 15)
        broker = {
            "source": "cache",
            "kr_cash_effective": 1_000_000.0,
            "kr_cash": 1_000_000.0,
            "kr_eval": 0.0,
            "cumulative": 1_000_000.0,
            "unrealized_krw": {"KR": 5_000.0, "US": 0.0},
            "cache": {"hit": True, "stale": False, "source": "cache"},
        }
        live = {
            "market": "KR",
            "trading_date": "2026-05-15",
            "market_realized_pnl_krw": 12_000.0,
        }

        with patch.object(dashboard_server, "_session_trade_date", return_value=session), patch.object(
            dashboard_server, "_broker_snapshot_fast", return_value=broker
        ), patch.object(
            dashboard_server, "_load_broker_equity_snapshots", return_value=[]
        ), patch.object(
            dashboard_server, "_load_live_status", return_value=live
        ), patch.object(
            dashboard_server, "_broker_today_fill_fifo_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_confirmed_local_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_deduped_local_session_realized_pnl", return_value=None
        ), patch.object(
            dashboard_server, "_broker_trade_rows_with_pnl", side_effect=AssertionError("fast path must not load broker trades")
        ), patch.object(
            dashboard_server, "_persist_broker_equity_snapshot"
        ) as persist:
            payload = dashboard_server._live_equity_payload_fast(
                "KR",
                "custom",
                "2026-05-15",
                "2026-05-15",
                mode="live",
            )

        self.assertEqual(payload["labels"], ["2026-05-15"])
        self.assertEqual(payload["realized_pnl_krw"], [12_000.0])
        self.assertEqual(payload["unrealized_today_delta_krw"], [5_000.0])
        self.assertEqual(payload["trading_pnl_krw"], [17_000.0])
        self.assertEqual(payload["cumulative_trading_pnl_krw"], [17_000.0])
        persist.assert_called_once_with(broker, mode="live", markets={"KR"})

    def test_broker_equity_persist_market_filter_does_not_zero_other_market(self) -> None:
        session = date(2026, 5, 15)
        broker = {
            "source": "cache",
            "kr_cash_effective": 1_000_000.0,
            "kr_cash": 1_000_000.0,
            "kr_eval": 0.0,
            "us_asset_krw": 0.0,
            "us_asset_cash_krw": 0.0,
            "us_eval_krw": 0.0,
            "unrealized_krw": {"KR": 0.0, "US": 0.0},
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_broker_equity_history.jsonl"
            path.write_text(
                '{"market":"US","date":"2026-05-15","asset_krw":2000000.0}\n',
                encoding="utf-8",
            )
            with patch.object(dashboard_server, "_broker_equity_history_path", return_value=path), patch.object(
                dashboard_server, "_session_trade_date", return_value=session
            ):
                dashboard_server._persist_broker_equity_snapshot(broker, mode="live", markets={"KR"})

            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        rows_by_market = {row["market"]: row for row in rows}
        self.assertEqual(rows_by_market["US"]["asset_krw"], 2_000_000.0)
        self.assertEqual(rows_by_market["KR"]["asset_krw"], 1_000_000.0)

    def test_live_equity_refresh_flag_keeps_explicit_blocking_refresh_path(self) -> None:
        refresh_payload = {"labels": ["refresh"], "equity": [2_000_000.0]}

        with patch.object(dashboard_server, "_live_equity_payload", return_value=refresh_payload) as refresh, patch.object(
            dashboard_server, "_live_equity_payload_fast", side_effect=AssertionError("refresh=1 must use explicit refresh path")
        ):
            response = dashboard_server.app.test_client().get("/api/chart/equity?market=KR&mode=live&period=3month&refresh=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["labels"], ["refresh"])
        refresh.assert_called_once()

    def test_today_page_does_not_block_load_all_on_price_refresh(self) -> None:
        response = dashboard_server.app.test_client().get("/")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertNotIn("refreshPrices(MARKET).then(() => loadMonitorTickers()).catch(() => {});", body)
        self.assertNotIn("await refreshPrices(MARKET);\n  await Promise.all", body)
        self.assertIn('id="broker-refresh-btn"', body)
        self.assertIn("refreshBrokerSnapshot", body)
        self.assertIn("/api/broker/refresh", body)
        load_all_body = body.split("async function loadAll()", 1)[1].split("}", 1)[0]
        self.assertNotIn("refreshBrokerSnapshot", load_all_body)

    def test_refresh_prices_default_returns_cached_status_without_kis(self) -> None:
        live = {
            "positions": [
                {"ticker": "005930", "current_price": 70000, "avg_price": 68000, "qty": 1},
            ]
        }

        with patch.object(dashboard_server, "_load_live_status", return_value=live), patch.object(
            dashboard_server, "get_access_token", side_effect=AssertionError("default refresh_prices must not call KIS")
        ), patch.object(
            dashboard_server, "get_price", side_effect=AssertionError("default refresh_prices must not call KIS")
        ):
            response = dashboard_server.app.test_client().post("/api/refresh_prices", json={"market": "KR", "mode": "live"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["source"], "live_status_cache")
        self.assertFalse(payload["live"])
        self.assertEqual(payload["positions"][0]["current_price"], 70000)

    def test_broker_refresh_enforces_server_side_cooldown(self) -> None:
        with patch.dict(os.environ, {"DASHBOARD_BROKER_REFRESH_COOLDOWN_SEC": "60"}), patch.object(
            dashboard_server, "_broker_snapshot", return_value={"source": "broker", "cache": {}}
        ) as refresh, patch.object(
            dashboard_server, "_broker_snapshot_fast", return_value={"source": "cache"}
        ):
            first = dashboard_server._broker_snapshot_refresh("live")
            second = dashboard_server._broker_snapshot_refresh("live")

        self.assertTrue(first["ok"])
        self.assertEqual(first["reason"], "refreshed")
        self.assertFalse(second["ok"])
        self.assertEqual(second["reason"], "cooldown")
        refresh.assert_called_once()

    def test_broker_refresh_api_returns_sanitized_payload(self) -> None:
        full_result = {
            "ok": True,
            "reason": "refreshed",
            "snapshot": {
                "source": "broker",
                "cache": {"hit": True, "stale": False, "age_sec": 0, "source": "cache"},
                "kis_profile": {"token_file": "state/live_kis_token.json", "app_key_fingerprint": "secret"},
                "kr_cash": 1_000_000,
                "us_cash_usd": 100.0,
                "positions": [{"ticker": "SECRET"}],
            },
        }

        with patch.object(dashboard_server, "_broker_snapshot_refresh", return_value=full_result):
            response = dashboard_server.app.test_client().post("/api/broker/refresh?mode=live")

        self.assertEqual(response.status_code, 200)
        text = response.get_data(as_text=True)
        payload = response.get_json()
        self.assertNotIn("snapshot", payload)
        self.assertIn("snapshot_meta", payload)
        for forbidden in ("kis_profile", "token_file", "app_key_fingerprint", "kr_cash", "us_cash_usd", "positions", "SECRET"):
            self.assertNotIn(forbidden, text)

    def test_broker_refresh_api_sanitizes_cooldown_and_pending_payloads(self) -> None:
        cooldown = {
            "ok": False,
            "reason": "cooldown",
            "retry_after_sec": 30,
            "snapshot": {"source": "broker", "cache": {}, "kis_profile": {"token_file": "secret"}, "kr_cash": 1},
        }
        pending = {
            "ok": False,
            "reason": "refresh_pending",
            "snapshot": {"source": "broker", "cache": {}, "positions": [{"ticker": "SECRET"}]},
        }
        client = dashboard_server.app.test_client()
        with patch.object(dashboard_server, "_broker_snapshot_refresh", return_value=cooldown):
            cooldown_response = client.post("/api/broker/refresh?mode=live")
        with patch.object(dashboard_server, "_broker_snapshot_refresh", return_value=pending):
            pending_response = client.post("/api/broker/refresh?mode=live")

        self.assertEqual(cooldown_response.status_code, 429)
        self.assertEqual(pending_response.status_code, 202)
        for response in (cooldown_response, pending_response):
            text = response.get_data(as_text=True)
            self.assertIn("snapshot_meta", response.get_json())
            self.assertNotIn("kis_profile", text)
            self.assertNotIn("token_file", text)
            self.assertNotIn("positions", text)
            self.assertNotIn("SECRET", text)

    def test_preopen_api_limits_and_compacts_default_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_load_preopen_dashboard(market: str, *, session_date: str, limit: int, mode: str) -> dict:
            captured.update({"market": market, "session_date": session_date, "limit": limit, "mode": mode})
            return {
                "market": market,
                "session_date": session_date,
                "summary": {"candidate_total_count": 500, "candidate_count": 500},
                "scheduler": {
                    "status": "active",
                    "last_tick_at": "2026-05-15T09:00:00+09:00",
                    "heartbeat_age_sec": 1,
                    "last_job": {"event": "ok", "market": "US", "kind": "collect", "raw": "x" * 1000},
                    "huge_raw": "x" * 1000,
                },
                "candidates": [
                    {"ticker": "A", "name": "A name", "shadow_preopen_rank": 1, "raw_payload": "x" * 1000},
                    {"ticker": "B", "name": "B name", "shadow_preopen_rank": 2, "digest_raw": "x" * 1000},
                ],
                "outcome_timeline": [
                    {"ticker": f"T{i}", "shadow_preopen_rank": i, "anchor_price": 10.0, "raw_payload": "x" * 1000}
                    for i in range(80)
                ],
                "state": {
                    "collector_status": "ok",
                    "candidates": [{"ticker": "BIG"}],
                    "raw_candidates": [{"ticker": "RAW"}],
                    "digest_prompt": "large prompt",
                    "digest_raw": {"large": True},
                },
                "outcome": [{"ticker": "A"}],
            }

        with patch("preopen.storage.load_preopen_dashboard", side_effect=fake_load_preopen_dashboard):
            response = dashboard_server.app.test_client().get("/api/preopen?market=US&mode=live&limit=999")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(captured["limit"], 200)
        self.assertEqual(payload["limit"], 200)
        self.assertEqual(payload["requested_limit"], 999)
        self.assertEqual(payload["returned_count"], 2)
        self.assertEqual(payload["total_count"], 500)
        self.assertTrue(payload["truncated"])
        self.assertEqual(payload["outcome"], [])
        self.assertEqual(payload["scheduler"]["status"], "active")
        self.assertNotIn("huge_raw", payload["scheduler"])
        self.assertNotIn("raw", payload["scheduler"]["last_job"])
        self.assertNotIn("raw_payload", payload["candidates"][0])
        self.assertNotIn("digest_raw", payload["candidates"][1])
        self.assertEqual(len(payload["outcome_timeline"]), 60)
        self.assertNotIn("raw_payload", payload["outcome_timeline"][0])
        self.assertEqual(payload["summary"]["outcome_display_count"], 60)
        self.assertNotIn("candidates", payload["state"])
        self.assertNotIn("raw_candidates", payload["state"])
        self.assertNotIn("digest_prompt", payload["state"])
        self.assertNotIn("digest_raw", payload["state"])

    def test_preopen_api_detail_full_preserves_raw_payload(self) -> None:
        captured: dict[str, object] = {}

        def fake_load_preopen_dashboard(market: str, *, session_date: str, limit: int, mode: str) -> dict:
            captured.update({"market": market, "session_date": session_date, "limit": limit, "mode": mode})
            return {
                "market": market,
                "session_date": session_date,
                "summary": {"candidate_total_count": 500, "candidate_count": 500},
                "scheduler": {"status": "active", "huge_raw": "x" * 1000},
                "candidates": [{"ticker": "A", "raw_payload": "x" * 1000}],
                "outcome_timeline": [{"ticker": f"T{i}", "raw_payload": "x" * 1000} for i in range(40)],
                "state": {
                    "candidates": [{"ticker": "BIG"}],
                    "raw_candidates": [{"ticker": "RAW"}],
                    "digest_prompt": "large prompt",
                    "digest_raw": {"large": True},
                },
                "outcome": [{"ticker": "A", "raw_payload": "keep"}],
            }

        with patch("preopen.storage.load_preopen_dashboard", side_effect=fake_load_preopen_dashboard):
            response = dashboard_server.app.test_client().get("/api/preopen?market=US&mode=live&limit=999&detail=full")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(captured["limit"], 200)
        self.assertEqual(payload["requested_limit"], 999)
        self.assertIn("huge_raw", payload["scheduler"])
        self.assertIn("raw_payload", payload["candidates"][0])
        self.assertEqual(len(payload["outcome_timeline"]), 40)
        self.assertIn("raw_payload", payload["outcome_timeline"][0])
        self.assertIn("candidates", payload["state"])
        self.assertIn("raw_candidates", payload["state"])
        self.assertIn("digest_prompt", payload["state"])
        self.assertIn("digest_raw", payload["state"])
        self.assertEqual(payload["outcome"], [{"ticker": "A", "raw_payload": "keep"}])


if __name__ == "__main__":
    unittest.main()
