from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import os
import tempfile
import unittest
from unittest.mock import patch

from decision.claude_price_plan import make_price_plan
from execution.claude_price_sell_manager import ExitSignal
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import KST, PathBRuntime
from trading_bot import TradingBot


class _USPathBBot:
    token = "token"
    session_active = True
    current_market = "US"
    usd_krw_rate = 1.0

    def __init__(self) -> None:
        self.risk = SimpleNamespace(positions=[])
        self.pending_orders = []
        self.price_cache_raw = {}
        self.price_cache = {}
        self.v2 = SimpleNamespace(brain_snapshot_ids={"US": "brain"})
        self.today_judgment = {"digest_prompt": ""}
        self.saved_positions = 0

    def _current_session_date_str(self, market: str) -> str:
        return "2026-06-04"

    def _market_regular_open_dt(self, market: str, session_date: str | None = None, now_dt=None) -> datetime:
        return datetime(2026, 6, 4, 22, 30, tzinfo=KST)

    def _minutes_to_close(self, market: str) -> float:
        return 390.0

    def _save_positions(self) -> None:
        self.saved_positions += 1

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        return pos

    def _compute_order_price(self, side: str, market: str, price: float) -> float:
        return float(price)

    def _token_for_market(self, market: str, *, force_refresh: bool = False) -> str:
        return "token"


def _runtime(tmp: str) -> tuple[PathBRuntime, object, dict]:
    bot = _USPathBBot()
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    plan = make_price_plan(
        decision_id="dec-us",
        ticker="TEST",
        market="US",
        session_date="2026-06-04",
        buy_zone_low=98,
        buy_zone_high=101,
        sell_target=110,
        stop_loss=95,
        hold_days=1,
        confidence=0.7,
    )
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_filled(
        plan.path_run_id,
        price=100,
        qty=1,
        execution_id="buy1",
        runtime_mode="live",
        brain_snapshot_id="brain",
    )
    pos = {
        "ticker": "TEST",
        "market": "US",
        "qty": 1,
        "entry": 100.0,
        "display_avg_price": 100.0,
        "display_current_price": 99.4,
        "sl": 99.5,
        "path_type": "claude_price",
        "pathb_path_run_id": plan.path_run_id,
        "selected_reason": "test thesis",
    }
    bot.risk.positions.append(pos)
    return runtime, plan, pos


