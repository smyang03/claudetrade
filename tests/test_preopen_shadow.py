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
    save_candidate_records,
    save_outcome_record,
    save_preopen_state,
    save_rank_diff_record,
)
from tools.preopen_collector import collect_once
from tools.preopen_outcome_updater import update_once
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

    def test_kr_collector_uses_kis_screen_when_token_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "state" / "live_kis_token.json"
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(
                json.dumps({
                    "access_token": "token",
                    "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
                }),
                encoding="utf-8",
            )
            with patch("tools.preopen_collector.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.storage.get_runtime_path",
                side_effect=_runtime_path(root),
            ), patch("kis_api.screen_market_kr", return_value=[
                {
                    "ticker": "006910",
                    "name": "보성파워텍",
                    "price": 14350,
                    "change_rate": 18.79,
                    "volume": 45_051_706,
                    "vol_ratio": 91.7,
                    "market_type": "KOSDAQ",
                    "screen_score": 431.57,
                }
            ]):
                state = collect_once("KR", mode="live")

        self.assertEqual(state["collector_status"], "ok")
        self.assertEqual(state["provider"], "kis_volume_rank")
        self.assertEqual(state["data_quality"], "kis_volume_rank")
        self.assertEqual(state["candidate_count"], 1)
        candidate = state["candidates"][0]
        self.assertEqual(candidate["ticker"], "006910")
        self.assertEqual(candidate["price"], 14350)
        self.assertEqual(candidate["volume_ratio"], 91.7)
        self.assertGreater(candidate["prior_day_traded_value"], 0)

    def test_outcome_updater_samples_price_for_dynamic_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            token_path = root / "state" / "live_kis_token.json"
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(
                json.dumps({
                    "access_token": "token",
                    "expires_at": (datetime.now() + timedelta(hours=2)).isoformat(),
                }),
                encoding="utf-8",
            )
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.preopen_collector.get_runtime_path",
                side_effect=_runtime_path(root),
            ):
                save_preopen_state("KR", {
                    "market": "KR",
                    "session_date": "2026-05-04",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "provider": "kis_volume_rank",
                    "data_quality": "kis_volume_rank",
                    "candidates": [{
                        "ticker": "006910",
                        "provider": "kis_volume_rank",
                        "data_quality": "kis_volume_rank",
                        "price": 1000,
                        "shadow_preopen_rank": 1,
                    }],
                }, session_date="2026-05-04")
                with patch("bot.session_date.resolve_session_date_str", return_value="2026-05-04"), patch(
                    "tools.preopen_outcome_updater.resolve_session_date_str",
                    return_value="2026-05-04",
                ), patch("kis_api.get_price", return_value={
                    "ticker": "006910",
                    "price": 1100,
                    "open": 1000,
                    "high": 1120,
                    "low": 980,
                    "volume": 12345,
                }):
                    result = update_once("KR", mode="live", offset_min=90)
                state = load_preopen_state("KR", session_date="2026-05-04", max_age_min=24 * 60)
                outcome = load_preopen_dashboard("KR", session_date="2026-05-04")["outcome"]

        self.assertEqual(result["sampled"], 1)
        candidate = state["candidates"][0]
        self.assertEqual(candidate["anchor_price"], 1000)
        self.assertEqual(candidate["anchor_price_source"], "price")
        self.assertEqual(candidate["regular_open_price"], 1000)
        self.assertEqual(candidate["last_price"], 1100)
        self.assertEqual(candidate["post_open_90m_return_pct"], 10.0)
        self.assertEqual(candidate["max_runup_pct"], 12.0)
        self.assertEqual(candidate["max_drawdown_pct"], -2.0)
        self.assertEqual(candidate["outcome_samples"][0]["offset_min"], 90)
        self.assertEqual(candidate["outcome_samples"][0]["return_basis"], "anchor_price")
        self.assertEqual(outcome[-1]["post_open_90m_return_pct"], 10.0)

    def test_paper_preopen_state_and_logs_are_separated_from_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tools.preopen_collector.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "preopen.storage.get_runtime_path",
                side_effect=_runtime_path(root),
            ):
                state = collect_once("US", mode="paper", tickers="AAPL")
                update_once("US", mode="paper", offset_min=5)
                session_date = state["session_date"]
                ymd = session_date.replace("-", "")
                paper_payload = load_preopen_dashboard("US", session_date=session_date, mode="paper")
                live_payload = load_preopen_dashboard("US", session_date=session_date, mode="live")

                self.assertTrue((root / "state" / f"preopen_paper_US_{ymd}.json").exists())
                self.assertFalse((root / "state" / f"preopen_US_{ymd}.json").exists())
                self.assertTrue((root / "logs" / "preopen" / f"{ymd}_US_candidates_paper.jsonl").exists())
                self.assertTrue((root / "logs" / "preopen" / f"{ymd}_US_outcome_paper.jsonl").exists())
                self.assertEqual(paper_payload["summary"]["candidate_count"], 1)
                self.assertEqual(live_payload["summary"]["collector_status"], "missing")

    def test_bot_rank_diff_record_is_defensive_and_preopen_specific(self) -> None:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.is_paper = True
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
        self.assertEqual(save_mock.call_args.kwargs["mode"], "paper")

    def test_bot_loads_preopen_state_from_current_runtime_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "candidates": [{"ticker": "LIVE", "shadow_preopen_rank": 1}],
                }, session_date="2026-05-02", mode="live")
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "candidates": [{"ticker": "PAPER", "shadow_preopen_rank": 1}],
                }, session_date="2026-05-02", mode="paper")

                bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
                bot._current_session_date_str = lambda market: "2026-05-02"

                bot.is_paper = True
                paper_state = trading_bot.TradingBot._load_preopen_state(bot, "US")

                bot.is_paper = False
                live_state = trading_bot.TradingBot._load_preopen_state(bot, "US")

        self.assertEqual(paper_state["mode"], "paper")
        self.assertEqual(paper_state["candidates"][0]["ticker"], "PAPER")
        self.assertEqual(live_state["mode"], "live")
        self.assertEqual(live_state["candidates"][0]["ticker"], "LIVE")

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
        self.assertEqual(payload["summary"]["empty_reason"], "ready")
        self.assertTrue(payload["recent_sessions"])
        self.assertIn("scheduler_guidance", payload)
        self.assertIn("candidates", payload["paths"])

    def test_dashboard_payload_groups_outcome_timeline_by_candidate_and_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "provider": "screen_cache",
                    "data_quality": "screen_cache_display",
                    "candidates": [{
                        "ticker": "CELC",
                        "name": "Celcuity Inc.",
                        "shadow_preopen_rank": 1,
                        "price": 100.0,
                        "anchor_price": 100.0,
                    }],
                }, session_date="2026-05-02")
                save_outcome_record("US", "2026-05-02", {
                    "ticker": "CELC",
                    "name": "Celcuity Inc.",
                    "offset_min": 150,
                    "anchor_price": 100.0,
                    "price": 112.0,
                    "post_open_return_pct": 12.0,
                    "post_open_150m_return_pct": 12.0,
                    "outcome_status": "WIN",
                })
                payload = load_preopen_dashboard("US", session_date="2026-05-02")

        self.assertIn(150, payload["outcome_offsets_min"])
        row = payload["outcome_timeline"][0]
        self.assertEqual(row["display_ticker"], "Celcuity Inc. (CELC)")
        self.assertEqual(row["anchor_price"], 100.0)
        self.assertEqual(row["returns_by_offset"]["150"], 12.0)
        self.assertIn("결과: 저장 1건", payload["summary"]["operator_status"])

    def test_dashboard_enriches_seed_candidates_from_screen_cache_for_display(self) -> None:
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
                    "candidates": [{
                        "ticker": "AAPL",
                        "name": "AAPL",
                        "shadow_preopen_rank": 1,
                        "price": None,
                        "extended_change_pct": None,
                        "extended_dollar_volume": None,
                    }],
                }, session_date="2026-05-02")
                cache_path = root / "state" / "us_screen_cache.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({
                    "date": "2026-05-02",
                    "candidates": [{
                        "ticker": "AAPL",
                        "name": "Apple Inc.",
                        "price": 190.5,
                        "change_rate": 4.2,
                        "volume": 2_000_000,
                        "vol_ratio": 1.4,
                    }],
                }), encoding="utf-8")

                payload = load_preopen_dashboard("US", session_date="2026-05-02")

        candidate = payload["candidates"][0]
        self.assertEqual(candidate["price"], 190.5)
        self.assertEqual(candidate["extended_change_pct"], 4.2)
        self.assertEqual(candidate["extended_dollar_volume"], 381_000_000.0)
        self.assertEqual(candidate["display_enrichment_source"], "screen_cache")
        self.assertEqual(payload["summary"]["candidate_source"], "screen_cache_fallback")
        self.assertEqual(payload["summary"]["screen_cache_fallback_count"], 1)
        self.assertEqual(payload["summary"]["provider"], "screen_cache")
        self.assertEqual(payload["summary"]["data_quality"], "screen_cache_display")

    def test_outcome_updater_uses_screen_cache_fallback_when_state_is_seed_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.preopen_collector.get_runtime_path",
                side_effect=_runtime_path(root),
            ):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "provider": "seed_watchlist",
                    "data_quality": "seed_only",
                    "candidates": [{
                        "ticker": "AAPL",
                        "provider": "seed_watchlist",
                        "data_quality": "seed_only",
                        "price": None,
                        "extended_change_pct": None,
                        "extended_dollar_volume": None,
                    }],
                }, session_date="2026-05-02")
                cache_path = root / "state" / "us_screen_cache.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps({
                    "date": "2026-05-02",
                    "candidates": [{
                        "ticker": "AAPL",
                        "name": "Apple Inc.",
                        "price": 190.5,
                        "change_rate": 4.2,
                        "volume": 2_000_000,
                    }],
                }), encoding="utf-8")
                with patch("tools.preopen_outcome_updater.resolve_session_date_str", return_value="2026-05-02"), patch(
                    "kis_api.get_price",
                    return_value={
                        "ticker": "AAPL",
                        "price": 199.5,
                        "open": 190.0,
                        "high": 201.0,
                        "low": 188.0,
                        "volume": 3_000_000,
                    },
                ):
                    result = update_once("US", mode="live", offset_min=30)
                state = load_preopen_state("US", session_date="2026-05-02", max_age_min=24 * 60)
                outcome = load_preopen_dashboard("US", session_date="2026-05-02")["outcome"]

        self.assertEqual(result["sampled"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(state["provider"], "screen_cache")
        self.assertEqual(state["outcome_source_candidates"], "screen_cache_fallback")
        self.assertEqual(state["candidates"][0]["anchor_price"], 190.5)
        self.assertEqual(state["candidates"][0]["post_open_30m_return_pct"], 4.7244)
        self.assertEqual(state["candidates"][0]["outcome_samples"][0]["return_basis"], "anchor_price")
        self.assertEqual(outcome[-1]["post_open_30m_return_pct"], 4.7244)

    def test_dashboard_payload_explains_missing_collector(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                payload = load_preopen_dashboard("US", session_date="2026-05-02")

        self.assertEqual(payload["summary"]["collector_status"], "missing")
        self.assertEqual(payload["summary"]["empty_reason"], "collector_not_run")
        self.assertIn("preopen_collector.py --market US", payload["next_actions"][0])
        self.assertEqual(payload["scheduler_guidance"]["market"], "US")

    def test_dashboard_payload_uses_current_mode_in_guidance_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                payload = load_preopen_dashboard("US", session_date="2026-05-02", mode="paper")

        self.assertIn("--mode paper", payload["next_actions"][0])
        self.assertIn("--mode paper", payload["scheduler"]["start_command"])
        self.assertIn("--mode paper", payload["scheduler_guidance"]["automatic_command"])
        self.assertIn("--mode paper", payload["scheduler_guidance"]["commands"][0])
        self.assertNotIn("--mode live", payload["scheduler_guidance"]["automatic_command"])

    def test_dashboard_payload_falls_back_to_candidate_log_when_state_is_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_at = (datetime.now(KST) - timedelta(days=3)).isoformat(timespec="seconds")
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                state = {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": old_at,
                    "collector_status": "ok",
                    "candidates": [{"ticker": "OLD", "shadow_preopen_rank": 99}],
                }
                save_preopen_state("US", state, session_date="2026-05-02")
                save_candidate_records(
                    "US",
                    "2026-05-02",
                    [
                        {"ticker": "AAPL", "shadow_preopen_rank": 1},
                        {"ticker": "MSFT", "shadow_preopen_rank": 2},
                    ],
                    state,
                )
                payload = load_preopen_dashboard("US", session_date="2026-05-02")

        self.assertEqual(payload["summary"]["collector_status"], "log_only")
        self.assertEqual(payload["summary"]["candidate_source"], "candidate_log")
        self.assertEqual(payload["summary"]["candidate_count"], 2)
        self.assertEqual(payload["summary"]["candidate_display_count"], 2)
        self.assertEqual([row["ticker"] for row in payload["candidates"]], ["AAPL", "MSFT"])
        self.assertEqual(payload["summary"]["empty_reason"], "waiting_for_claude_selection")

    def test_dashboard_payload_separates_total_and_display_candidate_counts(self) -> None:
        candidates = [{"ticker": f"T{i}", "shadow_preopen_rank": i} for i in range(1, 4)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                }, session_date="2026-05-02")
                payload = load_preopen_dashboard("US", session_date="2026-05-02", limit=2)

        self.assertEqual(payload["summary"]["candidate_count"], 3)
        self.assertEqual(payload["summary"]["candidate_total_count"], 3)
        self.assertEqual(payload["summary"]["candidate_display_count"], 2)
        self.assertEqual(len(payload["candidates"]), 2)

    def test_outcome_timeline_uses_initial_pool_not_display_slice(self) -> None:
        candidates = [{"ticker": f"T{i}", "shadow_preopen_rank": i} for i in range(1, 4)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                }, session_date="2026-05-02")
                save_outcome_record("US", "2026-05-02", {
                    "ticker": "T3",
                    "offset_min": 30,
                    "anchor_price": 10.0,
                    "price": 11.0,
                    "post_open_return_pct": 10.0,
                    "post_open_30m_return_pct": 10.0,
                })
                payload = load_preopen_dashboard("US", session_date="2026-05-02", limit=2)

        self.assertEqual([row["ticker"] for row in payload["candidates"]], ["T1", "T2"])
        self.assertIn("T3", [row["ticker"] for row in payload["outcome_timeline"]])
        self.assertEqual(payload["summary"]["outcome_display_limit"], 60)

    def test_dashboard_payload_reports_requested_outcome_coverage_gap(self) -> None:
        candidates = [{"ticker": f"T{i}", "shadow_preopen_rank": i} for i in range(1, 31)]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("preopen.storage.get_runtime_path", side_effect=_runtime_path(root)):
                save_preopen_state("US", {
                    "market": "US",
                    "session_date": "2026-05-02",
                    "captured_at": datetime.now(KST).isoformat(timespec="seconds"),
                    "collector_status": "ok",
                    "candidate_count": len(candidates),
                    "candidates": candidates,
                }, session_date="2026-05-02")
                payload = load_preopen_dashboard("US", session_date="2026-05-02", limit=60)

        self.assertEqual(payload["summary"]["candidate_display_count"], 30)
        self.assertEqual(payload["summary"]["requested_display_limit"], 60)
        self.assertEqual(payload["summary"]["outcome_display_count"], 30)
        self.assertEqual(payload["summary"]["outcome_missing_display_count"], 30)
        self.assertIn("원본 후보 30개만 수집됨", payload["summary"]["outcome_shortage_reason"])
        self.assertEqual(payload["outcome_timeline"][0]["statuses_by_offset"]["30"], "NO_DATA")

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
        self.assertIn("recent_sessions", payload)
        self.assertIn("next_actions", payload)


if __name__ == "__main__":
    unittest.main()
