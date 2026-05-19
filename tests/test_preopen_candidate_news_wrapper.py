from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import patch

from tools.collect_preopen_candidate_news import collect_preopen_candidate_news


class PreopenCandidateNewsWrapperTests(unittest.TestCase):
    def test_kr_wrapper_uses_preopen_targets_for_collect_and_digest(self) -> None:
        targets = {"005930": "Samsung", "000660": "SK hynix"}
        news_payload = {
            "market_news": [{"title": "market"}],
            "corp_news": {
                "005930": {"count": 2, "items": []},
                "000660": {"count": 0, "items": []},
            },
            "news_coverage": {"covered_ticker_count": 1, "coverage_ratio": 0.5},
        }

        with patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
             patch("phase1_trainer.kr_news_collector.collect_day", return_value=news_payload) as collect_day, \
             patch("tools.collect_preopen_candidate_news.build_kr_digest", return_value={"top_news": [{"title": "x"}]}) as build_digest, \
             patch("tools.collect_preopen_candidate_news.save_preopen_news_snapshot", return_value=Path("preopen.json")), \
             patch("tools.collect_preopen_candidate_news.enrich_preopen_state", return_value={"status": "ok", "flagged_count": 1}):
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
        self.assertEqual(summary["coverage_status"], "ok")
        self.assertEqual(summary["top_news_count"], 1)
        self.assertEqual(summary["state_news_flagged_count"], 1)

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
             patch("tools.collect_preopen_candidate_news.build_us_digest", return_value={"top_news": []}) as build_digest, \
             patch("tools.collect_preopen_candidate_news.save_preopen_news_snapshot", return_value=Path("preopen.json")), \
             patch("tools.collect_preopen_candidate_news.enrich_preopen_state", return_value={"status": "ok", "flagged_count": 2}):
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
        self.assertEqual(summary["state_news_flagged_count"], 2)

    def test_fail_on_empty_marks_summary_not_ok_and_sets_flags(self) -> None:
        targets = {"005930": "Samsung"}
        news_payload = {
            "market_news": [],
            "corp_news": {"005930": {"count": 0, "items": []}},
            "news_coverage": {"covered_ticker_count": 0, "coverage_ratio": 0.0},
        }

        with patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
             patch("phase1_trainer.kr_news_collector.collect_day", return_value=news_payload), \
             patch("tools.collect_preopen_candidate_news.build_kr_digest", return_value={"top_news": []}), \
             patch("tools.collect_preopen_candidate_news.save_preopen_news_snapshot", return_value=Path("preopen.json")), \
             patch("tools.collect_preopen_candidate_news.enrich_preopen_state", return_value={"status": "ok", "flagged_count": 0}):
            summary = collect_preopen_candidate_news(
                market="KR",
                session_date="2026-05-15",
                mode="live",
                min_coverage_ratio=0.5,
                min_corp_news_total=1,
                fail_on_empty=True,
            )

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["coverage_status"], "empty")
        self.assertIn("kr_news_empty", summary["data_quality_flags"])
        self.assertIn("kr_news_coverage_low", summary["data_quality_flags"])
        self.assertIn("kr_market_news_missing", summary["data_quality_flags"])


if __name__ == "__main__":
    unittest.main()
