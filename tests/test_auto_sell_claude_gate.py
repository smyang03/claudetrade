from __future__ import annotations

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

        with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "reason": "not yet"}), patch(
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
