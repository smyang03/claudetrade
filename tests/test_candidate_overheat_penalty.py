"""C2/C3 후보 과열 페널티 회귀 테스트 (2026-06-20).

실측(ticker_selection_log) 근거:
- C2 KR vol_ratio: 1.5-2.0 fwd -3.07% / 2.0+ -4~6% (US는 vol_ratio 실값 부재 → 미적용)
- C3 change_pct >= 15% fwd -2.15%
페널티는 후보 점수만 낮춘다. 주문/매도/broker truth 무관.
"""
import unittest

from runtime.candidate_pool_runtime import (
    CandidateRecord,
    score_candidate,
    SOURCE_PENALTIES,
    VOL_OVERHEAT_MID_RATIO,
    VOL_OVERHEAT_HIGH_RATIO,
    CHANGE_OVERHEAT_PCT,
)


def _rec(market, *, vol_ratio=None, change_pct=None, sources=("most_actives",)):
    r = CandidateRecord(ticker="TST", market=market)
    r.sources = list(sources)
    feats = {}
    if vol_ratio is not None:
        feats["vol_ratio"] = vol_ratio
    if change_pct is not None:
        feats["change_pct"] = change_pct
    r.latest_features = feats
    return r


class C2VolOverheatTests(unittest.TestCase):
    def test_kr_vol_below_mid_no_penalty(self):
        r = score_candidate(_rec("KR", vol_ratio=1.0))
        self.assertNotIn("vol_overheat_mid", r.prompt_score_components)
        self.assertNotIn("vol_overheat_high", r.prompt_score_components)

    def test_kr_vol_mid_penalty(self):
        r = score_candidate(_rec("KR", vol_ratio=VOL_OVERHEAT_MID_RATIO))
        self.assertEqual(
            r.prompt_score_components.get("vol_overheat_mid"),
            -SOURCE_PENALTIES["vol_overheat_mid"],
        )

    def test_kr_vol_high_penalty(self):
        r = score_candidate(_rec("KR", vol_ratio=VOL_OVERHEAT_HIGH_RATIO + 1.0))
        self.assertEqual(
            r.prompt_score_components.get("vol_overheat_high"),
            -SOURCE_PENALTIES["vol_overheat_high"],
        )
        # high 적용 시 mid는 중복 적용 안 됨
        self.assertNotIn("vol_overheat_mid", r.prompt_score_components)

    def test_us_vol_no_penalty(self):
        # US는 vol_ratio 실값 부재 → 페널티 미적용(KR/US 분리)
        r = score_candidate(_rec("US", vol_ratio=5.0))
        self.assertNotIn("vol_overheat_mid", r.prompt_score_components)
        self.assertNotIn("vol_overheat_high", r.prompt_score_components)


class C3ChangeOverheatTests(unittest.TestCase):
    def test_change_below_threshold_no_penalty(self):
        r = score_candidate(_rec("US", change_pct=CHANGE_OVERHEAT_PCT - 1.0))
        self.assertNotIn("change_overheat", r.prompt_score_components)

    def test_change_overheat_penalty_both_markets(self):
        for mk in ("US", "KR"):
            r = score_candidate(_rec(mk, change_pct=CHANGE_OVERHEAT_PCT + 5.0))
            self.assertEqual(
                r.prompt_score_components.get("change_overheat"),
                -SOURCE_PENALTIES["change_overheat"],
                msg=f"market={mk}",
            )

    def test_negative_change_overheat_uses_abs(self):
        # 급락도 과열(abs) — 분출/패닉 양극단
        r = score_candidate(_rec("US", change_pct=-(CHANGE_OVERHEAT_PCT + 1.0)))
        self.assertIn("change_overheat", r.prompt_score_components)


if __name__ == "__main__":
    unittest.main()
