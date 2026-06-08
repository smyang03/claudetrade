from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import kis_api
from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import EntrySignal
from execution.safety_gate import validate_reason_code
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


def _us_filled_sell_runtime(tmp: str, *, ccld_provider):
    bot = _MarketTokenBot()
    bot.current_market = "US"
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    runtime.control_store = _Control()
    runtime.broker_truth = BrokerTruthSnapshot(
        runtime_mode="live",
        path=Path(tmp) / "broker_truth.json",
        token_provider=lambda market="US": "token",
        balance_provider=lambda market, force: {
            "cash": 1_000,
            "stocks": [{"ticker": "AAPL", "qty": 2, "avg_price": 180.0, "current_price": 190.0}],
        },
        ccld_provider=ccld_provider,
        date_provider=lambda market: "2026-05-01",
    )
    plan = make_price_plan(
        decision_id="dec_us_sell_qty_reject",
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
        "display_avg_price": 180.0,
        "path_type": "claude_price",
        "pathb_path_run_id": plan.path_run_id,
    }
    bot.risk.positions.append(pos)
    return runtime, bot, plan, pos


def _install_passing_entry_broker_truth(runtime: PathBRuntime, tmp: str) -> None:
    if hasattr(runtime.bot, "token"):
        runtime.bot.token = "token"
    runtime.broker_truth = BrokerTruthSnapshot(
        runtime_mode="live",
        path=Path(tmp) / "broker_truth.json",
        token_provider=lambda market="KR": "token",
        balance_provider=lambda market, force: {"cash": 1_000_000, "stocks": []},
        ccld_provider=lambda market, day: [],
        date_provider=lambda market: "2026-04-27",
    )


