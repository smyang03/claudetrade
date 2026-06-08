from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import preopen_candidate_replay as replay


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenCandidateReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _write_jsonl(self, path: Path, rows: list[dict]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

    def test_sanitize_candidate_excludes_future_fields(self) -> None:
        row = {
            "ticker": "005930",
            "name": "Samsung",
            "shadow_preopen_rank": 1,
            "news_or_earnings_sample_title": "specific catalyst",
            "preopen_news_edge": True,
            "preopen_pin_tier": "HARD",
            "preopen_pin_source": "news_strict_catalyst",
            "outcome_5m_price": 100,
            "post_open_5m_return_pct": 7.0,
            "actual_selected": True,
            "last_price": 110,
        }

        clean = replay.sanitize_candidate(row)

        self.assertEqual(clean["ticker"], "005930")
        self.assertTrue(clean["preopen_news_edge"])
        self.assertEqual(clean["preopen_pin_tier"], "HARD")
        self.assertEqual(clean["preopen_pin_source"], "news_strict_catalyst")
        self.assertNotIn("outcome_5m_price", clean)
        self.assertNotIn("post_open_5m_return_pct", clean)
        self.assertNotIn("actual_selected", clean)
        self.assertNotIn("last_price", clean)

    def test_load_candidate_snapshot_uses_latest_preopen_rows(self) -> None:
        rows = [
            {
                "ticker": "AAA",
                "market": "KR",
                "session_date": "2026-06-08",
                "captured_at": "2026-06-08T08:30:00+09:00",
                "shadow_preopen_rank": 1,
                "provider_rank": 1,
                "price": 100,
            },
            {
                "ticker": "BBB",
                "market": "KR",
                "session_date": "2026-06-08",
                "captured_at": "2026-06-08T08:46:00+09:00",
                "shadow_preopen_rank": 1,
                "provider_rank": 2,
                "price": 200,
            },
            {
                "ticker": "CCC",
                "market": "KR",
                "session_date": "2026-06-08",
                "captured_at": "2026-06-08T09:05:00+09:00",
                "shadow_preopen_rank": 1,
                "provider_rank": 1,
                "price": 300,
            },
        ]
        path = self.root / "logs" / "preopen" / "20260608_KR_candidates.jsonl"
        self._write_jsonl(path, rows)

        with patch("tools.preopen_candidate_replay.get_runtime_path", side_effect=_runtime_path(self.root)):
            snapshot_at, candidates = replay.load_candidate_snapshot("KR", "2026-06-08")

        self.assertEqual(snapshot_at, "2026-06-08T08:46:00+09:00")
        self.assertEqual([row["ticker"] for row in candidates], ["BBB"])

    def test_load_candidate_snapshot_overlays_preopen_news_payload(self) -> None:
        rows = [
            {
                "ticker": "AAPL",
                "market": "US",
                "session_date": "2026-06-05",
                "captured_at": "2026-06-05T22:00:00+09:00",
                "shadow_preopen_rank": 1,
                "provider_rank": 1,
                "price": 100,
            }
        ]
        self._write_jsonl(self.root / "logs" / "preopen" / "20260605_US_candidates.jsonl", rows)
        news_path = self.root / "data" / "news" / "us" / "2026-06-05_preopen.json"
        news_path.parent.mkdir(parents=True, exist_ok=True)
        news_path.write_text(
            json.dumps(
                {
                    "preopen_snapshot": True,
                    "snapshot_written_at": "2026-06-05T22:20:00+09:00",
                    "corp_news": {
                        "AAPL": {
                            "name": "Apple",
                            "count": 1,
                            "items": [
                                {
                                    "source": "GoogleNews",
                                    "source_type": "company_news",
                                    "importance": "A",
                                    "date": "2026-06-05",
                                    "published_at": "2026-06-05T21:50:00+09:00",
                                    "title": "Apple unveils AI product upgrade",
                                    "summary": "Apple AI product launch supports same-day catalyst.",
                                }
                            ],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch("tools.preopen_candidate_replay.get_runtime_path", side_effect=_runtime_path(self.root)):
            snapshot_at, candidates = replay.load_candidate_snapshot("US", "2026-06-05")

        self.assertEqual(snapshot_at, "2026-06-05T22:00:00+09:00")
        self.assertEqual(candidates[0]["news_or_earnings_sample_title"], "Apple unveils AI product upgrade")
        self.assertTrue(candidates[0]["news_prompt_eligible"])
        self.assertEqual(candidates[0]["news_signal_type"], "direct_catalyst")
        self.assertIn("news_prompt_summary", candidates[0])
        self.assertTrue(candidates[0]["preopen_news_edge"])
        self.assertEqual(candidates[0]["preopen_pin_tier"], "HARD")
        self.assertEqual(candidates[0]["preopen_pin_source"], "news_strict_catalyst")

    def test_load_candidate_snapshot_ignores_news_payload_written_after_open(self) -> None:
        rows = [
            {
                "ticker": "AAPL",
                "market": "US",
                "session_date": "2026-06-05",
                "captured_at": "2026-06-05T22:00:00+09:00",
                "shadow_preopen_rank": 1,
                "provider_rank": 1,
            }
        ]
        self._write_jsonl(self.root / "logs" / "preopen" / "20260605_US_candidates.jsonl", rows)
        news_path = self.root / "data" / "news" / "us" / "2026-06-05_preopen.json"
        news_path.parent.mkdir(parents=True, exist_ok=True)
        news_path.write_text(
            json.dumps(
                {
                    "preopen_snapshot": True,
                    "snapshot_written_at": "2026-06-05T22:35:00+09:00",
                    "corp_news": {
                        "AAPL": {
                            "count": 1,
                            "items": [{"source": "GoogleNews", "source_type": "company_news", "title": "late"}],
                        }
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with patch("tools.preopen_candidate_replay.get_runtime_path", side_effect=_runtime_path(self.root)):
            _snapshot_at, candidates = replay.load_candidate_snapshot("US", "2026-06-05")

        self.assertNotIn("news_or_earnings_sample_title", candidates[0])

    def test_validate_decision_infers_drop_and_evaluate_stats(self) -> None:
        candidates = [{"ticker": "AAA"}, {"ticker": "BBB"}, {"ticker": "CCC"}]
        raw = {
            "promote": [{"ticker": "AAA", "confidence": 1.2, "edge_code": "EDGE", "risk_code": "RISK"}],
            "keep_watch": [{"ticker": "BBB", "reason_code": "WATCH"}],
            "reject_summary": [],
        }
        decision = replay.validate_decision(raw, {"AAA", "BBB", "CCC"})
        outcomes = {
            "AAA": {"ret_5m": 1, "ret_30m": 2, "ret_60m": 3, "ret_120m": 4, "ret_close": 5, "mfe": 6, "mae": -1},
            "BBB": {"ret_5m": -1, "ret_30m": -2, "ret_60m": -3, "ret_120m": -4, "ret_close": -5, "mfe": 0, "mae": -6},
            "CCC": {"ret_5m": 0, "ret_30m": 0, "ret_60m": 0, "ret_120m": 0, "ret_close": -1, "mfe": 1, "mae": -2},
        }

        evaluation = replay.evaluate_decision(candidates=candidates, decision=decision, outcomes=outcomes)

        self.assertEqual(decision["promote"][0]["confidence"], 1.0)
        self.assertEqual([row["ticker"] for row in decision["drop"]], ["CCC"])
        self.assertEqual(evaluation["promote"]["avg_close"], 5.0)
        self.assertEqual(evaluation["drop"]["avg_close"], -1.0)

    def test_load_outcomes_uses_last_available_offset_as_close_proxy(self) -> None:
        path = self.root / "logs" / "preopen" / "20260605_US_outcome.jsonl"
        self._write_jsonl(
            path,
            [
                {
                    "ticker": "AAA",
                    "name": "Alpha",
                    "post_open_30m_return_pct": 1.0,
                    "post_open_360m_return_pct": 4.0,
                    "outcome_samples": [
                        {"offset_min": 5, "return_pct": -1.0},
                        {"offset_min": 360, "return_pct": 4.5},
                    ],
                }
            ],
        )

        with patch("tools.preopen_candidate_replay.get_runtime_path", side_effect=_runtime_path(self.root)):
            outcomes = replay.load_outcomes("US", "2026-06-05")

        self.assertEqual(outcomes["AAA"]["close_offset_min"], 360)
        self.assertEqual(outcomes["AAA"]["ret_close"], 4.5)

    def test_build_prompt_supports_market_specific_versions(self) -> None:
        candidate = {
            "ticker": "NVDA",
            "name": "NVIDIA",
            "market": "US",
            "session_date": "2026-06-05",
            "captured_at": "2026-06-05T22:00:00+09:00",
            "shadow_preopen_rank": 1,
        }

        prompt = replay.build_prompt(
            market="US",
            session_date="2026-06-05",
            snapshot_at="2026-06-05T22:00:00+09:00",
            candidates=[candidate],
            prompt_version=replay.PROMPT_MARKET_GROWTH_TAPE_V3,
        )

        self.assertIn("Prompt version: market_growth_tape_v3", prompt)
        self.assertIn("US PROMOTE rules", prompt)
        self.assertIn("Growth momentum", prompt)

        liquid_prompt = replay.build_prompt(
            market="US",
            session_date="2026-06-05",
            snapshot_at="2026-06-05T22:00:00+09:00",
            candidates=[candidate],
            prompt_version=replay.PROMPT_US_LIQUID_QUALITY_V4,
        )

        self.assertIn("Prompt version: us_liquid_quality_v4", liquid_prompt)
        self.assertIn("LIQUID_QUALITY_TAPE", liquid_prompt)

        edge_prompt = replay.build_prompt(
            market="US",
            session_date="2026-06-05",
            snapshot_at="2026-06-05T22:00:00+09:00",
            candidates=[candidate],
            prompt_version=replay.PROMPT_US_EDGE_HUNTER_V5,
        )

        self.assertIn("Prompt version: us_edge_hunter_v5", edge_prompt)
        self.assertIn("OVERSOLD_REVERSAL", edge_prompt)
        self.assertIn("MOMENTUM_CONTINUATION", edge_prompt)

        adaptive_prompt = replay.build_prompt(
            market="US",
            session_date="2026-06-05",
            snapshot_at="2026-06-05T22:00:00+09:00",
            candidates=[candidate],
            prompt_version=replay.PROMPT_US_SLATE_ADAPTIVE_V6,
        )

        self.assertIn("Prompt version: us_slate_adaptive_v6", adaptive_prompt)
        self.assertIn("GAP_FADE_RISK", adaptive_prompt)
        self.assertIn("QUALITY_MOST_ACTIVE", adaptive_prompt)

    def test_build_prompt_rejects_unknown_prompt_version(self) -> None:
        with self.assertRaisesRegex(replay.ReplayError, "unsupported_prompt_version"):
            replay.build_prompt(
                market="KR",
                session_date="2026-06-08",
                snapshot_at="2026-06-08T08:46:00+09:00",
                candidates=[{"ticker": "005930"}],
                prompt_version="unknown",
            )


if __name__ == "__main__":
    unittest.main()
