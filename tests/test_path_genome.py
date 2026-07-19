from __future__ import annotations

import unittest

from bot.path_genome import classify_path_genome


class PathGenomeTests(unittest.TestCase):
    def test_dip_then_run_confirmed_ride_candidate(self) -> None:
        g = classify_path_genome(
            entry_at="2026-07-20T09:00:00",
            low_at="2026-07-20T09:10:00",   # 저점 먼저
            peak_at="2026-07-20T11:00:00",  # 고점 나중 → 회복형
            mfe_pct=5.0, mae_pct=-2.0, pnl_pct=4.0,
        )
        self.assertEqual(g["shape"], "dip_then_run")
        self.assertTrue(g["early_confirmed"])
        self.assertTrue(g["ride_candidate"])
        self.assertEqual(g["outcome_tag"], "confirmed_win")
        self.assertEqual(g["time_to_peak_min"], 120.0)
        self.assertEqual(g["time_to_low_min"], 10.0)

    def test_run_then_giveback_confirmed_but_lost(self) -> None:
        g = classify_path_genome(
            peak_at="2026-07-20T09:10:00",  # 고점 먼저
            low_at="2026-07-20T11:00:00",   # 저점 나중 → 반납형
            mfe_pct=3.0, mae_pct=-3.0, pnl_pct=-2.5,
        )
        self.assertEqual(g["shape"], "run_then_giveback")
        self.assertTrue(g["early_confirmed"])   # 녹색은 갔음
        self.assertFalse(g["ride_candidate"])    # 반납형이라 연장 후보 아님
        self.assertEqual(g["outcome_tag"], "confirmed_but_lost")

    def test_unconfirmed_low_mfe(self) -> None:
        g = classify_path_genome(
            peak_at="2026-07-20T09:05:00", low_at="2026-07-20T09:30:00",
            mfe_pct=0.3, mae_pct=-2.0, pnl_pct=-1.5,
        )
        self.assertFalse(g["early_confirmed"])
        self.assertFalse(g["ride_candidate"])
        self.assertEqual(g["outcome_tag"], "unconfirmed_loss")

    def test_partial_no_times(self) -> None:
        g = classify_path_genome(mfe_pct=2.0, mae_pct=-1.0, pnl_pct=1.5)
        self.assertEqual(g["shape"], "unknown")   # 시각 없으면 형태 미상
        self.assertTrue(g["early_confirmed"])
        self.assertEqual(g["outcome_tag"], "confirmed_win")
        self.assertNotIn("time_to_peak_min", g)

    def test_empty(self) -> None:
        g = classify_path_genome()
        self.assertEqual(g["shape"], "unknown")
        self.assertFalse(g["early_confirmed"])
        self.assertEqual(g["outcome_tag"], "unconfirmed_loss")


if __name__ == "__main__":
    unittest.main()
