from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore
from execution.sizing import FixedSizingResult
from runtime.candidate_quality_trainer import score_candidate_for_trainer
from trading_bot import KST, TradingBot, _mode_family


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))

    def get(self, key: str, default: object = "") -> object:
        return self.values.get(key, default)


class _DummyV2:
    def __init__(self) -> None:
        self.registered_meta: dict | None = None

    def register_trade_ready(self, market: str, meta: dict) -> dict[str, str]:
        self.registered_meta = dict(meta)
        return {ticker: f"dec_{ticker}" for ticker in meta.get("trade_ready", [])}

    def decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return f"dec_{ticker}"


class _DummyPathB:
    def __init__(self, active_run: dict | None = None) -> None:
        self.registered_meta: dict | None = None
        self.active_run = active_run
        self.cancelled: list[tuple[str, str, str]] = []
        self.broker_truth = None

    def register_from_selection_meta(self, market: str, meta: dict) -> list[str]:
        self.registered_meta = dict(meta)
        return ["path_wait"] if meta.get("_pathb_wait_tickers") else []

    def _active_path_for_ticker(self, market: str, ticker: str) -> dict | None:
        return self.active_run

    def cancel_waiting_for_ticker(self, market: str, ticker: str, *, reason: str) -> int:
        self.cancelled.append((market, ticker, reason))
        return 1


class _HealthTracker:
    def __init__(self, states: dict[str, dict] | None = None) -> None:
        self.states = states or {}

    def state_for(self, ticker: str) -> dict:
        key = str(ticker).upper()
        return dict(self.states.get(key, {"ticker": key, "health_state": "OBSERVE"}))


class _BrokerTruthProbe:
    def __init__(self, last_success_at: str) -> None:
        self.last_success_at = last_success_at
        self.snapshots: list[tuple[str, int | None]] = []

    def market_snapshot(self, market: str, ttl_sec: int | None = None) -> dict:
        self.snapshots.append((market, ttl_sec))
        return {"last_success_at": self.last_success_at}


class _PathBRefreshProbe:
    def __init__(self, last_success_at: str) -> None:
        self.broker_truth = _BrokerTruthProbe(last_success_at)
        self.refresh_calls: list[tuple[str, bool, int | None]] = []

    def refresh_broker_truth(self, market: str, *, force: bool = False, ttl_sec: int | None = None) -> dict:
        self.refresh_calls.append((market, force, ttl_sec))
        return {}


def _make_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.runtime_config = _RuntimeConfig(
        {
            "ENABLE_CLAUDE_CANDIDATE_ACTIONS": True,
            "ENABLE_ACTION_ROUTING": True,
            "ENABLE_ACTION_ROUTING_SHADOW": False,
            "ENABLE_UNIFIED_CANDIDATE_POOL_SHADOW": False,
            "ENABLE_ADD_READY_LIVE": False,
            "PROBE_SIZE_RATIO": 0.30,
            "ADD_SIZE_RATIO": 0.30,
            "PLANB_CANCEL_CONFIDENCE_MIN": 0.75,
            "KR_LATE_ENTRY_GATE_ENABLED": False,
            "KR_MICROSTRUCTURE_CONTEXT_ENABLED": False,
        }
    )
    bot.selection_meta = {"US": {}, "KR": {}}
    bot.selection_stages = {"US": {}, "KR": {}}
    bot.trade_ready_tickers = {"US": [], "KR": []}
    bot.today_judgment = {"market": "US", "consensus": {"mode": "BALANCED"}}
    bot.risk = type("Risk", (), {"positions": []})()
    bot.pending_orders = []
    bot.enable_continuation_live = True
    bot.enable_kr_momentum_shrink = False
    bot._data_insufficient_watch_tickers = {"US": set(), "KR": set()}
    bot._record_candidate_funnel_snapshot = lambda *args, **kwargs: None
    bot._v2_same_day_stop_tickers = {"US": set(), "KR": set()}
    bot._ticker_no_signal_minutes = {}
    bot._candidate_cohort_reliability_cache = {"market": "US", "cohorts": {}}
    bot._current_session_date_str = lambda market: "2026-05-07"
    bot._candidate_health_tracker = lambda market: _HealthTracker()
    bot._gate_events = []
    bot._write_funnel_event = lambda event_type, market, payload: bot._gate_events.append((event_type, market, payload))
    bot.v2 = _DummyV2()
    bot.pathb = _DummyPathB()
    return bot


