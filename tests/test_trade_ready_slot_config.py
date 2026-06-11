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

    def test_mean_reversion_quality_guard_demotes_bad_range_without_consuming_slot(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["BB", "SNDK", "MDT"],
            "trade_ready": ["BB", "SNDK", "MDT"],
            "recommended_strategy": {
                "BB": "mean_reversion",
                "SNDK": "mean_reversion",
                "MDT": "mean_reversion",
            },
            "from_high_pct": {"BB": -5.0, "SNDK": 3.0, "MDT": -1.0},
            "above_ma60": {"BB": False, "SNDK": True, "MDT": True},
            "candidate_actions": [
                {"ticker": "BB", "action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "SNDK", "action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "MDT", "action": "BUY_READY", "strategy": "mean_reversion"},
            ],
            "_candidate_action_routes": [
                {"ticker": "BB", "final_action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "SNDK", "final_action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "MDT", "final_action": "BUY_READY", "strategy": "mean_reversion"},
            ],
        }

        env = {
            "SELECTION_MEAN_REVERSION_QUALITY_GUARD_ENABLED": "true",
            "SELECTION_MEAN_REVERSION_QUALITY_GUARD_RISK_ON_ENABLED": "false",
            "SELECTION_MEAN_REVERSION_FROM_HIGH_MIN_PCT": "-2",
            "SELECTION_MEAN_REVERSION_FROM_HIGH_MAX_PCT": "1",
            "SELECTION_MEAN_REVERSION_REQUIRE_FROM_HIGH": "true",
            "SELECTION_MEAN_REVERSION_REQUIRE_ABOVE_MA60": "false",
            "SELECTION_MEAN_REVERSION_DEEP_PULLBACK_REQUIRES_ABOVE_MA60": "true",
            "US_BALANCED_TRADE_READY_SLOT_MEAN_REVERSION": "1",
            "US_BALANCED_TRADE_READY_SLOT_TOTAL": "5",
        }
        with patch.dict(os.environ, env, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="NEUTRAL")

        self.assertEqual(normalized["trade_ready"], ["MDT"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["BB"],
            "selection_quality:mean_reversion_from_high_below_min",
        )
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["SNDK"],
            "selection_quality:mean_reversion_from_high_above_max",
        )
        self.assertEqual(normalized["_selection_quality_demoted_tickers"], ["BB", "SNDK"])
        bb_action = next(item for item in normalized["candidate_actions"] if item["ticker"] == "BB")
        self.assertEqual(bb_action["action"], "WATCH")
        self.assertEqual(bb_action["selection_quality_demoted_from"], "BUY_READY")
        sndk_route = next(item for item in normalized["_candidate_action_routes"] if item["ticker"] == "SNDK")
        self.assertEqual(sndk_route["final_action"], "WATCH")
        self.assertEqual(sndk_route["selection_quality_reason"], "mean_reversion_from_high_above_max")

    def test_mean_reversion_quality_guard_requires_ma60_confirmation(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["BB", "MDT"],
            "trade_ready": ["BB", "MDT"],
            "recommended_strategy": {
                "BB": "mean_reversion",
                "MDT": "mean_reversion",
            },
            "_final_prompt_pool": [
                {"ticker": "BB", "from_high_pct": -0.5, "above_ma60": False},
                {"ticker": "MDT", "from_high_pct": -1.0, "above_ma60": True},
            ],
            "candidate_actions": [
                {"ticker": "BB", "action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "MDT", "action": "BUY_READY", "strategy": "mean_reversion"},
            ],
            "_candidate_action_routes": [
                {"ticker": "BB", "final_action": "BUY_READY", "strategy": "mean_reversion"},
                {"ticker": "MDT", "final_action": "BUY_READY", "strategy": "mean_reversion"},
            ],
        }

        with patch.dict(
            os.environ,
            {
                "SELECTION_MEAN_REVERSION_QUALITY_GUARD_ENABLED": "true",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MIN_PCT": "-2",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MAX_PCT": "1",
                "SELECTION_MEAN_REVERSION_REQUIRE_FROM_HIGH": "true",
                "SELECTION_MEAN_REVERSION_REQUIRE_ABOVE_MA60": "true",
                "US_BALANCED_TRADE_READY_SLOT_MEAN_REVERSION": "1",
            },
            clear=False,
        ):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="BALANCED")

        self.assertEqual(normalized["trade_ready"], ["MDT"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["BB"],
            "selection_quality:mean_reversion_ma60_unconfirmed",
        )
        bb_action = next(item for item in normalized["candidate_actions"] if item["ticker"] == "BB")
        self.assertEqual(bb_action["reason"], "selection_quality:mean_reversion_ma60_unconfirmed")
        self.assertTrue(bb_action["selection_quality_evidence"]["require_above_ma60"])

    def test_mean_reversion_quality_guard_allows_deep_pullback_with_ma60_support(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["BB"],
            "trade_ready": ["BB"],
            "recommended_strategy": {"BB": "mean_reversion"},
            "_final_prompt_pool": [{"ticker": "BB", "from_high_pct": -5.0, "above_ma60": True}],
            "candidate_actions": [{"ticker": "BB", "action": "BUY_READY", "strategy": "mean_reversion"}],
            "_candidate_action_routes": [{"ticker": "BB", "final_action": "BUY_READY", "strategy": "mean_reversion"}],
        }

        with patch.dict(
            os.environ,
            {
                "SELECTION_MEAN_REVERSION_QUALITY_GUARD_ENABLED": "true",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MIN_PCT": "-2",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MAX_PCT": "",
                "SELECTION_MEAN_REVERSION_REQUIRE_FROM_HIGH": "true",
                "SELECTION_MEAN_REVERSION_REQUIRE_ABOVE_MA60": "false",
                "SELECTION_MEAN_REVERSION_DEEP_PULLBACK_REQUIRES_ABOVE_MA60": "true",
            },
            clear=False,
        ):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="BALANCED")

        self.assertEqual(normalized["trade_ready"], ["BB"])
        self.assertNotIn("BB", normalized["_runtime_filtered_trade_ready"])

    def test_mean_reversion_quality_guard_does_not_require_ma60_for_shallow_default(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["PG"],
            "trade_ready": ["PG"],
            "recommended_strategy": {"PG": "mean_reversion"},
            "_final_prompt_pool": [{"ticker": "PG", "from_high_pct": -0.5}],
            "candidate_actions": [{"ticker": "PG", "action": "BUY_READY", "strategy": "mean_reversion"}],
            "_candidate_action_routes": [{"ticker": "PG", "final_action": "BUY_READY", "strategy": "mean_reversion"}],
        }

        with patch.dict(
            os.environ,
            {
                "SELECTION_MEAN_REVERSION_QUALITY_GUARD_ENABLED": "true",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MIN_PCT": "-2",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MAX_PCT": "",
                "SELECTION_MEAN_REVERSION_REQUIRE_FROM_HIGH": "true",
                "SELECTION_MEAN_REVERSION_REQUIRE_ABOVE_MA60": "false",
            },
            clear=False,
        ):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="BALANCED")

        self.assertEqual(normalized["trade_ready"], ["PG"])
        self.assertNotIn("PG", normalized["_runtime_filtered_trade_ready"])

    def test_mean_reversion_quality_guard_skips_risk_on_by_default(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["BB"],
            "trade_ready": ["BB"],
            "recommended_strategy": {"BB": "mean_reversion"},
            "from_high_pct": {"BB": -5.0},
            "above_ma60": {"BB": False},
            "candidate_actions": [{"ticker": "BB", "action": "BUY_READY", "strategy": "mean_reversion"}],
            "_candidate_action_routes": [{"ticker": "BB", "final_action": "BUY_READY", "strategy": "mean_reversion"}],
        }

        with patch.dict(
            os.environ,
            {
                "SELECTION_MEAN_REVERSION_QUALITY_GUARD_ENABLED": "true",
                "SELECTION_MEAN_REVERSION_QUALITY_GUARD_RISK_ON_ENABLED": "false",
                "SELECTION_MEAN_REVERSION_FROM_HIGH_MAX_PCT": "",
            },
            clear=False,
        ):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="MILD_BULL")

        self.assertEqual(normalized["trade_ready"], ["BB"])
        self.assertNotIn("BB", normalized["_runtime_filtered_trade_ready"])

    def test_us_midrange_trap_guard_demotes_2_to_5_pct_movers(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["AAA", "BBB", "CCC", "DDD"],
            "trade_ready": ["AAA", "BBB", "CCC", "DDD"],
            "recommended_strategy": {
                "AAA": "momentum",
                "BBB": "momentum",
                "CCC": "momentum",
                "DDD": "momentum",
            },
            # AAA +3.5%(함정 구간) / BBB +1.0%(통과) / CCC +7.0%(통과) / DDD 결측(통과)
            "change_pct": {"AAA": 3.5, "BBB": 1.0, "CCC": 7.0},
            "candidate_actions": [
                {"ticker": "AAA", "action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "BBB", "action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "CCC", "action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "DDD", "action": "BUY_READY", "strategy": "momentum"},
            ],
            "_candidate_action_routes": [
                {"ticker": "AAA", "final_action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "BBB", "final_action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "CCC", "final_action": "BUY_READY", "strategy": "momentum"},
                {"ticker": "DDD", "final_action": "BUY_READY", "strategy": "momentum"},
            ],
        }

        env = {
            "SELECTION_US_MIDRANGE_TRAP_GUARD_ENABLED": "true",
            "SELECTION_US_MIDRANGE_TRAP_MIN_PCT": "2",
            "SELECTION_US_MIDRANGE_TRAP_MAX_PCT": "5",
            # momentum slot cap에 잘리지 않게 확보 — 이 테스트는 trap guard만 검증
            "US_BALANCED_TRADE_READY_SLOT_MOMENTUM": "4",
            "US_BALANCED_TRADE_READY_SLOT_TOTAL": "8",
        }
        with patch.dict(os.environ, env, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="NEUTRAL")

        self.assertNotIn("AAA", normalized["trade_ready"])
        self.assertIn("BBB", normalized["trade_ready"])
        self.assertIn("CCC", normalized["trade_ready"])
        self.assertIn("DDD", normalized["trade_ready"])
        self.assertEqual(
            normalized["_runtime_filtered_trade_ready"]["AAA"],
            "selection_quality:us_midrange_momentum_trap",
        )
        aaa_action = next(item for item in normalized["candidate_actions"] if item["ticker"] == "AAA")
        self.assertEqual(aaa_action["action"], "WATCH")

    def test_us_midrange_trap_guard_does_not_apply_to_kr(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["005930"],
            "trade_ready": ["005930"],
            "recommended_strategy": {"005930": "gap_pullback"},
            "change_pct": {"005930": 3.5},
            "candidate_actions": [{"ticker": "005930", "action": "BUY_READY", "strategy": "gap_pullback"}],
            "_candidate_action_routes": [{"ticker": "005930", "final_action": "BUY_READY", "strategy": "gap_pullback"}],
        }

        with patch.dict(os.environ, {"SELECTION_US_MIDRANGE_TRAP_GUARD_ENABLED": "true"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("KR", meta, meta["watchlist"], mode="NEUTRAL")

        self.assertIn("005930", normalized["trade_ready"])
        self.assertNotIn("005930", normalized["_runtime_filtered_trade_ready"])

    def test_us_midrange_trap_guard_demotes_pullback_wait(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["AAA", "BBB"],
            "trade_ready": [],
            "recommended_strategy": {"AAA": "momentum", "BBB": "momentum"},
            "change_pct": {"AAA": 3.5, "BBB": 7.0},
            "candidate_actions": [
                {"ticker": "AAA", "action": "PULLBACK_WAIT", "strategy": "momentum",
                 "price_targets": {"buy_zone_low": 1, "buy_zone_high": 2}},
                {"ticker": "BBB", "action": "PULLBACK_WAIT", "strategy": "momentum"},
            ],
            "_candidate_action_routes": [
                {"ticker": "AAA", "final_action": "PULLBACK_WAIT", "strategy": "momentum"},
                {"ticker": "BBB", "final_action": "PULLBACK_WAIT", "strategy": "momentum"},
            ],
        }
        env = {
            "SELECTION_US_MIDRANGE_TRAP_GUARD_ENABLED": "true",
            "SELECTION_US_MIDRANGE_TRAP_BLOCK_PULLBACK_WAIT": "true",
        }
        with patch.dict(os.environ, env, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="NEUTRAL")
        aaa = next(a for a in normalized["candidate_actions"] if a["ticker"] == "AAA")
        self.assertEqual(aaa["action"], "WATCH")  # +3.5% 함정구간 → 강등
        self.assertEqual(aaa["price_targets"], {})
        bbb = next(a for a in normalized["candidate_actions"] if a["ticker"] == "BBB")
        self.assertEqual(bbb["action"], "PULLBACK_WAIT")  # +7% 러너 → 유지

    def test_trap_pullback_extension_disabled_keeps_pullback_wait(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["AAA"],
            "trade_ready": [],
            "recommended_strategy": {"AAA": "momentum"},
            "change_pct": {"AAA": 3.5},
            "candidate_actions": [{"ticker": "AAA", "action": "PULLBACK_WAIT", "strategy": "momentum"}],
            "_candidate_action_routes": [{"ticker": "AAA", "final_action": "PULLBACK_WAIT", "strategy": "momentum"}],
        }
        env = {
            "SELECTION_US_MIDRANGE_TRAP_GUARD_ENABLED": "true",
            "SELECTION_US_MIDRANGE_TRAP_BLOCK_PULLBACK_WAIT": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            bot._apply_trap_zone_pullback_guard("US", meta, "NEUTRAL")
        aaa = next(a for a in meta["candidate_actions"] if a["ticker"] == "AAA")
        self.assertEqual(aaa["action"], "PULLBACK_WAIT")

    def test_us_midrange_trap_guard_disabled_keeps_candidates(self) -> None:
        bot = self._bot()
        meta = {
            "watchlist": ["AAA"],
            "trade_ready": ["AAA"],
            "recommended_strategy": {"AAA": "momentum"},
            "change_pct": {"AAA": 3.5},
            "candidate_actions": [{"ticker": "AAA", "action": "BUY_READY", "strategy": "momentum"}],
            "_candidate_action_routes": [{"ticker": "AAA", "final_action": "BUY_READY", "strategy": "momentum"}],
        }

        with patch.dict(os.environ, {"SELECTION_US_MIDRANGE_TRAP_GUARD_ENABLED": "false"}, clear=False):
            normalized = bot._normalize_selection_meta_runtime("US", meta, meta["watchlist"], mode="NEUTRAL")

        self.assertIn("AAA", normalized["trade_ready"])
        self.assertNotIn("AAA", normalized["_runtime_filtered_trade_ready"])


if __name__ == "__main__":
    unittest.main()
