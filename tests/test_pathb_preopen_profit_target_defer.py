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


class _PathBBot:
    token = "token"
    session_active = True
    usd_krw_rate = 1.0

    def __init__(self, market: str = "US") -> None:
        self.current_market = str(market or "US").upper()
        self.risk = SimpleNamespace(positions=[])
        self.pending_orders = []
        self.price_cache_raw = {}
        self.price_cache = {}
        self.v2 = SimpleNamespace(brain_snapshot_ids={self.current_market: "brain"})
        self.today_judgment = {"digest_prompt": ""}
        self.saved_positions = 0

    def _current_session_date_str(self, market: str) -> str:
        return "2026-06-04" if str(market or "").upper() == "US" else "2026-06-05"

    def _market_regular_open_dt(self, market: str, session_date=None, now_dt=None) -> datetime:
        if str(market or "").upper() == "KR":
            return datetime(2026, 6, 5, 9, 0, tzinfo=KST)
        return datetime(2026, 6, 4, 22, 30, tzinfo=KST)

    def _minutes_to_close(self, market: str) -> float:
        return 390.0

    def _save_positions(self) -> None:
        self.saved_positions += 1

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        return pos

    def _token_for_market(self, market: str, *, force_refresh: bool = False) -> str:
        return "token"


def _runtime(tmp: str, *, market: str = "US"):
    market_key = str(market or "US").upper()
    bot = _PathBBot(market_key)
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    session_date = "2026-06-04" if market_key == "US" else "2026-06-05"
    ticker = "TEST" if market_key == "US" else "005930"
    plan = make_price_plan(
        decision_id=f"dec-{market_key.lower()}",
        ticker=ticker,
        market=market_key,
        session_date=session_date,
        buy_zone_low=98,
        buy_zone_high=101,
        sell_target=110,
        stop_loss=95,
        hold_days=1,
        confidence=0.7,
    )
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_filled(
        plan.path_run_id, price=100, qty=1, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain"
    )
    pos = {
        "ticker": ticker,
        "market": market_key,
        "qty": 1,
        "entry": 100.0,
        "display_avg_price": 100.0,
        "display_current_price": 110.0,
        "sl": 95.0,
        "path_type": "claude_price",
        "pathb_path_run_id": plan.path_run_id,
    }
    bot.risk.positions.append(pos)
    return runtime, plan, pos


def _target_signal(plan, price: float = 110.0) -> ExitSignal:
    return ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", price, plan.path_run_id)


class PreopenProfitTargetDeferTests(unittest.TestCase):
    def test_us_enforce_preopen_target_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            with patch.dict(os.environ, {"US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "enforce"}, clear=False):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
            self.assertEqual(decision["action"], "DEFER")
            self.assertEqual(decision["preopen_exit_policy_decision"], "DEFER_OPEN_RECHECK")
            self.assertEqual(decision["preopen_exit_policy_severity"], "profit_target_runner")
            self.assertTrue(decision["preopen_exit_defer_active"])

    def test_off_mode_proceeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            with patch.dict(os.environ, {"US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "off"}, clear=False):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
            self.assertEqual(decision["action"], "PROCEED")
            self.assertNotIn("preopen_exit_policy_decision", decision)

    def test_shadow_mode_records_but_proceeds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            with patch.dict(os.environ, {"US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "shadow"}, clear=False):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
            self.assertEqual(decision["action"], "PROCEED")
            self.assertEqual(decision.get("shadow_decision"), "DEFER_OPEN_RECHECK")

    def test_profit_ladder_not_deferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            ladder_signal = ExitSignal(True, "profit_ladder", "CLOSED_PROFIT_LADDER", 108.0, plan.path_run_id)
            with patch.dict(os.environ, {"US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "enforce"}, clear=False):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, ladder_signal)
            self.assertEqual(decision["action"], "PROCEED")
            self.assertNotIn("preopen_exit_policy_decision", decision)

    def test_kr_market_off_blocks_target_defer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp, market="KR")
            runtime._now_kst = lambda: datetime(2026, 6, 5, 8, 50, tzinfo=KST)  # type: ignore[method-assign]
            with patch.dict(
                os.environ,
                {
                    "PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "enforce",
                    "KR_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "off",
                },
                clear=False,
            ):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
            self.assertEqual(decision["action"], "PROCEED")
            self.assertNotIn("preopen_exit_policy_decision", decision)

    def test_stop_reason_unaffected_by_target_toggle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
            stop_signal = ExitSignal(True, "hard_stop", "CLOSED_HARD_STOP", 99.4, plan.path_run_id)
            # target toggle off, but stop policy enforce → 기존 stop defer 유지(회귀)
            with patch.dict(
                os.environ,
                {
                    "PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                    "US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "off",
                },
                clear=False,
            ):
                decision = runtime._pathb_preopen_exit_policy_decision(plan, pos, stop_signal)
            self.assertEqual(decision["action"], "DEFER")
            self.assertEqual(decision["preopen_exit_policy_severity"], "shallow_loss_stop")

    def test_open_confirm_sells_after_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan, pos = _runtime(tmp)
            with patch.dict(os.environ, {"US_PATHB_PREOPEN_PROFIT_TARGET_DEFER_MODE": "enforce"}, clear=False):
                # 1) 프리오픈 22:20 → DEFER 기록
                runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 20, tzinfo=KST)  # type: ignore[method-assign]
                d1 = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
                self.assertEqual(d1["action"], "DEFER")
                # 2) 개장+6분(22:36) 같은 목표 신호 재발화 → 개장확인 후 매도 PROCEED
                runtime._now_kst = lambda: datetime(2026, 6, 4, 22, 36, tzinfo=KST)  # type: ignore[method-assign]
                d2 = runtime._pathb_preopen_exit_policy_decision(plan, pos, _target_signal(plan))
            self.assertEqual(d2["action"], "PROCEED")
            self.assertEqual(d2.get("open_confirm_recheck_result"), "SELL_NOW_AFTER_OPEN_CONFIRM")


if __name__ == "__main__":
    unittest.main()
