from __future__ import annotations

import unittest

from bot.regime_entry_gate import (
    evaluate_regime_entry_gate,
    normalize_mode,
    parse_block_modes,
    DEFAULT_BLOCK_MODES,
)


class RegimeEntryGateTests(unittest.TestCase):
    def test_off_is_noop(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "off")
        self.assertEqual(v["decision"], "off")
        self.assertFalse(v["block"])

    def test_enforce_blocks_default_cautious(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "enforce")
        self.assertEqual(v["decision"], "skip")
        self.assertTrue(v["block"])

    def test_enforce_allows_good_regime(self) -> None:
        v = evaluate_regime_entry_gate("MODERATE_BULL", "enforce")
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_shadow_would_skip_no_block(self) -> None:
        v = evaluate_regime_entry_gate("CAUTIOUS", "shadow")
        self.assertEqual(v["decision"], "would_skip")
        self.assertFalse(v["block"])

    def test_fail_open_unknown_regime(self) -> None:
        v = evaluate_regime_entry_gate("", "enforce")
        self.assertEqual(v["decision"], "allow_no_regime")
        self.assertFalse(v["block"])

    def test_custom_block_modes(self) -> None:
        # MILD_BEAR·CAUTIOUS 둘 다 차단
        v = evaluate_regime_entry_gate("MILD_BEAR", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertTrue(v["block"])
        # MILD_BULL은 목록에 없으면 통과
        v2 = evaluate_regime_entry_gate("MILD_BULL", "enforce", block_modes="CAUTIOUS,MILD_BEAR")
        self.assertFalse(v2["block"])

    def test_case_insensitive(self) -> None:
        v = evaluate_regime_entry_gate("cautious", "enforce")
        self.assertTrue(v["block"])

    def test_parse_block_modes_default(self) -> None:
        self.assertEqual(parse_block_modes(None), DEFAULT_BLOCK_MODES)
        self.assertEqual(parse_block_modes("MILD_BEAR, CAUTIOUS"), ("MILD_BEAR", "CAUTIOUS"))

    def test_normalize_mode(self) -> None:
        self.assertEqual(normalize_mode("ENFORCE"), "enforce")
        self.assertEqual(normalize_mode("bogus"), "off")


if __name__ == "__main__":
    unittest.main()
