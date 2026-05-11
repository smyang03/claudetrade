from __future__ import annotations

import json
import os
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from bot.candidate_policy import normalize_selection_result
from decision.claude_price_plan import parse_plan_from_claude
from minority_report import hold_advisor, postmortem


class SelectionSizingContractTests(unittest.TestCase):
    def test_normalize_selection_preserves_new_sizing_fields(self) -> None:
        parsed = {
            "watchlist": ["005930", "000660"],
            "trade_ready": ["005930"],
            "allocation_intent": {"005930": "small"},
            "max_order_cap_pct": {"005930": 35},
            "risk_budget_pct": {"005930": 0.35},
            "size_reason": {"005930": "high RS but ATR elevated"},
            "price_targets": {
                "005930": {
                    "buy_zone_low": 70000,
                    "buy_zone_high": 70500,
                    "sell_target": 72500,
                    "stop_loss": 69000,
                    "hold_days": 1,
                    "confidence": 0.7,
                },
                "000660": {"buy_zone_low": 1},
            },
        }
        normalized = normalize_selection_result(
            parsed,
            [{"ticker": "005930"}, {"ticker": "000660"}],
            "KR",
        )

        self.assertEqual(normalized["allocation_intent"]["005930"], "small")
        self.assertEqual(normalized["max_order_cap_pct"]["005930"], 35)
        self.assertEqual(normalized["max_position_pct"]["005930"], 35)
        self.assertEqual(normalized["risk_budget_pct"]["005930"], 0.35)
        self.assertEqual(normalized["size_reason"]["005930"], "high RS but ATR elevated")
        self.assertIn("005930", normalized["price_targets"])
        self.assertNotIn("000660", normalized["price_targets"])


class PricePlanContractTests(unittest.TestCase):
    def test_parse_plan_round_trips_additive_execution_fields(self) -> None:
        plan, errors = parse_plan_from_claude(
            decision_id="d1",
            ticker="005930",
            market="KR",
            session_date="2026-05-01",
            min_confidence=0.1,
            raw={
                "reference_price": 73200,
                "buy_zone_low": 72400,
                "buy_zone_high": 73300,
                "sell_target": 75800,
                "stop_loss": 71100,
                "reward_risk": 1.5,
                "risk_pct": 2.8,
                "reward_pct": 3.4,
                "hold_days": 1,
                "confidence": 0.64,
                "cancel_if_open_above": 74200,
                "target_basis": "VWAP reclaim + resistance",
                "invalid_if": "breaks opening range low",
            },
        )

        self.assertEqual(errors, [])
        self.assertIsNotNone(plan)
        data = plan.to_dict()
        self.assertEqual(data["reference_price"], 73200)
        self.assertEqual(data["reward_risk"], 1.5)
        self.assertEqual(data["target_basis"], "VWAP reclaim + resistance")
        self.assertEqual(data["invalid_if"], "breaks opening range low")

    def test_declared_reward_risk_below_minimum_is_rejected(self) -> None:
        plan, errors = parse_plan_from_claude(
            decision_id="d1",
            ticker="AAPL",
            market="US",
            session_date="2026-05-01",
            min_confidence=0.1,
            raw={
                "buy_zone_low": 100,
                "buy_zone_high": 101,
                "sell_target": 104,
                "stop_loss": 98,
                "reward_risk": 1.0,
                "hold_days": 1,
                "confidence": 0.7,
            },
        )

        self.assertIsNone(plan)
        self.assertIn("declared_reward_risk_below_minimum", errors)


