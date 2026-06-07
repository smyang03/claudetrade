from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from phase1_trainer import us_news_collector as collector


class _FakeKisResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class UsNewsCollectorTargetsTests(unittest.TestCase):
    def test_fetch_kis_news_filters_target_date_symbol_and_duplicates(self) -> None:
        response = _FakeKisResponse({
            "outblock1": [
                {
                    "data_dt": "20260515",
                    "data_tm": "091530",
                    "source": "KISProvider",
                    "hts_pbnt_titl_cntt": "Cisco headline",
                    "news_key": "1",
                    "symb": "CSCO",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "091600",
                    "source": "KISProvider",
                    "hts_pbnt_titl_cntt": "Cisco headline",
                    "news_key": "duplicate",
                    "symb": "CSCO",
                },
                {
                    "data_dt": "20260514",
                    "data_tm": "120000",
                    "source": "KISProvider",
                    "hts_pbnt_titl_cntt": "Old headline",
                    "news_key": "old",
                    "symb": "CSCO",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "130000",
                    "source": "KISProvider",
                    "hts_pbnt_titl_cntt": "Other symbol headline",
                    "news_key": "other",
                    "symb": "MSFT",
                },
                {
                    "data_dt": "20260515",
                    "data_tm": "140000",
                    "source": "",
                    "title": "Second Cisco headline",
                    "news_key": "2",
                    "symb": "CSCO",
                },
            ]
        })

        with patch.object(collector, "get_kis_market_profile", return_value=SimpleNamespace(base_url="https://kis.example")), \
             patch.object(collector, "get_access_token", return_value="token"), \
             patch.object(collector, "_get_us_quote_codes", return_value=("NAS", "NAS")), \
             patch.object(collector, "_headers", return_value={"authorization": "Bearer token"}), \
             patch.object(collector, "_kis_get", return_value=response) as kis_get:
            items = collector.fetch_kis_news("CSCO", "2026-05-15")

        self.assertEqual([item["title"] for item in items], ["Cisco headline", "Second Cisco headline"])
        self.assertEqual(items[0]["provider"], "KISProvider")
        self.assertEqual(items[0]["date"], "2026-05-15")
        self.assertEqual(items[0]["published_at"], "2026-05-15T09:15:30+09:00")
        self.assertEqual(items[0]["ticker"], "CSCO")
        called_params = kis_get.call_args.kwargs["params"]
        self.assertEqual(called_params["SYMB"], "CSCO")
        self.assertEqual(called_params["DATA_DT"], "20260515")

    def test_collect_day_uses_explicit_targets_and_excludes_market_etfs(self) -> None:
        fh_item = {
            "source": "Finnhub",
            "date": "2026-05-15",
            "title": "Cisco headline",
            "content": "",
            "url": "",
            "ticker": "CSCO",
        }

        with tempfile.TemporaryDirectory() as tmpdir, \
             patch.object(collector, "NEWS_DIR", Path(tmpdir)), \
             patch.object(collector, "FINNHUB_KEY", "key"), \
             patch.object(collector, "AV_KEY", ""), \
             patch.object(collector, "fetch_market_overview", return_value=[]), \
             patch.object(collector, "fetch_finnhub_news", return_value=[fh_item]) as finnhub, \
             patch.object(collector, "fetch_kis_news", return_value=[]), \
             patch.object(collector, "fetch_sec_filings", return_value=[]), \
             patch.object(collector.time, "sleep", lambda *_args, **_kwargs: None):
            result = collector.collect_day(
                "2026-05-15",
                targets={"CSCO": "Cisco", "SPY": "S&P ETF"},
                force=True,
            )

        self.assertEqual(list(result["corp_news"]), ["CSCO"])
        self.assertEqual(result["target_tickers"], ["CSCO"])
        self.assertEqual(result["target_source"], "explicit_targets")
        self.assertEqual(result["provider_counts"], {"Finnhub": 1})
        finnhub.assert_called_once_with("CSCO", "2026-05-15")

    def test_collect_day_force_false_reuses_matching_existing_file(self) -> None:
        existing = {
            "date": "2026-05-15",
            "market_news": [],
            "corp_news": {"CSCO": {"name": "Cisco", "items": [], "count": 0}},
            "target_tickers": ["CSCO"],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            news_dir = Path(tmpdir)
            (news_dir / "2026-05-15.json").write_text(
                json.dumps(existing, ensure_ascii=False),
                encoding="utf-8",
            )
            market_news = Mock(return_value=[])
            with patch.object(collector, "NEWS_DIR", news_dir), \
                 patch.object(collector, "fetch_market_overview", market_news):
                result = collector.collect_day(
                    "2026-05-15",
                    targets={"CSCO": "Cisco"},
                    force=False,
                )

        self.assertEqual(result["target_tickers"], ["CSCO"])
        market_news.assert_not_called()


if __name__ == "__main__":
    unittest.main()
