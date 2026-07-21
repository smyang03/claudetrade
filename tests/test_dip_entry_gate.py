from __future__ import annotations

import unittest

from bot.dip_entry_gate import (
    DEFAULT_THRESHOLD,
    evaluate_dip_entry,
    normalize_mode,
    parse_markets,
)


class DipEntryGateTests(unittest.TestCase):
    def test_off_is_noop(self) -> None:
        v = evaluate_dip_entry(-10.0, "off", market="US")
        self.assertEqual(v["decision"], "off")
        self.assertFalse(v["block"])

    def test_enforce_blocks_deep_dip_us(self) -> None:
        v = evaluate_dip_entry(-6.2, "enforce", market="US")
        self.assertEqual(v["decision"], "block")
        self.assertTrue(v["block"])
        self.assertEqual(v["reason"], "dip_rebound_bet")

    def test_enforce_allows_moderate(self) -> None:
        v = evaluate_dip_entry(-4.9, "enforce", market="US")
        self.assertEqual(v["decision"], "allow")
        self.assertFalse(v["block"])

    def test_shadow_would_block_no_block(self) -> None:
        v = evaluate_dip_entry(-8.0, "shadow", market="US")
        self.assertEqual(v["decision"], "would_block")
        self.assertFalse(v["block"])

    def test_kr_not_targeted_by_default(self) -> None:
        # KR은 정반대(급등추격이 손실원) — 기본 US 전용
        v = evaluate_dip_entry(-20.0, "enforce", market="KR")
        self.assertEqual(v["decision"], "not_applicable_market")
        self.assertFalse(v["block"])

    def test_fail_open_missing_feature(self) -> None:
        v = evaluate_dip_entry(None, "enforce", market="US")
        self.assertEqual(v["decision"], "allow_no_feature")
        self.assertFalse(v["block"])

    def test_custom_threshold_and_markets(self) -> None:
        v = evaluate_dip_entry(-3.5, "enforce", market="US", threshold=-3.0)
        self.assertTrue(v["block"])
        v2 = evaluate_dip_entry(-9.0, "enforce", market="KR", markets="US,KR")
        self.assertTrue(v2["block"])

    def test_normalize_and_parse(self) -> None:
        self.assertEqual(normalize_mode("ENFORCE"), "enforce")
        self.assertEqual(normalize_mode("bogus"), "off")
        self.assertEqual(parse_markets(None), ("US",))
        self.assertEqual(parse_markets("us, kr"), ("US", "KR"))
        self.assertEqual(DEFAULT_THRESHOLD, -5.0)


if __name__ == "__main__":
    unittest.main()
