from __future__ import annotations

import unittest

from runtime.candidate_actions import (
    action_counts,
    candidate_action_prompt_contract,
    candidate_actions_from_response,
    legacy_selection_to_candidate_actions,
)
from bot.candidate_policy import normalize_selection_result


class ClaudeCandidateActionsTests(unittest.TestCase):
    def test_legacy_selection_creates_shadow_actions(self) -> None:
        actions = legacy_selection_to_candidate_actions(
            market="US",
            selection_meta={
                "watchlist": ["AAPL", "MSFT"],
                "trade_ready": ["AAPL"],
                "price_targets": {"AAPL": {"entry_below": 190.0}},
            },
            created_at="2026-05-06T09:05:00",
            source_prompt_id="prompt_1",
        )

        by_ticker = {item["ticker"]: item for item in actions}
        self.assertEqual(by_ticker["AAPL"]["action"], "BUY_READY")
        self.assertEqual(by_ticker["AAPL"]["size_intent"], "normal")
        self.assertEqual(by_ticker["AAPL"]["price_targets"]["entry_below"], 190.0)
        self.assertTrue(by_ticker["AAPL"]["legacy_schema"])
        self.assertEqual(by_ticker["MSFT"]["action"], "WATCH")
        self.assertLess(by_ticker["AAPL"]["expires_at"], by_ticker["MSFT"]["expires_at"])

    def test_action_counts(self) -> None:
        counts = action_counts([
            {"action": "BUY_READY"},
            {"action": "WATCH"},
            {"action": "WATCH"},
        ])

        self.assertEqual(counts, {"BUY_READY": 1, "WATCH": 2})

    def test_candidate_actions_response_is_normalized_with_runtime_ttl(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "ticker": "AAPL",
                        "action": "PROBE_READY",
                        "confidence": 1.2,
                        "size_intent": "probe",
                        "valid_until": "2026-05-06T09:20:00",
                    }
                ]
            },
            market="US",
            created_at="2026-05-06T09:05:00",
            source_prompt_id="prompt_2",
        )

        self.assertEqual(actions[0]["action"], "PROBE_READY")
        self.assertEqual(actions[0]["confidence"], 1.0)
        self.assertEqual(actions[0]["expires_at"], "2026-05-06T09:10:00")
        self.assertFalse(actions[0]["legacy_schema"])

    def test_candidate_actions_ignores_valid_until_before_created_at(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "ticker": "NVDA",
                        "action": "BUY_READY",
                        "confidence": 0.8,
                        "valid_until": "2026-05-06T10:15:00",
                    }
                ]
            },
            market="US",
            created_at="2026-05-06T22:40:52",
        )

        self.assertEqual(actions[0]["expires_at"], "2026-05-06T22:43:52")
        self.assertIn("raw_valid_until_before_created_ignored", actions[0]["warnings"])

    def test_invalid_claude_system_action_is_downgraded_to_watch(self) -> None:
        actions = candidate_actions_from_response(
            {"candidate_actions": [{"ticker": "AAPL", "action": "HARD_BLOCK"}]},
            market="US",
            created_at="2026-05-06T09:05:00",
        )

        self.assertEqual(actions[0]["action"], "WATCH")
        self.assertIn("invalid_action:HARD_BLOCK", actions[0]["warnings"])

    def test_missing_candidate_actions_uses_legacy_fallback(self) -> None:
        actions = candidate_actions_from_response(
            {"watchlist": ["AAPL"], "trade_ready": ["AAPL"]},
            market="US",
            created_at="2026-05-06T09:05:00",
        )

        self.assertTrue(actions[0]["legacy_schema"])
        self.assertEqual(actions[0]["action"], "BUY_READY")

    def test_selection_normalizer_preserves_candidate_actions_for_runtime(self) -> None:
        meta = normalize_selection_result(
            {
                "watchlist": ["AAPL"],
                "trade_ready": ["AAPL"],
                "candidate_actions": [{"ticker": "AAPL", "action": "PROBE_READY"}],
            },
            [{"ticker": "AAPL"}],
            "US",
        )

        self.assertEqual(meta["candidate_actions"][0]["action"], "PROBE_READY")

    def test_prompt_contract_excludes_system_hard_block(self) -> None:
        contract = candidate_action_prompt_contract(enabled=True)

        self.assertIn("candidate_actions", contract)
        self.assertIn("must not output HARD_BLOCK", contract)
        self.assertIn("hold_days", contract)

    def test_string_confidence_is_downgraded_to_zero_and_cycle_continues(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {"ticker": "AMD", "action": "BUY_READY", "confidence": "high"},
                    {"ticker": "NVDA", "action": "BUY_READY", "confidence": 0.9},
                ]
            },
            market="US",
        )

        by_ticker = {a["ticker"]: a for a in actions}
        self.assertEqual(len(actions), 2)
        self.assertEqual(by_ticker["AMD"]["confidence"], 0.0)
        self.assertTrue(any("invalid_confidence" in w for w in by_ticker["AMD"].get("warnings", [])))
        self.assertAlmostEqual(by_ticker["NVDA"]["confidence"], 0.9)

    def test_non_finite_confidence_is_downgraded_to_zero(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {"ticker": "AMD", "action": "BUY_READY", "confidence": "NaN"},
                    {"ticker": "NVDA", "action": "BUY_READY", "confidence": "inf"},
                ]
            },
            market="US",
        )

        by_ticker = {a["ticker"]: a for a in actions}
        self.assertEqual(by_ticker["AMD"]["confidence"], 0.0)
        self.assertEqual(by_ticker["NVDA"]["confidence"], 0.0)
        self.assertTrue(any("invalid_confidence" in w for w in by_ticker["AMD"].get("warnings", [])))
        self.assertTrue(any("invalid_confidence" in w for w in by_ticker["NVDA"].get("warnings", [])))


if __name__ == "__main__":
    unittest.main()
