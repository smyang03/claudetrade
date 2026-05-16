from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from phase1_trainer import kr_news_collector as collector


class _FakeKisResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class KrNewsCollectorKisTests(unittest.TestCase):
    def test_fetch_kis_news_normalizes_and_filters_target_date(self) -> None:
        response = _FakeKisResponse({
            "rt_cd": "0",
            "output": [
                {
                    "data_dt": "20260515",
                    "data_tm": "091530",
                    "dorg": "ProviderA",
                    "hts_pbnt_titl_cntt": "Samsung headline",
                    "cntt_usiq_srno": "1",
                    "iscd1": "005930",
                    "kor_isnm1": "Samsung",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "092000",
                    "dorg": "ProviderA",
                    "hts_pbnt_titl_cntt": "Samsung headline",
                    "cntt_usiq_srno": "duplicate",
                    "iscd1": "005930",
                },
                {
                    "data_dt": "20260514",
                    "data_tm": "153000",
                    "dorg": "ProviderB",
                    "hts_pbnt_titl_cntt": "Old headline",
                    "cntt_usiq_srno": "old",
                    "iscd1": "005930",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "100000",
                    "dorg": "ProviderC",
                    "hts_pbnt_titl_cntt": "Other stock headline",
                    "cntt_usiq_srno": "other",
                    "iscd1": "000660",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "101010",
                    "dorg": "",
                    "hts_pbnt_titl_cntt": "Second headline",
                    "cntt_usiq_srno": "2",
                    "iscd1": "005930",
                },
            ],
        })

        with patch.object(collector, "get_kis_market_profile", return_value=SimpleNamespace(base_url="https://kis.example")), \
             patch.object(collector, "get_access_token", return_value="token"), \
             patch.object(collector, "_headers", return_value={"authorization": "Bearer token"}), \
             patch.object(collector, "_kis_get", return_value=response) as kis_get:
            items = collector.fetch_kis_news("005930", "2026-05-15", max_results=10)

        self.assertEqual([item["title"] for item in items], ["Samsung headline", "Second headline"])
        self.assertEqual(items[0]["source"], "KIS")
        self.assertEqual(items[0]["provider"], "ProviderA")
        self.assertEqual(items[0]["date"], "2026-05-15")
        self.assertEqual(items[0]["published_at"], "2026-05-15T09:15:30+09:00")
        self.assertEqual(items[0]["ticker"], "005930")
        self.assertEqual(items[0]["related_tickers"], ["005930"])
        self.assertEqual(items[0]["related_names"], ["Samsung"])
        called_params = kis_get.call_args.kwargs["params"]
        self.assertEqual(called_params["FID_INPUT_ISCD"], "005930")
        self.assertEqual(called_params["FID_INPUT_DATE_1"], "20260515")

    def test_collect_day_uses_kis_and_leaves_naver_legacy_disabled(self) -> None:
        kis_item = {
            "source": "KIS",
            "date": "2026-05-15",
            "title": "Samsung headline",
            "content": "",
            "url": "",
        }
        naver = Mock(return_value=[])

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(collector, "NEWS_DIR", Path(tmpdir)), \
             patch.object(collector, "TARGET_CORPS", {"005930": "Samsung"}), \
             patch.object(collector, "ENABLE_NAVER_LEGACY", False), \
             patch.object(collector, "fetch_market_news", return_value=[]), \
             patch.object(collector, "fetch_kis_news", return_value=[kis_item]), \
             patch.object(collector, "fetch_naver_news", naver), \
             patch.object(collector, "fetch_bigkinds_news", return_value=[]), \
             patch.object(collector, "get_dart_corp_code", return_value=""), \
             patch.object(collector.time, "sleep", lambda *_args, **_kwargs: None):
            result = collector.collect_day("2026-05-15")

        self.assertEqual(result["corp_news"]["005930"]["count"], 1)
        self.assertEqual(result["corp_news"]["005930"]["items"][0]["source"], "KIS")
        self.assertEqual(result["target_source"], "fallback_target_corps")
        self.assertEqual(result["target_tickers"], ["005930"])
        self.assertEqual(result["provider_counts"], {"KIS": 1})
        naver.assert_not_called()

    def test_collect_day_force_false_reuses_matching_existing_file(self) -> None:
        existing = {
            "date": "2026-05-15",
            "market_news": [],
            "corp_news": {"005930": {"name": "Samsung", "items": [], "count": 0}},
            "disclosures": {},
            "target_tickers": ["005930"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            news_dir = Path(tmpdir)
            (news_dir / "2026-05-15.json").write_text(
                json.dumps(existing, ensure_ascii=False),
                encoding="utf-8",
            )
            kis_news = Mock(return_value=[])
            with patch.object(collector, "NEWS_DIR", news_dir), \
                 patch.object(collector, "fetch_kis_news", kis_news):
                result = collector.collect_day(
                    "2026-05-15",
                    targets={"005930": "Samsung"},
                    force=False,
                )

        self.assertEqual(result["target_tickers"], ["005930"])
        kis_news.assert_not_called()


if __name__ == "__main__":
    unittest.main()
