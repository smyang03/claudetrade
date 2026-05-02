from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from bot.session_date import KST, resolve_session_date
from preopen.models import normalize_candidate
from preopen.scorer import score_candidates
from preopen.storage import (
    load_preopen_dashboard,
    load_preopen_state,
    save_outcome_record,
    save_preopen_state,
    save_rank_diff_record,
)
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

    def test_candidate_schema_keeps_plan_alias_fields(self) -> None:
        candidate = normalize_candidate(
            {
                "ticker": "twlo",
                "provider": "seed_provider",
                "price": 80.5,
                "gap_pct": 7.2,
                "volume_ratio": 4.1,
                "data_quality": "seed_only",
            },
            market="US",
            session_date="2026-05-02",
            captured_at="2026-05-02T20:00:00+09:00",
        )

        self.assertEqual(candidate["ticker"], "TWLO")
        self.assertEqual(candidate["provider"], "seed_provider")
        self.assertEqual(candidate["extended_price"], 80.5)
        self.assertEqual(candidate["extended_change_pct"], 7.2)
        self.assertEqual(candidate["volume_ratio"], 4.1)
        self.assertEqual(candidate["data_quality"], "seed_only")

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

    def test_us_collector_marks_expired_token_as_stale_without_refreshing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "state" / "live_kis_token_us.json"
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(
                json.dumps({
                    "access_token": "expired",
                    "expires_at": (datetime.now() - timedelta(minutes=5)).isoformat(),
                }),
                encoding="utf-8",
            )
            with patch("tools.preopen_collector.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.storage.get_runtime_path",
                side_effect=_runtime_path(root),
            ):
                state = collect_once("US", mode="live", tickers="AAPL")

        self.assertEqual(state["collector_status"], "token_expired")
        self.assertEqual(state["token_status"], "token_expired")
        self.assertTrue(state["stale"])
        self.assertEqual(state["candidate_count"], 0)

    def test_shadow_collector_does_not_call_order_path_even_when_flags_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {
                "PREOPEN_SHADOW_ENABLED": "true",
                "PREOPEN_SORT_ENABLED": "true",
                "FAST_LANE_ENABLED": "true",
                "PREMARKET_BUY_ENABLED": "true",
            }
            with patch.dict("os.environ", env), patch(
                "tools.preopen_collector.get_runtime_path",
                side_effect=_runtime_path(root),
            ), patch(
                "preopen.storage.get_runtime_path",
                side_effect=_runtime_path(root),
            ), patch(
                "kis_api.place_order"
            ) as place_order:
                state = collect_once("US", mode="live", tickers="AAPL")

        self.assertEqual(state["collector_mode"], "shadow_only")
        self.assertEqual(state["candidate_count"], 1)
        place_order.assert_not_called()

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

    def test_dashboard_payload_includes_performance_summary_for_later_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "provider": "seed_watchlist",
                    "data_quality": "seed_only",
                    "candidates": [{"ticker": "AAPL", "shadow_preopen_rank": 1}],
                }, session_date="2026-05-02")
                save_rank_diff_record("US", "2026-05-02", {
                    "ticker": "AAPL",
                    "shadow_preopen_rank": 1,
                    "actual_selected": True,
                    "actual_trade_ready": False,
                })
                save_outcome_record("US", "2026-05-02", {
                    "ticker": "AAPL",
                    "post_open_30m_return_pct": 1.5,
                    "post_open_60m_return_pct": 2.5,
                })
                payload = load_preopen_dashboard("US", session_date="2026-05-02")

        self.assertEqual(payload["summary"]["provider"], "seed_watchlist")
        self.assertEqual(payload["performance_summary"]["top3_selected"], 1)
        self.assertEqual(payload["performance_summary"]["avg_30m_return_pct"], 1.5)
        self.assertEqual(
            payload["performance_summary"]["review_status"],
            "collect_5_to_10_sessions_before_enabling_behavior",
        )

    def test_dashboard_api_returns_shadow_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "provider": "seed_watchlist",
                    "data_quality": "seed_only",
                    "candidates": [{"ticker": "AAPL", "shadow_preopen_rank": 1}],
                }, session_date="2026-05-02")
                from dashboard import dashboard_server

                with dashboard_server.app.test_client() as client:
                    response = client.get("/api/preopen?market=US&session_date=2026-05-02")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["summary"]["collector_status"], "ok")
        self.assertEqual(payload["summary"]["provider"], "seed_watchlist")
        self.assertEqual(payload["performance_summary"]["review_status"], "collect_5_to_10_sessions_before_enabling_behavior")


if __name__ == "__main__":
    unittest.main()