class HoldAdvisorStageContractTests(unittest.TestCase):
    def test_coerce_vote_preserves_stage_fields(self) -> None:
        vote = hold_advisor._coerce_vote(
            {
                "action": "SELL",
                "confidence": 0.8,
                "sell_urgency": "next_open",
                "trail_pct": 0.01,
                "protective_stop": 101,
                "next_review_min": 2,
                "invalid_if": "loses VWAP",
            },
            decision_stage="PRE_CLOSE_CARRY",
        )

        self.assertEqual(vote["action"], "SELL")
        self.assertEqual(vote["sell_urgency"], "next_open")
        self.assertEqual(vote["trail_pct"], 0.02)
        self.assertEqual(vote["protective_stop"], 101)
        self.assertEqual(vote["next_review_min"], 5)
        self.assertEqual(vote["decision_stage"], "PRE_CLOSE_CARRY")

    def test_ask_keeps_existing_contract_with_stage_metadata(self) -> None:
        captured = []

        def _fake_ask_one(*args, **kwargs):
            captured.append(kwargs)
            return {
                "action": "HOLD",
                "confidence": 0.6,
                "trail_pct": 0.03,
                "sell_urgency": "wait",
                "protective_stop": 0.0,
                "next_review_min": 30,
                "reason": "trend intact",
                "invalid_if": "breaks support",
            }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 102.0}
        with patch.object(hold_advisor, "_ask_one", side_effect=_fake_ask_one):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="INTRADAY_REVIEW")

        self.assertEqual(result["action"], "HOLD")
        self.assertIn("trail_pct", result)
        self.assertIn("votes", result)
        self.assertEqual(result["decision_stage"], "INTRADAY_REVIEW")
        self.assertEqual(len(captured), 3)
        self.assertTrue(all(call["decision_stage"] == "INTRADAY_REVIEW" for call in captured))

    def test_pre_close_carry_prompt_uses_stage_lead_and_minutes(self) -> None:
        captured: dict[str, str] = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"action":"SELL","confidence":0.8,"sell_urgency":"next_open",'
                            '"trail_pct":0.03,"protective_stop":0,"next_review_min":30,'
                            '"invalid_if":"close weak","reason":"carry risk"}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0, "qty": 1}
        with patch.object(hold_advisor.client.messages, "create", side_effect=_fake_create), \
             patch.object(hold_advisor, "credit_record", lambda *args, **kwargs: None), \
             patch.object(hold_advisor, "save_raw_call", lambda *args, **kwargs: None):
            hold_advisor._ask_one(
                "neutral",
                pos,
                "KR",
                "market context",
                decision_stage="PRE_CLOSE_CARRY",
                minutes_to_close=12.5,
            )

        self.assertIn("장마감까지 12.5분", captured["prompt"])
        self.assertIn("다음 세션으로 이월", captured["prompt"])
        self.assertNotIn("목표가에 도달한 포지션", captured["prompt"])


