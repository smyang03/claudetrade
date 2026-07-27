"""PathB RR 앵커 3택(low/mid/high) 계약 검증.

배경 (실측 2026-07-28):
  2026-07-01 커밋 d44e99a가 risk 앵커를 buy_zone_low → buy_zone_high로 바꾼 뒤
    존 폭      2.52% → 0.83%   (RR을 맞추려 존을 좁힘)
    존 도달률    52% → 8%
    세션당 플랜  12.3 → 1.5
    Path B 체결 265건(5~6월) → 4건(7월)
  RR = reward/risk에서 risk = anchor - stop이므로 앵커를 존 상단으로 올리면
  risk가 커져 RR이 떨어지고, 그걸 되돌리는 유일한 수단이 존을 좁히는 것이었다.

  7/1 이전 플랜 711건 재계산(reward는 존 상단 고정, 캡 6%):
    low   RR중앙 2.17 / ≥1.2 통과 98%   ← 게이트가 사실상 무력
    mid   RR중앙 1.33 / ≥1.2 통과 61%   ← 변별력 있는 구간
    high  RR중앙 1.00 / ≥1.2 통과 27%   ← 과잉 차단

  reward는 buy_zone_high 고정을 유지한다 — 기존 low/high 두 모드의 계약이
  test_pathb_entry_quality_gates에 고정돼 있고, 앵커 도입이 그걸 건드리면
  변경 범위가 불필요하게 커진다.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision import claude_price_plan as cpp  # noqa: E402

_ENV = ("PATHB_REWARD_RISK_ANCHOR", "PATHB_CONSISTENT_REWARD_RISK")


class RewardRiskAnchorTests(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in _ENV}
        for k in _ENV:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # ---- 앵커 해석 ----
    def test_default_is_low_when_nothing_set(self):
        self.assertEqual(cpp._reward_risk_anchor(), "low")

    def test_legacy_toggle_on_means_high(self):
        """PATHB_REWARD_RISK_ANCHOR 미설정 시 기존 토글 동작을 그대로 따른다."""
        os.environ["PATHB_CONSISTENT_REWARD_RISK"] = "true"
        self.assertEqual(cpp._reward_risk_anchor(), "high")

    def test_legacy_toggle_off_means_low(self):
        os.environ["PATHB_CONSISTENT_REWARD_RISK"] = "false"
        self.assertEqual(cpp._reward_risk_anchor(), "low")

    def test_explicit_anchor_overrides_legacy_toggle(self):
        os.environ["PATHB_CONSISTENT_REWARD_RISK"] = "true"
        os.environ["PATHB_REWARD_RISK_ANCHOR"] = "mid"
        self.assertEqual(cpp._reward_risk_anchor(), "mid")

    def test_invalid_anchor_falls_back_to_legacy(self):
        os.environ["PATHB_REWARD_RISK_ANCHOR"] = "banana"
        os.environ["PATHB_CONSISTENT_REWARD_RISK"] = "true"
        self.assertEqual(cpp._reward_risk_anchor(), "high")

    def test_anchor_is_case_insensitive(self):
        os.environ["PATHB_REWARD_RISK_ANCHOR"] = "  MID  "
        self.assertEqual(cpp._reward_risk_anchor(), "mid")

    # ---- 앵커 가격 ----
    def test_anchor_price_per_mode(self):
        cases = {"low": 98.0, "mid": 99.0, "high": 100.0}
        for mode, expected in cases.items():
            with self.subTest(mode=mode):
                os.environ["PATHB_REWARD_RISK_ANCHOR"] = mode
                self.assertAlmostEqual(cpp._anchor_price(98.0, 100.0), expected)

    def test_mid_is_between_low_and_high(self):
        os.environ["PATHB_REWARD_RISK_ANCHOR"] = "mid"
        mid = cpp._anchor_price(90.0, 110.0)
        self.assertGreater(mid, 90.0)
        self.assertLess(mid, 110.0)
        self.assertAlmostEqual(mid, 100.0)

    def test_degenerate_zone_mid_equals_bounds(self):
        """존 폭이 0이면 세 앵커가 같은 값이어야 한다(0 나눗셈 방어는 상위에서)."""
        for mode in ("low", "mid", "high"):
            os.environ["PATHB_REWARD_RISK_ANCHOR"] = mode
            self.assertAlmostEqual(cpp._anchor_price(100.0, 100.0), 100.0)

    # ---- RR 판정에 실제로 반영되는가 ----
    def _rr_of(self, anchor: str, *, low=98.0, high=100.0, stop=96.0, target=102.6):
        """같은 플랜을 앵커만 바꿔 RR을 계산."""
        os.environ["PATHB_REWARD_RISK_ANCHOR"] = anchor
        risk = cpp._anchor_price(low, high) - stop
        reward = target - high          # reward는 존 상단 고정 (계약 유지)
        return reward / risk

    def test_anchor_ordering_low_gives_highest_rr(self):
        """앵커가 높아질수록 risk가 커져 RR이 낮아진다 — 붕괴의 기전."""
        rr_low = self._rr_of("low")
        rr_mid = self._rr_of("mid")
        rr_high = self._rr_of("high")
        self.assertGreater(rr_low, rr_mid)
        self.assertGreater(rr_mid, rr_high)
        self.assertAlmostEqual(rr_low, 1.3, places=2)    # (102.6-100)/(98-96)
        self.assertAlmostEqual(rr_mid, 0.8667, places=3)  # /(99-96)
        self.assertAlmostEqual(rr_high, 0.65, places=2)   # /(100-96)

    def test_mid_lets_wider_zone_pass_than_high(self):
        """존 폭 3.5%대 플랜이 mid에서는 살고 high에서는 죽는 구간이 실재한다.

        존 96.5~100(폭 3.5%), stop 96, target 103 → reward 3.0
          mid  risk = 98.25-96 = 2.25 → RR 1.33  통과
          high risk = 100-96  = 4.00 → RR 0.75  차단
        이 구간이 7/1 이후 사라진 플랜들이다(6월 존 폭 중앙 2.52%).
        """
        kw = dict(low=96.5, high=100.0, stop=96.0, target=103.0)
        rr_mid = self._rr_of("mid", **kw)
        rr_high = self._rr_of("high", **kw)
        self.assertGreaterEqual(rr_mid, 1.2)   # 통과
        self.assertLess(rr_high, 1.2)          # 차단


if __name__ == "__main__":
    unittest.main()
