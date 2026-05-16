from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.collect_preopen_candidate_news import collect_preopen_candidate_news


class PreopenCandidateNewsWrapperTests(unittest.TestCase):
    def test_kr_wrapper_uses_preopen_targets_for_collect_and_digest(self) -> None:
        targets = {"005930": "Samsung", "000660": "SK hynix"}
        news_payload = {
            "corp_news": {
                "005930": {"count": 2, "items": []},
                "000660": {"count": 0, "items": []},
            },
            "news_coverage": {"covered_ticker_count": 1, "coverage_ratio": 0.5},
        }

        with patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
             patch("phase1_trainer.kr_news_collector.collect_day", return_value=news_payload) as collect_day, \
             patch("tools.collect_preopen_candidate_news.build_kr_digest", return_value={"top_news": [{"title": "x"}]}) as build_digest:
            summary = collect_preopen_candidate_news(
                market="KR",
                session_date="2026-05-15",
                mode="live",
            )

        collect_day.assert_called_once_with(
            "2026-05-15",
            targets=targets,
            force=False,
            target_source="preopen_top60",
        )
        build_digest.assert_called_once_with("2026-05-15", universe_tickers=list(targets))
        self.assertEqual(summary["target_count"], 2)
        self.assertEqual(summary["corp_news_total"], 2)
        self.assertEqual(summary["coverage_ratio"], 0.5)
        self.assertEqual(summary["top_news_count"], 1)

    def test_us_wrapper_uses_same_universe(self) -> None:
        targets = {"CSCO": "Cisco", "LUMN": "Lumen"}
        news_payload = {
            "corp_news": {
                "CSCO": {"count": 1, "items": []},
                "LUMN": {"count": 1, "items": []},
            },
            "news_coverage": {"covered_ticker_count": 2, "coverage_ratio": 1.0},
        }

        with patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
             patch("phase1_trainer.us_news_collector.collect_day", return_value=news_payload) as collect_day, \
             patch("tools.collect_preopen_candidate_news.build_us_digest", return_value={"top_news": []}) as build_digest:
            summary = collect_preopen_candidate_news(
                market="US",
                session_date="2026-05-15",
                mode="live",
                force=True,
            )

        collect_day.assert_called_once_with(
            "2026-05-15",
            targets=targets,
            force=True,
            target_source="preopen_top60",
        )
        build_digest.assert_called_once_with("2026-05-15", universe_tickers=list(targets))
        self.assertEqual(summary["market"], "US")
        self.assertEqual(summary["target_count"], 2)


if __name__ == "__main__":
    unittest.main()