class PreopenAutoSellRecheckTests(unittest.TestCase):
    def test_preopen_shallow_hard_stop_deferred_without_advisor_or_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]

            with patch.dict(os.environ, {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce"}, clear=False), patch(
                "minority_report.hold_advisor.ask"
            ) as advisor, patch("runtime.pathb_runtime.precheck_order") as precheck:
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id),
                )

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertFalse(ok)
            advisor.assert_not_called()
            precheck.assert_not_called()
            self.assertEqual(run["plan"]["preopen_exit_policy_decision"], "DEFER_OPEN_RECHECK")
            self.assertEqual(run["plan"]["preopen_exit_policy_severity"], "shallow_loss_stop")
            self.assertNotIn("auto_sell_review_action", run["plan"])

    def test_preopen_shadow_records_but_keeps_existing_sell_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]

            with patch.dict(
                os.environ,
                {"PATHB_PREOPEN_EXIT_POLICY_MODE": "shadow", "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "false"},
                clear=False,
            ), patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}) as precheck, patch(
                "runtime.pathb_runtime.place_order", return_value={"success": True, "order_no": "sell1"}
            ):
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id),
                )

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertTrue(ok)
            precheck.assert_called_once()
            self.assertEqual(run["plan"]["preopen_exit_policy_decision"], "DEFER_OPEN_RECHECK")
            self.assertEqual(run["plan"]["preopen_exit_policy_status"], "shadow_observed")
            self.assertFalse(bool(run["plan"].get("preopen_exit_defer_active")))

    def test_preopen_shadow_ignores_legacy_active_defer_after_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "preopen_exit_defer_active": True,
                    "preopen_exit_defer_status": "waiting_open",
                    "preopen_exit_defer_reason": "hard_stop",
                    "preopen_exit_defer_close_reason": "CLOSED_HARD_STOP",
                    "preopen_exit_defer_recorded_at": "2026-06-04T22:20:00+09:00",
                },
                merge_plan=True,
            )
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 31, tzinfo=KST)  # type: ignore[method-assign]

            with patch.dict(
                os.environ,
                {"PATHB_PREOPEN_EXIT_POLICY_MODE": "shadow", "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "false"},
                clear=False,
            ), patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}) as precheck, patch(
                "runtime.pathb_runtime.place_order", return_value={"success": True, "order_no": "sell1"}
            ):
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id),
                )

            self.assertTrue(ok)
            precheck.assert_called_once()

    def test_preopen_severe_and_boundary_stops_sell_now(self) -> None:
        cases = [
            (97.0, 99.5, "severe_loss_stop", False),
            (98.2, 99.0, "boundary_loss_stop", True),
        ]
        for price, stop, severity, boundary in cases:
            with self.subTest(price=price, stop=stop):
                with tempfile.TemporaryDirectory() as tmp:
                    runtime, plan, pos = _runtime(tmp)
                    pos["sl"] = stop
                    runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]

                    with patch.dict(
                        os.environ,
                        {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce", "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "false"},
                        clear=False,
                    ), patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}) as precheck, patch(
                        "runtime.pathb_runtime.place_order", return_value={"success": True, "order_no": "sell1"}
                    ):
                        ok = runtime._submit_sell(
                            plan,
                            pos,
                            ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", price, plan.path_run_id),
                        )

                    run = runtime.store.find_path_run(plan.path_run_id)
                    self.assertTrue(ok)
                    precheck.assert_called_once()
                    self.assertEqual(run["plan"]["preopen_exit_policy_decision"], "SELL_NOW")
                    self.assertEqual(run["plan"]["preopen_exit_policy_severity"], severity)
                    self.assertEqual(bool(run["plan"].get("severity_boundary_case", False)), boundary)

    def test_open_confirm_recheck_does_not_consume_auto_sell_hold_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            signal = ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id)

            with patch.dict(os.environ, {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce"}, clear=False), patch(
                "minority_report.hold_advisor.ask"
            ) as advisor:
                first = runtime._submit_sell(plan, pos, signal)
            self.assertFalse(first)
            advisor.assert_not_called()

            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 36, tzinfo=KST)  # type: ignore[method-assign]
            with patch.dict(
                os.environ,
                {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce", "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"},
                clear=False,
            ), patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}) as advisor, patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ) as precheck, patch("runtime.pathb_runtime.place_order", return_value={"success": True, "order_no": "sell1"}):
                second = runtime._submit_sell(plan, pos, signal)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertTrue(second)
            advisor.assert_called_once()
            precheck.assert_called_once()
            self.assertEqual(run["plan"]["open_confirm_recheck_result"], "SELL_NOW_AFTER_OPEN_CONFIRM")
            self.assertNotEqual(run["plan"].get("auto_sell_review_action"), "HOLD")

    def test_pathb_advisor_context_contains_preopen_session_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]

            with patch.dict(os.environ, {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"}, clear=False), patch(
                "minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}
            ) as advisor:
                result = runtime._run_pathb_sell_review_gate(
                    plan,
                    pos,
                    ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id),
                )

            self.assertTrue(result["allowed"])
            advisor_pos = advisor.call_args.args[0]
            ctx = advisor_pos["advisor_context_v2"]
            self.assertEqual(ctx["session_phase"], "preopen")
            self.assertEqual(ctx["or_status_reason"], "regular_market_not_open")
            self.assertEqual(ctx["exit_signal_severity"], "shallow_loss_stop")
            self.assertEqual(ctx["original_selected_reason"], "test thesis")
            self.assertTrue(ctx["regular_open_at"])


class IntradayReviewStalePositionGuardTests(unittest.TestCase):
    def test_intraday_review_skips_position_removed_by_prior_close(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(positions=[])
        bot._ticker_market = lambda ticker: "US"  # type: ignore[method-assign]
        old_pos = {"ticker": "HPE", "market": "US", "qty": 5, "pathb_path_run_id": "path-hpe"}

        ok, reason, current = bot._position_still_live_for_intraday_review(old_pos, "US")

        self.assertFalse(ok)
        self.assertEqual(reason, "local_position_missing")
        self.assertIsNone(current)

    def test_intraday_review_skips_pathb_run_already_closed(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        pos = {"ticker": "HPE", "market": "US", "qty": 5, "pathb_path_run_id": "path-hpe"}
        bot.risk = SimpleNamespace(positions=[pos])
        bot._ticker_market = lambda ticker: "US"  # type: ignore[method-assign]
        bot.pathb = SimpleNamespace(store=SimpleNamespace(find_path_run=lambda path_run_id: {"status": "CLOSED"}))

        ok, reason, current = bot._position_still_live_for_intraday_review(pos, "US")

        self.assertFalse(ok)
        self.assertEqual(reason, "pathb_run_closed")
        self.assertIs(current, pos)


if __name__ == "__main__":
    unittest.main()
