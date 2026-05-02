from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from bot.session_date import KST, resolve_session_date
from preopen.scorer import score_candidates
from preopen.storage import load_preopen_state, save_preopen_state
from tools.preopen_collector import collect_once
import trading_bot


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class PreopenShadowTests(unittest.TestCase):
    def test_us_session_date_uses_previous_date_before_5am_kst(self) -> None:
        now = datetime(2026, 5, 2, 4, 59, tzinfo=KST)

        self.assertEqual(resolve_session_date("US", now).isoformat(), "2026-05-01")
        self.assertEqual(resolve_session_date("KR", now).isoformat(), "2026-05-02")

    def test_preopen_state_stale_or_corrupt_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_at = (datetime.now(KST) - timedelta(minutes=120)).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": old_at,
                    "candidates": [],
                }, session_date="2026-05-02")

                self.assertEqual(load_preopen_state("US", session_date="2026-05-02", max_age_min=60), {})

                path = root / "state" / "preopen_US_20260502.json"
                path.write_text("{broken", encoding="utf-8")
                self.assertEqual(load_preopen_state("US", session_date="2026-05-02", max_age_min=60), {})

    def test_scorer_assigns_shadow_rank_without_order_side_effects(self) -> None:
        scored = score_candidates("US", [
            {"ticker": "A", "extended_change_pct": 2, "extended_dollar_volume": 100_000},
            {"ticker": "B", "extended_change_pct": 9, "extended_dollar_volume": 6_000_000, "spread_pct": 0.2},
        ])

        self.assertEqual(scored[0]["ticker"], "B")
        self.assertEqual(scored[0]["shadow_preopen_rank"], 1)
        self.assertIn(scored[0]["preopen_grade"], {"A", "B"})

    def test_kr_collector_marks_token_unavailable_without_refreshing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tools.preopen_collector.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.storage.get_runtime_path",
                side_effect=_runtime_path(root),
            ):
                state = collect_once("KR", mode="live", tickers="005930")

        self.assertEqual(state["collector_status"], "token_unavailable")
        self.assertEqual(state["token_status"], "token_unavailable")
        self.assertEqual(state["candidate_count"], 0)

    def test_bot_rank_diff_record_is_defensive_and_preopen_specific(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._current_session_date_str = lambda market: "2026-05-02"
        bot._load_preopen_state = lambda market: {
            "collector_status": "ok",
            "captured_at": "2026-05-02T22:00:00+09:00",
            "candidates": [
                {
                    "ticker": "TWLO",
                    "name": "Twilio",
                    "shadow_preopen_rank": 1,
                    "preopen_score": 0.82,
                    "preopen_grade": "A",
                    "source_overlap_count": 2,
                    "preopen_reason": ["premarket_strength"],
                }
            ],
        }

        with patch("preopen.storage.save_rank_diff_record") as save_mock:
            trading_bot.TradingBot._record_preopen_rank_diff(
                bot,
                "US",
                ["AAPL", "TWLO"],
                {"trade_ready": ["TWLO"]},
                {"TWLO": "strong continuation"},
                phase="test",
            )

        save_mock.assert_called_once()
        record = save_mock.call_args.args[2]
        self.assertEqual(record["ticker"], "TWLO")
        self.assertEqual(record["actual_selection_rank"], 2)
        self.assertEqual(record["rank_delta"], 1)
        self.assertTrue(record["actual_trade_ready"])


if __name__ == "__main__":
    unittest.main()
