from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import EntrySignal
from execution.claude_price_sell_manager import ExitSignal
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.pathb_runtime import KST, PathBControlState, PathBRuntime, _bot_token


class _Risk:
    def __init__(self) -> None:
        self.cash = 1_000_000
        self.positions = []

    def close_position(
        self,
        ticker: str,
        exit_price: float,
        reason: str,
        session_date: str | None = None,
        exit_meta: dict | None = None,
    ) -> dict | None:
        for idx, pos in enumerate(list(self.positions)):
            if str(pos.get("ticker", "")) != ticker:
                continue
            self.positions.pop(idx)
            qty = int(pos.get("qty", 0) or 0)
            entry = float(pos.get("entry", 0) or 0)
            pnl = (float(exit_price or 0) - entry) * qty
            pnl_pct = ((float(exit_price or 0) / entry) - 1.0) * 100.0 if entry > 0 else 0.0
            result = {
                **pos,
                **dict(exit_meta or {}),
                "ticker": ticker,
                "qty": qty,
                "entry": entry,
                "exit_price": float(exit_price or 0),
                "exit_reason": reason,
                "pnl": pnl,
                "pnl_krw": pnl,
                "pnl_pct": pnl_pct,
            }
            return result
        return None


class _V2:
    brain_snapshot_ids = {"KR": "brain_kr"}

    def daily_entry_count(self, market: str) -> int:
        return 0

    def max_daily_entries(self, market: str | None = None) -> int | None:
        return None


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
        self._daily_sl_count = {"KR": 0, "US": 0}
        self.saved_positions = False
        self.blocked_entries = []
        self.decision_events = []

    def _current_session_date_str(self, market: str) -> str:
        return "2026-04-27"

    def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
        return f"dec_{market}_{ticker}"

    def _lookup_ticker_name(self, ticker: str, market: str) -> str:
        return ticker

    def _save_positions(self) -> None:
        self.saved_positions = True

    def _add_pending_order(self, order: dict) -> None:
        self.pending_orders.append(dict(order))

    def _block_entry(self, ticker: str, minutes: int, reason: str) -> None:
        self.blocked_entries.append((ticker, minutes, reason))

    def _record_decision_event(self, market: str, action: str, ticker: str, **kwargs) -> None:
        self.decision_events.append({"market": market, "action": action, "ticker": ticker, **kwargs})


class _PnlBot:
    def _market_realized_daily_return_pct(self, market: str) -> float:
        return -0.25

    def _market_daily_return_pct(self, market: str) -> float:
        return -3.5

    def _daily_pnl_pct(self, market: str) -> float:
        return -4.0


class _MarketTokenBot(_Bot):
    def __init__(self) -> None:
        super().__init__()
        self.token = "legacy-token"
        self.token_calls: list[tuple[str, bool]] = []

    def _token_for_market(self, market: str, *, force_refresh: bool = False) -> str:
        market_key = str(market or "").upper()
        self.token_calls.append((market_key, bool(force_refresh)))
        return f"token-{market_key}-{int(bool(force_refresh))}"


class _Control:
    def load(self) -> PathBControlState:
        return PathBControlState(enabled=True, emergency_disabled=False)


class PathBRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pathb_env = patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "true"})
        self._pathb_env.start()

    def tearDown(self) -> None:
        self._pathb_env.stop()

    def test_bot_token_helper_uses_market_token_when_available(self) -> None:
        bot = _MarketTokenBot()

        token = _bot_token(bot, "US", force_refresh=True)

        self.assertEqual(token, "token-US-1")
        self.assertEqual(bot.token_calls, [("US", True)])

    def test_bot_token_helper_falls_back_to_legacy_token(self) -> None:
        bot = _Bot()
        bot.token = "legacy-only"

        self.assertEqual(_bot_token(bot, "US"), "legacy-only")

    def test_record_blocked_max_daily_entries_sends_new_buy_alert(self) -> None:
        lifecycle_events: list[tuple[tuple, dict]] = []
        alerts: list[tuple] = []
        bot = _Bot()
        bot._v2_record_lifecycle_event = lambda *args, **kwargs: lifecycle_events.append((args, kwargs))
        bot._maybe_alert_new_buy_block = lambda *args: alerts.append(args)
        bot._audit_emit_signal = lambda *args, **kwargs: None

        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = bot
        runtime._base_daily_entry_count = lambda market: 40
        runtime._base_max_daily_entries = lambda market: 40

        runtime._record_blocked(
            "US",
            "AAPL",
            "decision-1",
            "MAX_DAILY_ENTRIES",
            {"gate": "daily_cap"},
            "path-run-1",
        )
        runtime._record_blocked(
            "US",
            "MSFT",
            "decision-2",
            "MARKET_CLOSED",
            {},
            "path-run-2",
        )

        self.assertEqual(len(lifecycle_events), 2)
        self.assertEqual(len(alerts), 1)
        market, reason, scope, payload = alerts[0]
        self.assertEqual(market, "US")
        self.assertEqual(reason, "MAX_DAILY_ENTRIES")
        self.assertEqual(scope, "market")
        self.assertEqual(payload["ticker"], "AAPL")
        self.assertEqual(payload["path_run_id"], "path-run-1")
        self.assertEqual(payload["decision_id"], "decision-1")
        self.assertEqual(payload["daily_count"], 40)
        self.assertEqual(payload["max_daily_entries"], 40)

    def test_pathb_broker_truth_provider_passes_market_to_bot_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))

            token = runtime.broker_truth._token("US")

        self.assertEqual(token, "token-US-0")
        self.assertIn(("US", False), bot.token_calls)

    def test_daily_pnl_uses_realized_return_and_keeps_equity_metric_separate(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = _PnlBot()

        self.assertEqual(runtime._daily_pnl_pct("US"), -0.25)
        self.assertEqual(runtime._equity_daily_pnl_pct("US"), -3.5)

    def test_pathb_sell_in_flight_detects_pending_sell_evidence(self) -> None:
        self.assertTrue(PathBRuntime._pathb_sell_in_flight({"status": "SELL_SENT"}, {}))
        self.assertTrue(
            PathBRuntime._pathb_sell_in_flight(
                {"status": "FILLED", "plan": {"exit_execution_id": "sell-1"}},
                {},
            )
        )
        self.assertTrue(PathBRuntime._pathb_sell_in_flight({"status": "FILLED"}, {"pathb_closing": "2026-05-06T23:00:00+09:00"}))
        self.assertTrue(PathBRuntime._pathb_sell_in_flight({"status": "FILLED"}, {"pathb_pending_sell_order_no": "sell-2"}))
        self.assertFalse(PathBRuntime._pathb_sell_in_flight({"status": "FILLED"}, {}))

    def test_pathb_small_stop_can_be_ticker_only_for_stop_cluster(self) -> None:
        env = {
            "STOP_CLUSTER_PATHB_TICKER_ONLY_ENABLED": "true",
            "STOP_CLUSTER_PATHB_TICKER_ONLY_MAX_COST_KRW": "250000",
            "STOP_CLUSTER_PATHB_TICKER_ONLY_MAX_LOSS_PCT": "2.5",
        }
        with patch.dict("os.environ", env, clear=False):
            self.assertTrue(PathBRuntime._pathb_stop_ticker_only({"qty": 1, "entry": 160000, "pnl_pct": -1.6}, "US"))
            self.assertFalse(PathBRuntime._pathb_stop_ticker_only({"qty": 2, "entry": 160000, "pnl_pct": -1.6}, "US"))
            self.assertFalse(PathBRuntime._pathb_stop_ticker_only({"qty": 1, "entry": 160000, "pnl_pct": -3.0}, "US"))

    def test_pathb_mfe_breakeven_signal_after_peak(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = _Bot()
        plan = make_price_plan(
            decision_id="dec_mfe",
            ticker="005930",
            market="KR",
            session_date="2026-05-07",
            buy_zone_low=98,
            buy_zone_high=101,
            sell_target=110,
            stop_loss=95,
            hold_days=1,
            confidence=0.7,
        )
        pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 2.6}

        with patch.dict(
            "os.environ",
            {"PATHB_MFE_BREAKEVEN_ENABLED": "true", "PATHB_MFE_BREAKEVEN_TRIGGER_PCT": "2.5"},
            clear=False,
        ):
            signal = runtime._pathb_mfe_breakeven_signal(plan, pos, 100.05, hard_stop_price=95.0)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.reason, "mfe_breakeven")
        self.assertEqual(signal.close_reason, "CLOSED_MFE_BREAKEVEN")

    def test_pathb_mfe_breakeven_does_not_override_hard_stop_breach(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = _Bot()
        plan = make_price_plan(
            decision_id="dec_mfe_hard",
            ticker="005930",
            market="KR",
            session_date="2026-05-07",
            buy_zone_low=98,
            buy_zone_high=101,
            sell_target=110,
            stop_loss=95,
            hold_days=1,
            confidence=0.7,
        )
        pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 2.6}

        signal = runtime._pathb_mfe_breakeven_signal(plan, pos, 94.0, hard_stop_price=95.0)

        self.assertIsNone(signal)

    def test_pathb_mfe_breakeven_does_not_override_loss_cap_breach(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        runtime.bot = _Bot()
        plan = make_price_plan(
            decision_id="dec_mfe_loss_cap",
            ticker="005930",
            market="KR",
            session_date="2026-05-07",
            buy_zone_low=98,
            buy_zone_high=101,
            sell_target=110,
            stop_loss=95,
            hold_days=1,
            confidence=0.7,
        )
        pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 2.6}

        signal = runtime._pathb_mfe_breakeven_signal(
            plan,
            pos,
            98.9,
            hard_stop_price=95.0,
            loss_cap_price=99.0,
        )

        self.assertIsNone(signal)

    def test_balance_snapshot_uses_market_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))

            with patch("runtime.pathb_runtime.get_balance", return_value={"cash": 0, "stocks": []}) as get_balance:
                runtime._balance_for_snapshot("US", True)

        get_balance.assert_called_once_with("token-US-0", market="US", force_refresh=True)

    def test_pathb_buy_entry_uses_market_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=store,
                config=V2Config(pathb_fixed_order_krw=500_000, us_min_order_krw=100_000),
            )
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_us_buy",
                ticker="AAPL",
                market="US",
                session_date="2026-05-01",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")

            with patch("runtime.pathb_runtime.precheck_order", return_value={"ok": True}) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "us-buy-1"},
            ) as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=180.5, limit_price=180.5, path_run_id=plan.path_run_id),
                )

        self.assertTrue(accepted)
        self.assertEqual(precheck.call_args.args[4], "token-US-0")
        self.assertEqual(precheck.call_args.kwargs["market"], "US")
        self.assertEqual(place.call_args.args[4], "token-US-0")
        self.assertEqual(place.call_args.kwargs["market"], "US")

    def test_pathb_sell_exit_uses_market_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_us_sell",
                ticker="AAPL",
                market="US",
                session_date="2026-05-01",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=180,
                qty=2,
                execution_id="us-buy-1",
                runtime_mode="live",
                brain_snapshot_id="brain-us",
            )
            pos = {
                "ticker": "AAPL",
                "market": "US",
                "qty": 2,
                "entry": 180.0,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
            }

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "us-sell-1"},
            ) as place:
                accepted = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id),
                )

        self.assertTrue(accepted)
        self.assertEqual(precheck.call_args.args[4], "token-US-0")
        self.assertEqual(precheck.call_args.kwargs["market"], "US")
        self.assertEqual(place.call_args.args[4], "token-US-0")
        self.assertEqual(place.call_args.kwargs["market"], "US")

    def test_pathb_sell_attempt_lock_blocks_duplicate_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_us_sell_lock",
                ticker="AAPL",
                market="US",
                session_date="2026-05-01",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=180,
                qty=2,
                execution_id="us-buy-1",
                runtime_mode="live",
                brain_snapshot_id="brain-us",
            )
            pos = {
                "ticker": "AAPL",
                "market": "US",
                "qty": 2,
                "entry": 180.0,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
            }
            signal = ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": True, "order_no": "us-sell-1"},
            ):
                first = runtime._submit_sell(plan, pos, signal)
                second = runtime._submit_sell(plan, pos, signal)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(precheck.call_count, 1)

    def test_pathb_sell_zero_holding_precheck_triggers_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_sell_pending = Mock(return_value={"checked": 1})
            plan = make_price_plan(
                decision_id="dec_us_sell_reconcile",
                ticker="AAPL",
                market="US",
                session_date="2026-05-01",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=180,
                qty=2,
                execution_id="us-buy-1",
                runtime_mode="live",
                brain_snapshot_id="brain-us",
            )
            pos = {
                "ticker": "AAPL",
                "market": "US",
                "qty": 2,
                "entry": 180.0,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
            }

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order",
                return_value={"ok": False, "reason": "insufficient_holding", "allowed_qty": 0, "msg": "no shares"},
            ), patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_sell(
                    plan,
                    pos,
                    ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id),
                )

        self.assertFalse(accepted)
        runtime.reconcile_sell_pending.assert_called_once_with("US", force=True)
        place.assert_not_called()

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

    def test_register_from_selection_meta_preserves_pullback_wait_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runs = runtime.register_from_selection_meta(
                "KR",
                {
                    "trade_ready": [],
                    "_pathb_registration_scope": "candidate_actions_wait_only",
                    "_pathb_wait_tickers": ["005930"],
                    "v2_decision_ids": {"005930": "dec_wait"},
                    "_pathb_wait_origins": {
                        "005930": {
                            "origin_action": "PULLBACK_WAIT",
                            "origin_route": "pathb_wait_only",
                            "registration_scope": "candidate_actions_wait_only",
                            "not_patha_trade_ready": True,
                            "reason": "wait for buy zone",
                        }
                    },
                    "_pathb_price_targets": {
                        "005930": {
                            "buy_zone_low": 52000,
                            "buy_zone_high": 52500,
                            "sell_target": 54500,
                            "stop_loss": 51000,
                            "hold_days": 1,
                            "confidence": 0.7,
                        }
                    },
                },
            )

            self.assertEqual(len(runs), 1)
            run = store.find_path_run(runs[0])
            plan = run["plan"]
            self.assertEqual(plan["origin_action"], "PULLBACK_WAIT")
            self.assertEqual(plan["origin_route"], "pathb_wait_only")
            self.assertEqual(plan["registration_scope"], "candidate_actions_wait_only")
            self.assertTrue(plan["not_patha_trade_ready"])
            self.assertEqual(plan["origin_reason"], "wait for buy zone")

    def test_pathb_order_and_position_metadata_include_origin(self) -> None:
        plan = make_price_plan(
            decision_id="dec_wait",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            buy_zone_low=52_000,
            buy_zone_high=52_500,
            sell_target=54_500,
            stop_loss=51_000,
            hold_days=1,
            confidence=0.7,
            origin_action="PULLBACK_WAIT",
            origin_route="pathb_wait_only",
            registration_scope="candidate_actions_wait_only",
            not_patha_trade_ready=True,
            origin_reason="wait for buy zone",
        )
        order: dict = {}
        pos: dict = {}

        PathBRuntime._attach_pathb_order_metadata(order, plan)
        PathBRuntime._attach_pathb_position_metadata(pos, plan)

        self.assertEqual(order["pathb_origin_action"], "PULLBACK_WAIT")
        self.assertEqual(order["pathb_origin_route"], "pathb_wait_only")
        self.assertTrue(order["not_patha_trade_ready"])
        self.assertEqual(pos["pathb_origin_action"], "PULLBACK_WAIT")
        self.assertTrue(pos["not_patha_trade_ready"])

    def test_register_from_selection_meta_keeps_plan_when_active_order_unknown_exists(self) -> None:
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

            self.assertEqual(len(created), 1)
            self.assertEqual(
                len(
                    store.path_runs_for_session(
                        market="KR",
                        runtime_mode="live",
                        session_date="2026-04-27",
                        path_type="claude_price",
                    )
                ),
                2,
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

    def test_scan_waiting_entries_records_kr_claude_price_new_entry_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime._record_blocked = Mock()
            runtime._submit_buy = Mock()
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
            bot.price_cache_raw["005930"] = 52_100

            with patch.dict("os.environ", {"KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK": "true"}, clear=False):
                runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_not_called()
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK")
            self.assertEqual(runtime._record_blocked.call_args.args[5], plan.path_run_id)

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

    def test_recover_on_startup_promotes_acked_run_with_local_position_to_filled(self) -> None:
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
            runtime.adapter.mark_order_acked(
                plan.path_run_id,
                execution_id="ord1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )
            bot.risk.positions.append({"ticker": "005930", "qty": 2, "entry": 52_200})

            summary = runtime.recover_on_startup()
            run = store.find_path_run(plan.path_run_id)

            self.assertEqual(summary["recovered_positions"], 1)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["entry_pending_resolution"], "local_pathb_holding_recovered")
            self.assertEqual(run["plan"]["filled_qty"], 2)
            self.assertEqual(run["plan"]["actual_entry_price"], 52_200)
            self.assertEqual(bot.risk.positions[0]["pathb_path_run_id"], plan.path_run_id)

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
            self.assertEqual(run["plan"]["cancel_reason"], "SAME_DAY_REENTRY_AFTER_STOP")

    def test_submit_buy_kr_order_time_gate_blocks_before_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            lifecycle_events: list[tuple[tuple, dict]] = []
            bot._v2_record_lifecycle_event = lambda *args, **kwargs: lifecycle_events.append((args, kwargs))
            bot._kr_late_entry_order_time_gate = lambda *args, **kwargs: {
                "enabled": True,
                "allowed": False,
                "reason": "kr_stale_chase_order_time_block",
                "final_action": "WATCH",
            }
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_kr_submit_gate",
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
            self.assertEqual(run["plan"]["cancel_reason"], "kr_stale_chase_order_time_block")
            self.assertEqual(lifecycle_events[0][0][0], "SAFETY_BLOCKED")
            self.assertEqual(lifecycle_events[0][1]["reason_code"], "kr_stale_chase_order_time_block")

    def test_submit_buy_us_preopen_market_closed_blocks_before_precheck(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            bot.session_active = False
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=store,
                config=V2Config(pathb_fixed_order_krw=500_000, us_min_order_krw=100_000),
            )
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_us_preopen",
                ticker="AAPL",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")

            with patch("runtime.pathb_runtime.precheck_order") as precheck, patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=180.5, limit_price=180.5, path_run_id=plan.path_run_id),
                )

            run = store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            precheck.assert_not_called()
            place.assert_not_called()
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual(run["plan"]["cancel_reason"], "MARKET_CLOSED")

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

    def test_previous_session_local_pathb_holding_is_included_in_exit_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_old",
                ticker="005930",
                market="KR",
                session_date="2026-04-26",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["005930"] = 121
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.close_reason, "CLOSED_CLAUDE_PRICE_TARGET")

    def test_scan_exits_prioritizes_loss_cap_over_mfe_breakeven(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_mfe_loss_cap_scan",
                ticker="005930",
                market="KR",
                session_date="2026-04-26",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=95,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=2, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "sl": 95,
                    "peak_pnl_pct": 2.6,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.risk.loss_cap_price = Mock(return_value=99.0)
            bot.price_cache_raw["005930"] = 98.9
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            with patch.dict(
                "os.environ",
                {"PATHB_MFE_BREAKEVEN_ENABLED": "true", "PATHB_MFE_BREAKEVEN_TRIGGER_PCT": "2.5"},
                clear=False,
            ):
                runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "loss_cap")
            self.assertEqual(signal.close_reason, "CLOSED_LOSS_CAP")

    def test_submit_buy_uses_combined_daily_entry_cap_from_v2(self) -> None:
        class _CappedV2(_V2):
            def daily_entry_count(self, market: str) -> int:
                return 1

            def max_daily_entries(self, market: str | None = None) -> int | None:
                return 1

        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.v2 = _CappedV2()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_daily_cap",
                ticker="005930",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=95,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            signal = EntrySignal(True, "buy_zone_hit", price=100, limit_price=100, path_run_id=plan.path_run_id)

            ok = runtime._submit_buy(plan, signal)

            self.assertFalse(ok)
            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertEqual((run.get("plan") or {}).get("cancel_reason"), "MAX_DAILY_ENTRIES")
            self.assertEqual(bot.pending_orders, [])

    def test_order_unknown_local_pathb_holding_recovers_to_filled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_unknown",
                ticker="005930",
                market="KR",
                session_date="2026-04-26",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=120,
                stop_loss=90,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_unknown(
                plan.path_run_id,
                detail="startup_recovery_missing_filled_position",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                    "v2_execution_id": "buy1",
                }
            )
            bot.price_cache_raw["005930"] = 110
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "local_pathb_holding_recovered")
            self.assertEqual(run["plan"]["filled_qty"], 2)
            runtime._submit_sell.assert_not_called()

    def test_finalize_pathb_sell_close_records_closed_decision_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_close",
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
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=100,
                qty=2,
                execution_id="buy1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                    "pathb_reference_target": 120,
                    "strategy": "claude_price",
                    "source_strategy": "claude_price",
                }
            )

            runtime._finalize_pathb_sell_close(
                plan,
                price=95,
                qty=2,
                execution_id="sell1",
                close_reason="CLOSED_LOSS_CAP",
                evidence={"broker_fill_event_id": 1},
            )

            self.assertEqual(len(bot.decision_events), 1)
            event = bot.decision_events[0]
            self.assertEqual(event["action"], "sell_filled")
            self.assertEqual(event["ticker"], "005930")
            self.assertEqual(event["order_no"], "sell1")
            self.assertEqual(event["reason"], "loss_cap")
            self.assertEqual(event["qty"], 2)
            self.assertEqual(event["pnl_krw"], -10)
            self.assertTrue(event["broker_fill_confirmed"])
            self.assertEqual(event["broker_fill_source"], "pathb_broker_truth")

    def test_on_buy_fill_records_common_entry_decision_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_buy",
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

            runtime.on_buy_fill(
                {
                    "pathb_path_run_id": plan.path_run_id,
                    "market": "KR",
                    "ticker": "005930",
                    "qty": 2,
                    "filled_price_native": 100,
                    "order_no": "buy1",
                }
            )

            self.assertEqual(len(bot.decision_events), 1)
            event = bot.decision_events[0]
            self.assertEqual(event["action"], "buy_order")
            self.assertEqual(event["path_type"], "claude_price")
            self.assertEqual(event["pathb_path_run_id"], plan.path_run_id)
            self.assertEqual(event["v2_decision_id"], "dec_buy")
            self.assertEqual(event["order_no"], "buy1")
            self.assertTrue(event["broker_fill_confirmed"])
            self.assertEqual(event["broker_fill_source"], "pathb_broker_truth")

    def test_finalize_pathb_sell_close_records_event_when_local_close_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_missing_close",
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
            runtime.adapter.mark_filled(
                plan.path_run_id,
                price=100,
                qty=2,
                execution_id="buy1",
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            runtime._finalize_pathb_sell_close(
                plan,
                price=95,
                qty=2,
                execution_id="sell_missing",
                close_reason="CLOSED_LOSS_CAP",
                evidence={"broker_fill_event_id": 2},
            )

            self.assertEqual(len(bot.decision_events), 1)
            event = bot.decision_events[0]
            self.assertEqual(event["action"], "sell_filled")
            self.assertEqual(event["ticker"], "005930")
            self.assertEqual(event["path_type"], "claude_price")
            self.assertEqual(event["pathb_path_run_id"], plan.path_run_id)
            self.assertEqual(event["order_no"], "sell_missing")
            self.assertEqual(event["reason"], "loss_cap")
            self.assertEqual(event["pnl_krw"], -10)
            self.assertAlmostEqual(event["pnl_pct"], -5.0)
            self.assertTrue(event["broker_fill_confirmed"])

    def test_stale_pathb_closing_is_cleared_for_still_held_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_stale",
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
            old = (datetime.now(KST) - timedelta(minutes=30)).isoformat(timespec="seconds")
            pos = {
                "ticker": "005930",
                "qty": 2,
                "entry": 100,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
                "pathb_closing": old,
                "pathb_pending_sell_order_no": "old_sell",
                "pending_next_open_sell": True,
            }
            bot.risk.positions.append(pos)
            bot.price_cache_raw["005930"] = 121
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            self.assertNotIn("pathb_closing", pos)
            self.assertNotIn("pathb_pending_sell_order_no", pos)
            self.assertTrue(pos["pending_next_open_sell"])
            self.assertTrue(bot.saved_positions)
            runtime._submit_sell.assert_called_once()

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
            self.assertEqual(advisor_pos["current_price"], 101.25 * 1350)
            self.assertEqual(advisor_pos["display_current_price"], 101.25)
            self.assertEqual(advisor_pos["display_avg_price"], 100.0)
            self.assertEqual(decision["decision"], "CARRY")

    def test_pre_close_carry_review_failure_defaults_to_carry(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = PathBRuntime(_Bot(), is_paper=False, store=EventStore(Path(tmp) / "events.db"))
            plan = make_price_plan(
                decision_id="dec_fail",
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
            pos = {"ticker": "005930", "qty": 1, "entry": 100, "current_price": 110}

            with patch("minority_report.hold_advisor.ask", side_effect=RuntimeError("timeout")):
                decision = runtime._run_pre_close_carry_review(plan, pos, current=110, minutes_to_close=10.5)

            self.assertEqual(decision["decision"], "CARRY")
            self.assertIn("hold_advisor_failed", decision["reason"])

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

    def test_pre_close_force_exit_requires_cached_sell_decision(self) -> None:
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

            self.assertFalse(runtime._pre_close_force_exit(plan.path_run_id, 10.0))
            store.update_path_run(plan.path_run_id, plan={"carry_decision": "SELL"}, merge_plan=True)
            self.assertTrue(runtime._pre_close_force_exit(plan.path_run_id, 10.0))
            store.update_path_run(plan.path_run_id, plan={"carry_decision": "CARRY"}, merge_plan=True)
            self.assertFalse(runtime._pre_close_force_exit(plan.path_run_id, 10.0))


if __name__ == "__main__":
    unittest.main()
