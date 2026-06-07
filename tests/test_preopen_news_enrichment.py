from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from preopen.news_enrichment import (
    build_news_index,
    build_news_index_with_summary,
    enrich_candidates_with_news,
    enrich_preopen_state,
    load_preopen_news_payload,
    save_preopen_news_snapshot,
)
from preopen.storage import load_preopen_state, save_preopen_state
from tools.preopen_collector import collect_once


def _runtime_path(root: Path):
    def _inner(*parts, make_parents: bool = True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenNewsEnrichmentTests(unittest.TestCase):
    def test_enrich_candidates_marks_news_and_rescores(self) -> None:
        candidates = [
            {
                "ticker": "AAPL",
                "market": "US",
                "extended_change_pct": 4.0,
                "extended_dollar_volume": 5_000_000,
                "spread_pct": 0.2,
            }
        ]
        payload = {
            "target_source": "preopen_top60",
            "corp_news": {
                "AAPL": {
                    "count": 2,
                    "items": [
                        {"source": "Finnhub", "title": "Apple catalyst headline"},
                        {"source": "SEC EDGAR", "title": "Apple filing"},
                    ],
                }
            },
        }

        enriched, summary = enrich_candidates_with_news(
            "US",
            candidates,
            session_date="2026-05-19",
            news_payload=payload,
        )

        self.assertEqual(summary["flagged_count"], 1)
        self.assertTrue(enriched[0]["news_or_earnings_flag"])
        self.assertEqual(enriched[0]["news_or_earnings_count"], 2)
        self.assertIn("Finnhub", enriched[0]["news_or_earnings_sources"])
        self.assertIn("catalyst", enriched[0]["preopen_reason"])
        self.assertGreaterEqual(enriched[0]["preopen_score"], 0.73)

    def test_build_news_index_ignores_stale_dated_corp_news_items(self) -> None:
        payload = {
            "date": "2026-06-05",
            "corp_news": {
                "ADPT": {
                    "count": 2,
                    "items": [
                        {"source": "KIS", "date": "2025-08-04", "title": "Old unrelated headline"},
                        {"source": "KIS", "published_at": "2026-06-05T09:10:00+09:00", "title": "Current ADPT headline"},
                    ],
                },
                "CAI": {
                    "count": 1,
                    "items": [
                        {"source": "KIS", "published_at": "2025-08-14T09:03:33+09:00", "title": "Stale CAI headline"},
                    ],
                },
            },
        }

        index, summary = build_news_index_with_summary("US", payload)

        self.assertEqual(index["ADPT"]["count"], 1)
        self.assertEqual(index["ADPT"]["sample_title"], "Current ADPT headline")
        self.assertNotIn("CAI", index)
        self.assertEqual(summary["stale_filtered_count"], 2)
        self.assertEqual(summary["usable_corp_item_count"], 1)

        legacy_index = build_news_index("US", payload)
        self.assertEqual(legacy_index["ADPT"]["count"], 1)
        self.assertNotIn("CAI", legacy_index)

    def test_news_enrichment_marks_unknown_date_and_broad_weak_news(self) -> None:
        candidates = [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "US",
                "extended_change_pct": 4.0,
                "extended_dollar_volume": 5_000_000,
                "spread_pct": 0.2,
            }
        ]
        payload = {
            "date": "2026-06-05",
            "corp_news": {
                "AAPL": {
                    "name": "Apple Inc.",
                    "items": [
                        {"source": "Finnhub", "title": "Technology stocks rise before the open"},
                        {"source": "Finnhub", "date": "2026-06-05", "title": "Apple unveils AI roadmap"},
                    ],
                }
            },
        }

        enriched, summary = enrich_candidates_with_news(
            "US",
            candidates,
            session_date="2026-06-05",
            news_payload=payload,
        )

        self.assertEqual(summary["unknown_date_count"], 1)
        self.assertEqual(summary["broad_weak_count"], 1)
        self.assertEqual(summary["stale_filtered_count"], 0)
        self.assertEqual(enriched[0]["news_quality"], "mixed")
        self.assertEqual(enriched[0]["news_date_quality"], "mixed_date")
        self.assertIn("broad_weak", enriched[0]["news_quality_tags"])
        self.assertIn("unknown_date", enriched[0]["news_quality_tags"])

    def test_enrich_preopen_state_preserves_rank_after_outcome_started(self) -> None:
        payload = {
            "target_source": "preopen_top60",
            "corp_news": {
                "MSFT": {
                    "count": 1,
                    "items": [{"source": "Finnhub", "title": "Microsoft catalyst"}],
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state(
                    "US",
                    {
                        "market": "US",
                        "session_date": "2026-05-19",
                        "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                        "last_outcome_update_at": datetime.now(KST).isoformat(timespec="seconds"),
                        "candidates": [
                            {"ticker": "AAPL", "shadow_preopen_rank": 1, "extended_dollar_volume": 5_000_000},
                            {"ticker": "MSFT", "shadow_preopen_rank": 2, "extended_dollar_volume": 5_000_000},
                        ],
                    },
                    session_date="2026-05-19",
                )

                summary = enrich_preopen_state(
                    "US",
                    "2026-05-19",
                    news_payload=payload,
                    news_path="memory",
                )
                state = load_preopen_state("US", session_date="2026-05-19", max_age_min=0)

        self.assertEqual(summary["flagged_count"], 1)
        self.assertFalse(summary["allow_rank_reorder"])
        self.assertEqual([row["ticker"] for row in state["candidates"]], ["AAPL", "MSFT"])
        self.assertEqual([row["shadow_preopen_rank"] for row in state["candidates"]], [1, 2])
        self.assertTrue(state["candidates"][1]["news_or_earnings_flag"])

    def test_preopen_snapshot_loads_before_regular_news_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            news_root = Path(tmp)
            regular = news_root / "us" / "2026-05-19.json"
            regular.parent.mkdir(parents=True, exist_ok=True)
            regular.write_text(
                '{"corp_news":{"AAPL":{"count":1,"items":[{"title":"regular"}]}}}',
                encoding="utf-8",
            )
            save_preopen_news_snapshot(
                "US",
                "2026-05-19",
                {"corp_news": {"MSFT": {"count": 1, "items": [{"title": "preopen"}]}}},
                news_root=news_root,
            )

            payload, path = load_preopen_news_payload("US", "2026-05-19", news_root=news_root)

        self.assertTrue(path.endswith("_preopen.json"))
        self.assertIn("MSFT", payload["corp_news"])
        self.assertNotIn("AAPL", payload["corp_news"])

    def test_preopen_collector_keeps_news_flag_when_it_runs_after_news_job(self) -> None:
        payload = {
            "target_source": "preopen_top60",
            "corp_news": {
                "AAPL": {
                    "count": 1,
                    "items": [{"source": "Finnhub", "title": "Apple preopen catalyst"}],
                }
            },
        }
        raw_candidates = [
            {
                "ticker": "AAPL",
                "name": "Apple Inc.",
                "market": "US",
                "session_date": "2026-05-19",
                "extended_change_pct": 5.0,
                "extended_dollar_volume": 10_000_000,
                "spread_pct": 0.2,
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.preopen_collector.get_runtime_path",
                side_effect=_runtime_path(root),
            ), patch(
                "tools.preopen_collector.resolve_session_date_str",
                return_value="2026-05-19",
            ), patch(
                "tools.preopen_collector._collect_us_screen_candidates",
                return_value=raw_candidates,
            ), patch(
                "preopen.news_enrichment.load_preopen_news_payload",
                return_value=(payload, "memory"),
            ):
                state = collect_once("US", mode="live")
                loaded = load_preopen_state("US", session_date="2026-05-19", max_age_min=0)

        self.assertEqual(state["news_enrichment"]["flagged_count"], 1)
        self.assertTrue(loaded["candidates"][0]["news_or_earnings_flag"])
        self.assertIn("catalyst", loaded["candidates"][0]["preopen_reason"])


if __name__ == "__main__":
    unittest.main()
