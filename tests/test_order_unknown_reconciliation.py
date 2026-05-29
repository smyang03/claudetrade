from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from execution.order_state import OrderUnknownEscalator
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
        date_provider=lambda market: "2026-04-27",
    )
    plan = _plan()
    runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain")
    runtime.adapter.mark_order_unknown(plan.path_run_id, detail="test", runtime_mode="live", brain_snapshot_id="brain")
    return runtime, plan


def _mark_exit_order_unknown(
    runtime: PathBRuntime,
    plan: object,
    *,
    entry_execution_id: str = "buy1",
    exit_execution_id: str = "stale-sell",
    qty: int = 1,
    entry_time: str = "2026-04-27T09:30:00+09:00",
    sell_sent_at: str = "2026-04-27T10:00:00+09:00",
) -> None:
    runtime.adapter.mark_filled(
        plan.path_run_id,
        price=100,
        qty=qty,
        execution_id=entry_execution_id,
        runtime_mode="live",
        brain_snapshot_id="brain",
    )
    runtime.sell_manager.mark_sell_order_sent(
        plan.path_run_id,
        execution_id=exit_execution_id,
        price=105,
        qty=qty,
        close_reason="CLOSED_CLAUDE_PRICE_PRE_CLOSE",
        runtime_mode="live",
        brain_snapshot_id="brain",
    )
    runtime.adapter.mark_order_unknown(
        plan.path_run_id,
        detail="sell_fill_not_confirmed:test",
        runtime_mode="live",
        brain_snapshot_id="brain",
        execution_id=exit_execution_id,
    )
    runtime.store.update_path_run(
        plan.path_run_id,
        plan={
            "filled_at": entry_time,
            "entry_execution_id": entry_execution_id,
            "entry_qty": qty,
            "actual_entry_price": 100,
            "sell_order_sent_at": sell_sent_at,
            "exit_execution_id": exit_execution_id,
            "exit_qty": qty,
            "exit_order_price": 105,
            "pending_close_reason": "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
        },
        merge_plan=True,
    )


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
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "path_a_origin_possible")
            self.assertEqual(run["plan"]["cancel_reason"], "order_unknown_path_a_origin_possible")

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

    def test_generic_pathb_fill_event_is_not_path_a_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 102}]},
                ccld_provider=lambda market, day: [{"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 1, "remaining_qty": 0, "avg_price": 100, "order_no": "ord_pathb"}],
            )
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"entry_execution_id": "ord_pathb", "entry_qty": 1},
                merge_plan=True,
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="ord_pathb",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"order_no": "ord_pathb", "qty": 1},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_fill"], 1)
            self.assertEqual(summary["path_a_origin_possible"], 0)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_fill_recovered")

    def test_pathb_closed_lifecycle_recovers_order_unknown_before_path_a_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [],
            )
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"entry_execution_id": "buy1", "entry_qty": 1},
                merge_plan=True,
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="buy1",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"order_no": "buy1", "qty": 1},
                )
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="sell1",
                    reason_code="CLOSED_USER_MANUAL",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={
                        "path_type": "claude_price",
                        "path_run_id": plan.path_run_id,
                        "close_reason": "CLOSED_USER_MANUAL",
                        "pnl_pct": 1.2,
                    },
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 1)
            self.assertEqual(summary["path_a_origin_possible"], 0)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_closed_lifecycle_recovered")
            self.assertEqual(run["plan"]["exit_execution_id"], "sell1")

    def test_exact_pathb_broker_fill_recovers_before_path_a_heuristic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 102}]},
                ccld_provider=lambda market, day: [
                    {"ticker": "005930", "side": "buy", "order_qty": 1, "filled_qty": 1, "remaining_qty": 0, "avg_price": 100, "order_no": "pathb_buy"}
                ],
            )
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"entry_execution_id": "pathb_buy", "entry_qty": 1},
                merge_plan=True,
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="path_a_decision",
                    execution_id="path_a_buy",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "timing_adapter", "order_no": "path_a_buy", "qty": 1},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_fill"], 1)
            self.assertEqual(summary["path_a_origin_possible"], 0)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_fill_recovered")

    def test_pathb_broker_position_recovers_before_path_a_heuristic_when_execution_id_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 102}]},
                ccld_provider=lambda market, day: [],
            )
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"entry_execution_id": "pathb_buy", "entry_qty": 1},
                merge_plan=True,
            )
            runtime.store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="path_a_decision",
                    execution_id="path_a_buy",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "timing_adapter", "order_no": "path_a_buy", "qty": 1},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_position"], 1)
            self.assertEqual(summary["path_a_origin_possible"], 0)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_position_recovered")
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
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_phase"], "UNKNOWN_PENDING")
            self.assertEqual(run["plan"]["order_unknown_reconcile_attempts"], 1)
            self.assertEqual(run["plan"]["order_unknown_soft_timeout_sec"], 90)
            self.assertEqual(run["plan"]["order_unknown_hard_timeout_sec"], 300)

        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: (_ for _ in ()).throw(RuntimeError("timeout")))
            runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["plan"]["order_unknown_resolution"], "broker_truth_unavailable")

    def test_order_unknown_phase_final_blocked_is_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "order_unknown_first_seen_at": "2026-01-01T09:00:00+09:00",
                    "order_unknown_reconcile_attempts": 1,
                },
                merge_plan=True,
            )

            runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_phase"], "UNKNOWN_FINAL_BLOCKED")
            self.assertEqual(run["plan"]["order_unknown_reconcile_attempts"], 2)
            self.assertEqual(run["plan"]["order_unknown_min_reconcile_attempts"], 2)

    def test_session_close_marks_unresolved_without_new_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.finalize_order_unknowns_at_session_close("KR")
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertTrue(run["plan"]["session_end_unresolved"])
            self.assertTrue(run["plan"]["manual_reconciliation_required"])
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
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "permanent_order_reject")
            self.assertEqual(run["plan"]["next_broker_truth_recheck_at"], "")
            self.assertEqual(run["plan"]["cancel_reason"], "order_unknown_permanent_reject")

    def test_buying_power_reject_is_permanent_order_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={"order_unknown_detail": "주문가능금액을 초과 했습니다"},
                merge_plan=True,
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["permanent_order_reject"], 1)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "permanent_order_reject")
            self.assertEqual(run["plan"]["cancel_reason"], "order_unknown_permanent_reject")

    def test_session_open_reconciles_cross_session_and_clears_escalator_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.bot._current_session_date_str = lambda market: "2026-04-28"  # type: ignore[method-assign]
            escalator = OrderUnknownEscalator(Path(tmp) / "unknown.json")
            escalator.record_unknown(market="KR", ticker="005930", execution_id="ord1", detail="stale local pending")
            escalator.record_unknown(market="KR", ticker="000660", execution_id="ord2", detail="stale local pending")
            runtime.bot.v2_order_unknown = escalator

            permanent = make_price_plan(
                decision_id="decision2",
                ticker="000660",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=106,
                stop_loss=98,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(permanent, runtime_mode="live", brain_snapshot_id="brain")
            runtime.adapter.mark_order_unknown(
                permanent.path_run_id,
                detail="unsupported symbol",
                runtime_mode="live",
                brain_snapshot_id="brain",
            )
            runtime.store.update_path_run(
                permanent.path_run_id,
                plan={"order_unknown_resolution": "permanent_order_reject"},
                merge_plan=True,
            )

            summary = runtime.reconcile_order_unknowns_at_open("KR")
            run = runtime.store.find_path_run(plan.path_run_id)
            permanent_run = runtime.store.find_path_run(permanent.path_run_id)
            state = escalator.state

            self.assertEqual(summary["checked"], 2)
            self.assertEqual(summary["auto_cleared_no_broker_evidence"], 1)
            self.assertEqual(summary["permanent_order_reject"], 1)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "auto_cleared_no_broker_evidence")
            self.assertEqual(permanent_run["status"], "CANCELLED")
            self.assertFalse(escalator.should_block_market("KR"))
            self.assertEqual(state["market_consecutive_unknown"]["KR"], 0)
            self.assertEqual(
                state["orders"]["KR:ord1"]["resolution"],
                "AUTO_CLEARED_NO_BROKER_EVIDENCE",
            )

    def test_session_open_does_not_auto_clear_manual_reconciliation_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.bot._current_session_date_str = lambda market: "2026-04-28"  # type: ignore[method-assign]
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "session_end_unresolved": True,
                    "manual_reconciliation_required": True,
                    "order_unknown_resolution": "session_end_unresolved",
                },
                merge_plan=True,
            )

            summary = runtime.reconcile_order_unknowns_at_open("KR")
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["manual_reconciliation_required"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "manual_reconciliation_required")
            self.assertTrue(run["plan"]["auto_clear_no_evidence_blocked"])
            self.assertTrue(run["plan"]["manual_reconciliation_required"])

    def test_session_open_treats_legacy_session_end_unresolved_as_manual_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.bot._current_session_date_str = lambda market: "2026-04-28"  # type: ignore[method-assign]
            runtime.store.update_path_run(
                plan.path_run_id,
                plan={
                    "session_end_unresolved": True,
                    "order_unknown_resolution": "session_end_unresolved",
                },
                merge_plan=True,
            )

            summary = runtime.reconcile_order_unknowns_at_open("KR")
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["manual_reconciliation_required"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "manual_reconciliation_required")
            self.assertTrue(run["plan"]["auto_clear_no_evidence_blocked"])
            self.assertTrue(run["plan"]["manual_reconciliation_required"])

    def test_external_close_updates_pathb_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})

            synced = runtime.on_external_close(
                {"pathb_path_run_id": plan.path_run_id, "pnl_pct": 1.25, "position_id": "pos1"},
                market="KR",
                execution_id="sell1",
                close_reason="CLOSED_USER_MANUAL",
                price=105,
            )
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertTrue(synced)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(run["plan"]["close_reason"], "CLOSED_USER_MANUAL")
            self.assertEqual(run["plan"]["exit_execution_id"], "sell1")
            self.assertTrue(run["plan"]["external_close_synced"])

    def test_exit_order_unknown_recovers_when_broker_sell_order_id_differs_and_zero_holding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan)

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "pathb_sell_fill_recovered_by_broker_evidence")
            self.assertTrue(run["plan"]["exit_execution_id_mismatch"])
            self.assertEqual(run["plan"]["stale_exit_execution_id"], "stale-sell")
            self.assertEqual(run["plan"]["matched_exit_execution_id"], "actual-sell")
            self.assertEqual(run["plan"]["broker_sell_fill_qty"], 1)
            self.assertEqual(run["plan"]["broker_position_qty_after_sell"], 0)
            self.assertFalse(run["plan"]["broker_open_sell_order_evidence"])

    def test_exit_order_unknown_allows_sell_fill_within_timestamp_grace_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "095930",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan, sell_sent_at="2026-04-27T10:00:00+09:00")

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(run["plan"]["timestamp_grace_period_sec"], 60)
            self.assertEqual(run["plan"]["sell_fill_timestamp_blocked_count"], 0)

    def test_exit_order_unknown_does_not_recover_when_sell_fill_precedes_local_exit_request_beyond_grace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "095800",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan, sell_sent_at="2026-04-27T10:00:00+09:00")

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "no_sell_fill_candidate")
            self.assertEqual(run["plan"]["sell_fill_timestamp_blocked_count"], 1)
            self.assertEqual(run["plan"]["order_unknown_resolution"], "broker_no_sell_evidence")

    def test_exit_order_unknown_does_not_recover_on_qty_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan, qty=2)

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "fallback_qty_mismatch")
            self.assertEqual(run["plan"]["broker_sell_fill_qty"], 1)

    def test_exit_order_unknown_does_not_recover_when_position_still_held(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {
                    "cash": 0,
                    "stocks": [{"ticker": "005930", "qty": 1, "avg_price": 100, "current_price": 105}],
                },
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan)

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "broker_position_still_held")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "sell_fill_not_confirmed")

    def test_exit_order_unknown_does_not_recover_when_multiple_sell_fills_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell-1",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    },
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell-2",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100020",
                    },
                ],
            )
            _mark_exit_order_unknown(runtime, plan)

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "ambiguous_sell_fill_candidates")
            self.assertEqual(run["plan"]["sell_fill_candidate_count"], 2)

    def test_exit_order_unknown_does_not_recover_when_other_active_pathb_exposure_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan)
            other_plan = _plan()
            runtime.adapter.register_plan(other_plan, runtime_mode="live", brain_snapshot_id="brain")
            runtime.adapter.mark_filled(
                other_plan.path_run_id,
                price=100,
                qty=1,
                execution_id="other-buy",
                runtime_mode="live",
                brain_snapshot_id="brain",
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "other_active_local_exposure")
            self.assertEqual(run["plan"]["other_active_local_exposure"][0]["source"], "pathb_run")

    def test_exit_order_unknown_does_not_recover_when_path_a_sell_evidence_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(
                tmp,
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "actual-sell",
                        "order_qty": 1,
                        "filled_qty": 1,
                        "remaining_qty": 0,
                        "avg_price": 105,
                        "order_date": "20260427",
                        "fill_time": "100015",
                    }
                ],
            )
            _mark_exit_order_unknown(runtime, plan)
            runtime.store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="actual-sell",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "timing_adapter", "order_no": "actual-sell", "side": "sell"},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["ambiguous_broker_truth"], 1)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["sell_close_evidence_reason"], "path_a_sell_evidence")
            self.assertTrue(run["plan"]["path_a_sell_evidence"])

    def test_closed_lifecycle_evidence_requires_same_path_run_when_decision_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            other_plan = _plan()
            runtime.adapter.register_plan(other_plan, runtime_mode="live", brain_snapshot_id="brain")
            runtime.sell_manager.mark_closed(
                other_plan.path_run_id,
                close_reason="CLOSED_USER_MANUAL",
                price=105,
                pnl_pct=1.0,
                runtime_mode="live",
                brain_snapshot_id="brain",
                execution_id="other-sell",
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 0)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertNotEqual(run["plan"].get("exit_execution_id"), "other-sell")

    def test_closed_lifecycle_evidence_allows_legacy_decision_only_when_single_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="legacy-sell",
                    reason_code="CLOSED_USER_MANUAL",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "claude_price", "close_reason": "CLOSED_USER_MANUAL", "pnl_pct": 1.5},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(
                run["plan"]["pathb_closed_lifecycle_evidence"]["closed_lifecycle_match_reason"],
                "legacy_single_decision_candidate",
            )

    def test_closed_lifecycle_evidence_matches_exit_execution_id_for_legacy_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            other_plan = _plan()
            runtime.adapter.register_plan(other_plan, runtime_mode="live", brain_snapshot_id="brain")
            runtime.store.update_path_run(plan.path_run_id, plan={"exit_execution_id": "legacy-sell"}, merge_plan=True)
            runtime.store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="legacy-sell",
                    reason_code="CLOSED_USER_MANUAL",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "claude_price", "close_reason": "CLOSED_USER_MANUAL", "pnl_pct": 1.5},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertEqual(
                run["plan"]["pathb_closed_lifecycle_evidence"]["closed_lifecycle_match_reason"],
                "legacy_exit_execution_id",
            )

    def test_closed_lifecycle_evidence_rejects_legacy_event_from_other_session_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, plan = _runtime(tmp, balance_provider=lambda market, force: {"cash": 0, "stocks": []})
            runtime.store.update_path_run(plan.path_run_id, plan={"exit_execution_id": "legacy-sell"}, merge_plan=True)
            runtime.store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-26",
                    ticker="005930",
                    decision_id=plan.decision_id,
                    execution_id="legacy-sell",
                    reason_code="CLOSED_USER_MANUAL",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"path_type": "claude_price", "close_reason": "CLOSED_USER_MANUAL", "pnl_pct": 1.5},
                )
            )

            summary = runtime.reconcile_order_unknowns("KR", force=True, path_run_id=plan.path_run_id)
            run = runtime.store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_closed"], 0)
            self.assertEqual(run["status"], "ORDER_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
