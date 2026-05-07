from __future__ import annotations

import unittest
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

        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
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

        with patch.object(analysts_module.client.messages, "create", side_effect=_fake_create), \
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


if __name__ == "__main__":
    unittest.main()
