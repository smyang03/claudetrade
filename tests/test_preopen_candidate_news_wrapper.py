from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.collect_preopen_candidate_news import collect_preopen_candidate_news


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


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

        with patch.dict(os.environ, {"PREOPEN_INVESTMENT_NEWS_BRIDGE_ENABLED": "false"}), \
             patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
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

        with patch.dict(os.environ, {"PREOPEN_INVESTMENT_NEWS_BRIDGE_ENABLED": "false"}), \
             patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
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

    def test_wrapper_appends_enriched_candidate_log_snapshot_before_open(self) -> None:
        targets = {"CSCO": "Cisco"}
        news_payload = {
            "corp_news": {"CSCO": {"count": 1, "items": []}},
            "news_coverage": {"covered_ticker_count": 1, "coverage_ratio": 1.0},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def fake_enrich_state(*_args, **_kwargs):
                state_path = root / "state" / "preopen_US_20260515.json"
                state_path.parent.mkdir(parents=True, exist_ok=True)
                state_path.write_text(
                    json.dumps(
                        {
                            "market": "US",
                            "mode": "live",
                            "session_date": "2026-05-15",
                            "captured_at": "2026-05-15T22:00:00+09:00",
                            "candidates": [
                                {
                                    "ticker": "CSCO",
                                    "market": "US",
                                    "session_date": "2026-05-15",
                                    "news_or_earnings_flag": True,
                                    "news_or_earnings_sample_title": "Cisco signs AI networking contract",
                                }
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                return {
                    "status": "ok",
                    "candidate_count": 1,
                    "flagged_count": 1,
                    "applied_at": "2026-05-15T22:20:00+09:00",
                }

            with patch.dict(os.environ, {"PREOPEN_INVESTMENT_NEWS_BRIDGE_ENABLED": "false"}), \
                 patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), \
                 patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
                 patch("phase1_trainer.us_news_collector.collect_day", return_value=news_payload), \
                 patch("tools.collect_preopen_candidate_news.build_us_digest", return_value={"top_news": []}), \
                 patch("tools.collect_preopen_candidate_news.save_preopen_news_snapshot", return_value=Path("preopen.json")), \
                 patch("tools.collect_preopen_candidate_news.enrich_preopen_state", side_effect=fake_enrich_state):
                summary = collect_preopen_candidate_news(
                    market="US",
                    session_date="2026-05-15",
                    mode="live",
                )

            rows = [
                json.loads(line)
                for line in (root / "logs" / "preopen" / "20260515_US_candidates.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertTrue(summary["candidate_log_appended"])
        self.assertEqual(summary["candidate_log_captured_at"], "2026-05-15T22:20:00+09:00")
        self.assertEqual(rows[0]["captured_at"], "2026-05-15T22:20:00+09:00")
        self.assertTrue(rows[0]["news_or_earnings_flag"])
        self.assertEqual(rows[0]["news_or_earnings_sample_title"], "Cisco signs AI networking contract")

    def test_fail_on_empty_marks_summary_not_ok_and_sets_flags(self) -> None:
        targets = {"005930": "Samsung"}
        news_payload = {
            "market_news": [],
            "corp_news": {"005930": {"count": 0, "items": []}},
            "news_coverage": {"covered_ticker_count": 0, "coverage_ratio": 0.0},
        }

        with patch.dict(os.environ, {"PREOPEN_INVESTMENT_NEWS_BRIDGE_ENABLED": "false"}), \
             patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
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

    def test_bridge_merge_recomputes_coverage_and_provider_counts(self) -> None:
        targets = {"NVDA": "Nvidia"}
        news_payload = {
            "market": "US",
            "corp_news": {"NVDA": {"name": "Nvidia", "count": 0, "items": []}},
            "target_tickers": ["NVDA"],
            "news_coverage": {"covered_ticker_count": 0, "missing_tickers": ["NVDA"], "coverage_ratio": 0.0},
            "provider_counts": {},
        }
        bridge_payload = {
            "date": "2026-05-15",
            "market": "US",
            "target_source": "investment_news_db_readonly",
            "corp_news": {
                "NVDA": {
                    "name": "Nvidia",
                    "count": 1,
                    "items": [
                        {
                            "source": "GoogleNews",
                            "title": "Nvidia unveils new AI platform",
                            "date": "2026-05-15",
                        }
                    ],
                }
            },
            "investment_news_bridge": {"row_count": 1, "corp_ticker_count": 1, "market_item_count": 0},
        }
        saved_payload = {}

        def fake_save(_market, _day, payload):
            saved_payload.update(payload)
            return Path("preopen.json")

        with patch("tools.collect_preopen_candidate_news.load_preopen_news_targets", return_value=targets), \
             patch("phase1_trainer.us_news_collector.collect_day", return_value=news_payload), \
             patch("tools.collect_preopen_candidate_news.build_us_digest", return_value={"top_news": []}), \
             patch("preopen.investment_news_bridge.build_preopen_payload_from_investment_news", return_value=bridge_payload), \
             patch("tools.collect_preopen_candidate_news.save_preopen_news_snapshot", side_effect=fake_save), \
             patch("tools.collect_preopen_candidate_news.enrich_preopen_state", return_value={"status": "ok", "flagged_count": 1}):
            summary = collect_preopen_candidate_news(
                market="US",
                session_date="2026-05-15",
                mode="live",
            )

        self.assertEqual(summary["covered_ticker_count"], 1)
        self.assertEqual(summary["coverage_ratio"], 1.0)
        self.assertEqual(saved_payload["news_coverage"]["missing_tickers"], [])
        self.assertEqual(saved_payload["provider_counts"]["GoogleNews"], 1)


if __name__ == "__main__":
    unittest.main()
