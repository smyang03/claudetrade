from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision.claude_price_plan import make_price_plan
from execution.claude_price_sell_manager import ExitSignal
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBRuntime
from trading_bot import TradingBot


def _plan_a_bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.risk = SimpleNamespace(
        positions=[
            {
                "ticker": "QCOM",
                "entry": 100.0,
                "current_price": 95.0,
                "display_avg_price": 100.0,
                "display_current_price": 95.0,
                "qty": 1,
                "strategy": "test",
            }
        ]
    )
    bot.price_cache_raw = {"QCOM": 95.0}
    bot.price_cache = {"QCOM": 95.0}
    bot.pending_orders = []
    bot.today_judgment = {"digest_prompt": ""}
    bot._sell_fail_at = {}
    bot._sell_fail_meta = {}
    bot._SELL_FAIL_COOLDOWN_SEC = 60
    bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
    bot._advisor_pos = lambda pos, market: pos  # type: ignore[method-assign]
    bot._record_decision_event = Mock()  # type: ignore[method-assign]
    bot._save_positions = Mock()  # type: ignore[method-assign]
    bot._note_sell_failure = Mock()  # type: ignore[method-assign]
    bot._compute_order_price = lambda side, market, price: float(price)  # type: ignore[method-assign]
    bot._token_for_market = lambda market: "token"  # type: ignore[method-assign]
    return bot


class _PathBBot:
    token = "token"
    session_active = True
    current_market = "KR"
    usd_krw_rate = 1350

    def __init__(self) -> None:
        self.risk = SimpleNamespace(positions=[])
        self.pending_orders = []
        self.price_cache_raw = {}
        self.price_cache = {}
        self.v2 = SimpleNamespace(brain_snapshot_ids={"KR": "brain"})
        self.saved_positions = False
        self.today_judgment = {"digest_prompt": ""}

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _save_positions(self) -> None:
        self.saved_positions = True

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        return pos

    def _build_intraday_context(self, market: str) -> str:
        return ""


def _pathb_runtime(tmp: str) -> tuple[PathBRuntime, object, dict]:
    bot = _PathBBot()
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    plan = make_price_plan(
        decision_id="dec1",
        ticker="005930",
        market="KR",
        session_date="2026-04-27",
        buy_zone_low=100,
        buy_zone_high=101,
        sell_target=120,
        stop_loss=90,
        hold_days=1,
        confidence=0.7,
    )
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=1, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain")
    pos = {"ticker": "005930", "qty": 1, "entry": 100, "path_type": "claude_price", "pathb_path_run_id": plan.path_run_id}
    bot.risk.positions.append(pos)
    return runtime, plan, pos