class _RuntimeConfig:
    def __init__(self, values: dict[str, object]) -> None:
        self.values = dict(values)

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, None)
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class PathBRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._pathb_env = patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "true"})
        self._pathb_env.start()

    def tearDown(self) -> None:
        self._pathb_env.stop()

    def _register_us_waiting_plan(
        self,
        runtime: PathBRuntime,
        ticker: str,
        *,
        confidence: float = 0.7,
    ) -> str:
        plan = make_price_plan(
            decision_id=f"dec_us_{ticker}",
            ticker=ticker,
            market="US",
            session_date="2026-04-27",
            buy_zone_low=100.0,
            buy_zone_high=110.0,
            sell_target=118.0,
            stop_loss=95.0,
            hold_days=1,
            confidence=confidence,
        )
        runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")
        return plan.path_run_id

    def _register_us_waiting_zone_plan(
        self,
        runtime: PathBRuntime,
        ticker: str = "AAPL",
        *,
        status: str = "WAITING",
    ) -> str:
        decision_id = f"dec_us_zone_{ticker}"
        runtime.store.create_decision(
            decision_id=decision_id,
            market="US",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker=ticker,
            prompt_version="pathb_price_v1.0",
            brain_snapshot_id="brain-us",
            status="CLAUDE_TRADE_READY",
        )
        plan = make_price_plan(
            decision_id=decision_id,
            ticker=ticker,
            market="US",
            session_date="2026-04-27",
            buy_zone_low=100.0,
            buy_zone_high=105.0,
            sell_target=140.0,
            stop_loss=95.0,
            hold_days=1,
            confidence=0.8,
            cancel_if_open_above=110.0,
        )
        runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")
        if status != "WAITING":
            runtime.store.update_path_run(plan.path_run_id, status=status)
        return plan.path_run_id

    def test_bot_token_helper_uses_market_token_when_available(self) -> None:
        bot = _MarketTokenBot()

        token = _bot_token(bot, "US", force_refresh=True)

        self.assertEqual(token, "token-US-1")
        self.assertEqual(bot.token_calls, [("US", True)])

    def test_bot_token_helper_falls_back_to_legacy_token(self) -> None:
        bot = _Bot()
        bot.token = "legacy-only"

        self.assertEqual(_bot_token(bot, "US"), "legacy-only")

    def test_pathb_detects_sellable_qty_reject_message(self) -> None:
        self.assertTrue(PathBRuntime._is_sellable_qty_reject("주문수량이 가능수량보다 큽니다"))
        self.assertTrue(PathBRuntime._is_sellable_qty_reject("insufficient sellable quantity"))
        self.assertFalse(PathBRuntime._is_sellable_qty_reject("temporary network error"))

    def test_pathb_sell_observation_required_uses_observation_flag_not_stale_metadata(self) -> None:
        self.assertTrue(
            PathBRuntime._pathb_sell_observation_required(
                {"plan": {"stale_exit_order_unconfirmed": True, "sellable_qty_observation_required": True}},
                {"stale_exit_order_unconfirmed": True},
            )
        )
        self.assertFalse(
            PathBRuntime._pathb_sell_observation_required(
                {"plan": {"stale_exit_order_unconfirmed": True, "sellable_qty_observation_required": False}},
                {"stale_exit_order_unconfirmed": True},
            )
        )

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
            bot.risk.positions.append(pos)

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

    def test_pathb_sellable_qty_reject_relinks_existing_broker_sell_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, bot, plan, pos = _us_filled_sell_runtime(
                tmp,
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "AAPL",
                        "side": "sell",
                        "order_no": "broker-sell-1",
                        "order_qty": 2,
                        "filled_qty": 0,
                        "remaining_qty": 2,
                        "avg_price": 0,
                    }
                ],
            )
            signal = ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": False, "msg": "주문수량이 가능수량보다 큽니다"},
            ) as place:
                accepted = runtime._submit_sell(plan, pos, signal)
                second = runtime._submit_sell(plan, pos, signal)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            self.assertFalse(second)
            self.assertEqual(precheck.call_count, 1)
            self.assertEqual(place.call_count, 1)
            self.assertEqual(run["status"], "SELL_ACKED")
            self.assertEqual(run["plan"]["exit_execution_id"], "broker-sell-1")
            self.assertEqual(run["plan"]["sellable_qty_reject_resolution"], "existing_open_sell_order_recovered")
            self.assertEqual(pos["pathb_pending_sell_order_no"], "broker-sell-1")
            self.assertEqual(pos["pathb_sell_state"], "broker_open_order_recovered_after_qty_reject")
            self.assertNotIn("sellable_qty_untrusted", pos)
            self.assertTrue(bot.saved_positions)

    def test_pathb_sellable_qty_reject_quarantines_when_no_broker_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, bot, plan, pos = _us_filled_sell_runtime(tmp, ccld_provider=lambda market, day: [])
            bot._log_risk_event = Mock()
            signal = ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}) as advisor, patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ) as precheck, patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": False, "msg": "주문수량이 가능수량보다 큽니다"},
            ) as place:
                accepted = runtime._submit_sell(plan, pos, signal)
                blocked = runtime._submit_sell(plan, pos, signal)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            self.assertFalse(blocked)
            self.assertEqual(advisor.call_count, 1)
            self.assertEqual(precheck.call_count, 1)
            self.assertEqual(place.call_count, 1)
            self.assertTrue(pos["sellable_qty_untrusted"])
            self.assertTrue(pos["manual_reconcile_required"])
            self.assertTrue(pos["broker_sell_lock_suspected"])
            self.assertEqual(pos["pathb_sell_state"], "sellable_qty_reject_no_open_order")
            self.assertEqual(run["plan"]["sellable_qty_reject_resolution"], "no_open_order_or_fill")
            self.assertTrue(run["plan"]["manual_reconciliation_required"])
            bot._log_risk_event.assert_called()
            self.assertEqual(bot._log_risk_event.call_args.args[0], "PATHB_SELLABLE_QTY_REJECT_UNRESOLVED")

    def test_pathb_sellable_qty_reject_partial_fill_keeps_original_exit_qty_for_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, plan, pos = _us_filled_sell_runtime(
                tmp,
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "AAPL",
                        "side": "sell",
                        "order_no": "broker-sell-partial",
                        "order_qty": 2,
                        "filled_qty": 1,
                        "remaining_qty": 1,
                        "avg_price": 190.0,
                    }
                ],
            )
            signal = ExitSignal(True, "claude_sell_target", "CLOSED_CLAUDE_PRICE_TARGET", 190.0, plan.path_run_id)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.9}), patch(
                "runtime.pathb_runtime.precheck_order", return_value={"ok": True}
            ), patch(
                "runtime.pathb_runtime.place_order",
                return_value={"success": False, "msg": "주문수량이 가능수량보다 큽니다"},
            ):
                accepted = runtime._submit_sell(plan, pos, signal)
                summary = runtime.reconcile_sell_pending("US", force=True)

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            self.assertEqual(run["status"], "SELL_PARTIAL_FILLED")
            self.assertEqual(run["plan"]["exit_qty"], 2)
            self.assertEqual(run["plan"]["exit_execution_id"], "broker-sell-partial")
            self.assertEqual(pos["qty"], 1)
            self.assertEqual(pos["pathb_pending_sell_order_no"], "broker-sell-partial")
            self.assertEqual(summary["partial"], 1)
            self.assertEqual(len(runtime.bot.risk.positions), 1)

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
            bot.risk.positions.append(pos)
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
            bot.risk.positions.append(pos)

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

    def test_pathb_sell_zero_holding_fresh_broker_truth_closes_stale_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="US": "token",
                balance_provider=lambda market, force: {"cash": 1_000, "stocks": []},
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-05-01",
            )
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
            runtime.adapter.mark_filled(plan.path_run_id, price=180, qty=2, execution_id="us-buy-1", runtime_mode="live", brain_snapshot_id="brain-us")
            pos = {
                "ticker": "AAPL",
                "market": "US",
                "qty": 2,
                "entry": 180.0,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
            }
            bot.risk.positions.append(pos)

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
            place.assert_not_called()
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "CLOSED")
            self.assertTrue(run["plan"]["broker_sync_reconciled"])
            self.assertTrue(run["plan"]["strategy_pnl_excluded"])
            self.assertEqual(bot.risk.positions, [])

    def test_pathb_sell_zero_holding_stale_broker_truth_keeps_local_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="US": "token",
                balance_provider=lambda market, force: (_ for _ in ()).throw(RuntimeError("stale")),
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-05-01",
            )
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
            runtime.adapter.mark_filled(plan.path_run_id, price=180, qty=2, execution_id="us-buy-1", runtime_mode="live", brain_snapshot_id="brain-us")
            pos = {
                "ticker": "AAPL",
                "market": "US",
                "qty": 2,
                "entry": 180.0,
                "path_type": "claude_price",
                "pathb_path_run_id": plan.path_run_id,
            }
            bot.risk.positions.append(pos)

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
            place.assert_not_called()
            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(len(bot.risk.positions), 1)

    def test_profit_review_timeout_records_hold_fallback_and_debounces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_profit_timeout",
                ticker="005930",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=100,
                buy_zone_high=101,
                sell_target=110,
                stop_loss=95,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_filled(plan.path_run_id, price=100, qty=1, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain1")
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
            pos = {"ticker": "005930", "qty": 1, "entry": 100.0, "peak_pnl_pct": 3.0}

            def slow_advisor(*args, **kwargs):
                time.sleep(0.05)
                return {"action": "HOLD"}

            env = {
                "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0.01",
                "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "0",
                "PATHB_PROFIT_REVIEW_MAX_PER_SCAN": "5",
                "PATHB_PROFIT_REVIEW_TIMEOUT_DEBOUNCE_SEC": "900",
                "PATHB_PROFIT_REVIEW_TIMEOUT_MAX_PER_TICKER": "1",
            }
            with patch.dict("os.environ", env, clear=False), patch("minority_report.hold_advisor.ask", side_effect=slow_advisor):
                first = runtime._maybe_trigger_profit_protection_review(plan, pos, 104.0, "KR")
                second = runtime._maybe_trigger_profit_protection_review(plan, pos, 104.0, "KR")

            run = runtime.store.find_path_run(plan.path_run_id)
            self.assertEqual(first["reason"], "timeout")
            self.assertIn(second["reason"], {"timeout_in_flight", "timeout_debounce"})
            self.assertEqual(run["plan"]["profit_review_action"], "HOLD")
            self.assertTrue(run["plan"]["profit_review_fallback"])
            self.assertTrue(run["plan"]["advisor_unavailable"])
            self.assertTrue(run["plan"]["learning_excluded"])

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

    def test_register_from_selection_meta_updates_active_waiting_zone_from_fresh_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot.price_cache_raw["AAPL"] = 112.0
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            path_run_id = self._register_us_waiting_zone_plan(runtime, "AAPL")

            env = {
                "PATHB_US_LIVE_ENABLED": "true",
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": "true",
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": "enforce",
                "US_PATHB_CANCEL_ABOVE_ZONE_MULTIPLIER": "1.05",
            }
            meta = {
                "_selection_source_type": "rescreen",
                "selection_snapshot_ts": "2026-04-27T10:00:00+09:00",
                "selection_call_id": "sel-zone-1",
                "trade_ready": ["AAPL"],
                "v2_decision_ids": {"AAPL": "dec_us_zone_new"},
                "price_targets": {
                    "AAPL": {
                        "buy_zone_low": 111.0,
                        "buy_zone_high": 113.0,
                        "sell_target": 160.0,
                        "stop_loss": 100.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    }
                },
            }
            with patch.dict("os.environ", env, clear=False):
                runs = runtime.register_from_selection_meta("US", meta)

            self.assertEqual(runs, [])
            run = store.find_path_run(path_run_id)
            self.assertEqual(run["status"], "WAITING")
            self.assertEqual(run["plan"]["buy_zone_low"], 111.0)
            self.assertEqual(run["plan"]["buy_zone_high"], 113.0)
            self.assertAlmostEqual(run["plan"]["cancel_if_open_above"], 118.65)
            self.assertEqual(run["plan"]["sell_target"], 140.0)
            self.assertEqual(run["plan"]["stop_loss"], 95.0)
            events = store.events_for_decision("dec_us_zone_AAPL")
            self.assertIn("PATHB_ZONE_UPDATED", [event["event_type"] for event in events])
            decision = store.find_decision(
                market="US",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="AAPL",
            )
            self.assertEqual(decision["status"], "CLAUDE_PRICE_WAITING")

    def test_register_from_selection_meta_does_not_update_hit_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot.price_cache_raw["AAPL"] = 112.0
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            path_run_id = self._register_us_waiting_zone_plan(runtime, "AAPL", status="HIT")
            meta = {
                "_selection_source_type": "rescreen",
                "trade_ready": ["AAPL"],
                "v2_decision_ids": {"AAPL": "dec_us_zone_new"},
                "price_targets": {
                    "AAPL": {
                        "buy_zone_low": 111.0,
                        "buy_zone_high": 113.0,
                        "sell_target": 160.0,
                        "stop_loss": 100.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    }
                },
            }
            env = {
                "PATHB_US_LIVE_ENABLED": "true",
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": "true",
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": "enforce",
            }
            with patch.dict("os.environ", env, clear=False):
                runs = runtime.register_from_selection_meta("US", meta)

            self.assertEqual(runs, [])
            run = store.find_path_run(path_run_id)
            self.assertEqual(run["status"], "HIT")
            self.assertEqual(run["plan"]["buy_zone_low"], 100.0)
            self.assertEqual(run["plan"]["buy_zone_high"], 105.0)

    def test_register_from_selection_meta_does_not_update_when_current_inside_old_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot.price_cache_raw["AAPL"] = 102.0
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            path_run_id = self._register_us_waiting_zone_plan(runtime, "AAPL")
            meta = {
                "_selection_source_type": "rescreen",
                "trade_ready": ["AAPL"],
                "v2_decision_ids": {"AAPL": "dec_us_zone_new"},
                "price_targets": {
                    "AAPL": {
                        "buy_zone_low": 101.0,
                        "buy_zone_high": 104.0,
                        "sell_target": 160.0,
                        "stop_loss": 98.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    }
                },
            }
            env = {
                "PATHB_US_LIVE_ENABLED": "true",
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": "true",
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": "enforce",
            }
            with patch.dict("os.environ", env, clear=False):
                runtime.register_from_selection_meta("US", meta)

            run = store.find_path_run(path_run_id)
            self.assertEqual(run["plan"]["buy_zone_low"], 100.0)
            self.assertEqual(run["plan"]["buy_zone_high"], 105.0)

    def test_register_from_selection_meta_does_not_update_invalid_merged_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot.price_cache_raw["AAPL"] = 132.0
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            path_run_id = self._register_us_waiting_zone_plan(runtime, "AAPL")
            meta = {
                "_selection_source_type": "rescreen",
                "trade_ready": ["AAPL"],
                "v2_decision_ids": {"AAPL": "dec_us_zone_new"},
                "price_targets": {
                    "AAPL": {
                        "buy_zone_low": 130.0,
                        "buy_zone_high": 135.0,
                        "sell_target": 160.0,
                        "stop_loss": 125.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    }
                },
            }
            env = {
                "PATHB_US_LIVE_ENABLED": "true",
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": "true",
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": "enforce",
            }
            with patch.dict("os.environ", env, clear=False):
                runtime.register_from_selection_meta("US", meta)

            run = store.find_path_run(path_run_id)
            self.assertEqual(run["plan"]["buy_zone_low"], 100.0)
            self.assertEqual(run["plan"]["buy_zone_high"], 105.0)

    def test_register_from_selection_meta_does_not_update_zone_on_smart_skip_reuse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            bot = _Bot()
            bot.price_cache_raw["AAPL"] = 112.0
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            path_run_id = self._register_us_waiting_zone_plan(runtime, "AAPL")
            meta = {
                "_selection_source_type": "rescreen",
                "_smart_skip_reused": True,
                "trade_ready": ["AAPL"],
                "v2_decision_ids": {"AAPL": "dec_us_zone_new"},
                "price_targets": {
                    "AAPL": {
                        "buy_zone_low": 111.0,
                        "buy_zone_high": 113.0,
                        "sell_target": 160.0,
                        "stop_loss": 100.0,
                        "hold_days": 1,
                        "confidence": 0.8,
                    }
                },
            }
            env = {
                "PATHB_US_LIVE_ENABLED": "true",
                "PATHB_SELECTION_RECONCILE_ZONE_UPDATE_ENABLED": "true",
                "US_PATHB_SELECTION_RECONCILE_ZONE_UPDATE_MODE": "enforce",
            }
            with patch.dict("os.environ", env, clear=False):
                runtime.register_from_selection_meta("US", meta)

            run = store.find_path_run(path_run_id)
            self.assertEqual(run["plan"]["buy_zone_low"], 100.0)
            self.assertEqual(run["plan"]["buy_zone_high"], 105.0)

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

    def test_kr_live_disabled_can_register_shadow_price_plan(self) -> None:
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
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 1)
            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_WAITING")
            self.assertTrue(run["plan"]["shadow_only"])
            self.assertFalse(run["plan"]["live_order_enabled"])
            self.assertEqual(runtime.adapter.get_waiting_runs("KR", "live", "2026-04-27"), [])
            events = store.events_for_decision("dec1")
            self.assertEqual(
                [event["event_type"] for event in events],
                ["CLAUDE_PRICE_PLAN_CREATED", "CLAUDE_PRICE_WAITING"],
            )

    def test_kr_shadow_plan_preserves_demotion_metadata(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_shadow_meta"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "origin_reason": "kr_data_quality_not_confirmed",
                    "demoted_from": "BUY_READY",
                    "demotion_reason": "kr_data_quality_not_confirmed",
                    "microstructure_data_quality": "DATA_MISSING",
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 1)
            plan = store.find_path_run(runs[0])["plan"]
            self.assertTrue(plan["shadow_only"])
            self.assertFalse(plan["live_order_enabled"])
            self.assertFalse(plan["execution_allowed"])
            self.assertEqual(plan["origin_action"], "BUY_READY")
            self.assertEqual(plan["origin_route"], "pathb_shadow_only")
            self.assertEqual(plan["registration_scope"], "candidate_actions_shadow_only")
            self.assertEqual(plan["origin_reason"], "kr_data_quality_not_confirmed")
            self.assertEqual(plan["demoted_from"], "BUY_READY")
            self.assertEqual(plan["demotion_reason"], "kr_data_quality_not_confirmed")
            self.assertEqual(plan["microstructure_data_quality"], "DATA_MISSING")
            self.assertEqual(plan["pathb_shadow_reason"], "kr_data_quality_not_confirmed")

    def test_kr_live_shadow_candidate_registers_shadow_run_without_v2_trade_ready(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "origin_reason": "kr_data_quality_not_confirmed",
                    "demoted_from": "BUY_READY",
                    "demotion_reason": "kr_data_quality_not_confirmed",
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 1)
            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_WAITING")
            self.assertEqual(run["decision_id"], "shadow:live:KR:2026-04-27:005930")
            self.assertTrue(run["plan"]["shadow_only"])
            self.assertFalse(run["plan"]["live_order_enabled"])
            self.assertFalse(run["plan"]["execution_allowed"])
            self.assertEqual(run["plan"]["shadow_reason"], "kr_data_quality_not_confirmed")
            self.assertEqual(run["plan"]["pathb_shadow_reason"], "kr_data_quality_not_confirmed")
            self.assertEqual(runtime.adapter.get_waiting_runs("KR", "live", "2026-04-27"), [])

    def test_kr_live_shadow_candidate_does_not_reuse_or_create_v2_trade_ready_id(self) -> None:
        class _ShadowBot(_Bot):
            def _v2_decision_id_for_ticker(self, market: str, ticker: str) -> str:
                return f"dec_existing_{market}_{ticker}"

            def _v2_ensure_execution_decision_id(self, *args, **kwargs) -> str:
                raise AssertionError("shadow registration must not create V2 trade_ready ids")

        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_existing_meta"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "origin_reason": "kr_data_quality_not_confirmed",
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_ShadowBot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 1)
            run = store.find_path_run(runs[0])
            self.assertEqual(run["decision_id"], "shadow:live:KR:2026-04-27:005930")
            self.assertEqual(store.events_for_decision("dec_existing_meta"), [])
            self.assertEqual(store.events_for_decision("dec_existing_KR_005930"), [])

    def test_kr_live_shadow_candidate_skips_when_live_run_exists_for_same_ticker(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_shadow_same_ticker"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            live_plan = make_price_plan(
                decision_id="dec_live_same_ticker",
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
            live_run_id = runtime.adapter.register_plan(live_plan, runtime_mode="live", brain_snapshot_id="brain1")

            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(runs, [])
            live_run = store.find_path_run(live_run_id)
            self.assertEqual(live_run["ticker"], "005930")
            self.assertEqual(live_run["status"], "WAITING")
            self.assertFalse(live_run["plan"].get("shadow_only", False))
            all_runs = store.path_runs_for_session(market="KR", runtime_mode="live", session_date="2026-04-27")
            self.assertEqual([run["status"] for run in all_runs], ["WAITING"])

    def test_kr_live_shadow_candidate_registers_after_live_run_cancelled_for_same_ticker(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_shadow_after_cancelled_live"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            live_plan = make_price_plan(
                decision_id="dec_cancelled_live_same_ticker",
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
            live_run_id = runtime.adapter.register_plan(live_plan, runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(live_run_id, status="CANCELLED")

            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 1)
            cancelled_live_run = store.find_path_run(live_run_id)
            shadow_run = store.find_path_run(runs[0])
            self.assertEqual(cancelled_live_run["status"], "CANCELLED")
            self.assertEqual(shadow_run["ticker"], "005930")
            self.assertEqual(shadow_run["status"], "SHADOW_WAITING")
            self.assertTrue(shadow_run["plan"]["shadow_only"])
            all_runs = store.path_runs_for_session(market="KR", runtime_mode="live", session_date="2026-04-27")
            self.assertEqual(sorted(run["status"] for run in all_runs), ["CANCELLED", "SHADOW_WAITING"])

    def test_kr_live_and_shadow_candidates_register_as_separate_modes(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {
                "000660": "dec_live_wait",
                "005930": "dec_shadow_wait",
            },
            "_pathb_registration_scope": "candidate_actions_wait_only",
            "_pathb_wait_tickers": ["000660"],
            "_pathb_price_targets": {
                "000660": {
                    "buy_zone_low": 151000,
                    "buy_zone_high": 152000,
                    "sell_target": 158000,
                    "stop_loss": 149000,
                    "hold_days": 1,
                    "confidence": 0.72,
                    "entry_rationale": "support pullback",
                    "exit_rationale": "resistance target",
                    "rationale": "support pullback",
                }
            },
            "_pathb_wait_origins": {
                "000660": {
                    "origin_action": "PULLBACK_WAIT",
                    "origin_route": "pathb_wait_only",
                    "registration_scope": "candidate_actions_wait_only",
                    "not_patha_trade_ready": True,
                    "reason": "pullback plan",
                }
            },
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
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
            "_pathb_shadow_origins": {
                "005930": {
                    "origin_action": "BUY_READY",
                    "origin_route": "pathb_shadow_only",
                    "registration_scope": "candidate_actions_shadow_only",
                    "not_patha_trade_ready": True,
                    "pathb_shadow_reason": "kr_data_quality_not_confirmed",
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)

            self.assertEqual(len(runs), 2)
            by_ticker = {
                run["ticker"]: run
                for run in store.path_runs_for_session(
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    path_type="claude_price",
                )
            }
            self.assertEqual(by_ticker["000660"]["status"], "WAITING")
            self.assertFalse(by_ticker["000660"]["plan"].get("shadow_only", False))
            self.assertEqual(by_ticker["005930"]["status"], "SHADOW_WAITING")
            self.assertTrue(by_ticker["005930"]["plan"]["shadow_only"])
            self.assertFalse(by_ticker["005930"]["plan"]["live_order_enabled"])

    def test_kr_live_candidate_with_existing_shadow_path_is_explicitly_resolved(self) -> None:
        shadow_meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_shadow_existing"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
                "005930": {
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "sell_target": 54500,
                    "stop_loss": 51000,
                    "hold_days": 1,
                    "confidence": 0.7,
                }
            },
        }
        live_meta = {
            "trade_ready": ["005930"],
            "v2_decision_ids": {"005930": "dec_live_supersedes"},
            "price_targets": {
                "005930": {
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "sell_target": 54500,
                    "stop_loss": 51000,
                    "hold_days": 1,
                    "confidence": 0.7,
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                shadow_runs = runtime.register_from_selection_meta("KR", shadow_meta)
                live_runs = runtime.register_from_selection_meta("KR", live_meta)

            self.assertEqual(len(shadow_runs), 1)
            self.assertEqual(len(live_runs), 1)
            shadow_run = store.find_path_run(shadow_runs[0])
            live_run = store.find_path_run(live_runs[0])
            self.assertEqual(shadow_run["status"], "SHADOW_CANCELLED")
            self.assertEqual(shadow_run["plan"]["shadow_cancel_reason"], "live_candidate_supersedes_shadow")
            self.assertEqual(live_run["status"], "WAITING")
            self.assertFalse(live_run["plan"].get("shadow_only", False))

    def test_kr_shadow_waiting_scan_marks_hit_without_order(self) -> None:
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
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._audit_pathb_price_seen = Mock()
            runtime._current_native_price = Mock(return_value=52300)
            runtime._submit_buy = Mock()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_HIT")
            self.assertEqual(run["plan"]["shadow_hit_price"], 52300)
            runtime._submit_buy.assert_not_called()
            events = store.events_for_decision("dec1")
            self.assertIn("CLAUDE_PRICE_HIT", [event["event_type"] for event in events])

    def test_kr_live_enabled_shadow_waiting_scan_marks_hit_without_order(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_live_shadow_scan"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
                "005930": {
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "sell_target": 54500,
                    "stop_loss": 51000,
                    "hold_days": 1,
                    "confidence": 0.7,
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._audit_pathb_price_seen = Mock()
            runtime._current_native_price = Mock(return_value=52300)
            runtime._submit_buy = Mock()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_HIT")
            self.assertEqual(run["plan"]["shadow_hit_price"], 52300)
            runtime._submit_buy.assert_not_called()

    def test_kr_live_enabled_shadow_scan_runs_even_when_entry_gate_blocks_live(self) -> None:
        meta = {
            "trade_ready": [],
            "v2_decision_ids": {"005930": "dec_live_shadow_blocked_gate"},
            "_pathb_shadow_tickers": ["005930"],
            "_pathb_shadow_price_targets": {
                "005930": {
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "sell_target": 54500,
                    "stop_loss": 51000,
                    "hold_days": 1,
                    "confidence": 0.7,
                }
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._audit_pathb_price_seen = Mock()
            runtime._current_native_price = Mock(return_value=52300)
            runtime._submit_buy = Mock()
            runtime._audit_entry_scan_blocked = Mock()
            runtime._log_entry_scan_blocked = Mock()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "true", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)
                runtime._new_buy_block_state = Mock(
                    return_value={"allowed": False, "reason": "ORDER_UNKNOWN_UNRESOLVED", "scope": "market"}
                )
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_HIT")
            runtime._audit_entry_scan_blocked.assert_called_once()
            runtime._log_entry_scan_blocked.assert_called_once()
            runtime._submit_buy.assert_not_called()

    def test_shadow_only_scan_cancels_unsent_live_runs_before_shadow_scan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock(return_value={})
            runtime.reconcile_buy_pending_cancel_above = Mock(return_value={})
            runtime.process_miss_quality_followups = Mock()
            runtime._scan_shadow_waiting_entries = Mock(return_value=0)

            def register(ticker: str, status: str, *, shadow: bool = False) -> str:
                plan = make_price_plan(
                    decision_id=f"dec_{ticker}_{status}",
                    ticker=ticker,
                    market="KR",
                    session_date="2026-04-27",
                    buy_zone_low=52_000,
                    buy_zone_high=52_500,
                    sell_target=54_500,
                    stop_loss=51_000,
                    hold_days=1,
                    confidence=0.7,
                )
                runtime.adapter.register_plan(
                    plan,
                    runtime_mode="live",
                    brain_snapshot_id="brain1",
                    initial_status=status,
                    plan_overrides={"shadow_only": True} if shadow else None,
                )
                return plan.path_run_id

            unsent_live_ids = [
                register("005930", "WAITING"),
                register("000660", "HIT"),
            ]
            broker_submitted_ids = [
                (register("035420", "ORDER_SENT"), "ORDER_SENT"),
                (register("051910", "ORDER_ACKED"), "ORDER_ACKED"),
            ]
            order_unknown_id = register("096770", "ORDER_UNKNOWN")
            shadow_waiting_id = register("068270", "SHADOW_WAITING", shadow=True)
            shadow_hit_id = register("323410", "SHADOW_HIT", shadow=True)

            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runtime.scan_waiting_entries("KR", force=True)

            for path_run_id in unsent_live_ids:
                run = store.find_path_run(path_run_id)
                self.assertEqual(run["status"], "CANCELLED")
                self.assertEqual(run["plan"]["cancel_reason"], "PATHB_MANUALLY_DISABLED")
            for path_run_id, expected_status in broker_submitted_ids:
                run = store.find_path_run(path_run_id)
                self.assertEqual(run["status"], expected_status)
                self.assertNotIn("cancel_reason", run["plan"])
            self.assertEqual(store.find_path_run(order_unknown_id)["status"], "ORDER_UNKNOWN")
            self.assertEqual(store.find_path_run(shadow_waiting_id)["status"], "SHADOW_WAITING")
            self.assertEqual(store.find_path_run(shadow_hit_id)["status"], "SHADOW_HIT")
            runtime._scan_shadow_waiting_entries.assert_called_once_with("KR")

    def test_live_disabled_without_shadow_keeps_broker_submitted_live_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock(return_value={})
            runtime.reconcile_buy_pending_cancel_above = Mock(return_value={})
            runtime.process_miss_quality_followups = Mock()
            runtime._scan_shadow_waiting_entries = Mock(return_value=0)

            def register(ticker: str, status: str) -> str:
                plan = make_price_plan(
                    decision_id=f"dec_{ticker}_{status}",
                    ticker=ticker,
                    market="KR",
                    session_date="2026-04-27",
                    buy_zone_low=52_000,
                    buy_zone_high=52_500,
                    sell_target=54_500,
                    stop_loss=51_000,
                    hold_days=1,
                    confidence=0.7,
                )
                runtime.adapter.register_plan(
                    plan,
                    runtime_mode="live",
                    brain_snapshot_id="brain1",
                    initial_status=status,
                )
                return plan.path_run_id

            broker_submitted_ids = [
                (register("035420", "ORDER_SENT"), "ORDER_SENT"),
                (register("051910", "ORDER_ACKED"), "ORDER_ACKED"),
            ]

            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "false"},
            ):
                runtime.scan_waiting_entries("KR", force=True)

            for path_run_id, expected_status in broker_submitted_ids:
                run = store.find_path_run(path_run_id)
                self.assertEqual(run["status"], expected_status)
                self.assertNotIn("cancel_reason", run["plan"])
            runtime._scan_shadow_waiting_entries.assert_not_called()

    def test_live_disabled_without_shadow_cancels_shadow_waiting_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock(return_value={})
            runtime.reconcile_buy_pending_cancel_above = Mock(return_value={})
            runtime.process_miss_quality_followups = Mock()
            runtime._scan_shadow_waiting_entries = Mock(return_value=0)
            plan = make_price_plan(
                decision_id="dec_shadow_disabled_cancel",
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
            runtime.adapter.register_plan(
                plan,
                runtime_mode="live",
                brain_snapshot_id="brain1",
                initial_status="SHADOW_WAITING",
                plan_overrides={"shadow_only": True},
            )

            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "false"},
            ):
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "SHADOW_CANCELLED")
            self.assertEqual(run["plan"]["shadow_cancel_reason"], "PATHB_MANUALLY_DISABLED")
            runtime._scan_shadow_waiting_entries.assert_not_called()

    def test_shadow_hit_adapter_does_not_convert_live_waiting_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_live_waiting",
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

            marked = runtime.adapter.mark_shadow_hit(
                plan.path_run_id,
                price=52_100,
                runtime_mode="live",
                brain_snapshot_id="brain1",
            )

            self.assertFalse(marked)
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "WAITING")
            events = store.events_for_decision("dec_live_waiting")
            self.assertNotIn("CLAUDE_PRICE_HIT", [event["event_type"] for event in events])

    def test_scan_waiting_entries_caps_burst_when_analyst_size_is_low(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lifecycle_events: list[tuple[tuple, dict]] = []
            bot = _Bot()
            bot.today_judgment = {"consensus": {"mode": "NEUTRAL", "size": 28}}
            bot._v2_record_lifecycle_event = lambda *args, **kwargs: lifecycle_events.append((args, kwargs))
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._entry_scan_broker_truth_gate = Mock(return_value={"allowed": True})
            runtime._current_native_price = Mock(return_value=105.0)
            runtime._submit_buy = Mock(return_value=True)

            blocked_ids = [
                self._register_us_waiting_plan(runtime, "AMZN"),
                self._register_us_waiting_plan(runtime, "GOOGL"),
                self._register_us_waiting_plan(runtime, "TSLA"),
            ]

            with patch.dict(
                "os.environ",
                {
                    "PATHB_US_LIVE_ENABLED": "true",
                    "PATHB_SCAN_BURST_CAP_ANALYST_SIZE_PCT": "40%",
                },
            ):
                runtime.scan_waiting_entries("US", force=True)

            self.assertEqual(runtime._submit_buy.call_count, 1)
            submitted_ticker = runtime._submit_buy.call_args_list[0].args[0].ticker
            self.assertEqual(submitted_ticker, "AMZN")
            self.assertTrue(validate_reason_code("PATHB_SCAN_BURST_CAP"))
            self.assertEqual(len(lifecycle_events), 2)
            self.assertEqual({args[2] for args, _ in lifecycle_events}, {"GOOGL", "TSLA"})
            for args, kwargs in lifecycle_events:
                self.assertEqual(args[0], "SAFETY_BLOCKED")
                self.assertEqual(args[1], "US")
                self.assertEqual(kwargs["reason_code"], "PATHB_SCAN_BURST_CAP")
                self.assertEqual(kwargs["payload"]["candidate_priority"], "confidence_desc_then_existing_order")
            for path_run_id in blocked_ids[1:]:
                run = store.find_path_run(path_run_id)
                self.assertEqual(run["status"], "WAITING")
                self.assertEqual(run["plan"]["last_submit_block_reason"], "PATHB_SCAN_BURST_CAP")
                self.assertEqual(run["plan"]["submit_block_keep_reason"], "pathb_scan_burst_cap")
                gate = run["plan"]["last_submit_block_gate"]
                self.assertEqual(gate["max_submits_per_scan"], 1)
                self.assertEqual(gate["analyst_size_pct"], 28.0)
                self.assertIn("analyst_size_below_threshold", gate["trigger_reasons"])

    def test_scan_waiting_entries_prioritizes_confidence_when_burst_capped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.today_judgment = {"consensus": {"mode": "NEUTRAL", "size": 28}}
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._entry_scan_broker_truth_gate = Mock(return_value={"allowed": True})
            runtime._current_native_price = Mock(return_value=105.0)
            runtime._submit_buy = Mock(return_value=True)

            self._register_us_waiting_plan(runtime, "AMZN", confidence=0.55)
            high_conf_id = self._register_us_waiting_plan(runtime, "GOOGL", confidence=0.91)
            self._register_us_waiting_plan(runtime, "TSLA", confidence=0.72)

            with patch.dict("os.environ", {"PATHB_US_LIVE_ENABLED": "true"}):
                runtime.scan_waiting_entries("US", force=True)

            self.assertEqual(runtime._submit_buy.call_count, 1)
            submitted_plan = runtime._submit_buy.call_args_list[0].args[0]
            self.assertEqual(submitted_plan.ticker, "GOOGL")
            self.assertEqual(submitted_plan.path_run_id, high_conf_id)
            blocked_tickers = {
                run["ticker"]
                for run in store.path_runs_for_session(market="US", runtime_mode="live", status="WAITING")
                if (run.get("plan") or {}).get("last_submit_block_reason") == "PATHB_SCAN_BURST_CAP"
            }
            self.assertEqual(blocked_tickers, {"AMZN", "TSLA"})

    def test_scan_waiting_entries_does_not_cap_burst_when_analyst_size_is_normal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.today_judgment = {"consensus": {"mode": "NEUTRAL", "size": 45}}
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._entry_scan_broker_truth_gate = Mock(return_value={"allowed": True})
            runtime._current_native_price = Mock(return_value=105.0)
            runtime._submit_buy = Mock(return_value=True)

            for ticker in ("AMZN", "GOOGL", "TSLA"):
                self._register_us_waiting_plan(runtime, ticker)

            with patch.dict("os.environ", {"PATHB_US_LIVE_ENABLED": "true"}):
                runtime.scan_waiting_entries("US", force=True)

            self.assertEqual(runtime._submit_buy.call_count, 3)

    def test_scan_waiting_entries_caps_burst_when_market_mode_is_bearish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.today_judgment = {"consensus": {"mode": "MILD_BEAR", "size": 50}}
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._entry_scan_broker_truth_gate = Mock(return_value={"allowed": True})
            runtime._current_native_price = Mock(return_value=105.0)
            runtime._submit_buy = Mock(return_value=True)

            blocked_ids = [
                self._register_us_waiting_plan(runtime, "AMZN"),
                self._register_us_waiting_plan(runtime, "GOOGL"),
            ]

            with patch.dict("os.environ", {"PATHB_US_LIVE_ENABLED": "true"}):
                runtime.scan_waiting_entries("US", force=True)

            self.assertEqual(runtime._submit_buy.call_count, 1)
            run = store.find_path_run(blocked_ids[1])
            self.assertEqual(run["plan"]["last_submit_block_reason"], "PATHB_SCAN_BURST_CAP")
            self.assertIn("market_mode", run["plan"]["last_submit_block_gate"]["trigger_reasons"])

    def test_kr_shadow_scan_cancel_if_open_above_uses_shadow_cancel_status(self) -> None:
        meta = {
            "trade_ready": ["005930"],
            "v2_decision_ids": {"005930": "dec_shadow_cancel_above"},
            "price_targets": {
                "005930": {
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "sell_target": 54500,
                    "stop_loss": 51000,
                    "cancel_if_open_above": 53000,
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
            runtime.reconcile_order_unknowns = Mock()
            runtime.reconcile_buy_pending_cancel_above = Mock()
            runtime.process_miss_quality_followups = Mock()
            runtime._audit_pathb_price_seen = Mock()
            runtime._current_native_price = Mock(return_value=53_500)
            runtime._submit_buy = Mock()
            with patch.dict(
                "os.environ",
                {"PATHB_KR_LIVE_ENABLED": "false", "PATHB_KR_SHADOW_PLAN_ENABLED": "true"},
            ):
                runs = runtime.register_from_selection_meta("KR", meta)
                runtime.scan_waiting_entries("KR", force=True)

            run = store.find_path_run(runs[0])
            self.assertEqual(run["status"], "SHADOW_CANCELLED")
            self.assertEqual(run["plan"]["cancel_reason"], "shadow_cancel_if_open_above")
            self.assertEqual(run["plan"]["shadow_cancel_reason"], "shadow_cancel_if_open_above")
            self.assertEqual(run["plan"]["shadow_cancel_trigger_price"], 53_500)
            runtime._submit_buy.assert_not_called()

    def test_submit_buy_blocks_shadow_plan_even_when_market_live_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime._record_blocked = Mock()
            plan = make_price_plan(
                decision_id="dec_shadow_submit",
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
            runtime.adapter.register_plan(
                plan,
                runtime_mode="live",
                brain_snapshot_id="brain1",
                initial_status="SHADOW_WAITING",
                plan_overrides={"shadow_only": True, "live_order_enabled": False},
            )

            with patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "true"}), patch(
                "runtime.pathb_runtime.precheck_order"
            ) as precheck, patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=52_100, limit_price=52_100, path_run_id=plan.path_run_id),
                )

            self.assertFalse(accepted)
            precheck.assert_not_called()
            place.assert_not_called()
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "PATHB_SHADOW_ONLY")
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "SHADOW_WAITING")

    def test_submit_buy_blocks_when_plan_truth_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime._record_blocked = Mock()
            plan = make_price_plan(
                decision_id="dec_plan_truth_missing",
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
            runtime.store.find_path_run = Mock(side_effect=RuntimeError("db locked"))

            with patch.dict("os.environ", {"PATHB_KR_LIVE_ENABLED": "true"}), patch(
                "runtime.pathb_runtime.precheck_order"
            ) as precheck, patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=52_100, limit_price=52_100, path_run_id=plan.path_run_id),
                )

            self.assertFalse(accepted)
            precheck.assert_not_called()
            place.assert_not_called()
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "PATHB_SHADOW_ONLY")

    def test_cancel_waiting_and_session_close_cleanup_shadow_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(_Bot(), is_paper=False, store=store)
            runtime.control_store = _Control()
            first = make_price_plan(
                decision_id="dec_shadow_cancel",
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
            second = make_price_plan(
                decision_id="dec_shadow_expire",
                ticker="000660",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=100_000,
                buy_zone_high=101_000,
                sell_target=104_000,
                stop_loss=99_000,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(
                first,
                runtime_mode="live",
                brain_snapshot_id="brain1",
                initial_status="SHADOW_WAITING",
                plan_overrides={"shadow_only": True},
            )
            runtime.adapter.register_plan(
                second,
                runtime_mode="live",
                brain_snapshot_id="brain1",
                initial_status="SHADOW_HIT",
                plan_overrides={"shadow_only": True},
            )

            self.assertEqual(runtime.cancel_waiting_for_ticker("KR", "005930", reason="operator_cancel"), 1)
            self.assertEqual(store.find_path_run(first.path_run_id)["status"], "SHADOW_CANCELLED")
            self.assertEqual(runtime.expire_waiting_at_session_close("KR"), 1)
            second_run = store.find_path_run(second.path_run_id)
            self.assertEqual(second_run["status"], "SHADOW_CANCELLED")
            self.assertEqual(second_run["plan"]["shadow_cancel_reason"], "SESSION_CLOSE_EXPIRED")

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

    def test_register_from_selection_meta_skips_structurally_unaffordable_us_pathb_plan(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.usd_krw_rate = 1400
            bot.risk.cash = 5_000_000
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=EventStore(Path(tmp) / "events.db"),
                config=V2Config(
                    pathb_fixed_order_krw=450_000,
                    us_min_order_krw=50_000,
                    pathb_allow_one_share_over_budget=True,
                    pathb_one_share_over_budget_max_krw=700_000,
                    pathb_one_share_over_budget_max_account_pct=30.0,
                ),
            )
            runtime.control_store = _Control()
            runtime._record_blocked = Mock()

            runs = runtime.register_from_selection_meta(
                "US",
                {
                    "trade_ready": ["WDC"],
                    "v2_decision_ids": {"WDC": "dec_wdc"},
                    "price_targets": {
                        "WDC": {
                            "buy_zone_low": 533.0,
                            "buy_zone_high": 535.0,
                            "sell_target": 560.0,
                            "stop_loss": 520.0,
                            "hold_days": 1,
                            "confidence": 0.7,
                        }
                    },
                },
            )

            self.assertEqual(runs, [])
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "HIGH_PRICE_BUDGET_BLOCK")
            payload = runtime._record_blocked.call_args.args[4]
            self.assertEqual(payload["stage"], "pathb_plan_registration")
            self.assertTrue(payload["skip_plan_registration"])

    def test_register_from_selection_meta_keeps_us_pathb_plan_inside_one_share_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.usd_krw_rate = 1400
            bot.risk.cash = 5_000_000
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=EventStore(Path(tmp) / "events.db"),
                config=V2Config(
                    pathb_fixed_order_krw=450_000,
                    us_min_order_krw=50_000,
                    pathb_allow_one_share_over_budget=True,
                    pathb_one_share_over_budget_max_krw=700_000,
                    pathb_one_share_over_budget_max_account_pct=30.0,
                ),
            )
            runtime.control_store = _Control()

            runs = runtime.register_from_selection_meta(
                "US",
                {
                    "trade_ready": ["AMD"],
                    "v2_decision_ids": {"AMD": "dec_amd"},
                    "price_targets": {
                        "AMD": {
                            "buy_zone_low": 492.0,
                            "buy_zone_high": 494.0,
                            "sell_target": 520.0,
                            "stop_loss": 480.0,
                            "hold_days": 1,
                            "confidence": 0.7,
                        }
                    },
                },
            )

            self.assertEqual(len(runs), 1)

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
            _install_passing_entry_broker_truth(runtime, tmp)
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
            _install_passing_entry_broker_truth(runtime, tmp)
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

    def test_scan_waiting_entries_uses_runtime_config_for_kr_entry_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.runtime_config = _RuntimeConfig({"KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK": "true"})
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
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

            with patch.dict("os.environ", {"KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK": "false"}, clear=False):
                runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_not_called()
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK")
            self.assertEqual(runtime._record_blocked.call_args.args[5], plan.path_run_id)
            payload = runtime._record_blocked.call_args.args[4]
            self.assertEqual(payload["config_key"], "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK")
            self.assertEqual(payload["config_value"], "true")

    def test_scan_waiting_entries_falls_back_to_env_when_runtime_config_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.runtime_config = _RuntimeConfig({})
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
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
            payload = runtime._record_blocked.call_args.args[4]
            self.assertEqual(payload["config_value"], "true")

    def test_scan_waiting_entries_blocks_kr_risky_origin_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
            runtime._record_blocked = Mock()
            runtime._submit_buy = Mock()
            plan = make_price_plan(
                decision_id="dec1",
                ticker="069540",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=6_700,
                buy_zone_high=7_200,
                sell_target=7_600,
                stop_loss=6_500,
                hold_days=1,
                confidence=0.7,
                origin_reason="OR_MISSING_ATR_BLOCKED",
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["069540"] = 6_780

            runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_not_called()
            runtime._record_blocked.assert_called_once()
            self.assertEqual(runtime._record_blocked.call_args.args[3], "KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED")
            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "WAITING")
            self.assertEqual(run["plan"]["last_submit_block_reason"], "KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED")

    def test_scan_waiting_entries_allows_kr_risky_origin_with_minute_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot._last_post_open_features_by_ticker = {
                "KR": {
                    "069540": {
                        "data_quality": "minute_complete",
                        "current_price": 6_780,
                        "ret_3m_pct": 0.8,
                        "ret_5m_pct": 1.2,
                        "opening_range_break": True,
                    }
                }
            }
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
            runtime._record_blocked = Mock()
            runtime._submit_buy = Mock(return_value=True)
            plan = make_price_plan(
                decision_id="dec1",
                ticker="069540",
                market="KR",
                session_date="2026-04-27",
                buy_zone_low=6_700,
                buy_zone_high=7_200,
                sell_target=7_600,
                stop_loss=6_500,
                hold_days=1,
                confidence=0.7,
                origin_reason="OR_MISSING_ATR_BLOCKED",
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["069540"] = 6_780

            runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_called_once()
            runtime._record_blocked.assert_not_called()

    def test_scan_waiting_entries_does_not_treat_late_mover_low_atr_as_risky_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
            runtime._record_blocked = Mock()
            runtime._submit_buy = Mock(return_value=True)
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
                origin_reason="LATE_MOVER_LOW_ATR",
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["005930"] = 52_100

            runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_called_once()
            runtime._record_blocked.assert_not_called()

    def test_scan_waiting_entries_skips_risky_origin_gate_for_us(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            _install_passing_entry_broker_truth(runtime, tmp)
            runtime._record_blocked = Mock()
            runtime._submit_buy = Mock(return_value=True)
            plan = make_price_plan(
                decision_id="dec1",
                ticker="AAPL",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=180,
                buy_zone_high=181,
                sell_target=190,
                stop_loss=175,
                hold_days=1,
                confidence=0.7,
                origin_reason="OR_MISSING_ATR_BLOCKED",
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            bot.price_cache_raw["AAPL"] = 180.5

            runtime.scan_waiting_entries("US", force=True)

            runtime._submit_buy.assert_called_once()
            runtime._record_blocked.assert_not_called()

    def test_entry_scan_broker_truth_gate_blocks_when_token_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.token = ""
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))

            gate = runtime._entry_scan_broker_truth_gate("KR")

            self.assertFalse(gate["allowed"])
            self.assertTrue(gate["blocked"])
            self.assertEqual(gate["reason"], "BLOCKED_BROKER_TRUTH")
            self.assertEqual(gate["scope"], "market")
            self.assertEqual(gate["details"]["broker_truth_skip_reason"], "token_unavailable")
            self.assertTrue(gate["details"]["broker_truth_stale"])

    def test_entry_scan_broker_truth_gate_blocks_when_balance_provider_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            runtime = PathBRuntime(bot, is_paper=False, store=EventStore(Path(tmp) / "events.db"))

            gate = runtime._entry_scan_broker_truth_gate("KR")

            self.assertFalse(gate["allowed"])
            self.assertTrue(gate["blocked"])
            self.assertEqual(gate["reason"], "BLOCKED_BROKER_TRUTH")
            self.assertEqual(gate["scope"], "market")
            self.assertEqual(gate["details"]["broker_truth_skip_reason"], "bot_balance_provider_unavailable")
            self.assertTrue(gate["details"]["broker_truth_stale"])

    def test_scan_waiting_entries_refreshes_broker_truth_before_buy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 1_000_000, "stocks": []},
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-04-27",
            )
            runtime._submit_buy = Mock(return_value=True)
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

            runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_called_once()
            snapshot = runtime.broker_truth.market_snapshot("KR", ttl_sec=30)
            self.assertFalse(snapshot["stale"])
            self.assertTrue(snapshot["last_success_at"])

    def test_scan_waiting_entries_blocks_when_broker_truth_refresh_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _MarketTokenBot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: (_ for _ in ()).throw(RuntimeError("broker down")),
                ccld_provider=lambda market, day: [],
                date_provider=lambda market: "2026-04-27",
            )
            runtime._submit_buy = Mock(return_value=True)
            runtime._audit_entry_scan_blocked = Mock()
            runtime._log_entry_scan_blocked = Mock()
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

            runtime.scan_waiting_entries("KR", force=True)

            runtime._submit_buy.assert_not_called()
            runtime._audit_entry_scan_blocked.assert_called_once()
            gate = runtime._audit_entry_scan_blocked.call_args.args[1]
            self.assertEqual(gate["reason"], "BLOCKED_BROKER_TRUTH")
            self.assertTrue(gate["details"]["broker_truth_stale"])

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
            fill_events = [
                event
                for event in store.events_for_decision(plan.decision_id)
                if event["event_type"] == "FILLED"
                and (event.get("payload") or {}).get("path_run_id") == plan.path_run_id
            ]
            self.assertEqual(len(fill_events), 1)
            self.assertTrue((fill_events[0].get("payload") or {}).get("recovered_fill"))
            self.assertEqual((fill_events[0].get("payload") or {}).get("qty"), 2)

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
                        "order_date": "20260427",
                        "fill_time": "100000",
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
            store.update_path_run(
                plan.path_run_id,
                plan={"filled_at": "2026-04-27T09:30:00+09:00"},
                merge_plan=True,
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

    def test_submit_buy_us_early_gate_keeps_structurally_affordable_pathb_waiting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            bot.usd_krw_rate = 1400
            bot.risk.cash = 5_000_000
            bot._us_early_entry_soft_gate = lambda market: {
                "active": True,
                "size_mult": 0.5,
                "elapsed_min": 30.0,
            }
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=store,
                config=V2Config(
                    pathb_fixed_order_krw=450_000,
                    us_min_order_krw=50_000,
                    pathb_allow_one_share_over_budget=True,
                    pathb_one_share_over_budget_max_krw=700_000,
                    pathb_one_share_over_budget_max_account_pct=30.0,
                ),
            )
            runtime.control_store = _Control()
            plan = make_price_plan(
                decision_id="dec_amd_early_gate",
                ticker="AMD",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=492.0,
                buy_zone_high=494.0,
                sell_target=520.0,
                stop_loss=480.0,
                hold_days=1,
                confidence=0.8,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain-us")

            with patch("runtime.pathb_runtime.precheck_order") as precheck, patch("runtime.pathb_runtime.place_order") as place:
                accepted = runtime._submit_buy(
                    plan,
                    EntrySignal(True, "buy_zone_hit", price=492.0, limit_price=492.0, path_run_id=plan.path_run_id),
                )

            run = store.find_path_run(plan.path_run_id)
            self.assertFalse(accepted)
            precheck.assert_not_called()
            place.assert_not_called()
            self.assertEqual(run["status"], "WAITING")
            self.assertEqual(run["plan"]["last_submit_block_reason"], "ORDER_SIZE_TOO_SMALL_GATE")
            self.assertTrue(run["plan"]["submit_block_keeps_waiting"])

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

    def test_order_acked_buy_pending_ttl_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            self.assertEqual(summary["skipped"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "ORDER_ACKED")

    def test_order_sent_and_acked_store_entry_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
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
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            sent_run = store.find_path_run(plan.path_run_id)
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            acked_run = store.find_path_run(plan.path_run_id)

            self.assertEqual(sent_run["plan"]["entry_execution_id"], "ord1")
            self.assertIn("entry_order_sent_at", sent_run["plan"])
            self.assertEqual(acked_run["plan"]["entry_execution_id"], "ord1")
            self.assertIn("entry_order_acked_at", acked_run["plan"])
            self.assertIsNotNone(PathBRuntime._seconds_since_iso(sent_run["plan"]["entry_order_sent_at"]))
            self.assertIsNotNone(PathBRuntime._seconds_since_iso(acked_run["plan"]["entry_order_acked_at"]))

    def test_order_acked_buy_pending_ttl_uses_ack_time_not_plan_created_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
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
            )
            old_iso = (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds")
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={"created_at": old_iso, "pending_buy_created_at": old_iso},
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "60"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            self.assertEqual(summary["skipped"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "ORDER_ACKED")

    def test_order_acked_buy_pending_ttl_skips_without_order_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
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
            )
            old_iso = (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds")
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                status="ORDER_ACKED",
                plan={
                    "entry_execution_id": "ord1",
                    "entry_order_price": 52_300,
                    "entry_qty": 2,
                    "created_at": old_iso,
                    "pending_buy_created_at": old_iso,
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            self.assertEqual(summary["skipped"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(store.find_path_run(plan.path_run_id)["status"], "ORDER_ACKED")

    def test_order_acked_buy_pending_ttl_requests_cancel_for_open_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)
                    second_summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["cancel_requested"], 1)
            self.assertEqual(second_summary["still_open"], 1)
            self.assertEqual(cancel_mock.call_count, 1)
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertTrue(run["plan"]["pending_buy_open_order_evidence"])
            self.assertIn("pending_buy_ttl_cancel_requested_at", run["plan"])
            self.assertIn("pending_buy_ttl_still_open_at", run["plan"])

    def test_order_acked_buy_pending_ttl_confirms_cancel_after_open_order_disappears(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            orders = [
                {
                    "ticker": "005930",
                    "side": "buy",
                    "order_no": "ord1",
                    "order_qty": 2,
                    "filled_qty": 0,
                    "remaining_qty": 2,
                    "avg_price": 52_300,
                }
            ]
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: list(orders),
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
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    runtime.reconcile_buy_pending_cancel_above("KR", force=True)
                    orders.clear()
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["cancel_confirmed"], 1)
            self.assertEqual(cancel_mock.call_count, 1)
            self.assertEqual(run["status"], "CANCELLED")
            self.assertTrue(run["plan"]["pending_buy_ttl_cancel_confirmed"])
            self.assertEqual(run["plan"]["cancel_reason"], "buy_pending_ttl_no_open_order")

    def test_order_acked_buy_pending_ttl_recovers_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {
                    "cash": 0,
                    "stocks": [
                        {"ticker": "005930", "qty": 2, "avg_price": 52_250, "current_price": 52_400}
                    ],
                },
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "ord1",
                        "order_qty": 2,
                        "filled_qty": 2,
                        "remaining_qty": 0,
                        "avg_price": 52_250,
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["filled"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["filled_qty"], 2)
            self.assertEqual(run["plan"]["actual_entry_price"], 52_250)
            self.assertTrue(bot.saved_positions)

    def test_order_acked_buy_pending_ttl_defers_when_broker_truth_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: (_ for _ in ()).throw(RuntimeError("broker down")),
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
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["still_open"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertEqual(run["plan"]["pending_buy_ttl_deferred_reason"], "broker_truth_unavailable")

    def test_order_acked_buy_pending_ttl_does_not_recover_mismatched_fill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {
                    "cash": 0,
                    "stocks": [
                        {"ticker": "005930", "qty": 2, "avg_price": 52_250, "current_price": 52_400}
                    ],
                },
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "other",
                        "order_qty": 2,
                        "filled_qty": 2,
                        "remaining_qty": 0,
                        "avg_price": 52_250,
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["order_unknown"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_detail"], "buy_pending_ttl_fill_execution_mismatch")
            self.assertEqual(run["plan"]["pending_buy_ttl_deferred_reason"], "buy_pending_ttl_fill_execution_mismatch")

    def test_order_acked_buy_pending_ttl_does_not_cancel_mismatched_open_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "other",
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["still_open"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertEqual(run["plan"]["pending_buy_ttl_deferred_reason"], "buy_pending_ttl_open_order_execution_mismatch")

    def test_order_acked_buy_pending_ttl_allows_unique_open_order_without_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "",
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["cancel_requested"], 1)
            cancel_mock.assert_called_once()
            self.assertEqual(cancel_mock.call_args.args[1], "ord1")
            self.assertEqual(run["status"], "ORDER_ACKED")
            self.assertTrue(run["plan"]["pending_buy_open_order_evidence"])

    def test_order_acked_buy_pending_ttl_marks_multiple_open_orders_without_order_no_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": []},
                ccld_provider=lambda market, day: [
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "",
                        "order_qty": 1,
                        "filled_qty": 0,
                        "remaining_qty": 1,
                        "avg_price": 52_300,
                    },
                    {
                        "ticker": "005930",
                        "side": "buy",
                        "order_no": "",
                        "order_qty": 2,
                        "filled_qty": 0,
                        "remaining_qty": 2,
                        "avg_price": 52_350,
                    },
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
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(summary["order_unknown"], 1)
            cancel_mock.assert_not_called()
            self.assertEqual(run["status"], "ORDER_UNKNOWN")
            self.assertEqual(run["plan"]["order_unknown_detail"], "buy_pending_ttl_ambiguous_open_order")

    def test_order_acked_buy_pending_ttl_recovers_fill_after_cancel_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            orders = [
                {
                    "ticker": "005930",
                    "side": "buy",
                    "order_no": "ord1",
                    "order_qty": 2,
                    "filled_qty": 0,
                    "remaining_qty": 2,
                    "avg_price": 52_300,
                }
            ]
            stocks: list[dict] = []
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            runtime.control_store = _Control()
            runtime.broker_truth = BrokerTruthSnapshot(
                runtime_mode="live",
                path=Path(tmp) / "broker_truth.json",
                token_provider=lambda market="KR": "token",
                balance_provider=lambda market, force: {"cash": 0, "stocks": list(stocks)},
                ccld_provider=lambda market, day: list(orders),
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
            runtime.adapter.mark_order_sent(plan.path_run_id, execution_id="ord1", price=52_300, qty=2, runtime_mode="live", brain_snapshot_id="brain1")
            runtime.adapter.mark_order_acked(plan.path_run_id, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "entry_order_sent_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                    "entry_order_acked_at": (datetime.now(KST) - timedelta(minutes=10)).isoformat(timespec="seconds"),
                },
                merge_plan=True,
            )

            cancel_mock = Mock(return_value={"success": True, "msg": "cancel accepted", "order_no": "cncl1"})
            with patch.dict("os.environ", {"PATHB_KR_BUY_PENDING_TTL_SEC": "1"}, clear=False):
                with patch("runtime.pathb_runtime.cancel_order", cancel_mock):
                    first_summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)
                    orders[:] = [
                        {
                            "ticker": "005930",
                            "side": "buy",
                            "order_no": "ord1",
                            "order_qty": 2,
                            "filled_qty": 2,
                            "remaining_qty": 0,
                            "avg_price": 52_250,
                        }
                    ]
                    stocks[:] = [{"ticker": "005930", "qty": 2, "avg_price": 52_250, "current_price": 52_400}]
                    second_summary = runtime.reconcile_buy_pending_cancel_above("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(first_summary["cancel_requested"], 1)
            self.assertEqual(second_summary["filled"], 1)
            self.assertEqual(cancel_mock.call_count, 1)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["filled_qty"], 2)
            self.assertEqual(run["plan"]["actual_entry_price"], 52_250)

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
                    "display_current_price": 121,
                    "current_price_source": "broker_balance",
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
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
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

    def test_scan_exits_uses_broker_balance_price_over_stale_cache_for_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            bot.usd_krw_rate = 1350
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_qcom_stale_cache",
                ticker="QCOM",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=244,
                buy_zone_high=255,
                sell_target=265,
                stop_loss=238,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain_us")
            runtime.adapter.mark_filled(plan.path_run_id, price=249.935, qty=1, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain_us")
            bot.risk.positions.append(
                {
                    "ticker": "QCOM",
                    "qty": 1,
                    "entry": 249.935 * bot.usd_krw_rate,
                    "sl": 237.54 * bot.usd_krw_rate,
                    "display_current_price": 231.87,
                    "current_price": 231.87 * bot.usd_krw_rate,
                    "current_price_source": "broker_balance",
                    "price_source": "order_fill",
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["QCOM"] = 249.25
            bot.price_cache["QCOM"] = 249.25 * bot.usd_krw_rate
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("US", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "hard_stop")
            self.assertEqual(signal.close_reason, "CLOSED_HARD_STOP")
            self.assertAlmostEqual(signal.price, 231.87)
            self.assertAlmostEqual(bot.price_cache_raw["QCOM"], 231.87)

    def test_scan_exits_uses_broker_truth_snapshot_over_stale_cache_for_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            bot.usd_krw_rate = 1350
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_qcom_broker_truth_price",
                ticker="QCOM",
                market="US",
                session_date="2026-04-27",
                buy_zone_low=244,
                buy_zone_high=255,
                sell_target=265,
                stop_loss=238,
                hold_days=1,
                confidence=0.7,
            )
            runtime.adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain_us")
            runtime.adapter.mark_filled(plan.path_run_id, price=249.935, qty=1, execution_id="buy1", runtime_mode="live", brain_snapshot_id="brain_us")
            bot.risk.positions.append(
                {
                    "ticker": "QCOM",
                    "qty": 1,
                    "entry": 249.935 * bot.usd_krw_rate,
                    "sl": 237.54 * bot.usd_krw_rate,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            runtime.broker_truth.market_snapshot = Mock(
                return_value={
                    "missing": False,
                    "stale": False,
                    "error": "",
                    "positions": [{"market": "US", "ticker": "QCOM", "qty": 1, "current_price": 231.87}],
                }
            )
            bot.price_cache_raw["QCOM"] = 249.25
            bot.price_cache["QCOM"] = 249.25 * bot.usd_krw_rate
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("US", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "hard_stop")
            self.assertEqual(signal.close_reason, "CLOSED_HARD_STOP")
            self.assertAlmostEqual(signal.price, 231.87)
            self.assertAlmostEqual(bot.price_cache_raw["QCOM"], 231.87)

    def test_pathb_stop_recovery_policy_hard_stop_preempts_native_hard_stop_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_policy_hard_stop_first",
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
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "stop_recovery",
                        "hard_stop": 94.0,
                        "recover_above": 101.0,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "sl": 95,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["005930"] = 93.9
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "policy_hard_stop")
            self.assertEqual(signal.close_reason, "CLOSED_HARD_STOP")

    def test_pathb_expired_policy_stop_breach_still_sells(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_expired_policy_stop",
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
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "stop_recovery",
                        "hard_stop": 94.0,
                        "recover_above": 101.0,
                        "valid_until": (datetime.now(KST) - timedelta(minutes=1)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "sl": 95,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["005930"] = 93.9
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "policy_hard_stop")

    def test_pathb_invalid_policy_falls_back_to_native_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_invalid_policy_native_stop",
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
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "stop_recovery",
                        "hard_stop": 0.0,
                        "recover_above": 101.0,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "sl": 95,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["005930"] = 94.5
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "hard_stop")

    def test_pathb_policy_skip_does_not_suppress_native_loss_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)
            plan = make_price_plan(
                decision_id="dec_policy_skip_loss_cap",
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
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "target_extension",
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "reask_after_at": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                        "revised_sell_target": 130.0,
                        "protective_stop": 90.0,
                        "peak_price": 121.0,
                    }
                },
                merge_plan=True,
            )
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "sl": 95,
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.risk.loss_cap_price = Mock(return_value=99.0)
            bot.price_cache_raw["005930"] = 98.9
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            with patch.dict(os.environ, {"PATHB_HOLD_POLICY_MODE": "enforce"}, clear=False):
                runtime.scan_exits("KR", force=True)

            runtime._submit_sell.assert_called_once()
            signal = runtime._submit_sell.call_args.args[2]
            self.assertEqual(signal.reason, "loss_cap")

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
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
            runtime._submit_sell = Mock()

            runtime.scan_exits("KR", force=True)

            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["status"], "FILLED")
            self.assertEqual(run["plan"]["order_unknown_resolution"], "local_pathb_holding_recovered")
            self.assertEqual(run["plan"]["filled_qty"], 2)
            fill_events = [
                event
                for event in store.events_for_decision(plan.decision_id)
                if event["event_type"] == "FILLED"
                and (event.get("payload") or {}).get("path_run_id") == plan.path_run_id
            ]
            self.assertEqual(len(fill_events), 1)
            self.assertEqual((fill_events[0].get("payload") or {}).get("recovered_fill_source"), "local_pathb_holding")
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
                "display_current_price": 121,
                "current_price_source": "broker_balance",
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
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "display_current_price": 121,
                    "current_price_source": "broker_balance",
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
            bot.price_cache_raw["005930"] = 110
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
            runtime.reconcile_sell_pending = Mock(return_value={})
            runtime.reconcile_filled_positions = Mock(return_value={})
            runtime._minutes_to_close = lambda market: 10.9  # type: ignore[method-assign]
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
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
            bot.risk.positions.append(
                {
                    "ticker": "005930",
                    "qty": 2,
                    "entry": 100,
                    "display_current_price": 121,
                    "current_price_source": "broker_balance",
                    "path_type": "claude_price",
                    "pathb_path_run_id": plan.path_run_id,
                }
            )
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
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: float(bot.price_cache_raw.get(ticker, 0))
            )
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


    # ──────────────────────────────────────────────────────────────
    # _fetch_exit_price / exit 전용 가격 경로 테스트
    # ──────────────────────────────────────────────────────────────

    def test_fetch_exit_price_skips_stale_price_cache_raw(self) -> None:
        """price_cache_raw에 stale 값이 있어도 exit 경로는 그것을 반환하지 않는다.
        broker position / broker truth 가 모두 0이면 _fetch_exit_price()로 떨어지며,
        TTL 만료 + get_price() 를 호출해 fresh 가격을 쓴다."""
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)

            # stale 값을 price_cache_raw 에 주입
            bot.price_cache_raw["005930"] = 999.0

            # broker_truth 는 포지션 없음 → 0.0
            runtime.broker_truth = Mock()
            runtime.broker_truth.market_snapshot.return_value = {"positions": []}

            fresh_price = 70000.0
            with patch("runtime.pathb_runtime.get_price", return_value={"price": fresh_price}) as mock_get:
                result = runtime._current_native_price_for_exit(
                    "KR", "005930", {"current_price_source": "order_fill"}
                )

            mock_get.assert_called_once()
            self.assertEqual(result, fresh_price)
            # price_cache_raw 도 fresh 값으로 갱신됐는지 확인
            self.assertEqual(bot.price_cache_raw.get("005930"), fresh_price)

    def test_fetch_exit_price_uses_ttl_cache_within_window(self) -> None:
        """TTL 이내에 _exit_price_cache 에 값이 있으면 get_price() 를 호출하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)

            cached_price = 68000.0
            runtime._exit_price_cache["005930"] = (cached_price, time.time())  # 방금 캐시됨

            runtime.broker_truth = Mock()
            runtime.broker_truth.market_snapshot.return_value = {"positions": []}

            with patch("runtime.pathb_runtime.get_price") as mock_get:
                result = runtime._current_native_price_for_exit(
                    "KR", "005930", {"current_price_source": "order_fill"}
                )

            mock_get.assert_not_called()
            self.assertEqual(result, cached_price)

    def test_fetch_exit_price_returns_zero_on_api_failure(self) -> None:
        """broker truth 없고 get_price() 가 예외를 던지면 0.0 반환.
        price_cache_raw stale 값으로 되돌아가지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)

            bot.price_cache_raw["005930"] = 999.0  # stale

            runtime.broker_truth = Mock()
            runtime.broker_truth.market_snapshot.return_value = {"positions": []}

            with patch("runtime.pathb_runtime.get_price", side_effect=Exception("API error")):
                result = runtime._current_native_price_for_exit(
                    "KR", "005930", {"current_price_source": "order_fill"}
                )

            self.assertEqual(result, 0.0)
            # price_cache_raw 는 stale 값 그대로여야 함 (덮어쓰기 금지)
            self.assertEqual(bot.price_cache_raw.get("005930"), 999.0)

    def test_fetch_exit_price_bypasses_kis_price_cache_fallback(self) -> None:
        """KIS fresh 호출 실패 시 kis_api._PRICE_CACHE 값을 fresh로 승격하지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(bot, is_paper=False, store=store)

            runtime.broker_truth = Mock()
            runtime.broker_truth.market_snapshot.return_value = {"positions": []}

            old_price_cache = dict(kis_api._PRICE_CACHE)
            kis_api._PRICE_CACHE.clear()
            kis_api._cache_set(
                kis_api._PRICE_CACHE,
                ("KR", "005930"),
                {
                    "ticker": "005930",
                    "name": "005930",
                    "price": 999.0,
                    "change": 0,
                    "change_rate": 0.0,
                    "volume": 0,
                    "open": 0,
                    "high": 999.0,
                    "low": 999.0,
                },
            )
            try:
                with (
                    patch("kis_api._retry_kis", side_effect=RuntimeError("kis down")),
                    patch("kis_api._get_price_kr_yf", return_value={"price": 777.0}) as yf_mock,
                ):
                    result = runtime._current_native_price_for_exit(
                        "KR", "005930", {"current_price_source": "order_fill"}
                    )
            finally:
                kis_api._PRICE_CACHE.clear()
                kis_api._PRICE_CACHE.update(old_price_cache)

            self.assertEqual(result, 0.0)
            yf_mock.assert_not_called()
            self.assertNotIn("005930", runtime._exit_price_cache)
            self.assertNotIn("005930", bot.price_cache_raw)


class EarlyGateFloorOneShareTests(unittest.TestCase):
    """early_gate_floor_one_share: early gate로 qty=0이 됐지만 full 예산으로 1주 가능한 경우 qty=1 보장."""

    def _make_runtime(self, tmp: str) -> "PathBRuntime":
        bot = _Bot()
        bot.current_market = "US"
        bot.usd_krw_rate = 1_380
        bot.risk.cash = 5_000_000
        bot._us_early_entry_soft_gate = lambda market: {
            "active": True,
            "size_mult": 0.5,
            "elapsed_min": 20.0,
        }
        store = EventStore(Path(tmp) / "events.db")
        return PathBRuntime(
            bot,
            is_paper=False,
            store=store,
            config=V2Config(
                pathb_fixed_order_krw=450_000,
                us_min_order_krw=50_000,
                pathb_allow_one_share_over_budget=True,
                pathb_one_share_over_budget_max_krw=700_000,
                pathb_one_share_over_budget_max_account_pct=30.0,
            ),
        )

    def test_early_gate_floor_gives_qty_one_when_reduced_budget_is_too_small(self) -> None:
        """early gate × 0.5 = 225,000 KRW인데 1주 가격 270,000 KRW → qty=0 → floor → qty=1."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._make_runtime(tmp)
            runtime.control_store = _Control()
            # 1주 가격: $195.65 × 1,380 = 270,000 KRW > 225,000 (축소 예산), <= 450,000 (full)
            price_krw = 270_000.0
            qty, ctx = runtime._pathb_qty_with_context("US", price_krw, cash_krw=5_000_000.0)
            self.assertEqual(qty, 1)
            self.assertEqual(ctx["sizing_reason"], "early_gate_floor_one_share")
            self.assertTrue(ctx["early_gate_floor_applied"])
            self.assertTrue(ctx["early_gate_applied"])
            self.assertTrue(ctx["can_buy_1_share"])

    def test_early_gate_floor_keeps_within_cap_high_price_waiting_for_retry(self) -> None:
        """1-share cap 안이지만 early gate shortfall이 크면 즉시 floor하지 않고 waiting 재평가로 남긴다."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._make_runtime(tmp)
            runtime.control_store = _Control()
            # 1주 가격: $340 × 1,380 = 469,200 KRW > 450,000 (full 예산도 초과)
            price_krw = 469_200.0
            qty, ctx = runtime._pathb_qty_with_context("US", price_krw, cash_krw=5_000_000.0)
            self.assertEqual(qty, 0)
            self.assertFalse(ctx.get("early_gate_floor_applied", False))
            self.assertTrue(ctx["can_buy_1_share"])
            self.assertEqual(ctx["original_budget_krw"], 700_000)

    def test_early_gate_floor_not_applied_when_one_share_cap_is_exceeded(self) -> None:
        """1주 가격이 one-share cap을 넘으면 high-price block으로 유지한다."""
        with tempfile.TemporaryDirectory() as tmp:
            runtime = self._make_runtime(tmp)
            runtime.control_store = _Control()
            price_krw = 725_000.0
            qty, ctx = runtime._pathb_qty_with_context("US", price_krw, cash_krw=5_000_000.0)
            self.assertEqual(qty, 0)
            self.assertFalse(ctx.get("early_gate_floor_applied", False))
            self.assertFalse(ctx["can_buy_1_share"])

    def test_early_gate_floor_not_applied_when_no_early_gate(self) -> None:
        """early gate 비활성 상태에서는 floor 적용 안 됨."""
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot()
            bot.current_market = "US"
            bot.usd_krw_rate = 1_380
            bot.risk.cash = 5_000_000
            bot._us_early_entry_soft_gate = lambda market: {"active": False}
            store = EventStore(Path(tmp) / "events.db")
            runtime = PathBRuntime(
                bot,
                is_paper=False,
                store=store,
                config=V2Config(pathb_fixed_order_krw=450_000, us_min_order_krw=50_000),
            )
            runtime.control_store = _Control()
            price_krw = 270_000.0
            qty, ctx = runtime._pathb_qty_with_context("US", price_krw, cash_krw=5_000_000.0)
            # early gate 없으니 full budget으로 계산: 450,000 / 270,000 = 1주
            self.assertEqual(qty, 1)
            self.assertFalse(ctx.get("early_gate_floor_applied", False))
            self.assertFalse(ctx["early_gate_applied"])


if __name__ == "__main__":
    unittest.main()
