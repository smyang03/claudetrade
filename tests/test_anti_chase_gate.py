from __future__ import annotations

import unittest

from bot.anti_chase_gate import evaluate_anti_chase, normalize_mode


class AntiChaseGateTests(unittest.TestCase):
    def test_off_is_noop(self) -> None:
        v = evaluate_anti_chase(35.0, "off")
        self.assertEqual(v["decision"], "off")
        self.assertFalse(v["block"])

    def test_enforce_blocks_extreme_spike(self) -> None:
        v = evaluate_anti_chase(25.0, "enforce", threshold=20.0)
        self.assertEqual(v["decision"], "skip")
        self.assertTrue(v["block"])
        self.assertEqual(v["reason"], "extreme_spike_chase")

    def test_enforce_allows_moderate(self) -> None:
        # 중간 급등(8~20%)은 보존 — 검증상 정상/우수 코호트
        v = evaluate_anti_chase(15.0, "enforce", threshold=20.0)
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_shadow_would_skip_no_block(self) -> None:
        v = evaluate_anti_chase(25.0, "shadow", threshold=20.0)
        self.assertEqual(v["decision"], "would_skip")
        self.assertFalse(v["block"])

    def test_boundary_at_threshold_blocks(self) -> None:
        v = evaluate_anti_chase(20.0, "enforce", threshold=20.0)
        self.assertTrue(v["block"])  # >= threshold

    def test_fail_open_on_missing_max(self) -> None:
        for bad in (None, "", "n/a"):
            v = evaluate_anti_chase(bad, "enforce", threshold=20.0)
            self.assertEqual(v["decision"], "allow_no_data")
            self.assertFalse(v["block"])

    def test_custom_threshold(self) -> None:
        v = evaluate_anti_chase(13.0, "enforce", threshold=12.0)
        self.assertTrue(v["block"])
        v2 = evaluate_anti_chase(13.0, "enforce", threshold=25.0)
        self.assertFalse(v2["block"])

    def test_normalize_mode(self) -> None:
        self.assertEqual(normalize_mode("ENFORCE"), "enforce")
        self.assertEqual(normalize_mode("bogus"), "off")
        self.assertEqual(normalize_mode(None), "off")


if __name__ == "__main__":
    unittest.main()
