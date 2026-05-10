from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from audit.candidate_audit_store import CandidateAuditStore
from trading_bot import TradingBot, _mode_family


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))


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
        self.assertEqual(bot.pathb.registered_meta["_pathb_registration_scope"], "candidate_actions_wait_only")
        self.assertEqual(bot.pathb.registered_meta["_pathb_wait_tickers"], ["GXO"])
        self.assertIn("GXO", bot.v2.registered_meta["trade_ready"])

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
        self.assertEqual(route["reason"], "watch_suspends_stale_pathb")
        self.assertTrue(route["suspend_pathb"])
        self.assertTrue(route["pathb_suspend_shadow"])
        self.assertEqual(route["pathb_suspend_path_run_id"], "run_kbi")

    def test_confident_buy_ready_cancels_pathb_waiting_before_plana(self) -> None:
        bot = _make_bot()
        bot.pathb = _DummyPathB({"path_run_id": "run_1", "status": "WAITING"})
        raw_meta = {
            "watchlist": ["AAPL"],
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

    def test_candidate_audit_live_write_records_routes(self) -> None:
        bot = _make_bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        raw_meta = {
            "watchlist": ["AAPL"],
            "candidate_actions": [{"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.9, "reason": "ready"}],
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

        self.assertEqual(summary["calls"]["call_count"], 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "AAPL")
        self.assertEqual(rows[0]["claude_action"], "BUY_READY")
        self.assertEqual(rows[0]["route_final_action"], "BUY_READY")


if __name__ == "__main__":
    unittest.main()
