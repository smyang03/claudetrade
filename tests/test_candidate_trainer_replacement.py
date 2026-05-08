from __future__ import annotations

import unittest
import tempfile
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from trading_bot import TradingBot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))


class _HealthTracker:
    def __init__(self, states: dict[str, dict]) -> None:
        self.states = states
        self.data = {"tickers": {str(key): dict(value) for key, value in states.items()}}

    def state_for(self, ticker: str) -> dict:
        key = str(ticker)
        state = dict(self.states.get(key, self.states.get(key.upper(), {"health_state": "OBSERVE"})))
        state.setdefault("ticker", key)
        return state


def _bot_with_health(states: dict[str, dict]) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.runtime_config = _RuntimeConfig()
    bot.selection_meta = {"KR": {}, "US": {}}
    bot._ticker_no_signal_cycles = {}
    bot._ticker_no_signal_minutes = {}
    bot._invalid_price_count = {}
    bot._ticker_runtime_blocked_reasons = {"KR": {}, "US": {}}
    bot._ticker_runtime_rejection_reasons = {"KR": {}, "US": {}}
    bot._candidate_cohort_reliability_cache = {"market": "US", "cohorts": {}}
    bot._current_session_date_str = lambda market: "2026-05-07"
    bot._recommended_strategy_for_ticker = lambda market, ticker: ""
    bot._watch_only_bucket = lambda market, ticker: "NORMAL"
    bot._candidate_health_tracker = lambda market: _HealthTracker(states)
    return bot


