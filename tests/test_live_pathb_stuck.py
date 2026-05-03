from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.claude_price_sell_manager import ClaudePriceSellManager
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import PathBRuntime


class _Risk:
    cash = 1_000_000
    positions: list[dict] = []

    def close_position(self, ticker: str, exit_price: float, reason: str):
        for idx, pos in enumerate(self.positions):
            if pos.get("ticker") == ticker:
                return self.positions.pop(idx)
        return None


class _V2:
    brain_snapshot_ids = {"KR": "brain_kr"}


class _Bot:
    def __init__(self) -> None:
        self.is_paper = False
        self.token = ""
        self.risk = _Risk()
        self.risk.positions = []
        self.pending_orders = []
        self.v2 = _V2()
        self.session_active = True
        self.current_market = "KR"
        self.usd_krw_rate = 1350
        self.price_cache_raw = {}
        self.price_cache = {}
        self._v2_same_day_stop_tickers = {"KR": set(), "US": set()}

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _save_positions(self) -> None:
        pass

    def _execute_sell(self, cand: dict, market: str, reason: str):
        self.risk.close_position(cand["ticker"], cand["exit_price"], reason)


def _plan():
    return make_price_plan(
        decision_id="dec1",
        ticker="005930",
        market="KR",
        session_date="2026-04-27",
        buy_zone_low=52_000,
        buy_zone_high=52_500,
        sell_target=54_500,
        stop_loss=51_000,
        hold_days=1,
        confidence=0.7,
    )


class LivePathBStuckTests(unittest.TestCase):
    def test_order_acked_without_local_truth_escalates_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            plan = _plan()
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(
                plan.path_run_id,
                execution_id="ord1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            summary = runtime.recover_on_startup()

            self.assertEqual(summary["order_unknown"], 1)
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "ORDER_UNKNOWN")

    def test_buy_partial_fill_is_exit_monitored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = ClaudePriceAdapter(EventStore(Path(tmp) / "events.db"))
            manager = ClaudePriceSellManager(adapter)
            plan = _plan()
            adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            adapter.mark_partial_filled(
                plan.path_run_id,
                price=52_100,
                qty=1,
                execution_id="ord1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            signal = manager.check_exit(plan.path_run_id, 50_900)

            self.assertTrue(signal.signal)
            self.assertEqual(signal.close_reason, "CLOSED_CLAUDE_PRICE_STOP")

    def test_pathb_sell_order_stays_pending_until_broker_fill_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = _plan()
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=52_100,
                qty=2,
                execution_id="ord1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 52_100,
                    "display_avg_price": 52_100,
                    "display_current_price": 54_600,
                    "current_price": 54_600,
                    "sl": 51_000,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                    "v2_decision_id": "dec1",
                    "v2_execution_id": "ord1",
                    "position_id": "pos1",
                }
            )
            bot.price_cache_raw["005930"] = 54_600
            bot.price_cache["005930"] = 54_600

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ), patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "sell1"},
            ):
                runtime.scan_exits("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "SELL_SENT")
            self.assertEqual(bot.risk.positions[0]["qty"], 2)
            self.assertEqual(bot.risk.positions[0]["pathb_pending_sell_order_no"], "sell1")


if __name__ == "__main__":
    unittest.main()
