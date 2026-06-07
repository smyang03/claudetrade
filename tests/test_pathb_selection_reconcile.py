from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBControlState, PathBRuntime


class _V2:
    brain_snapshot_ids = {"KR": "brain-kr", "US": "brain-us"}


class _Bot:
    is_paper = False
    token = "token"
    usd_krw_rate = 1350
    price_cache_raw = {}
    price_cache = {}
    pending_orders = []
    risk = None
    v2 = _V2()

    def _current_session_date_str(self, market: str) -> str:
        return "2026-06-05"

    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return f"dec_{market}_{ticker}"


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


def _runtime(tmp: str, *, env: dict[str, str] | None = None) -> PathBRuntime:
    bot = _Bot()
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    runtime.control_store = _Control()
    return runtime


def _register_wait(runtime: PathBRuntime, ticker: str, *, market: str = "US") -> str:
    plan = make_price_plan(
        decision_id=f"dec_{market}_{ticker}",
        ticker=ticker,
        market=market,
        session_date="2026-06-05",
        buy_zone_low=100.0,
        buy_zone_high=101.0,
        sell_target=108.0,
        stop_loss=96.0,
        hold_days=1,
        confidence=0.75,
    )
    return runtime.adapter.register_plan(
        plan,
        runtime_mode="live",
        brain_snapshot_id=f"brain-{market.lower()}",
    )


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "PATHB_SELECTION_RECONCILE_ENABLED": "true",
        "PATHB_SELECTION_RECONCILE_MODE": "shadow",
        "US_PATHB_SELECTION_RECONCILE_MODE": "enforce",
        "KR_PATHB_SELECTION_RECONCILE_MODE": "shadow",
        "PATHB_SELECTION_RECONCILE_CANCEL_INVALID": "true",
        "PATHB_SELECTION_RECONCILE_CANCEL_SUSPENDED": "true",
        "PATHB_SELECTION_RECONCILE_UPDATE_VALID_TARGETS": "false",
        "PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL": "false",
        "PATHB_RECONCILE_FORCE_FRESH_AFTER_CANCEL": "false",
    }
    base.update(overrides)
    return base


