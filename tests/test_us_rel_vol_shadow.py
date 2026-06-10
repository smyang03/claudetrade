"""US rel_vol_shadow 계측 (vol_ratio 입력 품질 1단계) 검증."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kis_api


def _reset_cache():
    kis_api._US_AVG_VOL_CACHE["date"] = ""
    kis_api._US_AVG_VOL_CACHE["by_ticker"] = {}


class UsAvgDailyVolumeTests(unittest.TestCase):
    def setUp(self):
        _reset_cache()

    def _write_csv(self, tmp: Path, ticker: str, vols):
        d = tmp / "data" / "price" / "us"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["date,open,high,low,close,volume"]
        for i, v in enumerate(vols):
            lines.append(f"2026-05-{i+1:02d},10,11,9,10,{v}")
        (d / f"us_{ticker}.csv").write_text("\n".join(lines), encoding="utf-8")
        return tmp

    def test_avg_volume_from_csv_tail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_csv(Path(tmp), "TEST", [100] * 5 + [200] * 20)
            with mock.patch.object(kis_api, "_Path", lambda *_a, **_k: root / "kis_api.py"):
                avg = kis_api._us_avg_daily_volume("TEST", days=20)
        self.assertEqual(avg, 200.0)

    def test_missing_csv_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "data" / "price" / "us").mkdir(parents=True)
            with mock.patch.object(kis_api, "_Path", lambda *_a, **_k: root / "kis_api.py"):
                self.assertIsNone(kis_api._us_avg_daily_volume("NOPE"))

    def test_insufficient_samples_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = self._write_csv(Path(tmp), "THIN", [100] * 3)
            with mock.patch.object(kis_api, "_Path", lambda *_a, **_k: root / "kis_api.py"):
                self.assertIsNone(kis_api._us_avg_daily_volume("THIN", min_samples=10))


class AnnotateRelVolShadowTests(unittest.TestCase):
    def setUp(self):
        _reset_cache()

    def _annotate(self, rows, *, fraction=0.25, avg=1_000_000):
        with mock.patch.object(
            kis_api, "_us_projected_dollar_volume_context",
            return_value={"elapsed_session_fraction": fraction, "elapsed_min": 90.0},
        ), mock.patch.object(
            kis_api, "_us_avg_daily_volume",
            side_effect=lambda t, **k: avg if t == "AAA" else None,
        ), mock.patch.dict(os.environ, {"US_REL_VOL_SHADOW_ENABLED": "true"}):
            kis_api._annotate_us_rel_vol_shadow(rows)
        return rows

    def test_rel_vol_computed_with_session_fraction(self):
        rows = [{"ticker": "AAA", "volume": 500_000, "vol_ratio": 1.0}]
        self._annotate(rows, fraction=0.25, avg=1_000_000)
        # 500k / (1M * 0.25) = 2.0 — 평소 대비 2배 페이스
        self.assertEqual(rows[0]["rel_vol_shadow"], 2.0)
        self.assertEqual(rows[0]["rel_vol_shadow_avg20"], 1_000_000)
        # 기존 vol_ratio(전략 소비)는 불변
        self.assertEqual(rows[0]["vol_ratio"], 1.0)

    def test_no_baseline_leaves_row_untouched(self):
        rows = [{"ticker": "BBB", "volume": 500_000, "vol_ratio": 1.0}]
        self._annotate(rows)
        self.assertNotIn("rel_vol_shadow", rows[0])

    def test_volume_missing_rows_skipped(self):
        rows = [{"ticker": "AAA", "volume": 500_000, "volume_missing": True}]
        self._annotate(rows)
        self.assertNotIn("rel_vol_shadow", rows[0])

    def test_preopen_or_early_session_skipped(self):
        # 개장 전(elapsed_min=None) / 직후(15분 미만)엔 Yahoo volume이 전일 누적치라 기록 금지
        for elapsed in (None, 5.0):
            rows = [{"ticker": "AAA", "volume": 500_000}]
            with mock.patch.object(
                kis_api, "_us_projected_dollar_volume_context",
                return_value={"elapsed_session_fraction": 0.05, "elapsed_min": elapsed},
            ), mock.patch.object(
                kis_api, "_us_avg_daily_volume", return_value=1_000_000,
            ), mock.patch.dict(os.environ, {"US_REL_VOL_SHADOW_ENABLED": "true"}):
                kis_api._annotate_us_rel_vol_shadow(rows)
            self.assertNotIn("rel_vol_shadow", rows[0])

    def test_disabled_env_skips_all(self):
        rows = [{"ticker": "AAA", "volume": 500_000}]
        with mock.patch.dict(os.environ, {"US_REL_VOL_SHADOW_ENABLED": "false"}):
            kis_api._annotate_us_rel_vol_shadow(rows)
        self.assertNotIn("rel_vol_shadow", rows[0])


if __name__ == "__main__":
    unittest.main()
