from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.pathb_runtime import PathBControlState, PathBRuntime


class _Risk:
    cash = 1_000_000

    def __init__(self) -> None:
        self.positions: list[dict] = []


class _Bot:
    token = "token"
    session_active = True
    current_market = "KR"
    usd_krw_rate = 1350

    def __init__(self) -> None:
        self.risk = _Risk()
        self.pending_orders: list[dict] = []
        self.price_cache_raw: dict[str, float] = {}
        self.price_cache: dict[str, float] = {}
        self.saved = False

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _price_to_krw(self, price: float, market: str) -> float:
        return float(price)

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        return "Test"

    def _save_positions(self) -> None:
        self.saved = True


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


def _plan() -> object:
    return make_price_plan(
        decision_id="decision1",
        ticker="005930",
        market="KR",
        session_date="2026-04-27",
        buy_zone_low=100,
        buy_zone_high=101,
        sell_target=106,
        stop_loss=98,
        hold_days=1,
        confidence=0.8,
        cancel_if_open_above=103,
    )


def _runtime(tmp: str, *, balance_provider, ccld_provider=lambda market, day: []) -> tuple[PathBRuntime, object]:
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
    )
    plan = _plan()
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_order_unknown(plan.path_run_id, detail="test", runtime_mode="live", brain_snapshot_id="brain")
    return runtime, plan


class OrderUnknownReconciliationTests(unittest.TestCase):
    def test_path_a_lifecycle_prevents_pathb_fill_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 100}]},
                ccld_provider=lambda market, day: [{"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 1, "remaining_qty": 0}],
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="ORDER_SENT",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="path_a_decision",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "timing_adapter"},
                )
            )
            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["path_a_origin_possible"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "path_a_origin_possible")

    def test_broker_fill_recovers_pathb_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 102}]},
                ccld_provider=lambda market, day: [{"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 1, "remaining_qty": 0, "avg_price": 100, "order_no": "ord1"}],
            )
            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["recovered_fill"], 1)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_fill_recovered")
            self.assertEqual(len(runtime.bot.risk.positions), 1)

    def test_broker_open_order_recovers_order_acked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [{"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 0, "remaining_qty": 1, "avg_price": 100, "order_no": "ord1"}],
            )
            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["recovered_open_order"], 1)
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_open_order_recovered")

    def test_no_evidence_and_lookup_failure_are_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["plan"]["order_unknown_resolution"], "broker_no_evidence")

        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: (_ for _ in ()).throw(RuntimeError("timeout")))
            runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["plan"]["order_unknown_resolution"], "broker_truth_unavailable")

    def test_session_close_marks_unresolved_without_new_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.finalize_order_unknowns_at_session_close("KR")
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertTrue(run["plan"]["session_end_unresolved"])
            self.assertEqual(run["plan"]["order_unknown_resolution"], "session_end_unresolved")

    def test_permanent_order_reject_does_not_schedule_retry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"order_unknown_detail": "해당종목정보가 없습니다"},
                merge_plan=True,
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["permanent_order_reject"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "permanent_order_reject")
            self.assertEqual(run["plan"]["next_broker_truth_recheck_at"], "")


if __name__ == "__main__":
    unittest.main()