class PathBSelectionReconcileTests(unittest.TestCase):
    def test_us_enforce_cancels_waiting_negative_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            path_run_id = _register_wait(runtime, "NVDA")

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["NVDA"],
                    "candidate_actions": [{"ticker": "NVDA", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [
                        {
                            "ticker": "NVDA",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                            "suspend_pathb": False,
                        }
                    ],
                    "selection_snapshot_ts": "2026-06-05T23:07:13+09:00",
                },
                source="analyst_reinvoke",
            )

            self.assertEqual(outcomes[0]["verdict"], "SUSPENDED_CANCEL")
            self.assertEqual(outcomes[0]["action"], "cancel")
            self.assertEqual(runtime.store.find_path_run(path_run_id)["status"], "CANCELLED")

    def test_route_missing_keeps_waiting_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            path_run_id = _register_wait(runtime, "NVDA")

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["NVDA"],
                    "candidate_actions": [{"ticker": "NVDA", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [],
                },
                source="rescreen",
            )

            self.assertEqual(outcomes[0]["verdict"], "ROUTE_UNKNOWN_KEEP")
            self.assertEqual(outcomes[0]["action"], "keep")
            self.assertEqual(runtime.store.find_path_run(path_run_id)["status"], "WAITING")

    def test_hit_negative_route_is_log_only_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            path_run_id = _register_wait(runtime, "NVDA")
            runtime.adapter.mark_hit(path_run_id, price=100.5, runtime_mode="live", brain_snapshot_id="brain-us")

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["NVDA"],
                    "candidate_actions": [{"ticker": "NVDA", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [
                        {
                            "ticker": "NVDA",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                        }
                    ],
                },
                source="rescreen",
            )

            self.assertEqual(outcomes[0]["verdict"], "SUSPENDED_LOG")
            self.assertEqual(outcomes[0]["action"], "log")
            self.assertEqual(runtime.store.find_path_run(path_run_id)["status"], "HIT")

    def test_hit_negative_route_cancels_when_hit_suspend_cancel_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            _env(PATHB_SELECTION_RECONCILE_HIT_SUSPEND_CANCEL="true"),
            clear=False,
        ):
            runtime = _runtime(tmp)
            path_run_id = _register_wait(runtime, "NVDA")
            runtime.adapter.mark_hit(path_run_id, price=100.5, runtime_mode="live", brain_snapshot_id="brain-us")

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["NVDA"],
                    "candidate_actions": [{"ticker": "NVDA", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [
                        {
                            "ticker": "NVDA",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                        }
                    ],
                },
                source="rescreen",
            )

            self.assertEqual(outcomes[0]["verdict"], "SUSPENDED_CANCEL")
            self.assertEqual(outcomes[0]["action"], "cancel")
            self.assertEqual(runtime.store.find_path_run(path_run_id)["status"], "CANCELLED")

    def test_kr_shadow_logs_without_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            path_run_id = _register_wait(runtime, "005930", market="KR")

            outcomes = runtime.reconcile_waiting_from_selection(
                "KR",
                {
                    "watchlist": ["005930"],
                    "candidate_actions": [{"ticker": "005930", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [
                        {
                            "ticker": "005930",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                        }
                    ],
                },
                source="rescreen",
            )

            self.assertEqual(outcomes[0]["mode"], "shadow")
            self.assertEqual(outcomes[0]["action"], "shadow")
            self.assertEqual(runtime.store.find_path_run(path_run_id)["status"], "WAITING")

    def test_0605_batch_keeps_valid_plans_and_cancels_only_nvda(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            run_ids = {ticker: _register_wait(runtime, ticker) for ticker in ["AMZN", "GOOGL", "TSLA", "NVDA"]}

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["AMZN", "GOOGL", "TSLA", "NVDA"],
                    "candidate_actions": [
                        {"ticker": "AMZN", "action": "BUY_READY"},
                        {"ticker": "GOOGL", "action": "BUY_READY"},
                        {"ticker": "TSLA", "action": "PULLBACK_WAIT"},
                        {"ticker": "NVDA", "action": "PULLBACK_WAIT"},
                    ],
                    "_candidate_action_routes": [
                        {"ticker": "AMZN", "final_action": "BUY_READY", "reason": "buy_ready"},
                        {"ticker": "GOOGL", "final_action": "BUY_READY", "reason": "buy_ready"},
                        {"ticker": "TSLA", "final_action": "PULLBACK_WAIT", "reason": "pullback_wait"},
                        {
                            "ticker": "NVDA",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                        },
                    ],
                },
                source="analyst_reinvoke",
            )

            by_ticker = {item["ticker"]: item for item in outcomes}
            self.assertEqual(by_ticker["AMZN"]["verdict"], "VALID_KEEP")
            self.assertEqual(by_ticker["GOOGL"]["verdict"], "VALID_KEEP")
            self.assertEqual(by_ticker["TSLA"]["verdict"], "VALID_KEEP")
            self.assertEqual(by_ticker["NVDA"]["action"], "cancel")
            for ticker in ["AMZN", "GOOGL", "TSLA"]:
                self.assertEqual(runtime.store.find_path_run(run_ids[ticker])["status"], "WAITING")
            self.assertEqual(runtime.store.find_path_run(run_ids["NVDA"])["status"], "CANCELLED")

    def test_same_ticker_multiple_waiting_runs_are_all_evaluated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, _env(), clear=False):
            runtime = _runtime(tmp)
            first = _register_wait(runtime, "NVDA")
            second = _register_wait(runtime, "NVDA")

            outcomes = runtime.reconcile_waiting_from_selection(
                "US",
                {
                    "watchlist": ["NVDA"],
                    "candidate_actions": [{"ticker": "NVDA", "action": "PULLBACK_WAIT"}],
                    "_candidate_action_routes": [
                        {
                            "ticker": "NVDA",
                            "final_action": "WATCH",
                            "reason": "pullback_wait_blocked_negative_context",
                        }
                    ],
                },
                source="rescreen",
            )

            self.assertEqual(len(outcomes), 2)
            self.assertTrue(all(item["action"] == "cancel" for item in outcomes))
            self.assertEqual(runtime.store.find_path_run(first)["status"], "CANCELLED")
            self.assertEqual(runtime.store.find_path_run(second)["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
