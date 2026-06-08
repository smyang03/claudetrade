from __future__ import annotations

import unittest

from execution.single_symbol_judge import (
    build_single_symbol_judge_prompt,
    normalize_single_symbol_judge_result,
    parse_single_symbol_judge_response,
)


class SingleSymbolJudgeTests(unittest.TestCase):
    def test_prompt_states_runtime_owned_quantity_and_risk_gate(self) -> None:
        prompt = build_single_symbol_judge_prompt(
            market="US",
            ticker="AVGO",
            candidate={"ticker": "AVGO", "trainer_prompt_score": 90},
            features={"opening_range_break": True},
        )

        self.assertIn("ONE already-screened", prompt)
        self.assertIn("must not decide order quantity", prompt)
        self.assertIn("broker/risk gates", prompt)

    def test_parse_fenced_json_response(self) -> None:
        parsed = parse_single_symbol_judge_response(
            '```json\n{"ticker":"AVGO","market":"US","action":"WAIT_RECHECK","route":"wait"}\n```'
        )

        self.assertEqual(parsed["ticker"], "AVGO")
        self.assertEqual(parsed["action"], "WAIT_RECHECK")

    def test_pathb_buy_ready_normalizes_to_pullback_wait_with_valid_plan(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "avgo",
                "market": "US",
                "action": "BUY_READY",
                "route": "path_b",
                "confidence": 0.72,
                "reason": "fresh pullback",
                "invalid_if": "breaks opening range low",
                "buy_zone_low": 100.0,
                "buy_zone_high": 102.0,
                "sell_target": 108.0,
                "stop_loss": 97.0,
                "hold_days": 2,
                "structural_basis": "VWAP retest",
            }
        )

        self.assertTrue(normalized["valid"])
        self.assertEqual(normalized["ticker"], "AVGO")
        self.assertEqual(normalized["action"], "PULLBACK_WAIT")
        self.assertEqual(normalized["route"], "path_b")

    def test_pathb_plan_with_features_requires_reward_risk_floor(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "SOFI",
                "market": "US",
                "action": "PULLBACK_WAIT",
                "route": "path_b",
                "confidence": 0.62,
                "reason": "weak reward risk",
                "invalid_if": "breaks vwap",
                "buy_zone_low": 16.39,
                "buy_zone_high": 16.52,
                "sell_target": 16.85,
                "stop_loss": 16.17,
                "hold_days": 2,
                "structural_basis": "VWAP retest",
            },
            features={
                "current_price": 16.575,
                "anchor_price": 16.18,
                "vwap_distance_pct": 1.08,
                "pullback_from_high_pct": -0.51,
                "opening_range_break": True,
                "momentum_state": "unknown",
                "data_quality": "minute_complete",
            },
        )

        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["action"], "WAIT_RECHECK")
        self.assertIn("reward_risk_below_min", normalized["errors"])

    def test_pathb_plan_with_fade_features_fails_closed(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "TTAN",
                "market": "US",
                "action": "PULLBACK_WAIT",
                "route": "path_b",
                "confidence": 0.62,
                "reason": "bad revival",
                "invalid_if": "breaks support",
                "buy_zone_low": 72.0,
                "buy_zone_high": 73.0,
                "sell_target": 76.0,
                "stop_loss": 70.0,
                "hold_days": 2,
                "structural_basis": "none",
            },
            features={
                "current_price": 72.53,
                "anchor_price": 77.02,
                "vwap_distance_pct": -3.25,
                "opening_range_break": False,
                "momentum_state": "fade",
                "data_quality": "minute_complete",
            },
        )

        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["action"], "WAIT_RECHECK")
        self.assertIn("post_open_momentum_fade", normalized["errors"])

    def test_live_pathb_context_requires_post_open_features(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "SBRA",
                "market": "US",
                "action": "PULLBACK_WAIT",
                "route": "path_b",
                "confidence": 0.62,
                "reason": "missing features",
                "invalid_if": "breaks support",
                "buy_zone_low": 18.0,
                "buy_zone_high": 18.2,
                "sell_target": 19.0,
                "stop_loss": 17.5,
                "hold_days": 2,
                "structural_basis": "VWAP retest",
            },
            features={},
            risk_context={"pathb_plan_before_registration_only": True},
        )

        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["action"], "WAIT_RECHECK")
        self.assertIn("missing_post_open_features_for_pathb_plan", normalized["errors"])

    def test_missing_pathb_price_plan_fails_closed_to_wait_recheck(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "AVGO",
                "market": "US",
                "action": "PULLBACK_WAIT",
                "route": "path_b",
                "confidence": 0.72,
                "reason": "missing plan",
                "invalid_if": "breaks support",
                "buy_zone_low": 100.0,
                "buy_zone_high": 102.0,
            }
        )

        self.assertFalse(normalized["valid"])
        self.assertEqual(normalized["action"], "WAIT_RECHECK")
        self.assertEqual(normalized["route"], "wait")
        self.assertEqual(normalized["audit_reason"], "early_judge_pathb_price_plan_missing")
        self.assertIn("missing_sell_target", normalized["errors"])

    def test_reject_routes_to_reject_without_price_plan(self) -> None:
        normalized = normalize_single_symbol_judge_result(
            {
                "ticker": "005930",
                "market": "KR",
                "action": "REJECT",
                "reason": "overextended",
            }
        )

        self.assertTrue(normalized["valid"])
        self.assertEqual(normalized["route"], "reject")


if __name__ == "__main__":
    unittest.main()
