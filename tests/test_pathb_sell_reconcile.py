from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.pathb_runtime import KST, PathBControlState, PathBRuntime


class _Risk:
    cash = 1_000_000

    def __init__(self) -> None:
        self.positions: list[dict] = []
        self.closed: list[dict] = []

    def close_position(self, ticker: str, exit_price: float, reason: str, session_date: str | None = None) -> dict | None:
        for pos in list(self.positions):
            if pos.get("ticker") != ticker:
                continue
            self.positions.remove(pos)
            entry = float(pos.get("entry", 0) or 0)
            qty = int(pos.get("qty", 0) or 0)
            pnl = (float(exit_price) - entry) * qty
            pnl_pct = ((float(exit_price) / entry) - 1.0) * 100.0 if entry > 0 else 0.0
            closed = {**pos, "exit_price": exit_price, "reason": reason, "pnl": pnl, "pnl_pct": pnl_pct}
            self.closed.append(closed)
            return closed
        return None


class _Bot:
    token = "token"
    session_active = True
    current_market = "US"
    usd_krw_rate = 1000

    def __init__(self) -> None:
        self.risk = _Risk()
        self.pending_orders: list[dict] = []
        self.price_cache_raw: dict[str, float] = {}
        self.price_cache: dict[str, float] = {}
        self.saved = False

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _price_to_krw(self, price: float, market: str) -> float:
        return float(price) * (1000 if market == "US" else 1)

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        return ticker

    def _save_positions(self) -> None:
        self.saved = True


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


def _plan(ticker: str = "SNAP"):
    return make_price_plan(
        decision_id=f"dec_{ticker}",
        ticker=ticker,
        market="US",
        session_date="2026-04-27",
        buy_zone_low=6,
        buy_zone_high=6.2,
        sell_target=6.6,
        stop_loss=5.7,
        hold_days=1,
        confidence=0.7,
    )


