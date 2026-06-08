from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from trading_bot import TradingBot


def _base_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.runtime_config = None
    bot.selection_meta = {"KR": {}, "US": {"watchlist": ["NVDA"], "trade_ready": [], "candidate_actions": []}}
    bot.today_tickers = {"KR": [], "US": ["NVDA"]}
    bot.trade_ready_tickers = {"KR": [], "US": []}
    bot.today_judgment = {"consensus": {"mode": "BALANCED"}}
    bot.pending_orders = []
    bot.risk = SimpleNamespace(positions=[])
    bot._v2_same_day_stop_tickers = {"KR": set(), "US": set()}
    bot._last_post_open_features_by_ticker = {
        "KR": {},
        "US": {
            "AVGO": {
                "current_price": 103.0,
                "anchor_price": 100.0,
                "vwap_distance_pct": 1.0,
                "pullback_from_high_pct": -1.0,
                "opening_range_break": True,
                "volume_ratio_open": 2.0,
                "ret_3m_pct": 0.3,
                "ret_5m_pct": 0.2,
                "momentum_state": "unknown",
                "data_quality": "minute_complete",
            }
        },
    }
    bot._in_entry_blackout = lambda market: False
    bot._write_funnel_event = lambda *args, **kwargs: None
    return bot


class SingleSymbolJudgeIntegrationTests(unittest.TestCase):
    def test_triggered_pathb_judge_merges_candidate_action_overlay_only(self) -> None:
        bot = _base_bot()
        captured: dict[str, object] = {}
        bot._single_symbol_judge_client = lambda **kwargs: {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "BUY_READY",
            "route": "path_b",
            "confidence": 0.74,
            "reason": "fresh pullback setup",
            "invalid_if": "breaks opening range low",
            "buy_zone_low": 100.0,
            "buy_zone_high": 102.0,
            "sell_target": 109.0,
            "stop_loss": 97.0,
            "hold_days": 2,
            "structural_basis": "VWAP retest",
        }
        bot._apply_selection_meta = lambda market, selected, mode="", source="", meta_override=None: captured.update(
            {"market": market, "selected": selected, "source": source, "meta": meta_override}
        ) or meta_override

        rows = [{"ticker": "AVGO", "trainer_candidate_state": "PLAN_B", "trainer_prompt_score": 72.0, "buy_zone_near": True}]
        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_SCORE_MIN": "70",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=rows)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "PULLBACK_WAIT")
        self.assertTrue(results[0]["applied"])
        self.assertEqual(captured["market"], "US")
        self.assertEqual(captured["source"], "sub_screener")
        self.assertIn("AVGO", captured["selected"])
        meta = captured["meta"]
        actions = meta["candidate_actions"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["ticker"], "AVGO")
        self.assertEqual(actions[0]["action"], "PULLBACK_WAIT")
        self.assertEqual(actions[0]["price_targets"]["buy_zone_high"], 102.0)

    def test_invalid_pathb_judge_queues_recheck_without_overlay(self) -> None:
        bot = _base_bot()
        bot._single_symbol_judge_client = lambda **kwargs: {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "PULLBACK_WAIT",
            "route": "path_b",
            "confidence": 0.74,
            "reason": "missing target",
            "invalid_if": "breaks support",
            "buy_zone_low": 100.0,
            "buy_zone_high": 102.0,
        }
        bot._apply_selection_meta = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overlay should not apply"))

        rows = [{"ticker": "AVGO", "trainer_candidate_state": "PLAN_B", "trainer_prompt_score": 72.0, "buy_zone_near": True}]
        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_SCORE_MIN": "70",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=rows)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "WAIT_RECHECK")
        self.assertFalse(results[0]["applied"])
        self.assertEqual(bot._early_judge_recheck_queue["US"][0]["ticker"], "AVGO")
        self.assertIn("missing_sell_target", bot._early_judge_recheck_queue["US"][0]["errors"])

    def test_strategy_feasibility_soft_block_can_create_pathb_overlay(self) -> None:
        bot = _base_bot()
        bot.selection_meta["US"] = {
            "watchlist": ["AVGO"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "AVGO",
                    "market": "US",
                    "action": "WATCH",
                    "route": "WATCH",
                    "strategy": "momentum",
                    "confidence": 0.82,
                    "reason": "strategy_feasibility:breakout_not_ready",
                    "reason_code": "STRATEGY_FEASIBILITY",
                    "strategy_feasibility_reason": "breakout_not_ready",
                }
            ],
            "_runtime_filtered_trade_ready": {"AVGO": "strategy_feasibility:breakout_not_ready"},
        }
        captured: dict[str, object] = {}
        bot._single_symbol_judge_client = lambda **kwargs: {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "PULLBACK_WAIT",
            "route": "path_b",
            "confidence": 0.74,
            "reason": "vwap retest",
            "invalid_if": "breaks vwap",
            "buy_zone_low": 100.0,
            "buy_zone_high": 101.0,
            "sell_target": 108.0,
            "stop_loss": 97.0,
            "hold_days": 2,
            "structural_basis": "VWAP retest",
        }
        bot._apply_selection_meta = lambda market, selected, mode="", source="", meta_override=None: captured.update(
            {"market": market, "selected": selected, "source": source, "meta": meta_override}
        ) or meta_override

        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_MAX_CALLS_PER_RUN": "2",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="selection_soft_block", rows=[])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "PULLBACK_WAIT")
        self.assertTrue(results[0]["applied"])
        self.assertEqual(results[0]["soft_block_reason"], "strategy_feasibility:breakout_not_ready")
        actions = captured["meta"]["candidate_actions"]
        self.assertEqual(actions[0]["ticker"], "AVGO")
        self.assertEqual(actions[0]["action"], "PULLBACK_WAIT")

    def test_strategy_feasibility_soft_block_low_reward_risk_does_not_apply(self) -> None:
        bot = _base_bot()
        bot.selection_meta["US"] = {
            "watchlist": ["AVGO"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "AVGO",
                    "market": "US",
                    "action": "WATCH",
                    "route": "WATCH",
                    "strategy": "momentum",
                    "confidence": 0.82,
                    "reason": "strategy_feasibility:breakout_not_ready",
                    "strategy_feasibility_reason": "breakout_not_ready",
                }
            ],
            "_runtime_filtered_trade_ready": {"AVGO": "strategy_feasibility:breakout_not_ready"},
        }
        bot._single_symbol_judge_client = lambda **kwargs: {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "PULLBACK_WAIT",
            "route": "path_b",
            "confidence": 0.74,
            "reason": "weak reward risk",
            "invalid_if": "breaks vwap",
            "buy_zone_low": 100.0,
            "buy_zone_high": 101.0,
            "sell_target": 102.0,
            "stop_loss": 99.0,
            "hold_days": 2,
            "structural_basis": "VWAP retest",
        }
        bot._apply_selection_meta = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("overlay should not apply"))

        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_MAX_CALLS_PER_RUN": "2",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="selection_soft_block", rows=[])

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "WAIT_RECHECK")
        self.assertFalse(results[0]["applied"])
        self.assertIn("reward_risk_below_min", results[0]["errors"])


if __name__ == "__main__":
    unittest.main()
