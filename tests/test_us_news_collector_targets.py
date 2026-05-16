from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from phase1_trainer import us_news_collector as collector


class UsNewsCollectorTargetsTests(unittest.TestCase):
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