class AutoSellClaudeGateTests(unittest.TestCase):
    def test_plan_a_stop_loss_hold_blocks_broker_precheck(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "stop_loss"}

        with patch.dict("os.environ", {"AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT": "10"}), patch(
            "minority_report.hold_advisor.ask", return_value={"action": "HOLD", "reason": "not yet"}
        ), patch(
            "trading_bot.precheck_order"
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="stop_loss")

        self.assertFalse(ok)
        precheck.assert_not_called()
        self.assertEqual(bot.risk.positions[0]["auto_sell_review_action"], "HOLD")

    def test_plan_a_loss_cap_sell_reaches_broker_precheck(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "loss_cap"}

        with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
            "trading_bot.precheck_order",
            return_value={"ok": False, "msg": "test stop"},
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="loss_cap")

        self.assertFalse(ok)
        precheck.assert_called_once()
        self.assertEqual(cand["auto_sell_review_action"], "SELL")

    def test_plan_a_loss_cap_hold_blocks_when_loss_is_controlled(self) -> None:
        bot = _plan_a_bot()
        bot.price_cache_raw["QCOM"] = 98.8
        cand = {
            **bot.risk.positions[0],
            "exit_price": 98.8,
            "display_current_price": 98.8,
            "reason": "loss_cap",
        }

        with patch.dict("os.environ", {"AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT": "2.5"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={"action": "HOLD", "confidence": 0.8, "reason": "thesis intact"},
        ) as advisor, patch(
            "trading_bot.precheck_order"
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="loss_cap")

        self.assertFalse(ok)
        precheck.assert_not_called()
        self.assertEqual(cand["auto_sell_review_action"], "HOLD")
        self.assertTrue(cand["auto_sell_review_cooldown_until"])
        self.assertIn("reviewable risk alert", advisor.call_args.kwargs["default_policy"])

    def test_plan_a_loss_cap_hold_is_overridden_after_force_threshold(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "loss_cap"}

        with patch.dict("os.environ", {"AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT": "2.5"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={"action": "HOLD", "confidence": 0.8, "reason": "try one more review"},
        ), patch(
            "trading_bot.precheck_order",
            return_value={"ok": False, "msg": "test stop"},
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="loss_cap")

        self.assertFalse(ok)
        precheck.assert_called_once()
        self.assertEqual(cand["auto_sell_review_action"], "SELL")
        self.assertIn("system_force_sell_pre_cache", cand["auto_sell_review_detail"])

    def test_plan_a_take_profit_hold_blocks_broker_precheck(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "tp_check"}
        bot.price_cache_raw["QCOM"] = 106.0

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "reason": "extend"}), patch(
            "trading_bot.precheck_order"
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="take_profit")

        self.assertFalse(ok)
        precheck.assert_not_called()
        self.assertEqual(cand["auto_sell_review_action"], "HOLD")

    def test_plan_a_tp_trailing_always_calls_claude_when_analyst_disabled(self) -> None:
        bot = _plan_a_bot()
        bot.enable_trailing_analyst = False
        bot.trailing_stop_pct = 0.03
        bot.risk.activate_trailing = Mock(return_value=True)
        bot._execute_sell = Mock()  # type: ignore[method-assign]
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "tp_check"}

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "trail_pct": 0.04}), patch(
            "trading_bot.trailing_alert"
        ):
            bot._handle_tp_trailing(cand, "US")

        bot._execute_sell.assert_not_called()
        bot.risk.activate_trailing.assert_called_once_with("QCOM", 0.04, hold_advice={"action": "HOLD", "trail_pct": 0.04})

    def test_plan_a_trail_stop_advisor_exception_blocks_sell(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "trail_stop"}

        with patch("minority_report.hold_advisor.ask", side_effect=RuntimeError("timeout")), patch(
            "trading_bot.precheck_order"
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="trail_stop")

        self.assertFalse(ok)
        precheck.assert_not_called()
        self.assertTrue(cand["auto_sell_review_fallback"])

    def test_plan_a_manual_sell_bypasses_gate(self) -> None:
        bot = _plan_a_bot()
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "manual_sell"}

        with patch("minority_report.hold_advisor.ask") as advisor, patch(
            "trading_bot.precheck_order",
            return_value={"ok": False, "msg": "test stop"},
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="manual_sell")

        self.assertFalse(ok)
        advisor.assert_not_called()
        precheck.assert_called_once()

    def test_pre_close_force_sell_bypasses_existing_cooldown(self) -> None:
        bot = _plan_a_bot()
        future = (datetime.now().astimezone() + timedelta(minutes=20)).isoformat(timespec="seconds")
        cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "display_current_price": 5880.0,
            "qty": 25,
            "strategy": "RECOVERY_MICRO",
            "recovery_micro": True,
            "recovery_micro_no_carry": True,
            "exit_price": 5880.0,
            "reason": "pre_close",
            "auto_sell_review_cooldown_until": future,
        }

        with patch(
            "minority_report.hold_advisor.ask",
            return_value={"action": "HOLD", "confidence": 0.7, "reason": "wait"},
        ) as advisor:
            review = bot._run_auto_sell_review_gate(cand, "KR", "pre_close", current_native=5770.0)

        self.assertTrue(review["allowed"])
        advisor.assert_not_called()
        self.assertEqual(cand["auto_sell_review_action"], "SELL")
        self.assertIn("system_force_sell_pre_cache:pre_close", cand["auto_sell_review_detail"])

    def test_recovery_micro_force_time_stop_bypasses_existing_cooldown(self) -> None:
        bot = _plan_a_bot()
        future = (datetime.now().astimezone() + timedelta(minutes=20)).isoformat(timespec="seconds")
        cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "display_current_price": 5880.0,
            "qty": 25,
            "strategy": "RECOVERY_MICRO",
            "recovery_micro": True,
            "recovery_micro_no_carry": True,
            "recovery_micro_exit_trigger": "recovery_micro_force_time_stop",
            "exit_price": 5880.0,
            "reason": "recovery_micro_time_stop",
            "auto_sell_review_cooldown_until": future,
        }

        with patch(
            "minority_report.hold_advisor.ask",
            return_value={"action": "HOLD", "confidence": 0.7, "reason": "wait"},
        ) as advisor:
            review = bot._run_auto_sell_review_gate(cand, "KR", "recovery_micro_time_stop", current_native=5770.0)

        self.assertTrue(review["allowed"])
        advisor.assert_not_called()
        self.assertEqual(cand["auto_sell_review_action"], "SELL")
        self.assertIn("recovery_micro_force_time_stop", cand["auto_sell_review_detail"])

    def test_recovery_micro_time_stop_forces_sell_when_latest_price_breaks_stop_or_hard_loss(self) -> None:
        bot = _plan_a_bot()
        past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat(timespec="seconds")
        stop_cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "qty": 25,
            "strategy": "RECOVERY_MICRO",
            "recovery_micro_no_carry": True,
            "sl": 5811.5,
            "exit_price": 5880.0,
            "reason": "recovery_micro_time_stop",
        }
        hard_loss_cand = {
            **stop_cand,
            "sl": 0.0,
            "recovery_micro_hard_loss_pct": 1.5,
        }
        due_cand = {
            **stop_cand,
            "sl": 0.0,
            "recovery_micro_force_exit_at": past,
        }

        stop_force, stop_detail = bot._auto_sell_review_force_sell_required(
            stop_cand,
            "KR",
            "recovery_micro_time_stop",
            current_native=5770.0,
        )
        hard_force, hard_detail = bot._auto_sell_review_force_sell_required(
            hard_loss_cand,
            "KR",
            "recovery_micro_time_stop",
            current_native=5800.0,
        )
        due_force, due_detail = bot._auto_sell_review_force_sell_required(
            due_cand,
            "KR",
            "recovery_micro_time_stop",
            current_native=5880.0,
        )

        self.assertTrue(stop_force)
        self.assertIn("recovery_micro_stop_price", stop_detail)
        self.assertTrue(hard_force)
        self.assertIn("recovery_micro_hard_loss", hard_detail)
        self.assertTrue(due_force)
        self.assertIn("recovery_micro_force_exit_at_due", due_detail)

    def test_kr_auto_sell_review_uses_latest_native_price_context(self) -> None:
        bot = _plan_a_bot()
        cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "display_current_price": 5880.0,
            "qty": 25,
            "strategy": "RECOVERY_MICRO",
            "exit_price": 5880.0,
            "reason": "tp_check",
        }

        with patch(
            "minority_report.hold_advisor.ask",
            return_value={"action": "HOLD", "confidence": 0.7, "reason": "wait"},
        ) as advisor:
            review = bot._run_auto_sell_review_gate(cand, "KR", "tp_check", current_native=5770.0)

        self.assertFalse(review["allowed"])
        advisor_pos = advisor.call_args.args[0]
        self.assertEqual(advisor_pos["current_price"], 5770.0)
        self.assertEqual(advisor_pos["display_current_price"], 5770.0)
        self.assertEqual(advisor_pos["exit_price"], 5770.0)

    def test_ordinary_auto_sell_review_reason_still_respects_cooldown(self) -> None:
        bot = _plan_a_bot()
        future = (datetime.now().astimezone() + timedelta(minutes=20)).isoformat(timespec="seconds")
        cand = {
            **bot.risk.positions[0],
            "exit_price": 106.0,
            "reason": "tp_check",
            "auto_sell_review_cooldown_until": future,
        }

        with patch("minority_report.hold_advisor.ask") as advisor:
            review = bot._run_auto_sell_review_gate(cand, "US", "tp_check", current_native=106.0)

        self.assertFalse(review["allowed"])
        advisor.assert_not_called()
        self.assertEqual(cand["auto_sell_review_action"], "HOLD")
        self.assertIn("auto_sell_review_cooldown", cand["auto_sell_review_detail"])

    def test_soft_cache_hit_does_not_block_force_sell_pre_cache(self) -> None:
        bot = _plan_a_bot()
        bot._hold_advisor_soft_cache_get = Mock(side_effect=AssertionError("cache should not be read"))  # type: ignore[method-assign]
        cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "qty": 25,
            "exit_price": 5880.0,
            "reason": "pre_close",
        }

        with patch.dict(os.environ, {"HOLD_ADVISOR_SOFT_CACHE_ENABLED": "true"}, clear=False), patch(
            "minority_report.hold_advisor.ask"
        ) as advisor:
            review = bot._run_auto_sell_review_gate(cand, "KR", "pre_close", current_native=5770.0)

        self.assertTrue(review["allowed"])
        advisor.assert_not_called()
        bot._hold_advisor_soft_cache_get.assert_not_called()  # type: ignore[attr-defined]
        self.assertEqual(cand["auto_sell_review_action"], "SELL")
        self.assertIn("system_force_sell_pre_cache:pre_close", cand["auto_sell_review_detail"])

    def test_soft_cache_hit_rechecks_force_sell_before_return(self) -> None:
        bot = _plan_a_bot()
        bot._current_session_date_str = lambda market: "2026-05-12"  # type: ignore[method-assign]
        bot._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
        cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5900.0,
            "exit_price": 5900.0,
            "qty": 25,
            "entry_time": "2026-05-12T09:10:00+09:00",
            "reason": "trail_stop",
        }
        payload = {
            "auto_sell_review_reason": "trail_stop",
            "auto_sell_review_action": "HOLD",
            "auto_sell_review_detail": "cached hold",
            "auto_sell_review_confidence": 0.7,
            "auto_sell_review_fallback": False,
        }

        with patch.dict(os.environ, {"HOLD_ADVISOR_SOFT_CACHE_ENABLED": "true"}, clear=False):
            bot._hold_advisor_soft_cache_put(
                cand,
                "KR",
                "trail_stop",
                5900.0,
                payload,
                {"action": "HOLD", "next_review_min": 10},
            )
            calls = {"count": 0}

            def force_sell(*args, **kwargs):
                calls["count"] += 1
                return (False, "") if calls["count"] == 1 else (True, "forced_for_test")

            bot._auto_sell_review_force_sell_required = Mock(side_effect=force_sell)  # type: ignore[method-assign]
            with patch("minority_report.hold_advisor.ask") as advisor:
                review = bot._run_auto_sell_review_gate(cand, "KR", "trail_stop", current_native=5900.0)

        self.assertTrue(review["allowed"])
        advisor.assert_not_called()
        self.assertEqual(calls["count"], 2)
        self.assertEqual(cand["auto_sell_review_action"], "SELL")
        self.assertTrue(cand["hold_advisor_cache_bypassed"])
        self.assertIn("system_force_sell_after_cache_hit:forced_for_test", cand["auto_sell_review_detail"])

    def test_soft_cache_key_is_position_scoped_for_same_ticker_reentry(self) -> None:
        bot = _plan_a_bot()
        bot._current_session_date_str = lambda market: "2026-05-12"  # type: ignore[method-assign]
        bot._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
        first = {
            "ticker": "QCOM",
            "entry": 100.0,
            "current_price": 105.0,
            "exit_price": 105.0,
            "entry_time": "2026-05-12T09:30:00+09:00",
            "reason": "profit_floor",
        }
        second = {
            **first,
            "entry_time": "2026-05-12T10:30:00+09:00",
        }
        payload = {
            "auto_sell_review_reason": "profit_floor",
            "auto_sell_review_action": "HOLD",
            "auto_sell_review_detail": "first entry hold",
            "auto_sell_review_confidence": 0.8,
            "auto_sell_review_fallback": False,
        }

        with patch.dict(os.environ, {"HOLD_ADVISOR_SOFT_CACHE_ENABLED": "true"}, clear=False):
            bot._hold_advisor_soft_cache_put(
                first,
                "US",
                "profit_floor",
                105.0,
                payload,
                {"action": "HOLD", "next_review_min": 10},
            )
            self.assertIsNotNone(bot._hold_advisor_soft_cache_get(first, "US", "profit_floor", 105.0))
            self.assertIsNone(bot._hold_advisor_soft_cache_get(second, "US", "profit_floor", 105.0))

    def test_soft_cache_bypasses_near_close(self) -> None:
        bot = _plan_a_bot()
        bot._current_session_date_str = lambda market: "2026-05-12"  # type: ignore[method-assign]
        bot._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
        cand = {
            "ticker": "QCOM",
            "entry": 100.0,
            "current_price": 105.0,
            "exit_price": 105.0,
            "entry_time": "2026-05-12T09:30:00+09:00",
            "reason": "profit_floor",
        }
        payload = {
            "auto_sell_review_reason": "profit_floor",
            "auto_sell_review_action": "HOLD",
            "auto_sell_review_detail": "cached hold",
            "auto_sell_review_confidence": 0.8,
            "auto_sell_review_fallback": False,
        }

        with patch.dict(os.environ, {"HOLD_ADVISOR_SOFT_CACHE_ENABLED": "true"}, clear=False):
            bot._hold_advisor_soft_cache_put(
                cand,
                "US",
                "profit_floor",
                105.0,
                payload,
                {"action": "HOLD", "next_review_min": 10},
            )
            bot._minutes_to_close = lambda market: 5.0  # type: ignore[method-assign]
            with patch(
                "minority_report.hold_advisor.ask",
                return_value={"action": "HOLD", "confidence": 0.7, "reason": "fresh near-close review"},
            ) as advisor:
                review = bot._run_auto_sell_review_gate(cand, "US", "profit_floor", current_native=105.0)

        self.assertFalse(review["allowed"])
        advisor.assert_called_once()
        self.assertNotIn("hold_advisor_cache_hit", review)
        self.assertEqual(cand["auto_sell_review_detail"], "fresh near-close review")

    def test_recovery_micro_hard_loss_forces_sell_for_soft_exit_reasons(self) -> None:
        bot = _plan_a_bot()
        hard_loss_cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "exit_price": 5880.0,
            "recovery_micro_hard_loss_pct": 1.5,
        }
        stop_cand = {
            "ticker": "012610",
            "entry": 5900.0,
            "current_price": 5880.0,
            "exit_price": 5880.0,
            "recovery_micro": True,
            "sl": 5811.5,
        }
        normal_cand = {
            "ticker": "QCOM",
            "entry": 100.0,
            "current_price": 95.0,
            "exit_price": 95.0,
        }

        hard_force, hard_detail = bot._auto_sell_review_force_sell_required(
            hard_loss_cand,
            "KR",
            "trail_stop",
            current_native=5800.0,
        )
        stop_force, stop_detail = bot._auto_sell_review_force_sell_required(
            stop_cand,
            "KR",
            "profit_floor",
            current_native=5800.0,
        )
        normal_force, normal_detail = bot._auto_sell_review_force_sell_required(
            normal_cand,
            "US",
            "trail_stop",
            current_native=95.0,
        )

        self.assertTrue(hard_force)
        self.assertIn("recovery_micro_hard_loss", hard_detail)
        self.assertTrue(stop_force)
        self.assertIn("recovery_micro_stop_price", stop_detail)
        self.assertFalse(normal_force)
        self.assertEqual(normal_detail, "")

    def test_duplicate_cooldown_log_and_lifecycle_events_are_suppressed(self) -> None:
        bot = _plan_a_bot()
        future = (datetime.now().astimezone() + timedelta(minutes=20)).isoformat(timespec="seconds")
        payload = {
            "ticker": "012610",
            "candidate_reason": "recovery_micro_time_stop",
            "reason": "recovery_micro_time_stop",
            "final_action": "SELL",
        }
        position = {
            "auto_sell_review_reason": "recovery_micro_time_stop",
            "auto_sell_review_cooldown_until": future,
        }

        self.assertTrue(bot._should_log_auto_sell_cooldown("KR", "012610", "recovery_micro_time_stop", future))
        self.assertFalse(bot._should_log_auto_sell_cooldown("KR", "012610", "recovery_micro_time_stop", future))
        self.assertTrue(bot._should_write_exit_lifecycle_event("KR", position, payload))
        self.assertFalse(bot._should_write_exit_lifecycle_event("KR", position, payload))

    def test_auto_sell_review_decision_record_includes_action_detail(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.today_judgment = {"consensus": {"mode": "MODERATE_BULL"}}
        bot.decision_event_log = []
        bot._current_session_date_str = lambda market: "2026-05-06"  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            with patch("trading_bot.DECISIONS_FILE", decisions_path):
                bot._record_decision_event(
                    "US",
                    "auto_sell_review",
                    "AMD",
                    strategy="momentum",
                    qty=1,
                    price_native=405.0,
                    price_krw=586_581.75,
                    reason="loss_cap",
                    detail="thesis broken",
                    auto_sell_review_action="SELL",
                    auto_sell_review_detail="thesis broken",
                    auto_sell_review_confidence=0.82,
                    auto_sell_review_fallback=False,
                )

            record = json.loads(decisions_path.read_text(encoding="utf-8").splitlines()[-1])

        self.assertEqual(record["type"], "auto_sell_review")
        self.assertEqual(record["auto_sell_review_action"], "SELL")
        self.assertEqual(record["auto_sell_review_detail"], "thesis broken")
        self.assertAlmostEqual(record["auto_sell_review_confidence"], 0.82)

    def test_plan_a_live_sell_waits_for_broker_fill_confirmation(self) -> None:
        bot = _plan_a_bot()
        bot.is_paper = False
        bot.usd_krw_rate = 1000
        bot._write_live_status = Mock()  # type: ignore[method-assign]
        cand = {**bot.risk.positions[0], "exit_price": 95.0, "reason": "stop_loss"}

        with patch.dict("os.environ", {"LIVE_SELL_CONFIRM_ATTEMPTS": "1", "LIVE_SELL_CONFIRM_DELAY_SEC": "0"}), patch(
            "minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}
        ), patch(
            "trading_bot.precheck_order", return_value={"ok": True}
        ), patch(
            "trading_bot.place_order", return_value={"success": True, "order_no": "sell1"}
        ), patch.object(
            TradingBot, "_lookup_order_fill", return_value=None
        ):
            ok = bot._execute_sell(cand, "US", reason="stop_loss")

        self.assertTrue(ok)
        self.assertEqual(len(bot.risk.positions), 1)
        self.assertTrue(bot.risk.positions[0]["sell_confirmation_pending"])
        self.assertEqual(bot.risk.positions[0]["pending_sell_order_no"], "sell1")
        actions = [call.args[1] for call in bot._record_decision_event.call_args_list]
        self.assertNotIn("sell_filled", actions)

    def test_pathb_target_hold_blocks_submit_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "reason": "carry"}), patch(
                "runtime.pathb_runtime.precheck_order"
            ) as precheck:
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 121.0, plan.path_run_id),
                )

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertFalse(ok)
            precheck.assert_not_called()
            self.assertNotIn("pathb_closing", pos)
            self.assertEqual(run["plan"]["auto_sell_review_action"], "HOLD")

    def test_pathb_hard_stop_bypasses_hold_advisor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)

            with patch("minority_report.hold_advisor.ask") as advisor, patch(
                "runtime.pathb_runtime.precheck_order",
                return_value={"ok": True},
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "sell1"},
            ):
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 88.0, plan.path_run_id),
                )

            self.assertTrue(ok)
            advisor.assert_not_called()
            precheck.assert_called_once()

    def test_pathb_target_hold_stores_policy_and_revised_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)
            advice = {
                "action": "HOLD",
                "reason": "extend target with protected profit",
                "confidence": 0.78,
                "revised_sell_target": 130.0,
                "protective_stop": 112.0,
                "valid_for_min": 20,
                "reask_after_min": 15,
                "reask_drawdown_from_peak_pct": 0.8,
            }

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "enforce"}), patch(
                "minority_report.hold_advisor.ask", return_value=advice
            ), patch("runtime.pathb_runtime.precheck_order") as precheck:
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 121.0, plan.path_run_id),
                )

            run = runtime.store.find_path_run(plan.path_run_id)
            policy = run["plan"]["auto_sell_policy"]
            self.assertFalse(ok)
            precheck.assert_not_called()
            self.assertEqual(policy["mode"], "target_extension")
            self.assertEqual(policy["revised_sell_target"], 130.0)
            self.assertEqual(policy["protective_stop"], 112.0)
            self.assertEqual(run["plan"]["sell_target"], 130.0)

    def test_pathb_active_policy_suppresses_repeated_target_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)
            now = datetime.now().astimezone()
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "sell_target": 130.0,
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "target_extension",
                        "valid_until": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "reask_after_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "revised_sell_target": 130.0,
                        "protective_stop": 112.0,
                        "peak_price": 121.0,
                    },
                },
                merge_plan=True,
            )

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "enforce"}):
                result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 122.0)

            self.assertEqual(result["action"], "skip")
            self.assertEqual(result["reason"], "inside_target_policy")

    def test_pathb_policy_protective_stop_is_firm_exit_without_advisor_recheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)
            now = datetime.now().astimezone()
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "sell_target": 130.0,
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "target_extension",
                        "valid_until": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "reask_after_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "revised_sell_target": 130.0,
                        "protective_stop": 112.0,
                        "peak_price": 121.0,
                    },
                },
                merge_plan=True,
            )

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "enforce"}):
                result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 111.0)

            signal = result["signal"]
            self.assertEqual(result["action"], "sell")
            self.assertEqual(signal.reason, "policy_protective_stop")
            self.assertEqual(signal.close_reason, "CLOSED_CLAUDE_PRICE_STOP")

            with patch("minority_report.hold_advisor.ask") as advisor, patch(
                "runtime.pathb_runtime.precheck_order",
                return_value={"ok": True},
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "sell1"},
            ):
                ok = runtime._submit_sell(plan, pos, signal)

            self.assertTrue(ok)
            advisor.assert_not_called()
            precheck.assert_called_once()

    def test_pathb_expired_policy_does_not_suppress_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)
            now = datetime.now().astimezone()
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "target_extension",
                        "valid_until": (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
                        "reask_after_at": (now - timedelta(minutes=1)).isoformat(timespec="seconds"),
                        "revised_sell_target": 130.0,
                        "protective_stop": 112.0,
                    },
                },
                merge_plan=True,
            )

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "enforce"}):
                result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 122.0)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(result["action"], "proceed")
            self.assertEqual(run["plan"]["auto_sell_policy"]["status"], "expired")

    def test_pathb_shadow_policy_records_would_skip_but_proceeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)
            now = datetime.now().astimezone()
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "target_extension",
                        "valid_until": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "reask_after_at": (now + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "revised_sell_target": 130.0,
                        "protective_stop": 112.0,
                        "peak_price": 121.0,
                    },
                },
                merge_plan=True,
            )

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "shadow"}):
                result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 122.0)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(result["action"], "proceed")
            self.assertEqual(result["reason"], "shadow_inside_target_policy")
            self.assertEqual(run["plan"]["auto_sell_policy_shadow"]["reason"], "inside_target_policy")

    def test_pathb_claude_stop_recovery_rejects_too_wide_hard_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _plan, _pos = _pathb_runtime(tmp)
            us_plan = make_price_plan(
                decision_id="dec-us",
                ticker="TEST",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )

            policy, reject_reason = runtime._pathb_auto_sell_policy_from_advice(
                us_plan,
                {},
                ExitSignal(True, "claude_stop_loss", "CLOSED_CLAUDE_PRICE_STOP", 89.5, us_plan.path_run_id),
                {
                    "action": "HOLD",
                    "hard_stop": 86.0,
                    "recover_above": 90.5,
                    "valid_for_min": 10,
                    "reason": "watch recovery",
                },
                89.5,
                now=datetime.now().astimezone(),
            )

            self.assertEqual(policy, {})
            self.assertEqual(reject_reason, "hard_stop_gap_too_wide")

    def test_pathb_stop_sell_allows_submit_and_records_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _pathb_runtime(tmp)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order",
                return_value={"ok": True},
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "sell1"},
            ):
                ok = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "claude_stop_loss", "CLOSED_CLAUDE_PRICE_STOP", 89.0, plan.path_run_id),
                )

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertTrue(ok)
            precheck.assert_called_once()
            self.assertEqual(run["plan"]["auto_sell_review_action"], "SELL")
            self.assertEqual(pos["pathb_pending_sell_order_no"], "sell1")


if __name__ == "__main__":
    unittest.main()
