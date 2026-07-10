from __future__ import annotations

import unittest

from tools.early_tier_shadow_review import summarize, tier_counterfactual


class EarlyTierShadowTests(unittest.TestCase):
    def _t(self, entry, mfe, net):
        return {"entry": entry, "mfe": mfe, "net": net}

    def test_reached_partial_plus_runner(self):
        # entry 100, target 106(=+6% >0), level 2.3, f 0.5, cost 0.5, mfe 3.0(>=level), 실제 net -1.0
        cf = tier_counterfactual(self._t(100.0, 3.0, -1.0), 106.0, level=2.3, f=0.5, cost=0.5)
        self.assertIsNotNone(cf)
        self.assertTrue(cf["reached"])
        # 0.5*(2.3-0.5) + 0.5*(-1.0) = 0.9 - 0.5 = 0.4
        self.assertAlmostEqual(cf["cf_net"], 0.4, places=6)

    def test_not_reached_keeps_actual(self):
        cf = tier_counterfactual(self._t(100.0, 1.5, -2.0), 106.0, level=2.3, f=0.5, cost=0.5)
        self.assertFalse(cf["reached"])
        self.assertAlmostEqual(cf["cf_net"], -2.0, places=6)  # 미도달=실제net 유지

    def test_nonpositive_target_excluded(self):
        # sell_target 99 < entry 100 → target_pct<=0 → None
        self.assertIsNone(tier_counterfactual(self._t(100.0, 3.0, 1.0), 99.0, level=2.3, f=0.5, cost=0.5))

    def test_runner_preserved_when_f_below_one(self):
        # 큰 러너(mfe 10, 실제 net +9): f=0.5면 상방 절반 보존
        cf = tier_counterfactual(self._t(100.0, 10.0, 9.0), 106.0, level=2.3, f=0.5, cost=0.5)
        # 0.5*1.8 + 0.5*9 = 0.9 + 4.5 = 5.4  (하드청산 f=1.0이면 1.8로 러너 학살)
        self.assertAlmostEqual(cf["cf_net"], 5.4, places=6)

    def test_summarize_shapes(self):
        rows = [
            {"actual_net": -1.0, "cf_net": 0.4, "reached": True},
            {"actual_net": -2.0, "cf_net": -2.0, "reached": False},
        ]
        s = summarize(rows)
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(s["reach_rate"], 0.5, places=6)
        self.assertAlmostEqual(s["delta_mean"], mean_delta := ((0.4 + -2.0) / 2) - ((-1.0 + -2.0) / 2), places=6)

    def test_summarize_empty(self):
        self.assertEqual(summarize([]), {"n": 0})


if __name__ == "__main__":
    unittest.main()
