from __future__ import annotations

import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
