from __future__ import annotations

import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import trading_bot as trading_bot_module
from runtime import sub_screener
from trading_bot import TradingBot


def _trigger_result() -> sub_screener.SubScanResult:
    return sub_screener.SubScanResult(
        should_trigger=True,
        new_plan_a=[{"ticker": "SPOT", "trainer_candidate_state": "PLAN_A", "trainer_prompt_score": 90.0}],
        new_plan_b_high=[],
        all_new_scored=[],
        trigger_reason="new_plan_a:1",
    )


def _base_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.session_active = True
    bot.current_market = "US"
    bot._market_task_owner = {"KR": None, "US": None}
    bot._last_sub_screener_at = {"KR": 0.0, "US": 0.0}
    bot.today_judgment = {"consensus": {"mode": "BALANCED"}}
    bot.today_tickers = {"KR": [], "US": ["AAPL"]}
    bot.trade_ready_tickers = {"KR": [], "US": ["MSFT"]}
    bot.selection_meta = {"KR": {}, "US": {"watchlist": ["NVDA"], "trade_ready": ["TSLA"]}}
    bot.pending_orders = []
    bot.risk = SimpleNamespace(positions=[])
    bot.pathb = None
    bot._minutes_to_close = lambda market: 120.0
    bot._current_session_date_str = lambda market: "2026-05-22"
    return bot


class SubScreenerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.env = patch.dict(
            os.environ,
            {
                "SUB_SCREENER_ENABLED": "true",
                "SUB_SCREENER_TRIGGER_ENABLED": "true",
                "SUB_SCREENER_INTERVAL_MIN": "15",
                "SUB_SCREENER_MAX_PER_SESSION": "5",
                "SUB_SCREENER_MIN_INTERVAL_MIN": "15",
                "SUB_SCREENER_BLACKOUT_BEFORE_CLOSE_MIN": "30",
                "SUB_SCREENER_TRIAGE_ENABLED": "true",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_disabled_when_enabled_false(self) -> None:
        bot = _base_bot()
        calls: list[str] = []
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: calls.append("screen")

        with patch.dict(os.environ, {"SUB_SCREENER_ENABLED": "false"}, clear=False):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, [])

    def test_shadow_mode_records_scan_without_attempt(self) -> None:
        bot = _base_bot()
        rows = [{"ticker": "SPOT"}]
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: rows
        recorded: list[str] = []
        bot.manual_rescreen = lambda *args, **kwargs: recorded.append("rescreen")
        bot._reinvoke_analysts = lambda *args, **kwargs: recorded.append("reinvoke")

        with patch.dict(os.environ, {"SUB_SCREENER_TRIGGER_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan", side_effect=lambda *args, **kwargs: recorded.append("scan")), \
            patch("runtime.sub_screener.record_attempt", side_effect=AssertionError("attempt should not run")):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(recorded, ["scan"])

    def test_market_scoped_trigger_can_enable_kr_when_global_shadow(self) -> None:
        bot = _base_bot()
        bot.current_market = "KR"
        rows = [{"ticker": "005930"}]
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: rows
        recorded: list[str] = []
        bot._reinvoke_analysts = lambda *args, **kwargs: recorded.append("reinvoke")
        bot.manual_rescreen = lambda *args, **kwargs: recorded.append("rescreen")
        bot._apply_sub_screener_triage = lambda *args, **kwargs: recorded.append("triage") or {"added_tickers": ["005930"], "skipped_tickers": []}

        with patch.dict(
            os.environ,
            {
                "SUB_SCREENER_TRIGGER_ENABLED": "false",
                "SUB_SCREENER_KR_TRIGGER_ENABLED": "true",
            },
            clear=False,
        ), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan", side_effect=lambda *args, **kwargs: recorded.append("scan")), \
            patch("runtime.sub_screener.record_attempt", side_effect=lambda *args, **kwargs: recorded.append("attempt")), \
            patch("runtime.sub_screener.record_triage_success", side_effect=lambda *args, **kwargs: recorded.append("success")):
            TradingBot.maybe_run_sub_screener(bot, "KR")

        self.assertEqual(recorded, ["scan", "attempt", "triage", "success"])

    def test_market_scoped_trigger_can_keep_us_shadow_when_global_live(self) -> None:
        bot = _base_bot()
        rows = [{"ticker": "SPOT"}]
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: rows
        recorded: list[str] = []
        bot._reinvoke_analysts = lambda *args, **kwargs: recorded.append("reinvoke")
        bot.manual_rescreen = lambda *args, **kwargs: recorded.append("rescreen")

        with patch.dict(
            os.environ,
            {
                "SUB_SCREENER_TRIGGER_ENABLED": "true",
                "SUB_SCREENER_US_TRIGGER_ENABLED": "false",
            },
            clear=False,
        ), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan", side_effect=lambda *args, **kwargs: recorded.append("scan")), \
            patch("runtime.sub_screener.record_attempt", side_effect=AssertionError("attempt should not run")):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(recorded, ["scan"])

    def test_passes_market_scoped_plan_a_score_floor(self) -> None:
        bot = _base_bot()
        rows = [{"ticker": "SPOT"}]
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: rows
        captured: dict = {}

        def scan(*args, **kwargs):
            captured.update(kwargs)
            return _trigger_result()

        with patch.dict(
            os.environ,
            {
                "SUB_SCREENER_PLAN_A_MIN_SCORE": "70",
                "US_SUB_SCREENER_PLAN_A_MIN_SCORE": "72",
            },
            clear=False,
        ), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", side_effect=scan), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_triage_success"):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(captured["plan_a_min_score"], 72.0)

    def test_interval_respected(self) -> None:
        bot = _base_bot()
        bot._last_sub_screener_at["US"] = time.time()
        calls: list[str] = []
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: calls.append("screen")

        TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, [])

    def test_last_sub_screener_at_initialized(self) -> None:
        bot = _base_bot()
        delattr(bot, "_last_sub_screener_at")

        with patch("runtime.sub_screener.is_rate_limited", return_value=True):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertIn("US", bot._last_sub_screener_at)

    def test_legacy_candidate_override_passed_to_rescreen_when_triage_disabled(self) -> None:
        bot = _base_bot()
        rows = [{"ticker": "SPOT"}]
        captured: dict = {}
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: rows
        bot._reinvoke_analysts = lambda market, trigger: None
        bot.manual_rescreen = lambda market, *, source_type, trigger, candidate_override=None: captured.setdefault(
            "candidate_override", candidate_override
        ) or ["SPOT"]

        with patch.dict(os.environ, {"SUB_SCREENER_TRIAGE_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_success"):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertIs(captured["candidate_override"], rows)

    def test_legacy_reinvoke_same_mode_runs_override_rescreen_when_triage_disabled(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        calls: list[str] = []
        bot._reinvoke_analysts = lambda market, trigger: calls.append("reinvoke")
        bot.manual_rescreen = lambda market, *, source_type, trigger, candidate_override=None: calls.append("rescreen") or ["SPOT"]

        with patch.dict(os.environ, {"SUB_SCREENER_TRIAGE_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_success"):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["reinvoke", "rescreen"])

    def test_legacy_reinvoke_mode_change_still_rescreens_with_override_when_triage_disabled(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        calls: list[str] = []

        def reinvoke(_market: str, _trigger: str) -> None:
            calls.append("reinvoke")
            bot.today_judgment["consensus"]["mode"] = "RISK_ON"

        bot._reinvoke_analysts = reinvoke
        bot.manual_rescreen = lambda market, *, source_type, trigger, candidate_override=None: calls.append("rescreen") or ["SPOT"]

        with patch.dict(os.environ, {"SUB_SCREENER_TRIAGE_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_success"):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["reinvoke", "rescreen"])

    def test_legacy_reinvoke_fail_fallback_to_rescreen_when_triage_disabled(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        calls: list[str] = []

        def fail_reinvoke(_market: str, _trigger: str) -> None:
            calls.append("reinvoke")
            raise RuntimeError("boom")

        bot._reinvoke_analysts = fail_reinvoke
        bot.manual_rescreen = lambda market, *, source_type, trigger, candidate_override=None: calls.append("rescreen") or ["SPOT"]

        with patch.dict(os.environ, {"SUB_SCREENER_TRIAGE_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_success"):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["reinvoke", "rescreen"])

    def test_duplicate_trigger_suppresses_reinvoke_and_rescreen(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        bot._reinvoke_analysts = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reinvoke should not run"))
        bot.manual_rescreen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rescreen should not run"))
        calls: list[str] = []
        bot._apply_sub_screener_triage = lambda *args, **kwargs: calls.append("triage") or {"added_tickers": ["SPOT"], "skipped_tickers": []}

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(os.environ, {"SUB_SCREENER_STATE_DIR": tmp, "SUB_SCREENER_DEDUPE_TTL_MIN": "60"}, clear=False):
            sub_screener.record_attempt("US", "2026-05-22", _trigger_result())
            with patch("runtime.sub_screener.is_rate_limited", return_value=False), \
                patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
                patch("runtime.sub_screener.record_attempt", side_effect=AssertionError("attempt should not run")):
                TradingBot.maybe_run_sub_screener(bot, "US")

            state = sub_screener.load_session_counter("US", "2026-05-22")

        self.assertEqual(calls, ["triage"])
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["triage_success_count"], 1)
        self.assertEqual(state["dedupe_suppressed_count"], 1)
        self.assertEqual(state["last_dedupe_suppressed"]["new_tickers"], ["SPOT"])
        self.assertTrue(state["last_dedupe_suppressed"]["triage_allowed"])
        self.assertEqual(state["last_dedupe_suppressed"]["triage_added_tickers"], ["SPOT"])

    def test_early_judge_runs_before_triage_when_enabled(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        calls: list[str] = []
        bot.maybe_run_early_judge_triggers = lambda *args, **kwargs: calls.append("early") or [{"ticker": "SPOT"}]
        bot._apply_sub_screener_triage = lambda *args, **kwargs: calls.append("triage") or {"added_tickers": ["SPOT"], "skipped_tickers": []}

        with patch.dict(
            os.environ,
            {
                "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
            },
            clear=False,
        ), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan", side_effect=lambda *args, **kwargs: calls.append("scan")), \
            patch("runtime.sub_screener.record_attempt", side_effect=lambda *args, **kwargs: calls.append("attempt")), \
            patch("runtime.sub_screener.record_triage_success", side_effect=lambda *args, **kwargs: calls.append("success")):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["scan", "attempt", "early", "triage", "success"])

    def test_duplicate_trigger_can_still_run_early_judge_when_enabled(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        bot._reinvoke_analysts = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reinvoke should not run"))
        bot.manual_rescreen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rescreen should not run"))
        calls: list[str] = []
        bot.maybe_run_early_judge_triggers = lambda *args, **kwargs: calls.append("early") or [{"ticker": "SPOT"}]
        bot._apply_sub_screener_triage = lambda *args, **kwargs: calls.append("triage") or {"added_tickers": ["SPOT"], "skipped_tickers": []}

        with tempfile.TemporaryDirectory() as tmp, \
            patch.dict(
                os.environ,
                {
                    "SUB_SCREENER_STATE_DIR": tmp,
                    "SUB_SCREENER_DEDUPE_TTL_MIN": "60",
                    "EARLY_JUDGE_TRIGGER_ENABLED": "true",
                    "US_EARLY_JUDGE_TRIGGER_ENABLED": "true",
                },
                clear=False,
            ):
            sub_screener.record_attempt("US", "2026-05-22", _trigger_result())
            with patch("runtime.sub_screener.is_rate_limited", return_value=False), \
                patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
                patch("runtime.sub_screener.record_attempt", side_effect=AssertionError("attempt should not run")):
                TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["early", "triage"])

    def test_triage_suppresses_reinvoke_and_full_rescreen(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        calls: list[str] = []
        bot._reinvoke_analysts = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reinvoke should not run"))
        bot.manual_rescreen = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("rescreen should not run"))
        bot._apply_sub_screener_triage = lambda *args, **kwargs: calls.append("triage") or {"added_tickers": ["SPOT"], "skipped_tickers": []}

        with patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_triage_success", side_effect=lambda *args, **kwargs: calls.append("success")):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, ["triage", "success"])

    def test_loop_prevention_max_per_session(self) -> None:
        bot = _base_bot()
        calls: list[str] = []
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: calls.append("screen")

        with patch("runtime.sub_screener.is_rate_limited", return_value=True):
            TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, [])

    def test_blackout_prevents_trigger(self) -> None:
        bot = _base_bot()
        bot._minutes_to_close = lambda market: 10.0
        calls: list[str] = []
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: calls.append("screen")

        TradingBot.maybe_run_sub_screener(bot, "US")

        self.assertEqual(calls, [])

    def test_force_refresh_bypasses_us_cache_and_documents_kr_path(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_screen_candidates = {"KR": [], "US": []}
        bot._screen_top_n_for_market = lambda market: 30
        bot._load_persisted_screen_baseline = lambda market: []
        bot._screen_quality_guard = lambda market, rows, phase: rows
        bot._token_for_market = lambda market: "token"
        seen: dict[str, str] = {}

        def screen_us(*, top_n: int, mode: str, token: str | None = None) -> list[dict]:
            seen["us_ttl"] = os.environ.get("US_SCREEN_CACHE_TTL_SEC", "")
            return [{"ticker": "SPOT"}]

        def screen_kr(token: str, *, top_n: int, mode: str) -> list[dict]:
            seen["kr_called"] = token
            return [{"ticker": "005930"}]

        with patch.dict(os.environ, {"US_SCREEN_CACHE_TTL_SEC": "1800"}, clear=False), \
            patch.object(trading_bot_module, "screen_market_us", side_effect=screen_us), \
            patch.object(trading_bot_module, "screen_market_kr", side_effect=screen_kr):
            us_rows = TradingBot._screen_market_candidates(bot, "US", "BALANCED", force_refresh=True)
            seen["post_us_ttl"] = os.environ.get("US_SCREEN_CACHE_TTL_SEC", "")
            kr_rows = TradingBot._screen_market_candidates(bot, "KR", "BALANCED", force_refresh=True)

        self.assertEqual(seen["us_ttl"], "0")
        self.assertEqual(seen["post_us_ttl"], "1800")
        self.assertEqual(us_rows[0]["ticker"], "SPOT")
        self.assertEqual(kr_rows[0]["ticker"], "005930")
        self.assertEqual(seen["kr_called"], "token")

    def test_us_kis_shadow_token_failure_does_not_block_screener(self) -> None:
        """US KIS shadow token failures should not block the Yahoo/FMP screener path."""
        bot = _base_bot()
        bot._last_screen_candidates = {"KR": [], "US": []}
        bot._screen_top_n_for_market = lambda market: 30
        bot._load_persisted_screen_baseline = lambda market: []
        bot._screen_quality_guard = lambda market, rows, phase: rows
        bot._token_for_market = lambda market: (_ for _ in ()).throw(RuntimeError("token error"))
        seen: dict[str, object] = {}

        def screen_us(*, top_n: int, mode: str, token: str | None = None) -> list[dict]:
            seen["token"] = token
            return [{"ticker": "NVDA"}]

        with patch.dict(os.environ, {"US_KIS_RANKING_SHADOW_ENABLED": "true"}, clear=False), \
            patch.object(trading_bot_module, "screen_market_us", side_effect=screen_us):
            rows = TradingBot._screen_market_candidates(bot, "US", "BALANCED", force_refresh=True)

        self.assertEqual(seen["token"], None)
        self.assertEqual(rows[0]["ticker"], "NVDA")

    def test_success_count_only_on_rescreen_complete(self) -> None:
        bot = _base_bot()
        bot._screen_market_candidates = lambda market, mode, *, force_refresh=False: [{"ticker": "SPOT"}]
        bot._reinvoke_analysts = lambda market, trigger: None
        bot.manual_rescreen = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rescreen failed"))

        with patch.dict(os.environ, {"SUB_SCREENER_TRIAGE_ENABLED": "false"}, clear=False), \
            patch("runtime.sub_screener.is_rate_limited", return_value=False), \
            patch("runtime.sub_screener.scan_new_candidates", return_value=_trigger_result()), \
            patch("runtime.sub_screener.record_scan"), \
            patch("runtime.sub_screener.record_attempt"), \
            patch("runtime.sub_screener.record_success", side_effect=AssertionError("success should not run")):
            TradingBot.maybe_run_sub_screener(bot, "US")

    def test_entry_scan_continues_when_sub_screener_fails(self) -> None:
        bot = _base_bot()
        bot._last_entry_scan_at = {"US": 0.0}
        bot._entry_scan_interval_sec = lambda market: 300
        calls: list[str] = []
        bot.maybe_run_sub_screener = lambda market: (_ for _ in ()).throw(RuntimeError("sub failed"))
        bot.run_cycle = lambda market: calls.append(market)

        TradingBot.run_entry_scan(bot, "US")

        self.assertEqual(calls, ["US"])


if __name__ == "__main__":
    unittest.main()
