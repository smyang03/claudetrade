from __future__ import annotations

import unittest

from runtime.exit_lifecycle import decide_exit_lifecycle


class ExitLifecycleTests(unittest.TestCase):
    def test_profit_floor_cannot_be_overridden_by_claude_hold(self) -> None:
        decision = decide_exit_lifecycle(
            {"ticker": "001440"},
            exit_candidate={"ticker": "001440", "reason": "profit_floor"},
            claude_vote="HOLD",
        )

        self.assertEqual(decision.final_action, "SELL")
        self.assertFalse(decision.claude_override_allowed)
        self.assertIn("system_guard_precedes_claude", decision.warnings)

    def test_recovery_micro_time_stop_cannot_be_overridden_by_claude_hold(self) -> None:
        decision = decide_exit_lifecycle(
            {"ticker": "AAPL"},
            exit_candidate={"ticker": "AAPL", "reason": "recovery_micro_time_stop"},
            claude_vote="HOLD",
        )

        self.assertEqual(decision.final_action, "SELL")
        self.assertFalse(decision.claude_override_allowed)
        self.assertIn("system_guard_precedes_claude", decision.warnings)

    def test_claude_sell_can_exit_without_system_guard(self) -> None:
        decision = decide_exit_lifecycle({"ticker": "AAPL"}, claude_vote="SELL")

        self.assertEqual(decision.final_action, "SELL")
        self.assertEqual(decision.reason, "claude_sell")
        self.assertTrue(decision.claude_override_allowed)

    def test_no_trigger_holds(self) -> None:
        decision = decide_exit_lifecycle({"ticker": "AAPL"}, claude_vote="HOLD")

        self.assertEqual(decision.final_action, "HOLD")
        self.assertEqual(decision.reason, "no_exit_trigger")


if __name__ == "__main__":
    unittest.main()
