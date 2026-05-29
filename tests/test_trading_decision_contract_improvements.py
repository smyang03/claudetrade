from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bot.candidate_policy import normalize_selection_result
from decision.claude_price_plan import parse_plan_from_claude
from minority_report import hold_advisor, postmortem
from trading_bot import TradingBot


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
    def test_us_stop_price_converts_krw_scaled_sl_for_advisor_context(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0

        stop = bot._position_stop_price({"sl": 135000.0}, "US", 100.0)

        self.assertAlmostEqual(stop, 100.0)

    def test_us_stop_price_keeps_native_auto_sell_policy_stop(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0

        stop = bot._position_stop_price({"auto_sell_policy_protective_stop": 95.0}, "US", 100.0)

        self.assertAlmostEqual(stop, 95.0)

    def test_us_stop_price_converts_krw_scaled_strategy_and_loss_cap(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0

        strategy_stop = bot._position_stop_price({"strategy_stop_price": 132300.0}, "US", 100.0)
        loss_cap = bot._position_stop_price({"loss_cap_price": 131625.0}, "US", 100.0)

        self.assertAlmostEqual(strategy_stop, 98.0)
        self.assertAlmostEqual(loss_cap, 97.5)

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

    def test_triage_flags_disabled_keep_legacy_three_role_path(self) -> None:
        captured = []

        def _fake_ask_one(*args, **kwargs):
            captured.append(kwargs)
            return {
                "action": "HOLD",
                "confidence": 0.6,
                "trail_pct": 0.03,
                "sell_urgency": "wait",
                "protective_stop": 98.0,
                "next_review_min": 30,
                "reason": "legacy hold",
                "invalid_if": "breaks support",
            }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(
            os.environ,
            {
                "HOLD_ADVISOR_TRIAGE_ENABLED": "false",
                "HOLD_ADVISOR_TRIAGE_SHADOW": "false",
            },
            clear=False,
        ), patch.object(hold_advisor, "_ask_triage", side_effect=AssertionError("triage disabled")), patch.object(
            hold_advisor, "_ask_one", side_effect=_fake_ask_one
        ):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW")

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(len(captured), 3)
        self.assertNotIn("triage", result)

    def test_triage_stage_allowlist_keeps_non_auto_sell_review_legacy(self) -> None:
        captured = []

        def _fake_ask_one(*args, **kwargs):
            captured.append(kwargs)
            return {
                "action": "HOLD",
                "confidence": 0.7,
                "trail_pct": 0.03,
                "sell_urgency": "wait",
                "protective_stop": 98.0,
                "next_review_min": 30,
                "reason": "manual legacy",
                "invalid_if": "breaks support",
            }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(
            os.environ,
            {
                "HOLD_ADVISOR_TRIAGE_ENABLED": "true",
                "HOLD_ADVISOR_TRIAGE_STAGE_ALLOWLIST": "AUTO_SELL_REVIEW",
            },
            clear=False,
        ), patch.object(hold_advisor, "_ask_triage", side_effect=AssertionError("stage not allowed")), patch.object(
            hold_advisor, "_ask_one", side_effect=_fake_ask_one
        ):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="MANUAL_REVIEW")

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(len(captured), 3)
        self.assertEqual(result["decision_stage"], "MANUAL_REVIEW")

    def test_clear_stop_loss_triage_maps_to_sell_without_three_role_calls(self) -> None:
        triage = hold_advisor._coerce_triage_vote(
            {
                "category": "STOP_LOSS",
                "confidence": 0.82,
                "urgency": "now",
                "exit_driver": "failed_recovery",
                "protective_stop": 96.0,
                "hard_stop": 95.0,
                "next_review_min": 10,
                "invalid_if": "failed reclaim",
                "reason": "failed recovery window",
            },
            {"ticker": "TEST"},
            "AUTO_SELL_REVIEW",
            "policy",
        )
        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 97.0}
        with patch.dict(os.environ, {"HOLD_ADVISOR_TRIAGE_ENABLED": "true"}, clear=False), patch.object(
            hold_advisor, "_ask_triage", return_value=triage
        ) as ask_triage, patch.object(
            hold_advisor, "_ask_challenge", side_effect=AssertionError("challenge should be skipped")
        ), patch.object(hold_advisor, "_ask_one", side_effect=AssertionError("legacy should be skipped")):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW", default_policy="policy")

        ask_triage.assert_called_once()
        self.assertEqual(result["action"], "SELL")
        self.assertEqual(result["exit_category"], "STOP_LOSS")
        self.assertEqual(result["exit_driver"], "failed_recovery")
        self.assertEqual(set(result["votes"]), {"triage"})

    def test_non_stop_sell_triage_uses_conditional_challenge_by_default(self) -> None:
        triage = hold_advisor._coerce_triage_vote(
            {
                "category": "SELL",
                "confidence": 0.9,
                "urgency": "now",
                "exit_driver": "time_carry",
                "reason": "carry risk",
            },
            {"ticker": "TEST"},
            "AUTO_SELL_REVIEW",
            "policy",
        )
        challenge = {
            "confirm": False,
            "final_category": "HOLD",
            "confidence": 0.78,
            "hold_mode": "profit_pullback",
            "sell_urgency": "wait",
            "protective_stop": 98.5,
            "hard_stop": 96.0,
            "recover_above": 102.0,
            "next_review_min": 20,
            "invalid_if": "loses 98.5",
            "risk_if_wrong": "missed time exit",
            "minimum_condition_to_hold": "above 98.5",
            "reason": "sell trigger is not decisive enough",
            "challenge_prompt_version": hold_advisor.CHALLENGE_PROMPT_VERSION,
            "parse_error": False,
            "duration_ms": 12,
        }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(os.environ, {"HOLD_ADVISOR_TRIAGE_ENABLED": "true"}, clear=False), patch.object(
            hold_advisor, "_ask_triage", return_value=triage
        ), patch.object(hold_advisor, "_ask_challenge", return_value=challenge) as ask_challenge, patch.object(
            hold_advisor, "_ask_one", side_effect=AssertionError("legacy should be skipped")
        ):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW", default_policy="policy")

        ask_challenge.assert_called_once()
        self.assertIs(ask_challenge.call_args.args[-1], triage)
        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(set(result["votes"]), {"triage", "challenge"})
        self.assertTrue(result["second_opinion_used"])
        self.assertEqual(result["second_opinion_reason"], "non_stop_sell_escalation")
        self.assertEqual(result["exit_category"], "HOLD")
        self.assertEqual(result["protective_stop"], 98.5)
        self.assertEqual(result["invalid_if"], "loses 98.5")

    def test_model_requested_second_opinion_uses_challenge_even_for_stop_loss(self) -> None:
        triage = hold_advisor._coerce_triage_vote(
            {
                "category": "STOP_LOSS",
                "confidence": 0.91,
                "urgency": "now",
                "exit_driver": "loss_cap",
                "needs_second_opinion": True,
                "reason": "loss cap but context asks for verification",
            },
            {"ticker": "TEST"},
            "AUTO_SELL_REVIEW",
            "policy",
        )
        challenge = {
            "confirm": True,
            "final_category": "STOP_LOSS",
            "confidence": 0.86,
            "hold_mode": "",
            "sell_urgency": "now",
            "protective_stop": 0.0,
            "hard_stop": 95.0,
            "recover_above": 0.0,
            "next_review_min": 10,
            "invalid_if": "loss cap remains active",
            "risk_if_wrong": "extends a failed recovery",
            "minimum_condition_to_hold": "",
            "reason": "second pass confirms loss cap",
            "challenge_prompt_version": hold_advisor.CHALLENGE_PROMPT_VERSION,
            "parse_error": False,
            "duration_ms": 8,
        }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 96.0}
        with patch.dict(os.environ, {"HOLD_ADVISOR_TRIAGE_ENABLED": "true"}, clear=False), patch.object(
            hold_advisor, "_ask_triage", return_value=triage
        ), patch.object(hold_advisor, "_ask_challenge", return_value=challenge) as ask_challenge, patch.object(
            hold_advisor, "_ask_one", side_effect=AssertionError("legacy should be skipped")
        ):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW", default_policy="policy")

        ask_challenge.assert_called_once()
        self.assertEqual(result["action"], "SELL")
        self.assertEqual(result["second_opinion_reason"], "model_requested_second_opinion")
        self.assertEqual(result["challenge_final_category"], "STOP_LOSS")
        self.assertEqual(set(result["votes"]), {"triage", "challenge"})

    def test_challenge_parse_error_returns_safe_hold_without_legacy_by_default(self) -> None:
        triage = hold_advisor._coerce_triage_vote(
            {
                "category": "SELL",
                "confidence": 0.9,
                "urgency": "now",
                "exit_driver": "profit_protection",
                "reason": "profit trigger",
            },
            {"ticker": "TEST"},
            "AUTO_SELL_REVIEW",
            "policy",
        )
        challenge = {
            "confirm": False,
            "final_category": "HOLD",
            "confidence": 0.0,
            "reason": "challenge_error",
            "challenge_prompt_version": hold_advisor.CHALLENGE_PROMPT_VERSION,
            "parse_error": True,
            "duration_ms": 7,
        }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(
            os.environ,
            {
                "HOLD_ADVISOR_TRIAGE_ENABLED": "true",
                "HOLD_ADVISOR_TRIAGE_LEGACY_FALLBACK_ENABLED": "false",
            },
            clear=False,
        ), patch.object(hold_advisor, "_ask_triage", return_value=triage), patch.object(
            hold_advisor, "_ask_challenge", return_value=challenge
        ), patch.object(hold_advisor, "_ask_one", side_effect=AssertionError("legacy should be skipped")):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW", default_policy="policy")

        self.assertEqual(result["action"], "HOLD")
        self.assertTrue(result["second_opinion_used"])
        self.assertTrue(result["challenge_parse_error"])
        self.assertEqual(result["reason"], "challenge_error")
        self.assertEqual(set(result["votes"]), {"triage", "challenge"})

    def test_triage_parse_error_can_opt_into_legacy_fallback(self) -> None:
        triage = hold_advisor._fallback_triage("triage_error", "AUTO_SELL_REVIEW", "policy")
        legacy_calls = []

        def _fake_ask_one(*args, **kwargs):
            legacy_calls.append(kwargs)
            return {
                "action": "HOLD",
                "confidence": 0.7,
                "trail_pct": 0.03,
                "sell_urgency": "wait",
                "protective_stop": 98.0,
                "next_review_min": 30,
                "reason": "legacy bounded hold",
                "invalid_if": "breaks support",
            }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(
            os.environ,
            {
                "HOLD_ADVISOR_TRIAGE_ENABLED": "true",
                "HOLD_ADVISOR_TRIAGE_LEGACY_FALLBACK_ENABLED": "true",
            },
            clear=False,
        ), patch.object(hold_advisor, "_ask_triage", return_value=triage), patch.object(
            hold_advisor, "_ask_challenge", side_effect=AssertionError("parse error should not challenge")
        ), patch.object(hold_advisor, "_ask_one", side_effect=_fake_ask_one):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW", default_policy="policy")

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(len(legacy_calls), 3)
        self.assertEqual(result["second_opinion_reason"], "parse_error")

    def test_shadow_requires_positive_call_cap(self) -> None:
        hold_advisor._TRIAGE_SHADOW_CALLS = 0
        legacy_calls = []

        def _fake_ask_one(*args, **kwargs):
            legacy_calls.append(kwargs)
            return {
                "action": "HOLD",
                "confidence": 0.7,
                "trail_pct": 0.03,
                "sell_urgency": "wait",
                "protective_stop": 98.0,
                "next_review_min": 30,
                "reason": "bounded hold",
                "invalid_if": "breaks support",
            }

        pos = {"ticker": "TEST", "entry": 100.0, "current_price": 101.0}
        with patch.dict(
            os.environ,
            {
                "HOLD_ADVISOR_TRIAGE_SHADOW": "true",
                "HOLD_ADVISOR_TRIAGE_LIVE_SHADOW_MAX_CALLS": "0",
            },
            clear=False,
        ), patch.object(hold_advisor, "_ask_triage", side_effect=AssertionError("shadow cap blocks triage")), patch.object(
            hold_advisor, "_ask_one", side_effect=_fake_ask_one
        ):
            result = hold_advisor.ask(pos, "KR", delay=0, decision_stage="AUTO_SELL_REVIEW")

        self.assertEqual(result["action"], "HOLD")
        self.assertEqual(len(legacy_calls), 3)
        self.assertNotIn("triage_shadow", result)

    def test_triage_prompt_excludes_prior_votes_and_raw_response(self) -> None:
        prompt = hold_advisor._build_triage_prompt(
            {
                "ticker": "TEST",
                "entry": 100.0,
                "current_price": 99.0,
                "votes": {"bear": {"action": "SELL", "confidence": 0.99}},
                "raw_response": "previous claude response",
                "advisor_context_v2": {
                    "invalid_if": "breaks 98",
                    "raw_response": "nested raw answer",
                    "votes": {"old": "SELL"},
                },
                "pathb_exit_signal": {
                    "reason": "loss cap",
                    "raw_prompt": "nested prior prompt",
                    "messages": [{"role": "user", "content": "older prompt"}],
                },
            },
            "KR",
            "digest",
            "",
            "AUTO_SELL_REVIEW",
            "policy",
            120.0,
            False,
        )

        self.assertNotIn("previous claude response", prompt)
        self.assertNotIn("nested raw answer", prompt)
        self.assertNotIn("nested prior prompt", prompt)
        self.assertNotIn("older prompt", prompt)
        self.assertNotIn('"votes"', prompt)
        self.assertIn("category is the exit reason class", prompt)

    def test_challenge_prompt_uses_structured_first_pass_without_raw_response(self) -> None:
        triage = {
            "exit_category": "SELL",
            "action": "SELL",
            "confidence": 0.8,
            "exit_driver": "time_carry",
            "reason": "pre-close risk",
            "raw_response": "previous raw answer",
        }
        prompt = hold_advisor._build_challenge_prompt(
            {
                "ticker": "TEST",
                "entry": 100.0,
                "current_price": 101.0,
                "votes": {"bear": {"action": "SELL"}},
                "raw_response": "older raw response",
                "advisor_context_v2": {
                    "invalid_if": "breaks 98",
                    "raw_response": "nested challenge raw answer",
                    "votes": {"old": "SELL"},
                },
            },
            "KR",
            "digest",
            "",
            "AUTO_SELL_REVIEW",
            "policy",
            15.0,
            False,
            triage,
        )

        self.assertIn("First-pass result", prompt)
        self.assertIn("final_category", prompt)
        self.assertNotIn("older raw response", prompt)
        self.assertNotIn("nested challenge raw answer", prompt)
        self.assertNotIn("previous raw answer", prompt)
        self.assertNotIn('"votes"', prompt)

    def test_decision_log_preserves_vote_price_policy_fields(self) -> None:
        votes = {
            "bull": {
                "action": "HOLD",
                "confidence": 0.7,
                "reason": "extend",
                "revised_sell_target": 120.0,
                "protective_stop": 98.0,
                "hard_stop": 97.0,
                "valid_for_min": 20,
                "reask_after_min": 10,
                "hold_mode": "target_extension",
                "invalid_if": "loses 98",
            }
        }
        with tempfile.TemporaryDirectory() as tmp, patch(
            "minority_report.hold_advisor.get_runtime_path",
            return_value=Path(tmp) / "logs" / "hold_advisor",
        ):
            hold_advisor._log_decision(
                "TEST",
                "US",
                {"entry": 100.0, "current_price": 101.0, "display_avg_price": 100.0, "display_current_price": 101.0},
                "HOLD",
                0.02,
                votes,
                "AUTO_SELL_REVIEW",
                "policy",
            )
            files = list((Path(tmp) / "logs" / "hold_advisor").glob("decisions_*.jsonl"))
            row = json.loads(files[0].read_text(encoding="utf-8").splitlines()[0])

        logged_vote = row["votes"]["bull"]
        self.assertEqual(logged_vote["revised_sell_target"], 120.0)
        self.assertEqual(logged_vote["protective_stop"], 98.0)
        self.assertEqual(logged_vote["hard_stop"], 97.0)
        self.assertEqual(logged_vote["valid_for_min"], 20)
        self.assertEqual(logged_vote["reask_after_min"], 10)
        self.assertEqual(logged_vote["hold_mode"], "target_extension")
        self.assertEqual(logged_vote["invalid_if"], "loses 98")

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
    def test_prompt_policy_exclusion_cases(self) -> None:
        self.assertEqual(
            postmortem._prompt_policy_exclusion({}, execution_learning_excluded=True),
            (True, "execution_learning_excluded"),
        )
        self.assertEqual(
            postmortem._prompt_policy_exclusion(
                {"prompt_policy_excluded": True, "policy_exclusion_reason": "manual_review"},
                execution_learning_excluded=False,
            ),
            (True, "manual_review"),
        )
        self.assertEqual(
            postmortem._prompt_policy_exclusion(
                {"prompt_policy_excluded": False, "execution_contaminated": True},
                execution_learning_excluded=False,
            ),
            (False, ""),
        )
        self.assertEqual(
            postmortem._prompt_policy_exclusion(
                {"selection_evidence_verified": True, "execution_contaminated": True},
                execution_learning_excluded=False,
            ),
            (False, ""),
        )
        self.assertEqual(
            postmortem._prompt_policy_exclusion(
                {"execution_contaminated": True},
                execution_learning_excluded=False,
            ),
            (True, "execution_contaminated"),
        )
        self.assertEqual(
            postmortem._prompt_policy_exclusion({}, execution_learning_excluded=False),
            (True, "postmortem_policy_requires_approval"),
        )

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

    def test_warning_only_execution_keeps_daily_record_but_excludes_prompt_policy(self) -> None:
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

        self.assertEqual(calls["beliefs"], [])
        self.assertEqual(calls["issue_patterns"], [])
        self.assertEqual(calls["correction_guides"], [])
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertEqual(daily_record["key_lesson"], "Valid market lesson should persist.")
        self.assertEqual(daily_record["issue_type"], "selection")
        self.assertFalse(daily_record["execution_learning_excluded"])
        self.assertTrue(daily_record["prompt_policy_excluded"])
        self.assertEqual(daily_record["policy_exclusion_reason"], "execution_contaminated")
        self.assertTrue(daily_record["execution_warning"])

    def test_normal_postmortem_requires_approval_before_policy_updates(self) -> None:
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
                            '"key_lesson":"Unapproved daily market lesson.",'
                            '"issue_type":"selection","issue_desc":"candidate issue",'
                            '"pattern_id":"p-normal",'
                            '"brain_updates":{"new_lesson":"Unapproved new lesson",'
                            '"market_regime":"risk_on"},'
                            '"correction_guide":{"bull_adjustments":["change"],'
                            '"bear_adjustments":[],"tuning_rules":["new rule"],'
                            '"today_notes":"notes"}}'
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
                "2026-05-10",
                {
                    "judgments": {
                        "bull": {"stance": "MILD_BULL", "key_reason": "ok"},
                        "bear": {"stance": "NEUTRAL", "key_reason": "ok"},
                        "neutral": {"stance": "NEUTRAL", "key_reason": "ok"},
                    },
                    "consensus": {"mode": "MILD_BULL"},
                },
                {"market_change": 1.0, "pnl_pct": 0.5, "win": True},
                "US digest",
                trade_log=[{"side": "sell", "ticker": "SMCI", "pnl_pct": 0.5}],
                decision_event_log=[],
            )

        self.assertEqual(calls["beliefs"], [])
        self.assertEqual(calls["issue_patterns"], [])
        self.assertEqual(calls["correction_guides"], [])
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertEqual(daily_record["key_lesson"], "Unapproved daily market lesson.")
        self.assertFalse(daily_record["execution_learning_excluded"])
        self.assertTrue(daily_record["prompt_policy_excluded"])
        self.assertEqual(daily_record["policy_exclusion_reason"], "postmortem_policy_requires_approval")

    def test_contaminated_postmortem_without_explicit_learning_flag_skips_stats_by_design(self) -> None:
        calls: dict[str, list] = {
            "analysts": [],
            "mode_performance": [],
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
                            '"key_lesson":"Do not promote contaminated first-pass result.",'
                            '"issue_type":"selection","issue_desc":"candidate issue",'
                            '"pattern_id":"p-contaminated",'
                            '"brain_updates":{"new_lesson":"Contaminated first pass",'
                            '"market_regime":"risk_on"},'
                            '"correction_guide":{"bull_adjustments":["bad"],'
                            '"bear_adjustments":[],"tuning_rules":["bad"],'
                            '"today_notes":"bad"}}'
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
            stack.enter_context(patch.object(postmortem.BrainDB, "update_analyst", side_effect=lambda *a, **k: calls["analysts"].append((a, k))))
            stack.enter_context(patch.object(postmortem.BrainDB, "update_mode_performance", side_effect=lambda *a, **k: calls["mode_performance"].append((a, k))))
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
                    "execution_warning": True,
                    "execution_issues": ["broker_position_removed"],
                },
                "US digest",
                trade_log=[{"side": "sell", "ticker": "SMCI", "pnl_pct": 0.5}],
                decision_event_log=[],
            )

        self.assertEqual(calls["analysts"], [])
        self.assertEqual(calls["mode_performance"], [])
        self.assertEqual(calls["beliefs"], [])
        self.assertEqual(calls["issue_patterns"], [])
        self.assertEqual(calls["correction_guides"], [])
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertTrue(daily_record["execution_learning_excluded"])
        self.assertTrue(daily_record["prompt_policy_excluded"])
        self.assertEqual(daily_record["policy_exclusion_reason"], "execution_learning_excluded")

    def test_parse_failed_postmortem_writes_daily_record_without_policy_learning(self) -> None:
        calls: dict[str, list] = {"daily_records": [], "beliefs": [], "issue_patterns": []}

        def _fake_create(*, model, max_tokens, messages):
            return SimpleNamespace(
                content=[SimpleNamespace(text="not-json")],
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
            stack.enter_context(patch.object(postmortem.BrainDB, "update_correction_guide", no_op))
            postmortem.run(
                "US",
                "2026-05-10",
                {
                    "judgments": {
                        "bull": {"stance": "MILD_BULL", "key_reason": "ok"},
                        "bear": {"stance": "NEUTRAL", "key_reason": "ok"},
                        "neutral": {"stance": "NEUTRAL", "key_reason": "ok"},
                    },
                    "consensus": {"mode": "MILD_BULL"},
                },
                {"market_change": 0.0, "pnl_pct": 0.1, "win": True},
                "US digest",
                trade_log=[{"side": "sell", "ticker": "AAPL", "pnl_pct": 0.1}],
                decision_event_log=[],
            )

        self.assertEqual(calls["beliefs"], [])
        self.assertEqual(calls["issue_patterns"], [])
        self.assertEqual(len(calls["daily_records"]), 1)
        daily_record = calls["daily_records"][0][0][1]
        self.assertIn("Postmortem JSON parse failed", daily_record["key_lesson"])
        self.assertEqual(daily_record["issue_type"], "postmortem_parse_error")

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
        self.assertTrue(daily_record["prompt_policy_excluded"])
        self.assertEqual(daily_record["policy_exclusion_reason"], "execution_learning_excluded")


if __name__ == "__main__":
    unittest.main()
