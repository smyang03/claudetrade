from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.candidate_policy import normalize_selection_result
from decision.claude_price_plan import parse_plan_from_claude
from minority_report import hold_advisor


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


if __name__ == "__main__":
    unittest.main()
