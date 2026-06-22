"""P0: sell_in_flight 데드락 buster — broker truth 교차확인.

운영자가 broker에서 매도주문을 수동 취소하면 broker open_orders에는 사라지지만 봇
로컬 메모리(pending_orders)에는 잔존한다. 기존 `_find_pending_order`는 로컬만 봐서
`_clear_stale_pathb_closing_lock`이 영구히 막혔고(TTL 900초도 무력), 포지션이
sell_in_flight로 잠겨 관리(손절/익절)에서 제외됐다. broker truth 교차확인으로 끊는다.
"""
from __future__ import annotations

from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import unittest

import runtime.pathb_runtime as prt
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import KST, PathBRuntime
from tests.test_pathb_sell_reconcile import _Bot, _Control

from datetime import datetime


class _FakePlan:
    def __init__(self, ticker="AVGO", path_run_id="run1", market="US"):
        self.ticker = ticker
        self.path_run_id = path_run_id
        self.market = market


def _bare_runtime(tmp: str):
    store = EventStore(Path(tmp) / "events.db")
    bot = _Bot()
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    runtime.control_store = _Control()
    return runtime, bot


def _pos(age_min: float = 5.0) -> dict:
    closing = (datetime.now(KST) - timedelta(minutes=age_min)).isoformat(timespec="seconds")
    return {
        "ticker": "AVGO",
        "qty": 1,
        "entry_native": 392.0,
        "display_avg_price": 392.0,
        "pathb_closing": closing,
        "pathb_pending_sell_order_no": "0030194572",
        "pathb_pending_sell_price": 411.44,
        "pathb_pending_close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
        "pathb_path_run_id": "run1",
    }


def _fake_snapshot(runtime, snapshot: dict) -> None:
    runtime.refresh_broker_truth = lambda market, force=False: None
    runtime.broker_truth = type("_BT", (), {"market_snapshot": staticmethod(lambda market: snapshot)})()


class ClosingLockBrokerTruthTests(unittest.TestCase):
    def test_broker_confirms_no_sell_clears_lock_despite_local_pending(self) -> None:
        # 데드락 재현: 로컬 pending_orders에 매도주문 잔존(broker엔 수동취소로 없음)
        with tempfile.TemporaryDirectory() as tmp:
            runtime, bot = _bare_runtime(tmp)
            pos = _pos(age_min=5)
            bot.pending_orders.append(
                {"market": "US", "ticker": "AVGO", "side": "sell",
                 "order_no": "0030194572", "pathb_path_run_id": "run1"}
            )
            runtime._broker_confirms_no_pending_sell = lambda m, t, p="": True
            cleared = runtime._clear_stale_pathb_closing_lock(pos, "US", "run1")
            self.assertTrue(cleared)
            self.assertNotIn("pathb_closing", pos)
            self.assertNotIn("pathb_pending_sell_order_no", pos)

    def test_broker_unconfirmed_with_local_pending_stays_fail_closed(self) -> None:
        # broker 미확인 + 로컬 pending 존재 + age>TTL → 유지(기존 fail-closed)
        with tempfile.TemporaryDirectory() as tmp:
            runtime, bot = _bare_runtime(tmp)
            pos = _pos(age_min=20)  # TTL 900초 초과
            bot.pending_orders.append(
                {"market": "US", "ticker": "AVGO", "side": "sell",
                 "order_no": "0030194572", "pathb_path_run_id": "run1"}
            )
            runtime._broker_confirms_no_pending_sell = lambda m, t, p="": False
            cleared = runtime._clear_stale_pathb_closing_lock(pos, "US", "run1")
            self.assertFalse(cleared)
            self.assertIn("pathb_closing", pos)

    def test_broker_confirms_but_within_grace_keeps_lock(self) -> None:
        # broker 확인됐어도 grace(60초) 미달이면 유지(체결 반영 지연 보호)
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            pos = _pos(age_min=0.2)  # 12초
            runtime._broker_confirms_no_pending_sell = lambda m, t, p="": True
            cleared = runtime._clear_stale_pathb_closing_lock(pos, "US", "run1")
            self.assertFalse(cleared)
            self.assertIn("pathb_closing", pos)

    def test_zero_qty_never_clears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            pos = _pos(age_min=20)
            pos["qty"] = 0
            runtime._broker_confirms_no_pending_sell = lambda m, t, p="": True
            self.assertFalse(runtime._clear_stale_pathb_closing_lock(pos, "US", "run1"))
            self.assertIn("pathb_closing", pos)

    def test_confirms_helper_detects_open_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            _fake_snapshot(runtime, {"open_orders": [
                {"ticker": "AVGO", "side": "sell", "remaining_qty": 1}]})
            self.assertFalse(runtime._broker_confirms_no_pending_sell("US", "AVGO", "run1"))

    def test_confirms_helper_true_when_no_open_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            _fake_snapshot(runtime, {"open_orders": []})
            self.assertTrue(runtime._broker_confirms_no_pending_sell("US", "AVGO", "run1"))

    def test_confirms_helper_false_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            _fake_snapshot(runtime, {"stale": True, "open_orders": []})
            self.assertFalse(runtime._broker_confirms_no_pending_sell("US", "AVGO", "run1"))

    def test_confirms_helper_ignores_filled_sell_row(self) -> None:
        # remaining_qty=0 인 매도행(이미 체결)은 미체결로 안 봄 → True
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            _fake_snapshot(runtime, {"open_orders": [
                {"ticker": "AVGO", "side": "sell", "remaining_qty": 0}]})
            self.assertTrue(runtime._broker_confirms_no_pending_sell("US", "AVGO", "run1"))


