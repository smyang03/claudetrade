from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision.claude_price_plan import make_price_plan
from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.pathb_runtime import PathBControlState, PathBRuntime


class _Risk:
    def __init__(self) -> None:
        self.cash = 1_000_000
        self.positions = []


class _V2:
    brain_snapshot_ids = {"KR": "brain_kr"}


class _Bot:
    def __init__(self) -> None:
        self.is_paper = False
        self.token = ""
        self.risk = _Risk()
        self.pending_orders = []
        self.v2 = _V2()
        self.session_active = True
        self.current_market = "KR"
        self.usd_krw_rate = 1350
        self.price_cache_raw = {}
        self.price_cache = {}
        self._v2_same_day_stop_tickers = {"KR": set(), "US": set()}
        self.saved_positions = False

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return f"dec_{market}_{ticker}"

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        return ticker

    def _save_positions(self) -> None:
        self.saved_positions = True


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


class PathBRuntimeTests(unittest.TestCase):
    def test_brain_snapshot_id_has_cold_start_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.v2.brain_snapshot_ids = {}
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))

            self.assertEqual(runtime._brain_snapshot_id("KR"), "pathb_cold_start_kr")

    def test_register_from_selection_meta_creates_waiting_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runs = runtime.register_from_selection_meta(
                "KR",
                {
                    "trade_ready": ["005930"],
                    "v2_decision_ids": {"005930": "dec1"},
                    "price_targets": {
                        "005930": {
                            "buy_zone_low": 52000,
                            "buy_zone_high": 52500,
                            "sell_target": 54500,
                            "stop_loss": 51000,
                            "hold_days": 1,
                            "confidence": 0.7,
                            "entry_rationale": "support pullback",
                            "exit_rationale": "resistance target",
                            "rationale": "support pullback",
                        }
                    },
                },
            )

            self.assertEqual(len(runs), 1)
            run = store.find_path_run(runs[0])
            self.assertIsNotNone(run)
            self.assertEqual(run["status"], "WAITING")
            self.assertEqual(run["path_type"], "claude_price")

    def test_scan_waiting_entries_cancels_above_open_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
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
                cancel_if_open_above=53_500,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["005930"] = 54_000

            runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["cancel_reason"], "cancel_if_open_above")

    def test_scan_waiting_entries_keeps_reconcile_when_new_buy_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot._new_buy_block_state = Mock(
                return_value={"allowed": False, "reason": "ENTRY_BLACKOUT", "scope": "market"}
            )
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock(return_value={})
            runtime.reconcile_buy_pending_cancel_above = Mock(return_value={})
            runtime.adapter.get_waiting_runs = Mock(return_value=[])

            runtime.scan_waiting_entries("KR", force=True)

            runtime.reconcile_order_unknowns.assert_called_once_with("KR", force=False)
            runtime.reconcile_buy_pending_cancel_above.assert_called_once_with("KR", force=False)
            runtime.adapter.get_waiting_runs.assert_not_called()

    def test_recover_on_startup_attaches_existing_position_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
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
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=52_200,
                qty=2,
                execution_id="ord1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )
            bot.risk.positions.append({"ticker": "005930", "qty": 2, "entry": 52_200})

            summary = runtime.recover_on_startup()

            self.assertEqual(summary["recovered_positions"], 1)
            self.assertEqual(bot.risk.positions[0]["path_type"], "claude_price")
            self.assertEqual(bot.risk.positions[0]["pathb_path_run_id"], plan.path_run_id)
            self.assertTrue(bot.saved_positions)

    def test_recover_on_startup_escalates_sent_order_without_local_truth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
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
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(
                plan.path_run_id,
                execution_id="ord1",
                price=52_200,
                qty=2,
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            summary = runtime.recover_on_startup()

            self.assertEqual(summary["order_unknown"], 1)
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "ORDER_UNKNOWN")

    def test_recover_on_startup_keeps_filled_run_for_broker_sell_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "sell",
                        "order_no": "sell1",
                        "order_qty": 2,
                        "filled_qty": 2,
                        "remaining_qty": 0,
                        "avg_price": 53_000,
                    }
                ] if market == "KR" else [],
                date_provider=lambda market: "2026-04-27",
            )
            plan = make_price_plan(
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
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=52_200,
                qty=2,
                execution_id="buy1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            summary = runtime.recover_on_startup()
            run = store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["missing_positions"], 1)
            self.assertEqual(run["status"], "CLOSED")
            self.assertNotEqual(run["status"], "ORDER_UNKNOWN")

    def test_order_acked_cancel_above_requests_cancel_and_confirms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-04-27",
            )
            plan = make_price_plan(
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
                cancel_if_open_above=53_500,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["005930"] = 54_000

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                runtime.scan_waiting_entries("KR", force=True)
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(cancel_mock.call_count, 1)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertTrue(run["plan"]["cancel_above_after_ack"])
            self.assertTrue(run["plan"]["cancel_confirmed_by_broker"])

    def test_order_acked_cancel_above_does_not_duplicate_cancel_while_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "ord1",
                        "order_qty": 2,
                        "filled_qty": 0,
                        "remaining_qty": 2,
                        "avg_price": 52_300,
                    }
                ],
                date_provider=lambda market: "2026-04-27",
            )
            plan = make_price_plan(
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
                cancel_if_open_above=53_500,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["005930"] = 54_000

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                runtime.scan_waiting_entries("KR", force=True)
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(cancel_mock.call_count, 1)
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertTrue(run["plan"]["cancel_above_after_ack"])
            self.assertTrue(run["plan"]["cancel_open_order_evidence"])


if __name__ == "__main__":
    unittest.main()
