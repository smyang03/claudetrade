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
        self.assertIn("Every v2 candidate_action", contract)
        self.assertIn("hold_days", contract)

    def test_v2_watch_defaults_missing_action_ceiling_ack_for_audit(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "ticker": "018880",
                        "schema_version": "candidate_actions.v2",
                        "action": "WATCH",
                        "reason_code": "STALE_LATE_CHASE",
                    }
                ]
            },
            market="KR",
            created_at="2026-05-11T10:00:00",
        )

        self.assertEqual(actions[0]["action"], "WATCH")
        self.assertEqual(actions[0]["action_ceiling_ack"], "WATCH")
        self.assertIn("v2_missing_action_ceiling_ack_defaulted", actions[0]["contract_warnings"])

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

    def test_compact_action_keys_preserve_strategy_and_v2_ready_state(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "t": "NVDA",
                        "a": "BUY_READY",
                        "s": "opening_range_pullback",
                        "c": 0.73,
                        "fr": "FRESH",
                        "mat": "CONFIRMED",
                        "ceil": "BUY_READY",
                        "rc": "OR_PULLBACK_CONFIRMED",
                        "blk": [],
                        "inv": "break_OR_low",
                        "pt": {
                            "ref": 218.76,
                            "lo": 216.5,
                            "hi": 219.5,
                            "tgt": 226.0,
                            "stp": 213.5,
                            "d": 1,
                            "cf": 0.73,
                        },
                    }
                ]
            },
            market="US",
            created_at="2026-05-12T09:05:00",
            source_prompt_id="selection_compact.v1",
        )

        self.assertEqual(actions[0]["schema_version"], "candidate_actions.v2")
        self.assertEqual(actions[0]["action"], "BUY_READY")
        self.assertEqual(actions[0]["strategy"], "opening_range_pullback")
        self.assertEqual(actions[0]["size_intent"], "normal")
        self.assertEqual(actions[0]["why_not_watch"], "OR_PULLBACK_CONFIRMED:FRESH:CONFIRMED")
        self.assertEqual(actions[0]["price_targets"]["reference_price"], 218.76)
        self.assertNotIn("v2_missing_why_not_watch_demoted", actions[0]["warnings"])

    def test_compact_probe_defaults_to_probe_size(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "t": "QCOM",
                        "a": "PROBE_READY",
                        "s": "gap_pullback",
                        "c": 0.61,
                        "fr": "FRESH",
                        "mat": "EARLY",
                        "ceil": "PROBE_READY",
                        "rc": "EARLY_CONFIRMATION",
                        "inv": "fails_vwap",
                        "pt": {"ref": 190, "lo": 188, "hi": 191, "tgt": 197, "stp": 184, "d": 1, "cf": 0.61},
                    }
                ]
            },
            market="US",
            created_at="2026-05-12T09:05:00",
        )

        self.assertEqual(actions[0]["action"], "PROBE_READY")
        self.assertEqual(actions[0]["size_intent"], "probe")
        self.assertEqual(actions[0]["strategy"], "gap_pullback")


if __name__ == "__main__":
    unittest.main()