class UnfilledSellShadowTests(unittest.TestCase):
    def test_disabled_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            os.environ["PATHB_UNFILLED_SELL_SHADOW_ENABLED"] = "false"
            self.addCleanup(lambda: os.environ.pop("PATHB_UNFILLED_SELL_SHADOW_ENABLED", None))
            pos = _pos()
            runtime._log_unfilled_sell_shadow(pos, "US", _FakePlan())
            self.assertNotIn("_unfilled_sell_shadow_last_at", pos)

    def test_records_gap_and_chase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            runtime._current_native_price_for_exit = lambda m, t, p: 399.5
            out_path = Path(tmp) / "unfilled.jsonl"
            orig = prt.get_runtime_path
            prt.get_runtime_path = lambda *parts, make_parents=False: out_path
            self.addCleanup(lambda: setattr(prt, "get_runtime_path", orig))
            pos = _pos()
            runtime._log_unfilled_sell_shadow(pos, "US", _FakePlan())
            self.assertTrue(out_path.exists())
            rec = json.loads(out_path.read_text(encoding="utf-8").strip())
            # 지정가 411.44, 현재가 399.5 → 가격이 빠져 미체결(gap 음수)
            self.assertLess(rec["gap_pct"], 0)
            # 추격(399.5) pnl < 지정가(411.44) 가정 pnl, 둘 다 진입 392 대비 양수
            self.assertAlmostEqual(rec["chase_pnl_pct"], (399.5 / 392.0 - 1) * 100, places=2)
            self.assertGreater(rec["limit_pnl_pct"], rec["chase_pnl_pct"])
            self.assertIn("_unfilled_sell_shadow_last_at", pos)

    def test_throttle_skips_second_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _ = _bare_runtime(tmp)
            runtime._current_native_price_for_exit = lambda m, t, p: 399.5
            out_path = Path(tmp) / "unfilled.jsonl"
            orig = prt.get_runtime_path
            prt.get_runtime_path = lambda *parts, make_parents=False: out_path
            self.addCleanup(lambda: setattr(prt, "get_runtime_path", orig))
            pos = _pos()
            runtime._log_unfilled_sell_shadow(pos, "US", _FakePlan())
            runtime._log_unfilled_sell_shadow(pos, "US", _FakePlan())  # throttle 내 → skip
            lines = out_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 1)


if __name__ == "__main__":
    unittest.main()