def _runtime(tmp: str, *, balance_provider, ccld_provider) -> tuple[PathBRuntime, object]:
    store = EventStore(Path(tmp) / "events.db")
    bot = _Bot()
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    runtime.control_store = _Control()
    runtime.broker_truth = BrokerTruthSnapshot(
        runtime_mode="live",
        path=Path(tmp) / "broker_truth.json",
        token_provider=lambda: "token",
        balance_provider=balance_provider,
        ccld_provider=ccld_provider,
        date_provider=lambda market: "2026-04-27",
    )
    plan = _plan()
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_filled(plan.path_run_id, price=6.0, qty=12, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain")
    bot.risk.positions.append(
        {
            "ticker": plan.ticker,
            "qty": 12,
            "entry": 6000,
            "display_avg_price": 6.0,
            "path_type": "claude_price",
            "pathb_path_run_id": plan.path_run_id,
        }
    )
    runtime.sell_manager.mark_sell_order_sent(
        plan.path_run_id,
        execution_id="sell1",
        price=6.1,
        qty=12,
        close_reason="CLOSED_CLAUDE_PRICE_PRE_CLOSE",
        runtime_mode="live",
        brain_snapshot_id="brain",
    )
    return runtime, plan


class PathBSellReconcileTests(unittest.TestCase):
    def test_balance_zero_without_ccld_does_not_close_snap_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [],
            )

            summary = runtime.reconcile_sell_pending("US", force=True)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["acked"], 1)
            self.assertEqual(run["status"], "SELL_ACKED")
            self.assertNotEqual(run["status"], "CLOSED")
            self.assertEqual(len(runtime.bot.risk.positions), 1)

    def test_ccld_full_sell_closes_even_if_balance_lags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 12, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [
                    {"ticker": "SNAP", "side": "sell", "order_no": "sell1", "order_qty": 12, "filled_qty": 12, "remaining_qty": 0, "avg_price": 6.1}
                ],
            )

            summary = runtime.reconcile_sell_pending("US", force=True)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertTrue(run["plan"]["exit_fill_confirmed"])
            self.assertEqual(len(runtime.bot.risk.positions), 0)

    def test_ccld_partial_sell_marks_partial_and_keeps_remaining_qty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 7, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [
                    {"ticker": "SNAP", "side": "sell", "order_no": "sell1", "order_qty": 12, "filled_qty": 5, "remaining_qty": 7, "avg_price": 6.1}
                ],
            )

            summary = runtime.reconcile_sell_pending("US", force=True)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["partial"], 1)
            self.assertEqual(run["status"], "SELL_PARTIAL_FILLED")
            self.assertEqual(runtime.bot.risk.positions[0]["qty"], 7)

    def test_pending_sell_ttl_without_fill_becomes_order_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 12, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [],
            )
            old = (datetime.now(KST) - timedelta(minutes=20)).isoformat(timespec="seconds")
            runtime.store.update_path_run(plan.path_run_id, plan={"sell_order_sent_at": old}, merge_plan=True)

            summary = runtime.reconcile_sell_pending("US", force=True)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["order_unknown"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertIn("ttl_expired", run["plan"]["order_unknown_detail"])

    def test_session_close_pending_sell_without_ccld_becomes_order_unknown_before_ttl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 12, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [],
            )

            summary = runtime.finalize_sell_pending_at_session_close("US")
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["order_unknown"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertIn("session_end_unresolved", run["plan"]["order_unknown_detail"])

    def test_exit_order_unknown_does_not_recover_from_entry_buy_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 12, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [
                    {"ticker": "SNAP", "side": "buy", "order_no": "buy1", "order_qty": 12, "filled_qty": 12, "remaining_qty": 0, "avg_price": 6.0}
                ],
            )

            sell_summary = runtime.finalize_sell_pending_at_session_close("US")
            unknown_summary = runtime.finalize_order_unknowns_at_session_close("US")
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(sell_summary["order_unknown"], 1)
            self.assertEqual(unknown_summary["recovered_fill"], 0)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "session_end_unresolved")
            self.assertEqual(run["plan"]["order_unknown_side"], "exit")
            self.assertFalse(run["plan"]["broker_today_sell_fill_evidence"])
            self.assertEqual(len(runtime.bot.risk.positions), 1)

    def test_session_end_exit_unknown_partial_sell_stays_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 7, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [],
            )
            runtime.finalize_sell_pending_at_session_close("US")
            runtime.broker_truth.ccld_provider = lambda market, day: [
                {"ticker": "SNAP", "side": "sell", "order_no": "sell1", "order_qty": 12, "filled_qty": 5, "remaining_qty": 7, "avg_price": 6.1}
            ]

            summary = runtime.reconcile_order_unknowns("US", force=True, session_end=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["session_end_unresolved"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "session_end_unresolved")
            self.assertTrue(run["plan"]["session_end_partial_sell_fill"])
            self.assertEqual(run["plan"]["remaining_qty"], 7)
            self.assertTrue(run["plan"]["next_broker_truth_recheck_at"])

    def test_session_end_exit_unknown_open_sell_order_stays_retryable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "SNAP", "qty": 12, "avg_price": 6.0, "current_price": 6.1}]},
                ccld_provider=lambda market, day: [],
            )
            runtime.finalize_sell_pending_at_session_close("US")
            runtime.broker_truth.ccld_provider = lambda market, day: [
                {"ticker": "SNAP", "side": "sell", "order_no": "sell1", "order_qty": 12, "filled_qty": 0, "remaining_qty": 12, "avg_price": 0}
            ]

            summary = runtime.reconcile_order_unknowns("US", force=True, session_end=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["session_end_unresolved"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "session_end_unresolved")
            self.assertTrue(run["plan"]["session_end_open_sell_order"])
            self.assertTrue(run["plan"]["next_broker_truth_recheck_at"])

    def test_stale_filled_without_broker_position_recovers_from_ccld_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {"ticker": "SNAP", "side": "sell", "order_no": "sell2", "order_qty": 12, "filled_qty": 12, "remaining_qty": 0, "avg_price": 6.1}
                ],
                date_provider=lambda market: "2026-04-27",
            )
            plan = _plan()
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
            runtime.adapter.mark_filled(plan.path_run_id, price=6.0, qty=12, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain")

            summary = runtime.reconcile_filled_positions("US", force=True)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertTrue(run["plan"]["stale_filled_recovered"])


if __name__ == "__main__":
    unittest.main()
