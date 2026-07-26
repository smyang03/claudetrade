"""US vol_ratio 실값 연결(rel_vol_shadow 승격) 계약 검증.

배경: US vol_ratio는 1.0 placeholder였고 전략 게이트가 이를 실값처럼 소비해
거래량 변별력이 구조적으로 0이었다. rel_vol_shadow가 실값이므로 토글로 승격한다.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kis_api


class UsVolRatioRealValueTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("US_VOL_RATIO_FROM_REL_VOL")

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("US_VOL_RATIO_FROM_REL_VOL", None)
        else:
            os.environ["US_VOL_RATIO_FROM_REL_VOL"] = self._saved

    def test_toggle_off_keeps_placeholder_and_marks_it(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "false"
        rows = [{"ticker": "AAPL", "vol_ratio": 1.0, "rel_vol_shadow": 3.42}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[0]["vol_ratio"], 1.0)
        self.assertTrue(rows[0]["vol_ratio_placeholder"])
        self.assertEqual(rows[0]["vol_ratio_source"], "placeholder")

    def test_toggle_on_promotes_real_value(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "true"
        rows = [{"ticker": "AAPL", "vol_ratio": 1.0, "rel_vol_shadow": 3.42}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[0]["vol_ratio"], 3.42)
        self.assertFalse(rows[0]["vol_ratio_placeholder"])
        self.assertEqual(rows[0]["vol_ratio_source"], "rel_vol_shadow")

    def test_toggle_on_without_real_value_stays_placeholder(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "true"
        rows = [{"ticker": "NVDA", "vol_ratio": 1.0}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[0]["vol_ratio"], 1.0)
        self.assertTrue(rows[0]["vol_ratio_placeholder"])

    def test_non_positive_real_value_is_not_promoted(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "true"
        rows = [{"ticker": "F", "vol_ratio": 1.0, "rel_vol_shadow": 0.0}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[0]["vol_ratio"], 1.0)
        self.assertTrue(rows[0]["vol_ratio_placeholder"])

    def test_malformed_real_value_does_not_raise(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "true"
        rows = [{"ticker": "T", "vol_ratio": 1.0, "rel_vol_shadow": "not-a-number"}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[0]["vol_ratio"], 1.0)
        self.assertTrue(rows[0]["vol_ratio_placeholder"])

    def test_non_dict_rows_are_skipped(self):
        os.environ["US_VOL_RATIO_FROM_REL_VOL"] = "true"
        rows = [None, "x", {"ticker": "A", "rel_vol_shadow": 2.0}]
        kis_api._promote_us_vol_ratio_from_rel_vol(rows)
        self.assertEqual(rows[2]["vol_ratio"], 2.0)


if __name__ == "__main__":
    unittest.main()
