"""스크리너 채널 attribution: KR 후보 row의 category 생성 검증."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import kis_api


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class KrVolumeRankCategoryTests(unittest.TestCase):
    def test_volume_rank_rows_carry_category(self):
        payload = {
            "output": [
                {
                    "mksc_shrn_iscd": "005930",
                    "hts_kor_isnm": "삼성전자",
                    "stck_prpr": "70000",
                    "prdy_ctrt": "1.5",
                    "acml_vol": "1000000",
                    "vol_tnrt": "2.3",
                }
            ]
        }
        with mock.patch.object(kis_api, "_kis_get", return_value=_FakeResp(payload)):
            rows = kis_api._kis_volume_rank("tok", vol_cnt="0", top_n=5, input_iscd="0001")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "volume_rank")
        self.assertEqual(rows[0]["market_type"], "KOSPI")
        # KR vol_ratio는 KIS 실값(회전율) — placeholder 아님
        self.assertEqual(rows[0]["vol_ratio"], 2.3)


class KrScreenCacheCategoryTests(unittest.TestCase):
    def _write_cache(self, path: Path, candidates):
        path.write_text(
            json.dumps(
                {"date": datetime.now().strftime("%Y-%m-%d"), "candidates": candidates},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_cache_rows_without_category_get_prev_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "kr_screen_cache.json"
            self._write_cache(
                cache_path,
                [
                    {"ticker": "005930", "price": 70000},
                    {"ticker": "000660", "price": 200000, "category": "volume_rank"},
                ],
            )
            with mock.patch.object(kis_api, "_KR_SCREEN_CACHE_PATH", cache_path):
                rows = kis_api._load_kr_screen_cache()
        self.assertEqual(rows[0]["category"], "prev_cache")
        # 원본 category가 있으면 유지
        self.assertEqual(rows[1]["category"], "volume_rank")

    def test_stale_cache_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "kr_screen_cache.json"
            cache_path.write_text(
                json.dumps({"date": "2020-01-01", "candidates": [{"ticker": "005930"}]}),
                encoding="utf-8",
            )
            with mock.patch.object(kis_api, "_KR_SCREEN_CACHE_PATH", cache_path):
                rows = kis_api._load_kr_screen_cache()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
