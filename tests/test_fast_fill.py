import os
import unittest

from runtime import fast_fill


def _clear_env():
    for k in list(os.environ):
        if "FAST_FILL" in k:
            del os.environ[k]


class FastFillModeTests(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_default_mode_is_shadow(self):
        self.assertEqual(fast_fill.mode("US"), "shadow")
        self.assertEqual(fast_fill.mode("KR"), "shadow")
        self.assertTrue(fast_fill.is_active("US"))

    def test_per_market_override(self):
        os.environ["KR_PATHB_FAST_FILL_MODE"] = "off"
        os.environ["US_PATHB_FAST_FILL_MODE"] = "enforce"
        self.assertEqual(fast_fill.mode("KR"), "off")
        self.assertEqual(fast_fill.mode("US"), "enforce")
        self.assertFalse(fast_fill.is_active("KR"))

    def test_off_disables(self):
        os.environ["PATHB_FAST_FILL_MODE"] = "off"
        self.assertIsNone(fast_fill.requote_decision(
            market="US", limit_price=100, current=101, target=110))


class FastFillDecisionTests(unittest.TestCase):
    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_no_action_when_price_below_limit(self):
        # 정상 체결권(현재가 <= limit) → fast-fill 무관
        self.assertIsNone(fast_fill.requote_decision(
            market="US", limit_price=100, current=99, target=110))

    def test_requote_within_bound_093370_like(self):
        # 093370류: limit 17900, target 18500, 현재 18000 → bound(1%=18079) 안 → 재호가
        d = fast_fill.requote_decision(
            market="KR", limit_price=17900, current=18000, target=18500)
        self.assertEqual(d["action"], "REQUOTE")
        self.assertAlmostEqual(d["requote_price"], 18000)
        self.assertGreater(d["remaining_reward_pct"], 0)

    def test_miss_when_current_above_chase_cap(self):
        # 현재 18460: chase cap 1%(18079) 위 → MISS(추격 안 함, 손실 차단)
        d = fast_fill.requote_decision(
            market="KR", limit_price=17900, current=18460, target=18500)
        self.assertEqual(d["action"], "MISS")
        self.assertEqual(d["reason"], "current_above_bound")

    def test_miss_when_current_at_or_above_target(self):
        d = fast_fill.requote_decision(
            market="US", limit_price=100, current=110, target=110)
        self.assertEqual(d["action"], "MISS")
        self.assertEqual(d["reason"], "current_at_or_above_target")

    def test_reward_floor_caps_requote(self):
        # min_reward 1.5%: target 18500 → reward_ceiling 18222.5. chase 1%=18079가 더 낮아 18079.
        # 현재 18100 < 18079? no → MISS. 현재 18050 < 18079 → REQUOTE.
        d_miss = fast_fill.requote_decision(
            market="KR", limit_price=17900, current=18100, target=18500)
        self.assertEqual(d_miss["action"], "MISS")
        d_ok = fast_fill.requote_decision(
            market="KR", limit_price=17900, current=18050, target=18500)
        self.assertEqual(d_ok["action"], "REQUOTE")

    def test_cancel_threshold_caps_requote(self):
        # cancel 임계가 매우 낮으면 그 아래로만 재호가 허용
        d = fast_fill.requote_decision(
            market="US", limit_price=100, current=100.5, target=110, cancel_threshold=100.2)
        self.assertEqual(d["action"], "MISS")
        self.assertEqual(d["reason"], "current_above_bound")

    def test_invalid_inputs_return_none(self):
        self.assertIsNone(fast_fill.requote_decision(
            market="US", limit_price=0, current=101, target=110))
        self.assertIsNone(fast_fill.requote_decision(
            market="US", limit_price=100, current=0, target=110))


if __name__ == "__main__":
    unittest.main()