class CandidateTrainerReplacementTests(unittest.TestCase):
    def test_partial_replace_score_protects_healthy_stable_ready_mfe(self) -> None:
        bot = _bot_with_health(
            {
                "006345": {
                    "health_state": "STABLE_READY",
                    "ready_count": 1,
                    "mfe_pct": 27.197,
                    "mae_pct": 0.0,
                    "current_vs_first_ready_pct": 27.197,
                }
            }
        )

        score = TradingBot._partial_replace_score(bot, "KR", "006345")

        self.assertLessEqual(score, -6.0)

    def test_partial_replace_score_does_not_protect_degraded_mfe_spike(self) -> None:
        bot = _bot_with_health(
            {
                "024840": {
                    "health_state": "STABLE_READY",
                    "ready_count": 1,
                    "mfe_pct": 8.0,
                    "mae_pct": -4.0,
                    "current_vs_first_ready_pct": -3.0,
                }
            }
        )

        score = TradingBot._partial_replace_score(bot, "KR", "024840")

        self.assertGreaterEqual(score, 2.0)

    def test_candidate_health_guard_detects_degraded_ready_path(self) -> None:
        bot = _bot_with_health({})
        degraded = {
            "health_state": "STABLE_READY",
            "ready_count": 1,
            "mfe_pct": 8.0,
            "mae_pct": -4.0,
            "current_vs_first_ready_pct": -3.0,
        }

        self.assertTrue(TradingBot._candidate_health_ready_degraded(bot, degraded))
        self.assertFalse(TradingBot._candidate_health_mfe_protect_allowed(bot, degraded))

    def test_ready_action_no_signal_grace_is_time_limited(self) -> None:
        bot = _bot_with_health({})
        bot.runtime_config = _RuntimeConfig({"KR_READY_ACTION_NO_SIGNAL_GRACE_MIN": 30.0})
        bot.selection_meta = {
            "KR": {
                "_candidate_action_routes": [
                    {
                        "ticker": "006345",
                        "final_action": "PROBE_READY",
                        "action_created_at": "2026-05-07T09:00:00",
                        "action_expires_at": "2026-05-07T09:45:00",
                        "routed_at": "2026-05-07T09:00:00",
                    }
                ]
            }
        }

        self.assertTrue(
            TradingBot._ready_action_no_signal_grace_active(
                bot,
                "KR",
                "006345",
                now=datetime(2026, 5, 7, 9, 15),
            )
        )
        self.assertFalse(
            TradingBot._ready_action_no_signal_grace_active(
                bot,
                "KR",
                "006345",
                now=datetime(2026, 5, 7, 9, 31),
            )
        )

    def test_us_concentrated_stop_cluster_allows_other_ticker_before_disaster(self) -> None:
        bot = _bot_with_health({})
        bot._daily_sl_count = {"US": 2}
        bot._daily_sl_last_at = {"US": None}
        bot._v2_same_day_stop_tickers = {"US": {"EAT"}}

        with patch.dict(
            "os.environ",
            {
                "US_STOP_CLUSTER_CONCENTRATED_SCOPE_ENABLED": "true",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
            clear=False,
        ):
            state = TradingBot._daily_stop_cluster_state(bot, "US", "AAPL")

        self.assertTrue(state["allowed"])
        self.assertTrue(state["details"]["market_block_relaxed"])

    def test_us_concentrated_stop_cluster_defaults_to_market_block(self) -> None:
        bot = _bot_with_health({})
        bot._daily_sl_count = {"US": 2}
        bot._daily_sl_last_at = {"US": None}
        bot._v2_same_day_stop_tickers = {"US": {"EAT"}}

        with patch.dict(
            "os.environ",
            {
                "US_STOP_CLUSTER_CONCENTRATED_SCOPE_ENABLED": "",
                "STOP_CLUSTER_CONCENTRATED_SCOPE_ENABLED": "",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
            clear=False,
        ):
            state = TradingBot._daily_stop_cluster_state(bot, "US", "AAPL")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "STOP_CLUSTER_MARKET_BLOCK")

    def test_us_concentrated_stop_cluster_keeps_stopped_ticker_blocked(self) -> None:
        bot = _bot_with_health({})
        bot._daily_sl_count = {"US": 2}
        bot._daily_sl_last_at = {"US": None}
        bot._v2_same_day_stop_tickers = {"US": {"EAT"}}

        state = TradingBot._daily_stop_cluster_state(bot, "US", "EAT")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "SAME_DAY_REENTRY_AFTER_STOP")

    def test_replacement_delta_gate_rejects_weaker_incoming(self) -> None:
        bot = _bot_with_health(
            {
                "OUT": {"health_state": "STABLE_READY", "ready_count": 1, "mfe_pct": 5.0, "mae_pct": 0.0},
                "IN": {"health_state": "OBSERVE"},
            }
        )
        bot.runtime_config = _RuntimeConfig({"US_TRAINER_REPLACEMENT_MIN_IN_SCORE": 4.0, "US_TRAINER_REPLACEMENT_DELTA": 0.75})

        ok, gate = TradingBot._candidate_replacement_delta_ok(bot, "US", "OUT", "IN", {"IN": {"ticker": "IN"}})

        self.assertFalse(ok)
        self.assertLess(gate["incoming_score"], gate["outgoing_score"] + gate["delta"])

    def test_pick_partial_replace_in_accepts_stronger_incoming(self) -> None:
        bot = _bot_with_health(
            {
                "OUT": {"health_state": "WATCH_WEAK", "ready_count": 0},
                "IN": {
                    "health_state": "STRONG_READY",
                    "ready_count": 2,
                    "mfe_pct": 3.0,
                    "mae_pct": 0.0,
                    "current_vs_first_ready_pct": 3.0,
                },
            }
        )
        bot.runtime_config = _RuntimeConfig({"US_TRAINER_REPLACEMENT_MIN_IN_SCORE": 4.0, "US_TRAINER_REPLACEMENT_DELTA": 0.75})

        selected = TradingBot._pick_partial_replace_in(
            bot,
            "US",
            ["OUT"],
            ["IN"],
            {"recommended_strategy": {}},
            {"IN": {"ticker": "IN", "entry_priority_score": 1.0}},
            1,
        )

        self.assertEqual(selected, ["IN"])

    def test_candidate_trainer_tier_marks_core_and_quarantine(self) -> None:
        bot = _bot_with_health(
            {
                "CORE": {
                    "health_state": "STABLE_READY",
                    "ready_count": 1,
                    "mfe_pct": 5.0,
                    "mae_pct": 0.0,
                    "current_vs_first_ready_pct": 4.0,
                },
                "BAD": {"health_state": "FAILED_READY", "ready_count": 3},
            }
        )
        bot._v2_same_day_stop_tickers = {"US": set()}

        self.assertEqual(TradingBot._candidate_trainer_tier(bot, "US", "CORE", candidate={}), "CORE_PROTECTED")
        self.assertEqual(TradingBot._candidate_trainer_tier(bot, "US", "BAD", candidate={}), "QUARANTINE")

    def test_candidate_trainer_tier_marks_stale_ready_as_probation(self) -> None:
        bot = _bot_with_health(
            {
                "WULF": {
                    "health_state": "STABLE_READY",
                    "ready_count": 1,
                    "last_seen_at": "2000-01-01T00:00:00",
                    "last_status": "TRADE_READY",
                }
            }
        )

        self.assertEqual(TradingBot._candidate_trainer_tier(bot, "US", "WULF", candidate={}), "PROBATION")

    def test_trainer_snapshot_includes_inactive_ready_history(self) -> None:
        bot = _bot_with_health(
            {
                "NVDA": {"health_state": "OBSERVE", "ready_count": 0},
                "ZTS": {
                    "health_state": "WEAKENING_READY",
                    "ready_count": 3,
                    "mae_pct": -3.09,
                    "mfe_pct": 0.0,
                    "last_seen_at": "2026-05-08T01:50:43+09:00",
                    "last_status": "TRADE_READY",
                },
            }
        )
        bot._v2_same_day_stop_tickers = {"US": set()}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_path = Path(tmp.name) / "candidate_trainer.json"

        with patch("trading_bot.get_runtime_path", side_effect=lambda *parts, **kwargs: state_path):
            TradingBot._write_candidate_trainer_snapshot(
                bot,
                "US",
                "unit",
                ["NVDA"],
                [],
                [{"ticker": "NVDA"}],
            )

        data = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("ZTS", data["records"])
        self.assertFalse(data["records"]["ZTS"]["active_now"])
        self.assertEqual(data["records"]["ZTS"]["included_reason"], "health_failure")

    def test_cohort_reliability_shadow_state_updates(self) -> None:
        bot = _bot_with_health({"AAPL": {"health_state": "WATCH_WEAK"}})
        bot._current_session_date_str = lambda market: "2026-05-07"

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        state_path = Path(tmp.name) / "candidate_cohort.json"
        with patch("trading_bot.get_runtime_path", side_effect=lambda *parts, **kwargs: state_path):
            TradingBot._update_candidate_cohort_reliability(
                bot,
                "US",
                "unit",
                [{"ticker": "AAPL", "source": "unit", "primary_bucket": "volume_surge"}],
            )

        state = bot._candidate_cohort_reliability_cache
        self.assertTrue(state["cohorts"])
        self.assertLessEqual(next(iter(state["cohorts"].values()))["score"], 0.0)
        self.assertFalse(
            TradingBot._ready_action_no_signal_grace_active(
                bot,
                "KR",
                "006345",
                now=datetime(2026, 5, 7, 9, 46),
            )
        )

    def test_update_candidate_health_counts_routed_plan_a_ready(self) -> None:
        bot = _bot_with_health({})
        captured: dict[str, list[str]] = {}

        class _CaptureTracker:
            path = Path("candidate_health.json")
            session_date = "2026-05-07"

            def update_selection(self, *, watchlist, trade_ready, price_by_ticker, phase, now):
                captured["watchlist"] = list(watchlist)
                captured["trade_ready"] = list(trade_ready)
                return [{"ticker": "NVDA", "health_state": "STABLE_READY"}]

            def state_counts(self, states):
                return {"STABLE_READY": 1}

            def interesting_states(self, states):
                return []

        bot._candidate_health_tracker = lambda market: _CaptureTracker()

        with patch.dict(
            "os.environ",
            {
                "CANDIDATE_COHORT_RELIABILITY_ENABLED": "false",
                "CANDIDATE_TRAINER_STATE_ENABLED": "false",
            },
            clear=False,
        ):
            TradingBot._update_candidate_health(
                bot,
                "US",
                "unit",
                ["NVDA"],
                {
                    "watchlist": ["NVDA"],
                    "trade_ready": [],
                    "_candidate_action_routes": [
                        {"ticker": "NVDA", "final_action": "BUY_READY", "route": "PlanA.buy"}
                    ],
                },
                [],
            )

        self.assertEqual(captured["trade_ready"], ["NVDA"])


if __name__ == "__main__":
    unittest.main()
