from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


class TradeReadySlotConfigTests(unittest.TestCase):
    def _bot(self) -> TradingBot:
        bot = TradingBot.__new__(TradingBot)
        bot.enable_kr_momentum_shrink = True
        bot.enable_continuation_live = False
        bot.pending_orders = []
        bot.today_judgment = {}
        bot._data_insufficient_watch_tickers = {}
        bot.risk = type("Risk", (), {"positions": []})()
        return bot

    def test_us_opening_range_pullback_slot_can_be_overridden_by_env(self) -> None:
        bot = self._bot()

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "US")

        self.assertEqual(config["opening_range_pullback"], 2)
        self.assertEqual(config["momentum"], 2)

    def test_us_slot_override_does_not_change_kr(self) -> None:
        bot = self._bot()

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            config = bot._trade_ready_slot_config("MILD_BULL", "KR")

        self.assertEqual(config["opening_range_pullback"], 1)

    def test_held_trade_ready_does_not_consume_strategy_slot(self) -> None:
        bot = self._bot()
        bot.risk = type("Risk", (), {"positions": [{"market": "US", "ticker": "QCOM", "qty": 1}]})()
        meta = {
            "watchlist": ["NOK", "QCOM", "ASTS", "CRDO"],
            "trade_ready": ["NOK", "QCOM", "ASTS", "CRDO"],
            "recommended_strategy": {
                "NOK": "opening_range_pullback",
                "QCOM": "opening_range_pullback",
                "ASTS": "opening_range_pullback",
                "CRDO": "opening_range_pullback",
            },
        }

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["NOK", "ASTS"])
        self.assertEqual(normalized["_runtime_filtered_trade_ready"]["QCOM"], "already_holding")
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["CRDO"],
            "slot_cap:opening_range_pullback",
        )

    def test_pending_order_does_not_consume_strategy_slot(self) -> None:
        bot = self._bot()
        bot.risk = type("Risk", (), {"positions": []})()
        bot.pending_orders = [{"market": "US", "ticker": "QCOM", "qty": 1, "order_no": "pending-1"}]
        meta = {
            "watchlist": ["NOK", "QCOM", "ASTS", "CRDO"],
            "trade_ready": ["NOK", "QCOM", "ASTS", "CRDO"],
            "recommended_strategy": {
                "NOK": "opening_range_pullback",
                "QCOM": "opening_range_pullback",
                "ASTS": "opening_range_pullback",
                "CRDO": "opening_range_pullback",
            },
        }

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["NOK", "ASTS"])
        self.assertEqual(normalized["_runtime_filtered_trade_ready"]["QCOM"], "pending_order")
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["CRDO"],
            "slot_cap:opening_range_pullback",
        )

    def test_strategy_feasibility_demotes_expired_orp_without_slot_replacement(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["QCOM", "ASTS", "CRDO"],
            "trade_ready": ["QCOM", "ASTS", "CRDO"],
            "recommended_strategy": {
                "QCOM": "opening_range_pullback",
                "ASTS": "opening_range_pullback",
                "CRDO": "opening_range_pullback",
            },
            "_strategy_feasibility_by_ticker": {
                "QCOM": {
                    "opening_range_pullback": {
                        "action_ceiling": "WATCH",
                        "state": "expired",
                        "reason": "orp_entry_window_expired",
                        "hard_block": True,
                    }
                },
                "ASTS": {
                    "opening_range_pullback": {
                        "action_ceiling": "BUY_READY",
                        "state": "ready",
                        "reason": "ready",
                    }
                },
                "CRDO": {
                    "opening_range_pullback": {
                        "action_ceiling": "BUY_READY",
                        "state": "ready",
                        "reason": "ready",
                    }
                },
            },
            "candidate_actions": [
                {"ticker": "QCOM", "action": "BUY_READY", "strategy": "opening_range_pullback"},
                {"ticker": "ASTS", "action": "BUY_READY", "strategy": "opening_range_pullback"},
                {"ticker": "CRDO", "action": "BUY_READY", "strategy": "opening_range_pullback"},
            ],
            "_candidate_action_routes": [
                {"ticker": "QCOM", "final_action": "BUY_READY", "strategy": "opening_range_pullback"},
                {"ticker": "ASTS", "final_action": "BUY_READY", "strategy": "opening_range_pullback"},
                {"ticker": "CRDO", "final_action": "BUY_READY", "strategy": "opening_range_pullback"},
            ],
        }

        with patch.dict(os.environ, {"US_TRADE_READY_SLOT_OPENING_RANGE_PULLBACK": "2"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["ASTS", "CRDO"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["QCOM"],
            "strategy_feasibility:orp_entry_window_expired",
        )
        qcom_action = next(item for item in normalized["candidate_actions"] if item["ticker"] == "QCOM")
        self.assertEqual(qcom_action["action"], "WATCH")
        self.assertEqual(qcom_action["strategy_feasibility_demoted_from"], "BUY_READY")
        qcom_route = next(item for item in normalized["_candidate_action_routes"] if item["ticker"] == "QCOM")
        self.assertEqual(qcom_route["final_action"], "WATCH")
        self.assertEqual(qcom_route["route"], "WATCH")

    def test_strategy_feasibility_preserves_pathb_wait_fields(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["QCOM"],
            "trade_ready": ["QCOM"],
            "recommended_strategy": {"QCOM": "opening_range_pullback"},
            "price_targets": {"QCOM": {"entry": 100.0}},
            "_pathb_wait_tickers": ["QCOM"],
            "_pathb_price_targets": {"QCOM": {"entry": 98.0, "stop": 95.0}},
            "_pathb_registration_scope": "candidate_actions_wait_only",
            "_strategy_feasibility_by_ticker": {
                "QCOM": {
                    "opening_range_pullback": {
                        "action_ceiling": "WATCH",
                        "state": "expired",
                        "reason": "orp_entry_window_expired",
                        "hard_block": True,
                    }
                }
            },
            "candidate_actions": [
                {"ticker": "QCOM", "action": "BUY_READY", "strategy": "opening_range_pullback"}
            ],
            "_candidate_action_routes": [
                {"ticker": "QCOM", "final_action": "BUY_READY", "strategy": "opening_range_pullback"}
            ],
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(normalized["_pathb_wait_tickers"], ["QCOM"])
        self.assertEqual(normalized["_pathb_price_targets"], {"QCOM": {"entry": 98.0, "stop": 95.0}})
        self.assertNotIn("QCOM", normalized.get("price_targets") or {})

    def test_strategy_feasibility_demotes_missing_recommended_strategy_when_pack_exists(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["QCOM"],
            "trade_ready": ["QCOM"],
            "_strategy_feasibility_by_ticker": {
                "QCOM": {
                    "opening_range_pullback": {
                        "action_ceiling": "BUY_READY",
                        "state": "ready",
                        "reason": "ready",
                    }
                }
            },
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["QCOM"],
            "strategy_feasibility:missing_recommended_strategy",
        )

    def test_strategy_feasibility_uses_execution_fit_fallback_from_prompt_pool(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["QCOM"],
            "trade_ready": ["QCOM"],
            "_final_prompt_pool": [
                {
                    "ticker": "QCOM",
                    "execution_fit_strategy": "opening_range_pullback",
                    "strategy_feasibility": {
                        "opening_range_pullback": {
                            "action_ceiling": "BUY_READY",
                            "state": "ready",
                            "reason": "ready",
                        }
                    },
                }
            ],
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["QCOM"])

    def test_strategy_session_cooldown_demotes_trade_ready_without_pack(self) -> None:
        bot = self._bot()

        class Health:
            def strategy_cooldown_for(self, ticker, strategy):
                if ticker == "QCOM" and strategy == "opening_range_pullback":
                    return {"scope": "session", "reason": "orp_entry_window_expired", "count": 1}
                return {}

        bot._candidate_health_tracker = lambda market: Health()  # type: ignore[method-assign]
        meta = {
            "watchlist": ["QCOM"],
            "trade_ready": ["QCOM"],
            "recommended_strategy": {"QCOM": "opening_range_pullback"},
        }

        normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], [])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["QCOM"],
            "strategy_feasibility:session_cooldown:orp_entry_window_expired",
        )


if __name__ == "__main__":
    unittest.main()
