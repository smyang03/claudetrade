from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import EntrySignal
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
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
    def setUp(self) -> None:
        self._pathb_env = patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "true"})
        self._pathb_env.start()

    def tearDown(self) -> None:
        self._pathb_env.stop()

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

    def test_register_from_selection_meta_blocks_when_active_order_unknown_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot._new_buy_block_state = lambda *args, **kwargs: {"allowed": True, "blocked": False}
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            existing = make_price_plan(
                decision_id="dec_unknown",
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
            runtime.adapter.register_plan(existing, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_unknown(
                existing.path_run_id,
                detail="buy_order_exception:timeout",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            created = runtime.register_from_selection_meta(
                "KR",
                {
                    "trade_ready": ["000660"],
                    "v2_decision_ids": {"000660": "dec2"},
                    "price_targets": {
                        "000660": {
                            "buy_zone_low": 150_000,
                            "buy_zone_high": 151_000,
                            "sell_target": 155_000,
                            "stop_loss": 148_000,
                            "hold_days": 1,
                            "confidence": 0.7,
                            "entry_rationale": "support pullback",
                            "exit_rationale": "resistance target",
                            "rationale": "support pullback",
                        }
                    },
                },
            )

            self.assertEqual(created, [])
            self.assertEqual(
                len(
                    store.path_runs_for_session(
                        market="KR",
                        runtime_mode="live",
                        session_date="2026-04-27",
                        path_type="claude_price",
                    )
                ),
                1,
            )

    def test_kr_live_disabled_keeps_claude_price_paper_only(self) -> None:
        meta = {
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
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "false"}):
                self.assertEqual(runtime.register_from_selection_meta("KR", meta), [])

        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=True, store=store)
            runtime.control_store = _Control()
            with patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "false"}):
                self.assertEqual(len(runtime.register_from_selection_meta("KR", meta)), 1)

    def test_register_from_selection_meta_audits_missing_price_targets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PathBRuntime(_Bot(), is_paper=False, store=EventStore(Path(tmp) / "events.db"))
            runtime.control_store = _Control()
            runtime._record_blocked = Mock()

            runs = runtime.register_from_selection_meta(
                "KR",
                {
                    "trade_ready": ["005930"],
                    "v2_decision_ids": {"005930": "dec1"},
                    "price_targets": {},
                },
            )

            self.assertEqual(runs, [])
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "CLAUDE_PRICE_MISSING")

    def test_plan_from_run_rejects_invalid_reloaded_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PathBRuntime(_Bot(), is_paper=False, store=EventStore(Path(tmp) / "events.db"))
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
            ).to_dict()
            plan["buy_zone_low"] = 0

            self.assertIsNone(runtime._plan_from_run({"market": "KR", "ticker": "005930", "plan": plan}))

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

    def test_filled_reconcile_keeps_local_pathb_position_when_broker_balance_lags(self) -> None:
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
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 52_200,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )

            summary = runtime.reconcile_filled_positions("KR", force=True)
            run = store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["kept_open_local"], 1)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["entry_execution_id"], "buy1")

    def test_submit_buy_same_day_reentry_guard_blocks_before_order(self) -> None:
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
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="path_a_decision",
                    execution_id="sell1",
                    reason_code="CLOSED_STOP_LOSS",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"close_reason": "CLOSED_STOP_LOSS", "pnl_pct": -2.0},
                )
            )

            with patch("runtime.pathb_runtime.precheck_order") as precheck, patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=52_100, limit_price=52_100, path_run_id=plan.path_run_id),
                )

            run = store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            precheck.assert_not_called()
            place.assert_not_called()
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["cancel_reason"], "SAME_DAY_REENTRY_COOLDOWN")

    def test_consistency_health_reports_missing_pathb_lifecycle_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
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
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec1",
                    execution_id="buy1",
                    prompt_version="test",
                    brain_snapshot_id="brain",
                    payload={"order_no": "buy1", "qty": 1},
                )
            )

            health = runtime.consistency_health("KR")

            self.assertFalse(health["ok"])
            self.assertIn(
                "pathb_lifecycle_missing_path_run_id",
                {issue["code"] for issue in health["issues"]},
            )

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

    def test_pre_close_carry_review_caches_decision_and_session_close_marks_carried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda: "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": [{"ticker": "005930", "qty": 2, "avg_price": 100, "current_price": 110}]},
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-04-27",
            )
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
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            bot.risk.positions.append({"ticker": "005930", "qty": 2, "entry": 100, "path_type": "claude_price", "pathb_path_run_id": plan.path_run_id})
            bot.price_cache_raw["005930"] = 110
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 10.9  # type: ignore[method-assign]
            runtime._run_pre_close_carry_review = Mock(
                return_value={"decision": "CARRY", "reason": "trend intact", "confidence": 0.8, "advice": {"action": "HOLD"}}
            )

            runtime.scan_exits("KR", force=True)
            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["carry_decision"], "CARRY")

            runtime._minutes_to_close = lambda market: 5.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()
            runtime.scan_exits("KR", force=True)
            runtime._submit_sell.assert_not_called()

            summary = runtime.finalize_carried_positions_at_session_close("KR")
            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["carried"], 1)
            self.assertEqual(run["status"], "CARRIED_OUT")
            self.assertEqual(bot.risk.positions[0]["carry_source"], "pathb_preclose")
            self.assertEqual(bot.risk.positions[0]["origin_path_run_id"], plan.path_run_id)
            self.assertEqual(bot.risk.positions[0]["buy_path"], "path_b")
            self.assertTrue(bot.saved_positions)

    def test_us_pre_close_carry_review_passes_current_usd_display_price(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PathBRuntime(_Bot(), is_paper=False, store=EventStore(Path(tmp) / "events.db"))
            plan = make_price_plan(
                decision_id="dec_us",
                ticker="NVDA",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )
            pos = {
                "ticker": "NVDA",
                "qty": 1,
                "entry": 135000,
                "current_price": 135000,
                "display_current_price": 95.0,
            }

            with patch(
                "minority_report.hold_advisor.ask",
                return_value={"action": "HOLD", "confidence": 0.8, "reason": "trend intact"},
            ) as advisor_ask:
                decision = runtime._run_pre_close_carry_review(
                    plan,
                    pos,
                    current=101.25,
                    minutes_to_close=8.0,
                )

            advisor_pos = advisor_ask.call_args.args[0]
            self.assertEqual(advisor_pos["current_price"], 101.25)
            self.assertEqual(advisor_pos["display_current_price"], 101.25)
            self.assertEqual(decision["decision"], "CARRY")

    def test_cached_carry_does_not_block_hard_target_exit(self) -> None:
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
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(plan.path_run_id, plan={"carry_decision": "CARRY", "carry_reviewed_at": "2026-04-27T15:45:00+09:00"}, merge_plan=True)
            bot.risk.positions.append({"ticker": "005930", "qty": 2, "entry": 100, "path_type": "claude_price", "pathb_path_run_id": plan.path_run_id})
            bot.price_cache_raw["005930"] = 121
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 5.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.close_reason, "CLOSED_CLAUDE_PRICE_TARGET")

    def test_cached_carry_does_not_block_stop_exit(self) -> None:
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
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(plan.path_run_id, plan={"carry_decision": "CARRY", "carry_reviewed_at": "2026-04-27T15:45:00+09:00"}, merge_plan=True)
            bot.risk.positions.append({"ticker": "005930", "qty": 2, "entry": 100, "path_type": "claude_price", "pathb_path_run_id": plan.path_run_id})
            bot.price_cache_raw["005930"] = 89
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 5.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.close_reason, "CLOSED_CLAUDE_PRICE_STOP")

    def test_pre_close_force_exit_defaults_to_sell_without_carry_or_with_cached_sell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
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
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")

            self.assertTrue(runtime._pre_close_force_exit(plan.path_run_id, 10.0))
            store.update_path_run(plan.path_run_id, plan={"carry_decision": "SELL"}, merge_plan=True)
            self.assertTrue(runtime._pre_close_force_exit(plan.path_run_id, 10.0))


if __name__ == "__main__":
    unittest.main()
