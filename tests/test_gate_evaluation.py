from __future__ import annotations

import unittest

from runtime.gate_evaluation import (
    apply_size_cap_once,
    build_judgment_gate_evaluation,
    unconfirmed_soft_cap,
)


class GateEvaluationTests(unittest.TestCase):
    def test_unconfirmed_cap_is_soft_safety_output(self) -> None:
        cap = unconfirmed_soft_cap("ok_unconfirmed", cap_pct=70)

        self.assertTrue(cap["applies"])
        self.assertEqual(cap["size_cap_pct"], 70)
        self.assertIn("unconfirmed_phase_size_cap", cap["warnings"])

    def test_unconfirmed_cap_ignores_other_gate_reasons(self) -> None:
        cap = unconfirmed_soft_cap("non_executable_judgment_phase:preopen_watch", cap_pct=70)

        self.assertFalse(cap["applies"])
        self.assertIsNone(cap["size_cap_pct"])
        self.assertEqual(cap["warnings"], [])

    def test_size_cap_applies_once(self) -> None:
        capped, applied = apply_size_cap_once(90, size_cap_pct=70)
        self.assertEqual(capped, 70)
        self.assertTrue(applied)

        capped_again, applied_again = apply_size_cap_once(capped, size_cap_pct=70)
        self.assertEqual(capped_again, 70)
        self.assertFalse(applied_again)

    def test_judgment_gate_block_has_hard_block_final_action(self) -> None:
        gate = build_judgment_gate_evaluation(
            market="US",
            ticker="AAPL",
            judgment_gate_ok=False,
            judgment_gate_reason="non_executable_judgment_phase:preopen_watch",
        )

        self.assertEqual(gate.final_action, "HARD_BLOCK")
        self.assertEqual(gate.blocker, "judgment_not_executable")
        self.assertFalse(gate.hard_safety["passed"])


if __name__ == "__main__":
    unittest.main()