class CandidateActionLiveMappingTests(unittest.TestCase):
    def test_balanced_mode_family_is_not_risk_off(self) -> None:
        self.assertEqual(_mode_family("BALANCED"), "BALANCED")
        self.assertEqual(_mode_family("NEUTRAL"), "BALANCED")

    def test_us_early_entry_soft_gate_uses_elapsed_minutes_not_kst_hour(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "US_EARLY_ENTRY_SOFT_GATE_ENABLED": True,
                "US_EARLY_ENTRY_SOFT_GATE_START_MIN": 0.0,
                "US_EARLY_ENTRY_SOFT_GATE_END_MIN": 60.0,
                "US_EARLY_ENTRY_SIZE_MULT": 0.5,
            }
        )
        bot._market_open_elapsed_min = lambda market, now_dt=None: 30.0

        gate = TradingBot._us_early_entry_soft_gate(bot, "US")

        self.assertTrue(gate["active"])
        self.assertEqual(gate["elapsed_min"], 30.0)
        self.assertEqual(gate["size_mult"], 0.5)

        bot._market_open_elapsed_min = lambda market, now_dt=None: 75.0
        self.assertFalse(TradingBot._us_early_entry_soft_gate(bot, "US")["active"])
        self.assertFalse(TradingBot._us_early_entry_soft_gate(bot, "KR")["active"])

    def test_record_ticker_selection_batch_uses_rescreen_source_and_consensus_mode(self) -> None:
        bot = _make_bot()
        bot._tsdb_selection_ids = {"KR": {}}

        with patch("trading_bot.tsdb.insert_batch", return_value={"005930": 17}) as insert_batch:
            row_ids = TradingBot._record_ticker_selection_batch(
                bot,
                "2026-05-19",
                "KR",
                "rescreen",
                ["005930"],
                [{"ticker": "005930", "change_pct": 1.2}],
                {"005930": "strong"},
                "DEFENSIVE",
                {"trade_ready": ["005930"]},
            )

        self.assertEqual(row_ids, {"005930": 17})
        self.assertEqual(bot._tsdb_selection_ids["KR"]["005930"], 17)
        args, kwargs = insert_batch.call_args
        self.assertEqual(args[:7], (
            "2026-05-19",
            "KR",
            "rescreen",
            ["005930"],
            [{"ticker": "005930", "change_pct": 1.2}],
            {"005930": "strong"},
            "DEFENSIVE",
        ))
        self.assertTrue(str(kwargs["batch_id"]).startswith("2026-05-19_KR_rescreen_"))
        self.assertEqual(kwargs["selection_meta"], {"trade_ready": ["005930"]})

    def test_run_rescreen_records_scheduled_source_type(self) -> None:
        bot = _make_bot()
        bot.session_active = True
        bot.current_market = "KR"
        bot._last_rescreen_at = {"KR": 0.0}
        calls: list[tuple[str, str, str]] = []

        def manual_rescreen(market: str, *, source_type: str, trigger: str) -> list[str]:
            calls.append((market, source_type, trigger))
            return ["005930"]

        bot.manual_rescreen = manual_rescreen

        TradingBot.run_rescreen(bot, "KR")

        self.assertEqual(calls, [("KR", "rescreen", "scheduled")])

    def test_broker_truth_refresh_skips_fresh_snapshot_and_refreshes_stale_once(self) -> None:
        bot = _make_bot()
        bot._broker_truth_refresh_at = {"KR": 0.0, "US": 0.0}

        fresh_pathb = _PathBRefreshProbe(datetime.now(KST).isoformat(timespec="seconds"))
        bot.pathb = fresh_pathb
        with patch.dict(os.environ, {"BROKER_TRUTH_REFRESH_INTERVAL_SEC": "120"}):
            refreshed = TradingBot._maybe_refresh_broker_truth_snapshot(bot, "KR", reason="test")

        self.assertFalse(refreshed)
        self.assertEqual(fresh_pathb.refresh_calls, [])
        self.assertEqual(fresh_pathb.broker_truth.snapshots, [("KR", 120)])

        stale_pathb = _PathBRefreshProbe("2026-01-01T00:00:00+09:00")
        bot.pathb = stale_pathb
        bot._broker_truth_refresh_at = {"KR": 0.0, "US": 0.0}
        with patch.dict(os.environ, {"BROKER_TRUTH_REFRESH_INTERVAL_SEC": "120"}):
            refreshed = TradingBot._maybe_refresh_broker_truth_snapshot(bot, "KR", reason="test")
            throttled = TradingBot._maybe_refresh_broker_truth_snapshot(bot, "KR", reason="test")

        self.assertTrue(refreshed)
        self.assertFalse(throttled)
        self.assertEqual(stale_pathb.refresh_calls, [("KR", True, 120)])

    def test_screener_filter_audit_writes_data_insufficient_shadow_row(self) -> None:
        bot = _make_bot()
        bot.is_paper = False
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        bot._current_session_date_str = lambda market: "2026-05-19"
        bot._candidate_audit_store_cache = None

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {"CANDIDATE_AUDIT_DB_PATH": str(Path(tmp) / "candidate_audit.db")},
        ):
            TradingBot._write_candidate_audit_screener_filter(
                bot,
                "KR",
                phase="session_open",
                rows=[
                    {
                        "ticker": "439960",
                        "name": "shadow",
                        "price": 1210.0,
                        "data_quality": "DATA_INSUFFICIENT_SHADOW",
                        "history_status": "DATA_INSUFFICIENT",
                        "history_usable_rows": 23,
                        "history_required_rows": 65,
                        "selection_bias": "shadow_only",
                    }
                ],
                reasons={"439960": "data_insufficient(23usable)"},
            )

            conn = sqlite3.connect(Path(tmp) / "candidate_audit.db")
            conn.row_factory = sqlite3.Row
            try:
                call = conn.execute("SELECT * FROM audit_claude_calls").fetchone()
                row = conn.execute("SELECT * FROM audit_candidate_rows").fetchone()
            finally:
                conn.close()

        self.assertIsNotNone(call)
        self.assertEqual(call["label"], "screener_filter")
        self.assertEqual(call["source_file"], "trading_bot.screener_filter")
        self.assertIsNotNone(row)
        self.assertEqual(row["ticker"], "439960")
        self.assertEqual(row["source_file"], "trading_bot.screener_filter")
        self.assertEqual(row["classification"], "data_insufficient")
        self.assertEqual(row["in_prompt"], 0)
        self.assertEqual(row["input_to_claude_reported"], 0)
        self.assertEqual(row["prompt_excluded_reason"], "data_insufficient(23usable)")
        self.assertEqual(row["data_quality"], "DATA_INSUFFICIENT_SHADOW")
        self.assertEqual(row["history_status"], "DATA_INSUFFICIENT")
        self.assertEqual(row["history_usable_rows"], 23)
        self.assertEqual(row["history_required_rows"], 65)

    def test_v2_fixed_sizing_applies_us_early_entry_budget_multiplier(self) -> None:
        fixed = FixedSizingResult(
            market="US",
            qty=10,
            budget_krw=100_000,
            order_cost_krw=100_000,
            min_order_krw=30_000,
            price_krw=10_000,
        )

        qty, budget, order_cost = TradingBot._v2_fixed_size_order_values(
            fixed,
            10_000,
            budget_multiplier=0.5,
        )

        self.assertEqual(qty, 5)
        self.assertEqual(budget, 50_000)
        self.assertEqual(order_cost, 50_000)

    def test_v2_fixed_sizing_soft_gate_does_not_reexpand_to_min_order(self) -> None:
        fixed = FixedSizingResult(
            market="US",
            qty=2,
            budget_krw=70_000,
            order_cost_krw=56_000,
            min_order_krw=42_000,
            price_krw=28_000,
        )

        qty, budget, order_cost = TradingBot._v2_fixed_size_order_values(
            fixed,
            28_000,
            budget_multiplier=0.5,
        )

        self.assertEqual(qty, 1)
        self.assertEqual(budget, 35_000)
        self.assertEqual(order_cost, 28_000)

    def test_probe_ready_maps_to_trade_ready_with_probe_cap(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["INTC", "FSLY"],
            "trade_ready": [],
            "candidate_actions": [
                {"ticker": "INTC", "action": "PROBE_READY", "confidence": 0.64, "reason": "early strength"},
                {"ticker": "FSLY", "action": "WATCH"},
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["INTC", "FSLY"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["INTC"])
        self.assertEqual(bot.trade_ready_tickers["US"], ["INTC"])
        self.assertEqual(meta["allocation_intent"]["INTC"], "probe")
        self.assertEqual(meta["max_order_cap_pct"]["INTC"], 30)
        self.assertIn("INTC", meta["_trade_ready_without_price_targets_allowed"])

    def test_inline_replacement_updates_selection_meta_and_persists(self) -> None:
        bot = _make_bot()
        bot.selection_meta["US"] = {
            "watchlist": ["AKAM", "MNST", "CTRA"],
            "trade_ready": ["AKAM", "MNST"],
            "_pathb_wait_tickers": ["AKAM"],
            "_runtime_filtered_trade_ready": {},
        }
        persisted = []
        bot._persist_live_judgment = lambda market: persisted.append(market)

        changed = TradingBot._selection_meta_apply_inline_replacement(
            bot,
            "US",
            "AKAM",
            "TERN",
            persist=True,
        )

        self.assertTrue(changed)
        meta = bot.selection_meta["US"]
        self.assertEqual(meta["watchlist"], ["TERN", "MNST", "CTRA"])
        self.assertEqual(meta["trade_ready"], ["MNST"])
        self.assertEqual(meta["_pathb_wait_tickers"], [])
        self.assertEqual(
            meta["_runtime_filtered_trade_ready"]["AKAM"],
            "inline_replacement_no_signal:TERN",
        )
        self.assertEqual(bot.trade_ready_tickers["US"], ["MNST"])
        self.assertEqual(bot.today_judgment["selection_meta"], meta)
        self.assertEqual(persisted, ["US"])

    def test_runtime_filtered_removes_active_trade_ready_candidate(self) -> None:
        bot = _make_bot()
        bot.selection_meta["US"] = {
            "watchlist": ["DKNG", "RKLB"],
            "trade_ready": ["DKNG", "RKLB"],
            "_pathb_wait_tickers": ["DKNG"],
            "_runtime_filtered_trade_ready": {},
        }
        persisted = []
        bot._persist_live_judgment = lambda market: persisted.append(market)

        changed = TradingBot._selection_meta_mark_runtime_filtered(
            bot,
            "US",
            "DKNG",
            "loss_cap_exited",
            remove_trade_ready=True,
            persist=True,
        )

        self.assertTrue(changed)
        meta = bot.selection_meta["US"]
        self.assertEqual(meta["trade_ready"], ["RKLB"])
        self.assertEqual(meta["_pathb_wait_tickers"], [])
        self.assertEqual(meta["_runtime_filtered_trade_ready"]["DKNG"], "loss_cap_exited")
        self.assertEqual(bot.trade_ready_tickers["US"], ["RKLB"])
        self.assertEqual(bot.today_judgment["selection_meta"], meta)
        self.assertEqual(persisted, ["US"])

    def test_pullback_wait_registers_only_pathb_wait_tickers(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["INTC", "GXO"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "GXO",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.72,
                    "price_targets": {
                        "buy_zone_low": 40.0,
                        "buy_zone_high": 41.0,
                        "sell_target": 44.0,
                        "stop_loss": 39.0,
                        "hold_days": 1,
                        "confidence": 0.72,
                    },
                }
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["INTC", "GXO"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_pathb_wait_tickers"], ["GXO"])
        self.assertEqual(meta["_pathb_wait_origins"]["GXO"]["origin_action"], "PULLBACK_WAIT")
        self.assertTrue(meta["_pathb_wait_origins"]["GXO"]["not_patha_trade_ready"])
        self.assertEqual(bot.pathb.registered_meta["_pathb_registration_scope"], "candidate_actions_wait_only")
        self.assertEqual(bot.pathb.registered_meta["_pathb_wait_tickers"], ["GXO"])
        self.assertEqual(
            bot.pathb.registered_meta["_pathb_wait_origins"]["GXO"]["origin_route"],
            "pathb_wait_only",
        )
        self.assertIn("GXO", bot.v2.registered_meta["trade_ready"])
        self.assertEqual(bot.v2.registered_meta["_pathb_wait_origins"]["GXO"]["origin_action"], "PULLBACK_WAIT")

    def test_pullback_wait_with_fade_context_does_not_register_pathb(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["IONQ"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "IONQ",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.72,
                    "price_targets": {
                        "buy_zone_low": 45.5,
                        "buy_zone_high": 46.8,
                        "sell_target": 49.5,
                        "stop_loss": 43.8,
                        "hold_days": 1,
                        "confidence": 0.72,
                    },
                }
            ],
            "_post_open_features_by_ticker": {
                "IONQ": {"ticker": "IONQ", "market": "US", "momentum_state": "fade", "data_quality": "good"}
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["IONQ"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_pathb_wait_tickers"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "pullback_wait_blocked_negative_context")
        self.assertEqual(route["runtime_gate_reason"], "negative_pullback_context")

    def test_buy_ready_records_passing_gate_evaluation(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["NVDA"],
            "trade_ready": [],
            "candidate_actions": [{"ticker": "NVDA", "action": "BUY_READY", "confidence": 0.82}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["NVDA"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["NVDA"])
        gate_events = [payload for event_type, _, payload in bot._gate_events if event_type == "gate_evaluation"]
        self.assertTrue(any(evt.get("ticker") == "NVDA" and evt.get("passed") is True for evt in gate_events))

    def test_quarantine_blocks_pathb_wait_before_registration(self) -> None:
        bot = _make_bot()
        bot._candidate_health_tracker = lambda market: _HealthTracker(
            {
                "IONQ": {
                    "ticker": "IONQ",
                    "health_state": "FAILED_READY",
                    "ready_count": 4,
                    "mae_pct": -8.8,
                    "mfe_pct": 0.0,
                    "current_vs_first_ready_pct": -8.8,
                }
            }
        )
        raw_meta = {
            "watchlist": ["IONQ"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "IONQ",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.7,
                    "price_targets": {
                        "buy_zone_low": 45.5,
                        "buy_zone_high": 46.8,
                        "sell_target": 49.5,
                        "stop_loss": 43.8,
                        "hold_days": 1,
                        "confidence": 0.7,
                    },
                }
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["IONQ"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_pathb_wait_tickers"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "HARD_BLOCK")
        self.assertEqual(route["blocker"], "candidate_quarantine")
        self.assertEqual(route["reason"], "failed_ready")
        gate_events = [payload for event_type, _, payload in bot._gate_events if event_type == "gate_evaluation"]
        self.assertTrue(any(evt.get("ticker") == "IONQ" and evt.get("passed") is False for evt in gate_events))

    def test_add_ready_without_position_is_not_trade_ready(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["AAPL"],
            "candidate_actions": [{"ticker": "AAPL", "action": "ADD_READY", "confidence": 0.9}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AAPL"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_candidate_action_routes"][0]["reason"], "add_without_position")

    def test_pathb_active_order_blocks_plana_buy(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB({"path_run_id": "run_1", "status": "ORDER_SENT"})
        raw_meta = {
            "watchlist": ["AAPL"],
            "_post_open_features_by_ticker": {"AAPL": {"ticker": "AAPL", "market": "US", "data_quality": "good"}},
            "candidate_actions": [{"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.95}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AAPL"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_candidate_action_routes"][0]["reason"], "pathb_active_order_blocks_plana")

    def test_probe_ready_above_existing_pathb_zone_is_not_trade_ready(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB(
            {
                "path_run_id": "run_hb",
                "status": "WAITING",
                "plan": {"buy_zone_low": 4300.0, "buy_zone_high": 4420.0},
            }
        )
        raw_meta = {
            "watchlist": ["078150"],
            "candidate_actions": [
                {
                    "ticker": "078150",
                    "action": "PROBE_READY",
                    "confidence": 0.58,
                    "price_targets": {"buy_zone_low": 4560.0, "buy_zone_high": 4660.0},
                }
            ],
            "_post_open_features_by_ticker": {
                "078150": {"ticker": "078150", "market": "KR", "current_price": 4655.0, "data_quality": "good"}
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["078150"], mode="MODERATE_BULL")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["reason"], "probe_blocked_above_pathb_zone")
        self.assertEqual(route["runtime_gate"]["pathb_waiting_buy_zone_high"], 4420.0)
        self.assertEqual(bot.pathb.cancelled, [])

    def test_negative_watch_records_pathb_suspend_shadow(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB(
            {
                "path_run_id": "run_kbi",
                "status": "WAITING",
                "plan": {"buy_zone_low": 8900.0, "buy_zone_high": 9100.0},
            }
        )
        raw_meta = {
            "watchlist": ["024840"],
            "candidate_actions": [{"ticker": "024840", "action": "WATCH", "reason": "fade 지속, 방향 미확인"}],
            "_post_open_features_by_ticker": {
                "024840": {"ticker": "024840", "market": "KR", "momentum_state": "fade", "data_quality": "good"}
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["024840"], mode="MODERATE_BULL")

        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["reason"], "watch_keeps_pathb_waiting_hysteresis")
        self.assertFalse(route["suspend_pathb"])
        self.assertTrue(route["pathb_suspend_shadow"])
        self.assertTrue(route["pathb_suspend_deferred"])
        self.assertEqual(route["pathb_suspend_path_run_id"], "run_kbi")

    def test_negative_watch_suspends_pathb_after_hysteresis_threshold(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB(
            {
                "path_run_id": "run_kbi",
                "status": "WAITING",
                "plan": {"buy_zone_low": 8900.0, "buy_zone_high": 9100.0},
            }
        )
        raw_meta = {
            "watchlist": ["024840"],
            "candidate_actions": [{"ticker": "024840", "action": "WATCH", "reason": "fade"}],
            "_post_open_features_by_ticker": {
                "024840": {"ticker": "024840", "market": "KR", "momentum_state": "fade", "data_quality": "good"}
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            TradingBot._apply_selection_meta(bot, "KR", ["024840"], mode="MODERATE_BULL")
            TradingBot._apply_selection_meta(bot, "KR", ["024840"], mode="MODERATE_BULL")
            meta = TradingBot._apply_selection_meta(bot, "KR", ["024840"], mode="MODERATE_BULL")

        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["reason"], "watch_suspends_stale_pathb")
        self.assertTrue(route["suspend_pathb"])
        self.assertFalse(route.get("pathb_suspend_deferred", False))

    def test_confident_buy_ready_cancels_pathb_waiting_before_plana(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB({"path_run_id": "run_1", "status": "WAITING"})
        raw_meta = {
            "watchlist": ["AAPL"],
            "_post_open_features_by_ticker": {"AAPL": {"ticker": "AAPL", "market": "US", "data_quality": "good"}},
            "candidate_actions": [{"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.95}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AAPL"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["AAPL"])
        self.assertEqual(bot.pathb.cancelled, [("US", "AAPL", "candidate_action_buy_ready")])
        self.assertEqual(meta["_candidate_action_routes"][0]["pathb_cancelled"], 1)

    def test_off_list_candidate_actions_are_hard_blocked(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["AAPL"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "MSFT",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.8,
                    "price_targets": {
                        "buy_zone_low": 400.0,
                        "buy_zone_high": 405.0,
                        "sell_target": 430.0,
                        "stop_loss": 390.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    },
                },
                {"ticker": "NVDA", "action": "BUY_READY", "confidence": 0.9},
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AAPL"], mode="BALANCED")

        self.assertEqual(meta["watchlist"], ["AAPL"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_pathb_wait_tickers"], [])
        routes = meta["_candidate_action_routes"]
        self.assertEqual([route["ticker"] for route in routes], ["MSFT", "NVDA"])
        self.assertTrue(all(route["final_action"] == "HARD_BLOCK" for route in routes))
        self.assertTrue(all(route["blocker"] == "off_list_action" for route in routes))

    def test_buy_ready_uses_per_ticker_post_open_features_for_probe_demotion(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["AMD"],
            "candidate_actions": [{"ticker": "AMD", "action": "BUY_READY", "confidence": 0.94}],
            "_post_open_features_by_ticker": {
                "AMD": {
                    "ticker": "AMD",
                    "market": "US",
                    "current_price": 125.2,
                    "ret_5m_pct": 4.7,
                    "threshold_used": 3.0,
                    "momentum_state": "overextended",
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AMD"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["AMD"])
        self.assertEqual(meta["allocation_intent"]["AMD"], "probe")
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["requested_action"], "BUY_READY")
        self.assertEqual(route["original_action"], "BUY_READY")
        self.assertEqual(route["final_action"], "PROBE_READY")
        self.assertEqual(route["demoted_to"], "PROBE_READY")
        self.assertEqual(route["runtime_gate_reason"], "overextended")
        self.assertEqual(route["runtime_gate"]["reason"], "overextended")
        self.assertEqual(route["runtime_gate"]["ret_5m_pct"], 4.7)
        self.assertEqual(route["runtime_gate"]["threshold_used"], 3.0)

    def test_sustained_momentum_state_is_not_treated_as_overextended(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["AMD"],
            "candidate_actions": [{"ticker": "AMD", "action": "BUY_READY", "confidence": 0.94}],
            "_post_open_features_by_ticker": {
                "AMD": {
                    "ticker": "AMD",
                    "market": "US",
                    "current_price": 125.2,
                    "ret_5m_pct": 4.7,
                    "ret_30m_pct": 3.1,
                    "threshold_used": 3.0,
                    "momentum_state": "sustained",
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AMD"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["AMD"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "BUY_READY")
        self.assertEqual(route["reason"], "buy_ready")
        self.assertFalse(route["runtime_gate"]["overextended"])
        self.assertEqual(route["runtime_gate"]["momentum_state"], "sustained")

    def test_live_cache_price_takes_priority_over_reference_price_for_chase_block(self) -> None:
        bot = _make_bot()
        bot.price_cache_raw = {"AMD": 130.0}
        bot.price_cache = {}
        raw_meta = {
            "watchlist": ["AMD"],
            "candidate_actions": [
                {
                    "ticker": "AMD",
                    "action": "BUY_READY",
                    "confidence": 0.94,
                    "price_targets": {
                        "reference_price": 120.0,
                        "cancel_if_open_above": 124.0,
                    },
                }
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "US", ["AMD"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "buy_ready_chase_blocked")
        self.assertEqual(route["runtime_gate_reason"], "chase_above_cancel")
        self.assertEqual(route["runtime_gate"]["current_price"], 130.0)

    def test_kr_confirmation_shadow_keeps_ready_but_marks_confirming(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": True,
                "KR_CONFIRMATION_GATE_ENABLED": False,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": -0.2,
                    "ret_5m_pct": -0.4,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["005930"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "BUY_READY")
        self.assertEqual(route["confirmation_state"], "CONFIRMING")
        self.assertEqual(route["confirmation_reason"], "kr_momentum_not_confirmed")
        self.assertTrue(route["confirmation_shadow"])
        self.assertIn("kr_confirmation_required_shadow", route["warnings"])

    def test_kr_confirmation_live_blocks_unconfirmed_ready(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "PROBE_READY", "confidence": 0.8}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": -0.1,
                    "ret_5m_pct": -0.1,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["demoted_to"], "WATCH")
        self.assertEqual(route["runtime_gate_reason"], "kr_momentum_not_confirmed")
        self.assertEqual(route["confirmation_state"], "CONFIRMING")

    def test_kr_confirmation_live_blocks_unconfirmed_pullback_wait(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [
                {
                    "ticker": "005930",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.72,
                    "price_targets": {
                        "buy_zone_low": 69500,
                        "buy_zone_high": 70000,
                        "sell_target": 73000,
                        "stop_loss": 68000,
                        "hold_days": 1,
                        "confidence": 0.72,
                    },
                }
            ],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": -0.1,
                    "ret_5m_pct": -0.1,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta.get("_pathb_wait_tickers"), [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["runtime_gate_reason"], "kr_momentum_not_confirmed")
        self.assertEqual(route["confirmation_state"], "CONFIRMING")

    def test_kr_confirmation_live_blocks_missing_momentum(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["runtime_gate_reason"], "kr_momentum_not_confirmed")
        checks = route["runtime_gate"]["kr_confirmation_checks"]
        self.assertFalse(checks["ret_3m_present"])
        self.assertFalse(checks["ret_5m_present"])

    def test_kr_confirmation_live_blocks_missing_data_quality(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": 0.2,
                    "ret_5m_pct": 0.3,
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["runtime_gate_reason"], "kr_data_quality_not_confirmed")
        checks = route["runtime_gate"]["kr_confirmation_checks"]
        self.assertFalse(checks["data_quality_present"])

    def test_kr_confirmation_shadow_marks_missing_momentum_confirming(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": True,
                "KR_CONFIRMATION_GATE_ENABLED": False,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["005930"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "BUY_READY")
        self.assertEqual(route["confirmation_state"], "CONFIRMING")
        self.assertEqual(route["confirmation_reason"], "kr_momentum_not_confirmed")
        self.assertTrue(route["confirmation_shadow"])
        self.assertIn("kr_confirmation_required_shadow", route["warnings"])

    def test_kr_confirmation_live_allows_confirmed_ready(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
                "KR_CONFIRMATION_REQUIRE_VWAP": True,
                "KR_CONFIRMATION_REQUIRE_OR_HIGH": True,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70500,
                    "ret_3m_pct": 0.2,
                    "ret_5m_pct": 0.3,
                    "vwap": 70200,
                    "opening_range_high": 70400,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["005930"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "BUY_READY")
        self.assertEqual(route["confirmation_state"], "CONFIRMED")
        self.assertEqual(route["confirmation_reason"], "")

    def test_kr_fast_trigger_window_allows_ret_score_inside_window(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "KR_FAST_TRIGGER_WINDOW_MIN": 5,
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )

        state = TradingBot._kr_confirmation_gate_state(
            bot,
            "KR",
            "005930",
            {
                "current_price": 70000,
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "data_quality": "good",
                "market_open_elapsed_min": 3,
            },
        )

        self.assertTrue(state["kr_confirmation_confirmed"])
        self.assertEqual(state["kr_confirmation_score"], 2)
        self.assertEqual(state["kr_confirmation_score_items"], ["ret_3m_ok", "ret_5m_ok"])
        self.assertTrue(state["kr_confirmation_fast_window_ok"])

    def test_kr_fast_trigger_window_excludes_ret_only_score_after_window(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "KR_FAST_TRIGGER_WINDOW_MIN": 5,
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )

        state = TradingBot._kr_confirmation_gate_state(
            bot,
            "KR",
            "005930",
            {
                "current_price": 70000,
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "data_quality": "good",
                "market_open_elapsed_min": 8,
            },
        )

        self.assertFalse(state["kr_confirmation_confirmed"])
        self.assertEqual(state["kr_confirmation_reason"], "kr_fast_trigger_not_confirmed")
        self.assertEqual(state["kr_confirmation_score"], 0)
        self.assertEqual(state["kr_confirmation_score_items"], [])
        self.assertFalse(state["kr_confirmation_fast_window_ok"])

    def test_kr_fast_trigger_elapsed_missing_enforce_fails_closed(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )

        state = TradingBot._kr_confirmation_gate_state(
            bot,
            "KR",
            "005930",
            {
                "current_price": 70500,
                "vwap": 70200,
                "opening_range_high": 70400,
                "data_quality": "good",
            },
        )

        self.assertFalse(state["kr_confirmation_confirmed"])
        self.assertEqual(state["kr_confirmation_reason"], "kr_fast_window_elapsed_missing")
        self.assertTrue(state["kr_confirmation_fast_window_elapsed_missing"])

    def test_kr_fast_trigger_elapsed_missing_shadow_keeps_ready_with_warning(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": True,
                "KR_CONFIRMATION_GATE_ENABLED": False,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": 0.2,
                    "ret_5m_pct": 0.3,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        route = meta["_candidate_action_routes"][0]
        self.assertEqual(meta["trade_ready"], ["005930"])
        self.assertEqual(route["final_action"], "BUY_READY")
        self.assertEqual(route["confirmation_state"], "CONFIRMING")
        self.assertTrue(route["confirmation_shadow"])
        self.assertIn("kr_confirmation_required_shadow", route["warnings"])

    def test_kr_fast_trigger_elapsed_missing_shadow_still_records_missing_reason_with_strong_triggers(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": True,
                "KR_CONFIRMATION_GATE_ENABLED": False,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )

        state = TradingBot._kr_confirmation_gate_state(
            bot,
            "KR",
            "005930",
            {
                "current_price": 70500,
                "vwap": 70200,
                "opening_range_high": 70400,
                "data_quality": "good",
            },
        )

        self.assertFalse(state["kr_confirmation_confirmed"])
        self.assertEqual(state["kr_confirmation_reason"], "kr_fast_window_elapsed_missing")
        self.assertEqual(state["kr_confirmation_score_items"], ["vwap_reclaim", "or_high_reclaim"])

    def test_kr_fast_trigger_blocks_active_vi(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_CONFIRMATION_GATE_SHADOW": False,
                "KR_CONFIRMATION_GATE_ENABLED": True,
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "KR_FAST_TRIGGER_WINDOW_MIN": 5,
                "WATCH_TRIGGER_INITIAL_THRESHOLD": 2,
            }
        )

        state = TradingBot._kr_confirmation_gate_state(
            bot,
            "KR",
            "005930",
            {
                "current_price": 70000,
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "data_quality": "good",
                "market_open_elapsed_min": 3,
                "vi_active": True,
                "vi_state": {"data_quality": "OK", "vi_active": True},
            },
        )

        self.assertFalse(state["kr_confirmation_confirmed"])
        self.assertEqual(state["kr_confirmation_reason"], "kr_vi_active_not_confirmed")
        self.assertFalse(state["kr_confirmation_checks"]["vi_safe"])

    def test_kr_execution_context_attaches_microstructure_snapshot(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_MICROSTRUCTURE_CONTEXT_ENABLED": True,
                "KR_ORDERBOOK_CACHE_TTL_SEC": 2,
                "KR_VI_CACHE_TTL_SEC": 2,
            }
        )
        action = {
            "ticker": "005930",
            "action": "BUY_READY",
            "post_open_features": {"current_price": 70000, "data_quality": "good"},
        }

        with patch("trading_bot.get_access_token", return_value="token"), patch(
            "trading_bot.get_kr_orderbook_snapshot",
            return_value={
                "ticker": "005930",
                "spread_pct": 0.1,
                "imbalance": 0.2,
                "data_quality": "OK",
                "source": "kis_orderbook",
            },
        ), patch(
            "trading_bot.get_kr_vi_state",
            return_value={
                "ticker": "005930",
                "vi_active": False,
                "data_quality": "OK",
                "source": "kis_vi",
            },
        ):
            context = TradingBot._candidate_action_runtime_execution_context(
                bot,
                "KR",
                "005930",
                action,
                {},
                {},
            )

        self.assertEqual(context["microstructure_data_quality"], "OK")
        self.assertEqual(context["spread_bps"], 10.0)
        self.assertTrue(context["orderbook_support"])
        self.assertFalse(context["vi_active"])

    def test_soft_gate_validation_uses_local_stale_when_claude_omits_risk_tags(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"SOFT_GATE_OVERRIDE_VALIDATION_ENABLED": True})
        bot._candidate_health_tracker = lambda market: _HealthTracker(
            {
                "005930": {
                    "ticker": "005930",
                    "health_state": "STABLE_READY",
                    "ready_count": 1,
                    "last_seen_at": "2000-01-01T00:00:00",
                }
            }
        )
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [
                {
                    "ticker": "005930",
                    "action": "BUY_READY",
                    "confidence": 0.9,
                    "risk_tags": [],
                }
            ],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70500,
                    "ret_3m_pct": -0.2,
                    "ret_5m_pct": -0.3,
                    "data_quality": "good",
                }
            },
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "soft_gate_override_failed")
        self.assertIn("stale_candidate", route["runtime_gate"]["soft_gates"])
        self.assertIn("late_chase", route["runtime_gate"]["soft_gates"])

    def test_kr_late_entry_gate_blocks_stale_buy_ready(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_LATE_ENTRY_GATE_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 150.0
        raw_meta = {
            "watchlist": ["005930"],
            "_entry_route_source": "session_open",
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "kr_stale_late_entry_watch_only")
        self.assertEqual(route["kr_late_entry_gate"]["elapsed_min"], 150.0)
        self.assertFalse(route["kr_late_entry_gate"]["fresh_intraday"])

    def test_kr_late_entry_gate_blocks_stale_pullback_wait(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_LATE_ENTRY_GATE_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 150.0
        raw_meta = {
            "watchlist": ["005930"],
            "_entry_route_source": "session_open",
            "candidate_actions": [
                {
                    "ticker": "005930",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.72,
                    "price_targets": {
                        "buy_zone_low": 69500,
                        "buy_zone_high": 70000,
                        "sell_target": 73000,
                        "stop_loss": 68000,
                        "hold_days": 1,
                        "confidence": 0.72,
                    },
                }
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta.get("_pathb_wait_tickers"), [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "kr_stale_late_entry_watch_only")
        self.assertEqual(route["kr_late_entry_gate"]["requested_action"], "PULLBACK_WAIT")
        self.assertEqual(route["kr_late_entry_gate"]["elapsed_min"], 150.0)

    def test_kr_late_entry_gate_demotes_fresh_buy_to_probe(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_LATE_ENTRY_GATE_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 120.0
        raw_meta = {
            "watchlist": ["005930"],
            "_entry_route_source": "fresh_intraday",
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": 0.1,
                    "ret_5m_pct": 0.2,
                    "data_quality": "good",
                }
            },
            "selection_snapshot_ts": datetime.now(KST).isoformat(timespec="seconds"),
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["005930"], mode="BALANCED")

        self.assertEqual(meta["trade_ready"], ["005930"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "PROBE_READY")
        self.assertEqual(route["demoted_to"], "PROBE_READY")
        self.assertEqual(meta["allocation_intent"]["005930"], "probe")
        self.assertEqual(route["kr_late_entry_gate"]["reason"], "kr_late_fresh_buy_demoted_to_probe")
        self.assertTrue(route["kr_late_entry_gate"]["fresh_intraday"])

    def test_kr_late_entry_gate_treats_manual_rescreen_as_fresh_probe(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_LATE_ENTRY_GATE_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 120.0
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(
                bot,
                "KR",
                ["005930"],
                mode="BALANCED",
                source="manual_rescreen",
            )

        self.assertEqual(meta["trade_ready"], ["005930"])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "PROBE_READY")
        self.assertEqual(route["kr_late_entry_gate"]["route_source"], "manual_rescreen")
        self.assertEqual(route["kr_late_entry_gate"]["reason"], "kr_late_fresh_buy_demoted_to_probe")

    def test_kr_late_entry_gate_keeps_analyst_reinvoke_watch_only_after_cutoff(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_LATE_ENTRY_GATE_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 120.0
        raw_meta = {
            "watchlist": ["005930"],
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "confidence": 0.9}],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            meta = TradingBot._apply_selection_meta(
                bot,
                "KR",
                ["005930"],
                mode="BALANCED",
                source="analyst_reinvoke",
            )

        self.assertEqual(meta["trade_ready"], [])
        route = meta["_candidate_action_routes"][0]
        self.assertEqual(route["final_action"], "WATCH")
        self.assertEqual(route["reason"], "kr_late_replacement_watch_only")
        self.assertTrue(route["kr_late_entry_gate"]["late_replacement"])

    def test_kr_partial_replacement_is_watch_only_after_cutoff(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"KR_PARTIAL_REPLACEMENT_WATCH_ONLY_ENABLED": True})
        bot._market_open_elapsed_min = lambda market, now_dt=None: 120.0

        self.assertTrue(TradingBot._kr_partial_replacement_watch_only(bot, "KR"))

        bot._market_open_elapsed_min = lambda market, now_dt=None: 45.0
        self.assertFalse(TradingBot._kr_partial_replacement_watch_only(bot, "KR"))
        self.assertFalse(TradingBot._kr_partial_replacement_watch_only(bot, "US"))

    def test_kr_partial_reselect_replaces_watchlist_without_trade_ready_after_cutoff(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update(
            {
                "KR_LATE_ENTRY_GATE_ENABLED": True,
                "KR_PARTIAL_REPLACEMENT_WATCH_ONLY_ENABLED": True,
            }
        )
        bot._market_open_elapsed_min = lambda market, now_dt=None: 120.0
        bot.today_tickers = {"KR": ["001", "002", "003"]}
        bot.trade_ready_tickers = {"KR": ["001"], "US": []}
        bot.selection_meta["KR"] = {
            "watchlist": ["001", "002", "003"],
            "trade_ready": ["001"],
            "price_targets": {"001": {"reference_price": 100.0}},
            "recommended_strategy": {"001": "momentum"},
        }
        bot.today_ticker_reasons = {"KR": {"001": "keep", "002": "old", "003": "old"}}
        bot.today_judgment = {
            "market": "KR",
            "tickers": ["001", "002", "003"],
            "consensus": {"mode": "BALANCED"},
            "digest_prompt": "",
        }
        bot._partial_reselect_last = {}
        bot._ticker_exclude_log = {"KR": []}
        bot._tsdb_selection_ids = {"KR": {}}
        bot.price_cache_raw = {}
        bot._last_post_open_features_by_ticker = {"KR": {}}
        states = {
            "001": {"health_state": "STABLE_READY", "ready_count": 1},
            "002": {"health_state": "WATCH_WEAK", "ready_count": 0},
            "003": {"health_state": "WATCH_WEAK", "ready_count": 0},
            "010": {"health_state": "STRONG_READY", "ready_count": 1},
            "020": {"health_state": "STRONG_READY", "ready_count": 1},
        }
        bot._candidate_health_tracker = lambda market: _HealthTracker(states)
        scores = {"001": 0.0, "002": 5.0, "003": 4.0}
        bot._partial_replace_score = lambda market, ticker, protected=None: scores.get(ticker, 0.0)
        bot._screen_market_candidates = lambda market, mode: [
            {"ticker": "010", "entry_priority_score": 2.0},
            {"ticker": "020", "entry_priority_score": 2.0},
        ]
        bot._filter_candidates_by_history = lambda candidates, market, **kwargs: list(candidates)
        bot._annotate_selection_execution_features = lambda market, candidates, mode: list(candidates)
        bot._build_intraday_context = lambda market: ""
        bot._load_lesson_candidate_summary = lambda market: ""
        bot._get_market_change_pct = lambda market: 0.0
        bot._get_secondary_change_pct = lambda market: 0.0
        bot._current_judgment_phase = lambda market: "intraday_live"
        bot._persist_live_judgment = lambda market: None
        bot._update_candidate_health = lambda *args, **kwargs: None
        bot._run_param_review = lambda *args, **kwargs: None

        selection_meta = {
            "watchlist": ["010", "020"],
            "trade_ready": ["010", "020"],
            "candidate_actions": [
                {"ticker": "010", "action": "BUY_READY", "confidence": 0.9},
                {"ticker": "020", "action": "BUY_READY", "confidence": 0.9},
            ],
            "recommended_strategy": {"010": "momentum", "020": "momentum"},
            "price_targets": {"010": {"reference_price": 100.0}, "020": {"reference_price": 100.0}},
        }

        with patch("trading_bot.select_tickers", return_value=(["010", "020"], {"010": "new", "020": "new"})), patch(
            "trading_bot.get_last_selection_meta",
            return_value=selection_meta,
        ), patch("trading_bot.tsdb.insert_batch", return_value={"010": 11, "020": 12}), patch(
            "trading_bot.watchlist_change_alert"
        ):
            TradingBot._partial_reselect(bot, "KR")

        self.assertEqual(bot.today_tickers["KR"], ["001", "010", "020"])
        self.assertEqual(bot.trade_ready_tickers["KR"], ["001"])
        self.assertTrue(bot.selection_meta["KR"]["_kr_partial_replacement_watch_only"])
        self.assertEqual(
            bot.selection_meta["KR"]["_runtime_filtered_trade_ready"]["010"],
            "kr_partial_replacement_watch_only",
        )
        self.assertEqual(
            bot.selection_meta["KR"]["_runtime_filtered_trade_ready"]["020"],
            "kr_partial_replacement_watch_only",
        )

    def test_partial_reselect_gate_event_is_marked_pre_replacement(self) -> None:
        bot = _make_bot()
        raw_meta = {
            "watchlist": ["AAPL"],
            "candidate_actions": [
                {
                    "ticker": "AAPL",
                    "action": "BUY_READY",
                    "confidence": 0.9,
                    "price_targets": {"reference_price": 100.0},
                }
            ],
        }

        with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
            TradingBot._apply_selection_meta(
                bot,
                "US",
                ["AAPL"],
                mode="BALANCED",
                source="partial_reselect",
            )

        gate_payloads = [payload for event_type, _, payload in bot._gate_events if event_type == "gate_evaluation"]
        self.assertEqual(gate_payloads[0]["event_source"], "candidate_action_route_pre_replacement")
        self.assertTrue(gate_payloads[0]["pre_replacement"])
        self.assertEqual(gate_payloads[0]["route_source"], "partial_reselect")

    def test_partial_reselect_rejected_ready_writes_corrective_gate_event_and_snapshot(self) -> None:
        bot = _make_bot()
        bot.today_tickers = {"KR": ["001", "002"]}
        bot.trade_ready_tickers = {"KR": [], "US": []}
        bot.selection_meta["KR"] = {"watchlist": ["001", "002"], "trade_ready": []}
        bot.today_ticker_reasons = {"KR": {"001": "old", "002": "old"}}
        bot.today_judgment = {
            "market": "KR",
            "tickers": ["001", "002"],
            "consensus": {"mode": "BALANCED"},
            "digest_prompt": "",
        }
        bot._partial_reselect_last = {}
        bot._ticker_exclude_log = {"KR": []}
        bot._tsdb_selection_ids = {"KR": {}}
        bot.price_cache_raw = {}
        bot._last_post_open_features_by_ticker = {"KR": {}}
        bot._partial_replace_score = lambda market, ticker, protected=None: {"001": 5.0, "002": 4.0}.get(ticker, 0.0)
        bot._screen_market_candidates = lambda market, mode: [{"ticker": "010", "entry_priority_score": 0.0}]
        bot._filter_candidates_by_history = lambda candidates, market, **kwargs: list(candidates)
        bot._annotate_selection_execution_features = lambda market, candidates, mode: list(candidates)
        bot._build_intraday_context = lambda market: ""
        bot._load_lesson_candidate_summary = lambda market: ""
        bot._get_market_change_pct = lambda market: 0.0
        bot._get_secondary_change_pct = lambda market: 0.0
        bot._current_judgment_phase = lambda market: "intraday_live"
        bot._build_selection_evidence_pack = lambda market, candidates: {}
        bot._persist_live_judgment = lambda market: None
        bot._update_candidate_health = lambda *args, **kwargs: None
        bot._run_param_review = lambda *args, **kwargs: None
        bot._candidate_health_tracker = lambda market: _HealthTracker(
            {
                "001": {"ticker": "001", "health_state": "STABLE_READY", "ready_count": 1, "mfe_pct": 3.0},
                "002": {"ticker": "002", "health_state": "STABLE_READY", "ready_count": 1, "mfe_pct": 2.0},
                "010": {"ticker": "010", "health_state": "OBSERVE"},
            }
        )
        snapshots = []
        bot._record_candidate_funnel_snapshot = (
            lambda market, *, selected, meta, stages: snapshots.append((market, selected, meta, stages))
        )
        selection_meta = {
            "watchlist": ["010"],
            "trade_ready": ["010"],
            "candidate_actions": [
                {
                    "ticker": "010",
                    "action": "BUY_READY",
                    "confidence": 0.9,
                    "price_targets": {"reference_price": 100.0},
                }
            ],
            "recommended_strategy": {"010": "momentum"},
            "price_targets": {"010": {"reference_price": 100.0}},
        }

        with patch("trading_bot.select_tickers", return_value=(["010"], {"010": "new"})), patch(
            "trading_bot.get_last_selection_meta",
            return_value=selection_meta,
        ):
            TradingBot._partial_reselect(bot, "KR")

        self.assertEqual(bot.today_tickers["KR"], ["001", "002"])
        self.assertNotIn("010", bot.trade_ready_tickers["KR"])
        corrective = [
            payload
            for event_type, _, payload in bot._gate_events
            if event_type == "gate_evaluation"
            and payload.get("event_source") == "partial_reselect_replacement_gate"
        ]
        self.assertEqual(len(corrective), 1)
        self.assertEqual(corrective[0]["ticker"], "010")
        self.assertEqual(corrective[0]["final_action"], "WATCH")
        self.assertFalse(corrective[0]["passed"])
        self.assertEqual(corrective[0]["reason"], "trainer_replacement_delta_blocked")
        self.assertGreaterEqual(len(corrective[0]["replacement_gate"]["attempts"]), 1)
        self.assertEqual(snapshots[0][2]["_partial_reselect_replacement"]["rejected"]["010"]["reason"], "trainer_replacement_delta_blocked")
        self.assertEqual(snapshots[0][3]["applied"]["selected"], ["001", "002"])

    def test_candidate_audit_live_write_records_routes(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        raw_meta = {
            "watchlist": ["AAPL"],
            "candidate_actions": [{"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.9, "reason": "ready"}],
            "_final_prompt_pool": [
                {
                    "ticker": "AAPL",
                    "market": "US",
                    "prompt_rank": 1,
                    "change_pct": 5.0,
                    "trainer_score_rank": 1,
                    "trainer_prompt_score": 88.0,
                    "trainer_plan_a_score": 76.0,
                    "trainer_pathb_wait_score": 92.0,
                    "trainer_risk_score": 24.0,
                    "trainer_candidate_state": "PLAN_A",
                    "trainer_score_components": {
                        "version": "trainer_quality_v1",
                        "config": {"plan_a_score_min": 62.0},
                    },
                    "primary_bucket": "momentum_now",
                    "liquidity_bucket": "high",
                    "source_tags": ["US:momentum_now", "US:high"],
                    "candidate_quality_score": 81.0,
                    "quality_data_gaps": ["flow_missing"],
                    "candidate_pool_version": "trainer_quality_v1",
                    "prompt_pool_version": "trainer_prompt_pool_v1",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False):
                with patch("trading_bot.get_last_selection_meta", return_value=raw_meta):
                    meta = TradingBot._apply_selection_meta(bot, "US", ["AAPL"], mode="BALANCED")
                TradingBot._record_candidate_funnel_snapshot(
                    bot,
                    "US",
                    selected=["AAPL"],
                    meta=meta,
                    stages=bot.selection_stages["US"],
                )

            store = CandidateAuditStore(db_path)
            summary = store.summary(session_date="2026-05-07", market="US", runtime_mode="live")
            rows = store.rows(session_date="2026-05-07", market="US", runtime_mode="live")
            conn = store.connect()
            try:
                audit_row = conn.execute(
                    """
                    SELECT candidate_quality_score, quality_data_gaps_json,
                           scorer_input_snapshot_json, scorer_config_hash,
                           source_tags_json, trainer_candidate_state
                    FROM audit_candidate_rows
                    WHERE ticker='AAPL'
                    """
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(summary["calls"]["call_count"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["claude_action"], "BUY_READY")
        self.assertEqual(rows[0]["route_final_action"], "BUY_READY")
        self.assertEqual(audit_row["candidate_quality_score"], 81.0)
        self.assertIn("flow_missing", json.loads(audit_row["quality_data_gaps_json"]))
        snapshot = json.loads(audit_row["scorer_input_snapshot_json"])
        self.assertEqual(snapshot["primary_bucket"], "momentum_now")
        replay = score_candidate_for_trainer(snapshot, market="US")
        self.assertEqual(replay["trainer_candidate_state"], audit_row["trainer_candidate_state"])
        self.assertTrue(str(audit_row["scorer_config_hash"] or ""))
        self.assertIn("US:momentum_now", json.loads(audit_row["source_tags_json"]))

    def test_candidate_audit_marks_only_actual_prompt_as_input_to_claude(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        meta = {
            "selection_snapshot_ts": "2026-05-07T09:00:00+09:00",
            "watchlist": ["AAPL", "MSFT"],
            "trade_ready": [],
            "candidate_actions": [
                {"ticker": "AAPL", "action": "WATCH", "reason": "watch"},
                {"ticker": "MSFT", "action": "WATCH", "reason": "watch"},
            ],
            "_final_prompt_pool": [
                {"ticker": "AAPL", "market": "US", "prompt_rank": 1, "trainer_candidate_state": "PLAN_A"},
                {"ticker": "NVDA", "market": "US", "prompt_rank": 2, "trainer_candidate_state": "PLAN_A"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False):
                TradingBot._write_candidate_audit_live(
                    bot,
                    "US",
                    selected=["AAPL", "MSFT"],
                    meta=meta,
                    stages={},
                )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = {
                    row["ticker"]: row
                    for row in conn.execute(
                        """
                        SELECT ticker, input_to_claude_reported, classification
                        FROM audit_candidate_rows
                        """
                    ).fetchall()
                }
                call_payload = json.loads(
                    conn.execute("SELECT payload_json FROM audit_claude_calls").fetchone()["payload_json"]
                )
            finally:
                conn.close()

        self.assertEqual(rows["AAPL"]["input_to_claude_reported"], 1)
        self.assertEqual(rows["MSFT"]["input_to_claude_reported"], 0)
        self.assertEqual(rows["NVDA"]["input_to_claude_reported"], 1)
        self.assertEqual(rows["NVDA"]["classification"], "in_prompt_not_selected")
        self.assertEqual(call_payload["actual_prompt_tickers"], ["AAPL", "NVDA"])
        self.assertEqual(call_payload["actual_prompt_count"], 2)
        self.assertEqual(call_payload["plan_a_in_prompt"], 2)
        self.assertEqual(call_payload["overlay_mode"], "current_only")

    def test_candidate_audit_records_shadow_and_live_overlay_payloads(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        shadow_meta = {
            "selection_snapshot_ts": "2026-05-07T09:00:00+09:00",
            "watchlist": ["AAPL"],
            "trade_ready": [],
            "candidate_actions": [{"ticker": "AAPL", "action": "WATCH", "reason": "watch"}],
            "_final_prompt_pool": [{"ticker": "AAPL", "market": "US", "prompt_rank": 1}],
            "_prompt_overlay_mode": "shadow",
            "_shadow_overlay_tickers": ["AAPL", "PA1"],
            "_shadow_overlay_added_tickers": ["PA1"],
            "_shadow_overlay_removed_tickers": ["DROP1"],
            "_shadow_overlay_plan_a_available": 1,
            "_shadow_overlay_plan_a_added": 1,
            "_overlay_plan_b_used": False,
        }
        live_meta = {
            "selection_snapshot_ts": "2026-05-07T09:01:00+09:00",
            "watchlist": ["AAPL", "PA1"],
            "trade_ready": [],
            "candidate_actions": [{"ticker": "PA1", "action": "WATCH", "reason": "watch"}],
            "_final_prompt_pool": [
                {"ticker": "AAPL", "market": "US", "prompt_rank": 1, "prompt_overlay_added": False},
                {"ticker": "PA1", "market": "US", "prompt_rank": 2, "trainer_candidate_state": "PLAN_A", "prompt_overlay_added": True},
            ],
            "_prompt_overlay_mode": "live",
            "_overlay_added_tickers": ["PA1"],
            "_overlay_removed_tickers": ["DROP1"],
            "_overlay_plan_a_available": 1,
            "_overlay_plan_a_added": 1,
            "_overlay_plan_b_used": False,
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False):
                TradingBot._write_candidate_audit_live(bot, "US", selected=["AAPL"], meta=shadow_meta, stages={})
                TradingBot._write_candidate_audit_live(bot, "US", selected=["AAPL", "PA1"], meta=live_meta, stages={})
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                payloads = [
                    json.loads(row["payload_json"])
                    for row in conn.execute(
                        "SELECT payload_json FROM audit_claude_calls ORDER BY called_at"
                    ).fetchall()
                ]
                live_row_payload = json.loads(
                    conn.execute(
                        """
                        SELECT payload_json
                        FROM audit_candidate_rows
                        WHERE ticker='PA1'
                        ORDER BY known_at DESC
                        LIMIT 1
                        """
                    ).fetchone()["payload_json"]
                )
            finally:
                conn.close()

        self.assertEqual(payloads[0]["overlay_mode"], "shadow")
        self.assertEqual(payloads[0]["actual_prompt_tickers"], ["AAPL"])
        self.assertEqual(payloads[0]["shadow_overlay_tickers"], ["AAPL", "PA1"])
        self.assertEqual(payloads[0]["shadow_overlay_added_tickers"], ["PA1"])
        self.assertEqual(payloads[1]["overlay_mode"], "live")
        self.assertEqual(payloads[1]["actual_prompt_tickers"], ["AAPL", "PA1"])
        self.assertEqual(payloads[1]["overlay_added_tickers"], ["PA1"])
        self.assertTrue(live_row_payload["prompt_overlay_added"])
        self.assertFalse(payloads[1]["overlay_plan_b_used"])

    def test_strength_capture_shadow_fields_are_kr_neutral_only_and_require_ma60(self) -> None:
        bot = _make_bot()

        chg = TradingBot._strength_capture_shadow_fields(
            bot,
            {"change_pct": 25.0, "volume_ratio": 20.0},
            market="KR",
            consensus_mode="NEUTRAL",
        )
        self.assertTrue(chg["strength_capture_shadow"])
        self.assertEqual(chg["strength_capture_rules"], ["strength_v1_chg25_vol20"])

        near_bucket = TradingBot._strength_capture_shadow_fields(
            bot,
            {"from_high_bucket": "at_high"},
            market="KR",
            consensus_mode="CAUTIOUS",
        )
        self.assertEqual(near_bucket["strength_capture_rules"], ["strength_v1_near_high_bucket"])

        near_pct = TradingBot._strength_capture_shadow_fields(
            bot,
            {"from_high_pct": -1.0},
            market="KR",
            consensus_mode="NEUTRAL",
        )
        self.assertEqual(near_pct["strength_capture_rules"], ["strength_v1_near_high_pct"])

        pullback = TradingBot._strength_capture_shadow_fields(
            bot,
            {"from_high_pct": -5.0, "above_ma60": True},
            market="KR",
            consensus_mode="NEUTRAL",
        )
        self.assertEqual(pullback["strength_capture_rules"], ["strength_v1_pullback_strength"])

        missing_ma60 = TradingBot._strength_capture_shadow_fields(
            bot,
            {"from_high_pct": -5.0},
            market="KR",
            consensus_mode="NEUTRAL",
        )
        self.assertFalse(missing_ma60["strength_capture_shadow"])
        self.assertEqual(missing_ma60["strength_capture_rules"], [])

        defensive = TradingBot._strength_capture_shadow_fields(
            bot,
            {"change_pct": 30.0, "volume_ratio": 50.0},
            market="KR",
            consensus_mode="DEFENSIVE",
        )
        self.assertFalse(defensive["strength_capture_shadow"])

        us = TradingBot._strength_capture_shadow_fields(
            bot,
            {"change_pct": 30.0, "volume_ratio": 50.0},
            market="US",
            consensus_mode="NEUTRAL",
        )
        self.assertFalse(us["strength_capture_shadow"])

    def test_candidate_audit_live_write_records_strength_shadow_without_trade_ready_changes(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        meta = {
            "selection_snapshot_ts": "2026-05-07T09:00:00+09:00",
            "consensus_mode": "NEUTRAL",
            "watchlist": ["111111"],
            "trade_ready": [],
            "candidate_actions": [{"ticker": "111111", "action": "WATCH", "reason": "observe"}],
            "_final_prompt_pool": [
                {
                    "ticker": "111111",
                    "market": "KR",
                    "prompt_rank": 1,
                    "price": 1000,
                    "change_pct": 25.0,
                    "volume_ratio": 20.0,
                    "from_high_pct": -1.0,
                    "from_high_bucket": "near_high",
                    "above_ma60": True,
                },
                {
                    "ticker": "222222",
                    "market": "KR",
                    "prompt_rank": 2,
                    "price": 2000,
                    "change_pct": 3.0,
                    "volume_ratio": 2.0,
                    "from_high_pct": -5.0,
                    "above_ma60": True,
                },
            ],
            "_excluded_from_prompt": [
                {
                    "candidate": {
                        "ticker": "333333",
                        "market": "KR",
                        "price": 3000,
                        "change_pct": 1.0,
                        "volume_ratio": 1.0,
                        "from_high_pct": -5.0,
                        "above_ma60": True,
                    },
                    "prompt_excluded_reason": "hard_cap_cutoff",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False):
                TradingBot._write_candidate_audit_live(
                    bot,
                    "KR",
                    selected=["111111"],
                    meta=meta,
                    stages={},
                )
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                rows = {
                    row["ticker"]: row
                    for row in conn.execute(
                        """
                        SELECT ticker, classification, claude_trade_ready,
                               consensus_mode, from_high_pct,
                               strength_capture_shadow, strength_capture_rules
                        FROM audit_candidate_rows
                        ORDER BY ticker
                        """
                    )
                }
            finally:
                conn.close()

        self.assertEqual(rows["111111"]["claude_trade_ready"], 0)
        self.assertEqual(rows["111111"]["consensus_mode"], "NEUTRAL")
        self.assertEqual(rows["111111"]["from_high_pct"], -1.0)
        rules = set(json.loads(rows["111111"]["strength_capture_rules"]))
        self.assertIn("strength_v1_chg25_vol20", rules)
        self.assertIn("strength_v1_near_high_bucket", rules)
        self.assertIn("strength_v1_near_high_pct", rules)
        self.assertEqual(rows["222222"]["classification"], "in_prompt_not_selected")
        self.assertEqual(json.loads(rows["222222"]["strength_capture_rules"]), ["strength_v1_pullback_strength"])
        self.assertEqual(rows["333333"]["classification"], "not_in_prompt")
        self.assertEqual(json.loads(rows["333333"]["strength_capture_rules"]), ["strength_v1_pullback_strength"])

    def test_decision_event_updates_candidate_audit_entry_snapshot(self) -> None:
        bot = _make_bot()
        bot.is_paper = False
        bot.decision_event_log = []
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_entry",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-07",
                    "known_at": "2026-05-07T09:10:00+09:00",
                    "ticker": "005930",
                    "source_file": "trading_bot.selection_meta",
                }
            )
            decisions_path = Path(tmp) / "decisions.jsonl"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False), patch(
                "trading_bot.DECISIONS_FILE",
                decisions_path,
            ), patch("trading_bot.decision_event_alert"):
                TradingBot._record_decision_event(
                    bot,
                    "KR",
                    "buy_order",
                    "005930",
                    strategy="momentum",
                    qty=1,
                    price_native=70_000,
                    price_krw=70_000,
                    entry_timing_snapshot={
                        "candidate_to_order_delay_min": 14.0,
                        "price_change_candidate_to_order_pct": 2.4,
                        "price_change_signal_to_order_pct": -0.3,
                    },
                    candidate_health_snapshot={"health_state": "STABLE_READY"},
                    post_open_features={"ret_5m_pct": 0.7},
                    us_early_entry_gate={
                        "active": True,
                        "elapsed_min": 30.0,
                        "size_mult": 0.5,
                        "policy": "us_early_entry_soft_size",
                    },
                )

            conn = store.connect()
            try:
                row = conn.execute(
                    """
                    SELECT entry_timing_snapshot_json, candidate_health_snapshot_json,
                           post_open_features_json, entry_delay_min,
                           entry_price_vs_first_seen_pct,
                           entry_price_vs_first_ready_pct,
                           us_early_entry_window,
                           us_early_entry_elapsed_min,
                           us_early_entry_size_mult,
                           us_early_entry_confirmation_reason,
                           us_early_entry_gate_json
                    FROM audit_candidate_rows
                    WHERE ticker='005930'
                    """
                ).fetchone()
            finally:
                conn.close()

        self.assertIn("candidate_to_order_delay_min", row["entry_timing_snapshot_json"])
        self.assertIn("STABLE_READY", row["candidate_health_snapshot_json"])
        self.assertIn("ret_5m_pct", row["post_open_features_json"])
        self.assertEqual(row["entry_delay_min"], 14.0)
        self.assertEqual(row["entry_price_vs_first_seen_pct"], 2.4)
        self.assertEqual(row["entry_price_vs_first_ready_pct"], -0.3)
        self.assertEqual(row["us_early_entry_window"], "active")
        self.assertEqual(row["us_early_entry_elapsed_min"], 30.0)
        self.assertEqual(row["us_early_entry_size_mult"], 0.5)
        self.assertEqual(row["us_early_entry_confirmation_reason"], "us_early_entry_soft_size")
        self.assertIn("size_mult", row["us_early_entry_gate_json"])

    def test_us_decision_event_updates_candidate_audit_with_native_prices(self) -> None:
        bot = _make_bot()
        bot.is_paper = False
        bot.decision_event_log = []
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            store.upsert_candidate(
                {
                    "call_id": "call_us_entry",
                    "runtime_mode": "live",
                    "market": "US",
                    "session_date": "2026-05-07",
                    "known_at": "2026-05-07T23:10:00+09:00",
                    "ticker": "NVDA",
                    "source_file": "trading_bot.selection_meta",
                }
            )
            decisions_path = Path(tmp) / "decisions.jsonl"
            with patch.dict(os.environ, {"CANDIDATE_AUDIT_DB_PATH": str(db_path)}, clear=False), patch(
                "trading_bot.DECISIONS_FILE",
                decisions_path,
            ), patch("trading_bot.decision_event_alert"):
                TradingBot._record_decision_event(
                    bot,
                    "US",
                    "buy_order",
                    "NVDA",
                    strategy="momentum",
                    qty=1,
                    price_native=125.5,
                    price_krw=175_700,
                )
                TradingBot._record_decision_event(
                    bot,
                    "US",
                    "sell_filled",
                    "NVDA",
                    strategy="momentum",
                    qty=1,
                    price_native=129.25,
                    price_krw=180_950,
                    reason="take_profit",
                    pnl_pct=3.0,
                )

            conn = store.connect()
            try:
                row = conn.execute(
                    """
                    SELECT entry_price, exit_price
                    FROM audit_candidate_rows
                    WHERE ticker='NVDA'
                    """
                ).fetchone()
            finally:
                conn.close()

        self.assertEqual(row["entry_price"], 125.5)
        self.assertEqual(row["exit_price"], 129.25)


if __name__ == "__main__":
    unittest.main()
