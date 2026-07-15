from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
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
    def test_entry_blackout_candidate_is_queued_then_replayed_with_fresh_evidence(self) -> None:
        bot = _base_bot()
        bot.session_active = True
        bot.current_market = "US"
        bot._last_post_open_features_by_ticker["US"]["PYPL"] = {
            "current_price": 53.92,
            "opening_range_break": True,
            "volume_ratio_open": 2.2,
            "vwap_distance_pct": 0.8,
            "data_quality": "minute_complete",
        }
        blackout = {"active": True}
        bot._in_entry_blackout = lambda market: blackout["active"]
        calls: list[str] = []
        bot._single_symbol_judge_client = lambda **kwargs: calls.append(kwargs["ticker"]) or {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "PULLBACK_WAIT",
            "route": "path_b",
            "confidence": 0.72,
            "reason": "fresh event pullback",
            "buy_zone_low": 53.0,
            "buy_zone_high": 53.8,
            "sell_target": 57.0,
            "stop_loss": 51.5,
            "hold_days": 1,
            "invalid_if": "breaks event low",
            "structural_basis": "VWAP retest",
        }
        bot._apply_selection_meta = lambda *args, **kwargs: kwargs.get("meta_override")
        row = {
            "ticker": "PYPL",
            "trainer_candidate_state": "PLAN_B",
            "trainer_prompt_score": 80.0,
            "post_open_features": bot._last_post_open_features_by_ticker["US"]["PYPL"],
        }
        env = {
            "EARLY_JUDGE_TRIGGER_ENABLED": "true",
            "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
            "EARLY_JUDGE_RECHECK_CONSUMER_ENABLED": "true",
            "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false",
        }
        with patch.dict(os.environ, env, clear=False):
            first = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=[row])
            self.assertEqual(first, [])
            self.assertEqual(calls, [])
            self.assertEqual(bot._early_judge_recheck_queue["US"][0]["reason"], "entry_blackout")
            bot._early_judge_recheck_queue["US"][0]["due_at"] = (
                datetime.now() - timedelta(seconds=1)
            ).isoformat(timespec="seconds")
            blackout["active"] = False
            consumed = TradingBot.run_early_judge_rechecks(bot, "US")

        self.assertEqual(consumed["status"], "processed")
        self.assertEqual(consumed["called"], ["PYPL"])
        self.assertEqual(calls, ["PYPL"])

    def test_wait_recheck_queue_is_consumed_instead_of_becoming_write_only(self) -> None:
        bot = _base_bot()
        bot.session_active = True
        bot.current_market = "US"
        calls: list[str] = []

        def judge(**kwargs):
            calls.append(kwargs["ticker"])
            if len(calls) == 1:
                return {
                    "ticker": kwargs["ticker"],
                    "market": kwargs["market"],
                    "action": "WAIT_RECHECK",
                    "route": "wait",
                    "reason": "wait for confirmation",
                    "recheck_after_min": 5,
                }
            return {
                "ticker": kwargs["ticker"],
                "market": kwargs["market"],
                "action": "PULLBACK_WAIT",
                "route": "path_b",
                "confidence": 0.75,
                "reason": "confirmed pullback",
                "buy_zone_low": 100.0,
                "buy_zone_high": 101.0,
                "sell_target": 106.0,
                "stop_loss": 98.0,
                "hold_days": 1,
                "invalid_if": "breaks support",
                "structural_basis": "VWAP retest",
            }

        bot._single_symbol_judge_client = judge
        bot._apply_selection_meta = lambda *args, **kwargs: kwargs.get("meta_override")
        row = {
            "ticker": "AVGO",
            "trainer_candidate_state": "PLAN_B",
            "trainer_prompt_score": 80.0,
            "post_open_features": bot._last_post_open_features_by_ticker["US"]["AVGO"],
        }
        env = {
            "EARLY_JUDGE_TRIGGER_ENABLED": "true",
            "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
            "EARLY_JUDGE_RECHECK_CONSUMER_ENABLED": "true",
            "ADAPTIVE_REASK_CLAUDE_BRIDGE_ENABLED": "false",
            "EARLY_JUDGE_COOLDOWN_MIN": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            first = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=[row])
            self.assertEqual(first[0]["action"], "WAIT_RECHECK")
            self.assertEqual(bot._early_judge_recheck_queue["US"][0]["attempts"], 1)
            bot._early_judge_recheck_queue["US"][0]["due_at"] = (
                datetime.now() - timedelta(seconds=1)
            ).isoformat(timespec="seconds")
            consumed = TradingBot.run_early_judge_rechecks(bot, "US")

        self.assertEqual(calls, ["AVGO", "AVGO"])
        self.assertEqual(consumed["called"], ["AVGO"])
        self.assertEqual(bot._early_judge_recheck_queue["US"], [])

    def test_restart_budget_ledger_preserves_same_session_and_resets_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "early_judge_budget.json"
            source = _base_bot()
            source.is_paper = False
            source._early_judge_budget_state_loaded = True
            source._early_judge_budget_state_path = lambda: path
            source._early_judge_budget_marker = {"market": "US", "session_date": "2026-07-15"}
            source._early_judge_session_call_count = {"KR": 0, "US": 7}
            source._early_judge_ticker_session_call_count = {"KR": {}, "US": {"PYPL": 1, "BLK": 2}}
            source._early_judge_call_times = [datetime.now().isoformat(timespec="seconds")]
            TradingBot._save_early_judge_budget_state(source)

            restored = _base_bot()
            restored.is_paper = False
            restored._early_judge_budget_state_path = lambda: path
            TradingBot._load_early_judge_budget_state(restored)
            TradingBot._sync_early_judge_budget_session(
                restored, "US", "2026-07-15", trigger="startup_mid_session"
            )
            self.assertEqual(restored._early_judge_session_call_count["US"], 7)
            self.assertEqual(restored._early_judge_ticker_session_call_count["US"]["BLK"], 2)

            TradingBot._sync_early_judge_budget_session(restored, "KR", "2026-07-16", trigger="schedule")
            self.assertEqual(restored._early_judge_session_call_count, {"KR": 0, "US": 0})
            self.assertEqual(restored._early_judge_recheck_queue, {"KR": [], "US": []})

    def test_per_ticker_call_cap_prevents_one_symbol_from_hogging_budget(self) -> None:
        bot = _base_bot()
        bot._early_judge_ticker_session_call_count = {"KR": {}, "US": {"BLK": 2}}
        calls: list[str] = []
        bot._single_symbol_judge_client = lambda **kwargs: calls.append(kwargs["ticker"]) or {}
        row = {
            "ticker": "BLK",
            "trainer_candidate_state": "PLAN_B",
            "trainer_prompt_score": 99.0,
            "post_open_features": {"current_price": 1000.0, "data_quality": "minute_complete"},
        }
        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "EARLY_JUDGE_MAX_CALLS_PER_TICKER_PER_SESSION": "2",
            },
            clear=False,
        ):
            result = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=[row])

        self.assertEqual(result, [])
        self.assertEqual(calls, [])

    def test_watch_signal_bridge_queues_claude_rejudge_without_local_promotion(self) -> None:
        bot = _base_bot()
        bot._last_post_open_features_by_ticker["US"]["PYPL"] = {
            "current_price": 54.0,
            "data_quality": "minute_complete",
        }
        events: list[tuple[str, dict]] = []
        bot._write_funnel_event = lambda event, market, payload: events.append((event, dict(payload)))
        with patch.dict(
            os.environ,
            {"WATCH_TRIGGER_REASK_CLAUDE_ENABLED": "true"},
            clear=False,
        ):
            TradingBot._log_watch_trigger_shadow(
                bot,
                "US",
                "PYPL",
                price=54.0,
                mode="MILD_BULL",
                strategy="momentum",
                signal_fired=True,
                result="would_promote",
            )

        self.assertEqual(bot.trade_ready_tickers["US"], [])
        self.assertEqual(bot._early_judge_recheck_queue["US"][0]["ticker"], "PYPL")
        self.assertEqual(bot._early_judge_recheck_queue["US"][0]["source"], "watch_trigger_signal_reask")
        shadow_event = [payload for event, payload in events if event == "watch_trigger_shadow"][-1]
        self.assertTrue(shadow_event["claude_reask_queued"])

    def test_obviously_unaffordable_candidate_is_skipped_before_claude_call(self) -> None:
        bot = _base_bot()
        bot.pathb = SimpleNamespace(_pathb_registration_max_entry_krw=lambda market: 1_000_000)
        bot._price_to_krw = lambda price, market: float(price) * 1_500.0
        calls: list[str] = []
        events: list[dict[str, object]] = []
        bot._single_symbol_judge_client = lambda **kwargs: calls.append(kwargs["ticker"]) or {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "WAIT_RECHECK",
            "route": "wait",
            "reason": "should not be called",
        }
        bot._write_funnel_event = lambda event, market, payload: events.append(dict(payload))
        rows = [
            {
                "ticker": "BLK",
                "trainer_candidate_state": "PLAN_B",
                "trainer_prompt_score": 80.0,
                "post_open_features": {"current_price": 1_100.0, "data_quality": "minute_complete"},
            }
        ]

        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "PATHB_EARLY_JUDGE_AFFORDABILITY_PREFILTER_ENABLED": "true",
                "PATHB_EARLY_JUDGE_AFFORDABILITY_PULLBACK_BUFFER_PCT": "15",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=rows)

        self.assertEqual(results, [])
        self.assertEqual(calls, [])
        self.assertEqual(events[-1]["hard_skip_reason"], "high_price_unaffordable_before_judge")
        gate = events[-1]["affordability_prefilter"]
        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["max_entry_krw"], 1_000_000.0)
        self.assertEqual(gate["buffered_entry_krw"], 1_402_500.0)

    def test_near_cap_candidate_remains_eligible_for_pullback_judge(self) -> None:
        bot = _base_bot()
        bot.pathb = SimpleNamespace(_pathb_registration_max_entry_krw=lambda market: 1_000_000)
        bot._price_to_krw = lambda price, market: float(price) * 1_500.0
        bot._single_symbol_judge_client = lambda **kwargs: {
            "ticker": kwargs["ticker"],
            "market": kwargs["market"],
            "action": "WAIT_RECHECK",
            "route": "wait",
            "reason": "wait for a lower entry",
        }
        rows = [
            {
                "ticker": "AVGO",
                "trainer_candidate_state": "PLAN_B",
                "trainer_prompt_score": 80.0,
                "post_open_features": {"current_price": 700.0, "data_quality": "minute_complete"},
            }
        ]

        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "PATHB_EARLY_JUDGE_AFFORDABILITY_PREFILTER_ENABLED": "true",
                "PATHB_EARLY_JUDGE_AFFORDABILITY_PULLBACK_BUFFER_PCT": "15",
            },
            clear=False,
        ):
            results = TradingBot.maybe_run_early_judge_triggers(bot, "US", source="sub_screener", rows=rows)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["ticker"], "AVGO")

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
            # ★2026-07-14: judge RR 임계가 시장별 단일소스가 되어 US는 1.5다.
            # 기존 target 109는 RR=(109-102)/(102-97)=1.40으로 US 정책 미달이라
            # judge가 PULLBACK_WAIT로 승격시키지 않는다(WAIT_RECHECK로 강등). RR=1.6으로 맞춘다.
            "buy_zone_high": 102.0,
            "sell_target": 110.0,
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