class SelectionPromptContractTests(unittest.TestCase):
    def test_select_tickers_prompt_contains_contract_versions(self) -> None:
        from minority_report import analysts as analysts_module

        captured = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text='{"watchlist":["AAPL"],"trade_ready":[],"reasons":{"AAPL":"ok"}}'
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        with patch.dict(os.environ, {"CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda *args, **kwargs: None):
            analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=[{"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0}],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        prompt = captured["prompt"]
        self.assertIn("Decision contract:", prompt)
        self.assertIn("selection_rank_v3", prompt)
        self.assertIn("execution_plan_v1", prompt)
        self.assertIn("max_order_cap_pct", prompt)
        self.assertIn("reward_risk", prompt)

    def test_select_tickers_compact_prompt_normalizes_to_canonical_meta(self) -> None:
        from minority_report import analysts as analysts_module

        captured: dict[str, object] = {}
        raw_calls: list[dict] = []
        response = {
            "wl": ["AAPL"],
            "tr": ["AAPL"],
            "ca": [
                {
                    "t": "AAPL",
                    "a": "BUY_READY",
                    "s": "opening_range_pullback",
                    "c": 0.72,
                    "fr": "FRESH",
                    "mat": "CONFIRMED",
                    "ceil": "BUY_READY",
                    "rc": "OR_PULLBACK_CONFIRMED",
                    "blk": [],
                    "inv": "break_OR_low",
                    "pt": {"ref": 100.0, "lo": 99.0, "hi": 101.0, "tgt": 106.0, "stp": 97.0, "d": 1, "cf": 0.72},
                }
            ],
        }

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            captured["max_tokens"] = max_tokens
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(response))],
                usage=SimpleNamespace(input_tokens=1, output_tokens=120),
                stop_reason="end_turn",
            )

        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "true",
            "SELECTION_OUTPUT_COMPRESSION_ENABLED": "true",
            "CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS": "4000",
            "CLAUDE_SELECTION_COMPACT_WATCH_MAX": "15",
            "CLAUDE_SELECTION_COMPACT_TRADE_READY_MAX": "5",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(analysts_module, "throttle_state", return_value={"allowed": True, "tier": "normal"}), \
             patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
             patch.object(analysts_module, "_recent_selection_feedback_section", return_value=""), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: raw_calls.append(kwargs)):
            tickers, reasons = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=[{"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0}],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        prompt = str(captured["prompt"])
        meta = analysts_module.get_last_selection_meta()
        self.assertEqual(captured["max_tokens"], 4000)
        self.assertIn("MACHINE-COMPACT OUTPUT CONTRACT", prompt)
        self.assertNotIn('"watchlist"', prompt)
        self.assertNotIn('"price_targets"', prompt)
        self.assertEqual(tickers, ["AAPL"])
        self.assertEqual(reasons["AAPL"], "OR_PULLBACK_CONFIRMED")
        self.assertEqual(meta["trade_ready"], ["AAPL"])
        self.assertEqual(meta["recommended_strategy"]["AAPL"], "opening_range_pullback")
        self.assertEqual(meta["candidate_actions"][0]["strategy"], "opening_range_pullback")
        self.assertEqual(meta["_selection_raw_schema"], "compact")
        self.assertFalse(meta["_candidate_actions_missing_contract"])
        self.assertEqual(raw_calls[0]["prompt_version"], "selection_rank_v3+compact_v1")
        self.assertEqual(raw_calls[0]["parse_stage"], "strict_compact")
        self.assertEqual(raw_calls[0]["extra"]["prompt_contract"], "selection_compact.v1")
        self.assertEqual(raw_calls[0]["parsed"]["_normalized"]["price_targets"]["AAPL"]["reference_price"], 100.0)

    def test_select_tickers_compact_max_tokens_is_watch_only_failure(self) -> None:
        from minority_report import analysts as analysts_module

        raw_calls: list[dict] = []

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(text='{"wl":["AAPL"],"tr":["AAPL"],"ca":[')],
                usage=SimpleNamespace(input_tokens=1, output_tokens=4000),
                stop_reason="max_tokens",
            )

        env = {
            "CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "true",
            "SELECTION_OUTPUT_COMPRESSION_ENABLED": "true",
            "CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS": "4000",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(analysts_module, "throttle_state", return_value={"allowed": True, "tier": "normal"}), \
             patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
             patch.object(analysts_module, "_recent_selection_feedback_section", return_value=""), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: raw_calls.append(kwargs)):
            tickers, _ = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=[{"ticker": "AAPL", "price": 100.0, "volume": 1000, "change_rate": 1.0}],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        meta = analysts_module.get_last_selection_meta()
        self.assertEqual(tickers, ["AAPL"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_fallback_mode"], "selection_truncated")
        self.assertTrue(meta["_candidate_actions_missing_contract"])
        self.assertIn("stop_reason_max_tokens", meta["_compact_validation"]["errors"])
        self.assertTrue(raw_calls[0]["parse_error"])
        self.assertEqual(raw_calls[0]["parse_stage"], "compact_truncated")
        self.assertEqual(raw_calls[0]["parsed"]["_normalized"]["trade_ready"], [])

    def test_selection_retry_trade_ready_is_ignored_and_kept_watch_only(self) -> None:
        from minority_report import analysts as analysts_module

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(text="{}")],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        parsed = [
            {
                "watchlist": ["AMD"],
                "trade_ready": ["AMD"],
                "reasons": {"AMD": "partial recovery"},
                "_fallback_mode": "selection_partial",
                "_parse_recovered": True,
            },
            {
                "watchlist": ["AMD", "MSFT"],
                "trade_ready": ["AMD"],
                "reasons": {"AMD": "retry tried to promote", "MSFT": "watch"},
            },
        ]

        with patch.dict(os.environ, {"CLAUDE_SELECTION_COMPACT_SCHEMA_ENABLED": "false"}, clear=False), \
             patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
             patch.object(analysts_module, "_extract_json", side_effect=parsed), \
             patch.object(analysts_module, "build_active_lesson_context", return_value={"section": "", "metadata": {}}), \
             patch.object(analysts_module, "credit_record", lambda *args, **kwargs: None), \
             patch.object(analysts_module, "save_raw_call", lambda **kwargs: None):
            tickers, reasons = analysts_module.select_tickers(
                market="US",
                digest_prompt="market digest",
                consensus_mode="NEUTRAL",
                candidates=[
                    {"ticker": "AMD", "price": 100.0, "volume": 1000, "change_rate": 1.0},
                    {"ticker": "MSFT", "price": 200.0, "volume": 1000, "change_rate": 0.5},
                ],
                market_change_pct=0.0,
                secondary_change_pct=0.0,
            )

        meta = analysts_module.get_last_selection_meta()
        self.assertEqual(tickers, ["AMD", "MSFT"])
        self.assertEqual(reasons["AMD"], "retry tried to promote")
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_selection_retry_trade_ready_ignored"], ["AMD"])


class PostmortemPromptContractTests(unittest.TestCase):
    def test_us_postmortem_prompt_is_market_scoped(self) -> None:
        captured: dict[str, str] = {}

        def _fake_create(*, model, max_tokens, messages):
            captured["prompt"] = messages[0]["content"]
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"bull_result":"HIT","bear_result":"MISS","neutral_result":"PARTIAL",'
                            '"bull_why":"ok","bear_why":"ok","neutral_why":"ok",'
                            '"best_trade":null,"worst_trade":null,"worst_trade_reason":"",'
                            '"key_lesson":"US lesson","issue_type":"none","issue_desc":"",'
                            '"pattern_id":null,'
                            '"brain_updates":{"bull_reliability_change":"stable",'
                            '"bear_reliability_change":"stable","new_lesson":null,'
                            '"market_regime":"unknown"},'
                            '"correction_guide":{"bull_adjustments":[],"bear_adjustments":[],'
                            '"tuning_rules":[],"today_notes":""}}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        brain_payload = {"markets": {"US": {"recent_days": []}}}
        no_op = lambda *args, **kwargs: None
        with patch.object(postmortem.client.messages, "create", side_effect=_fake_create), \
             patch.object(postmortem, "credit_record", no_op), \
             patch.object(postmortem, "save_raw_call", no_op), \
             patch.object(postmortem.BrainDB, "generate_prompt_summary", return_value=""), \
             patch.object(postmortem.BrainDB, "load", return_value=brain_payload), \
             patch.object(postmortem.BrainDB, "update_analyst", no_op), \
             patch.object(postmortem.BrainDB, "update_mode_performance", no_op), \
             patch.object(postmortem.BrainDB, "update_beliefs", no_op), \
             patch.object(postmortem.BrainDB, "update_issue_pattern", no_op), \
             patch.object(postmortem.BrainDB, "add_daily_record", no_op), \
             patch.object(postmortem.BrainDB, "get_recent_selection_feedback_text", return_value=""), \
             patch.object(postmortem.BrainDB, "update_strategy_performance", no_op), \
             patch.object(postmortem.BrainDB, "update_debate_outcome", no_op), \
             patch.object(postmortem.BrainDB, "update_correction_guide", no_op):
            postmortem.run(
                "US",
                "2026-05-07",
                {
                    "judgments": {
                        "bull": {"stance": "MILD_BULL", "key_reason": "SPY +1%"},
                        "bear": {"stance": "NEUTRAL", "key_reason": "VIX calm"},
                        "neutral": {"stance": "NEUTRAL", "key_reason": "mixed"},
                    },
                    "consensus": {"mode": "MILD_BULL"},
                },
                {"market_change": 1.0, "pnl_pct": 0.5, "win": True},
                "US digest",
                trade_log=[],
                decision_event_log=[],
            )

        self.assertIn("미국 주식 자동매매 시스템", captured["prompt"])
        self.assertNotIn("한국 주식 자동매매 시스템", captured["prompt"])

    def test_warning_only_execution_still_writes_policy_lessons(self) -> None:
        calls: dict[str, list] = {
            "beliefs": [],
            "issue_patterns": [],
            "daily_records": [],
            "correction_guides": [],
        }

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"bull_result":"HIT","bear_result":"MISS","neutral_result":"PARTIAL",'
                            '"bull_why":"ok","bear_why":"ok","neutral_why":"ok",'
                            '"best_trade":"SMCI","worst_trade":null,"worst_trade_reason":"",'
                            '"key_lesson":"Valid market lesson should persist.",'
                            '"issue_type":"selection","issue_desc":"valid issue",'
                            '"pattern_id":"p-good",'
                            '"brain_updates":{"new_lesson":"Valid new lesson should persist",'
                            '"market_regime":"risk_on"},'
                            '"correction_guide":{"bull_adjustments":["keep bullish filter"],'
                            '"bear_adjustments":[],"tuning_rules":["keep valid rule"],'
                            '"today_notes":"valid notes"}}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        no_op = lambda *args, **kwargs: None

        with ExitStack() as stack:
            stack.enter_context(patch.object(postmortem.client.messages, "create", side_effect=_fake_create))
            stack.enter_context(patch.object(postmortem, "credit_record", no_op))
            stack.enter_context(patch.object(postmortem, "save_raw_call", no_op))
            stack.enter_context(patch.object(postmortem.BrainDB, "generate_prompt_summary", return_value=""))
            stack.enter_context(patch.object(postmortem.BrainDB, "load", return_value={"markets": {"US": {"recent_days": []}}}))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_analyst", no_op))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_mode_performance", no_op))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_beliefs", side_effect=lambda *a, **k: calls["beliefs"].append((a, k))))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_issue_pattern", side_effect=lambda *a, **k: calls["issue_patterns"].append((a, k))))
            stack.enter_context(patch.object(postmortem.BrainDB, "add_daily_record", side_effect=lambda *a, **k: calls["daily_records"].append((a, k))))
            stack.enter_context(patch.object(postmortem.BrainDB, "get_recent_selection_feedback_text", return_value=""))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_strategy_performance", no_op))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_debate_outcome", no_op))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_correction_guide", side_effect=lambda *a, **k: calls["correction_guides"].append((a, k))))
            postmortem.run(
                "US",
                "2026-05-09",
                {
                    "judgments": {
                        "bull": {"stance": "MILD_BULL", "key_reason": "ok"},
                        "bear": {"stance": "NEUTRAL", "key_reason": "ok"},
                        "neutral": {"stance": "NEUTRAL", "key_reason": "ok"},
                    },
                    "consensus": {"mode": "MILD_BULL"},
                },
                {
                    "market_change": 1.0,
                    "pnl_pct": 0.5,
                    "win": True,
                    "execution_contaminated": True,
                    "execution_learning_excluded": False,
                    "execution_warning": True,
                    "execution_issues": ["broker_position_removed"],
                },
                "US digest",
                trade_log=[{"side": "sell", "ticker": "SMCI", "pnl_pct": 0.5}],
                decision_event_log=[],
            )

        self.assertEqual(len(calls["beliefs"]), 2)
        self.assertEqual(calls["beliefs"][0][0], ("US", {"new_lesson": "Valid new lesson should persist"}))
        self.assertEqual(calls["beliefs"][1][0], ("US", {"market_regime": "risk_on"}))
        self.assertEqual(len(calls["issue_patterns"]), 1)
        self.assertEqual(len(calls["correction_guides"]), 1)
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertEqual(daily_record["key_lesson"], "Valid market lesson should persist.")
        self.assertEqual(daily_record["issue_type"], "selection")
        self.assertFalse(daily_record["execution_learning_excluded"])
        self.assertTrue(daily_record["execution_warning"])

    def test_execution_learning_excluded_does_not_write_policy_lessons(self) -> None:
        calls: dict[str, list] = {
            "analysts": [],
            "beliefs": [],
            "debate_outcomes": [],
            "issue_patterns": [],
            "mode_performance": [],
            "daily_records": [],
            "correction_guides": [],
            "strategy_performance": [],
        }

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        text=(
                            '{"bull_result":"MISS","bear_result":"HIT","neutral_result":"MISS",'
                            '"bull_why":"ok","bear_why":"ok","neutral_why":"ok",'
                            '"best_trade":null,"worst_trade":"AAPL","worst_trade_reason":"bad fill",'
                            '"key_lesson":"Do not learn from contaminated execution.",'
                            '"issue_type":"execution","issue_desc":"bad execution",'
                            '"pattern_id":"p1",'
                            '"brain_updates":{"new_lesson":"Contaminated new lesson must not persist",'
                            '"market_regime":"risk_off"},'
                            '"correction_guide":{"bull_adjustments":["bad"],'
                            '"bear_adjustments":["bad"],"tuning_rules":["bad"],"today_notes":"bad"}}'
                        )
                    )
                ],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            )

        no_op = lambda *args, **kwargs: None

        def record_beliefs(*args, **kwargs):
            calls["beliefs"].append((args, kwargs))

        def record_analyst(*args, **kwargs):
            calls["analysts"].append((args, kwargs))

        def record_mode_performance(*args, **kwargs):
            calls["mode_performance"].append((args, kwargs))

        def record_issue_pattern(*args, **kwargs):
            calls["issue_patterns"].append((args, kwargs))

        def record_daily(*args, **kwargs):
            calls["daily_records"].append((args, kwargs))

        def record_correction(*args, **kwargs):
            calls["correction_guides"].append((args, kwargs))

        def record_strategy_performance(*args, **kwargs):
            calls["strategy_performance"].append((args, kwargs))

        def record_debate_outcome(*args, **kwargs):
            calls["debate_outcomes"].append((args, kwargs))

        with patch.object(postmortem.client.messages, "create", side_effect=_fake_create), \
             patch.object(postmortem, "credit_record", no_op), \
             patch.object(postmortem, "save_raw_call", no_op), \
             patch.object(postmortem.BrainDB, "generate_prompt_summary", return_value=""), \
             patch.object(postmortem.BrainDB, "load", return_value={"markets": {"US": {"recent_days": []}}}), \
             patch.object(postmortem.BrainDB, "update_analyst", side_effect=record_analyst), \
             patch.object(postmortem.BrainDB, "update_mode_performance", side_effect=record_mode_performance), \
             patch.object(postmortem.BrainDB, "update_beliefs", side_effect=record_beliefs), \
             patch.object(postmortem.BrainDB, "update_issue_pattern", side_effect=record_issue_pattern), \
             patch.object(postmortem.BrainDB, "add_daily_record", side_effect=record_daily), \
             patch.object(postmortem.BrainDB, "get_recent_selection_feedback_text", return_value=""), \
             patch.object(postmortem.BrainDB, "update_strategy_performance", side_effect=record_strategy_performance), \
             patch.object(postmortem.BrainDB, "update_debate_outcome", side_effect=record_debate_outcome), \
             patch.object(postmortem.BrainDB, "update_correction_guide", side_effect=record_correction):
            postmortem.run(
                "US",
                "2026-05-08",
                {
                    "judgments": {
                        "bull": {"stance": "MILD_BULL", "key_reason": "ok"},
                        "bear": {"stance": "NEUTRAL", "key_reason": "ok"},
                        "neutral": {"stance": "NEUTRAL", "key_reason": "ok"},
                    },
                    "consensus": {"mode": "MILD_BULL"},
                },
                {
                    "market_change": 0.0,
                    "pnl_pct": -1.0,
                    "win": False,
                    "execution_contaminated": True,
                    "execution_learning_excluded": True,
                    "execution_issues": ["broker_sync_trade"],
                },
                "US digest",
                trade_log=[{"side": "sell", "ticker": "AAPL", "pnl_pct": -1.0}],
                decision_event_log=[],
            )

        self.assertEqual(calls["analysts"], [])
        self.assertEqual(calls["mode_performance"], [])
        self.assertEqual(calls["beliefs"], [])
        self.assertEqual(calls["issue_patterns"], [])
        self.assertEqual(calls["strategy_performance"], [])
        self.assertEqual(calls["debate_outcomes"], [])
        self.assertEqual(calls["correction_guides"], [])
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertEqual(daily_record["key_lesson"], "")
        self.assertEqual(daily_record["issue_type"], "")
        self.assertTrue(daily_record["execution_learning_excluded"])


if __name__ == "__main__":
    unittest.main()
