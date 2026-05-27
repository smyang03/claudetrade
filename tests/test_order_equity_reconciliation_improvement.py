from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import tempfile
import unittest

from bot import market_utils
from execution.order_state import OrderUnknownEscalator
from runtime.market_resolver import infer_ticker_market
import trading_bot


class _FrozenDateTime(datetime):
    fixed: datetime | None = None

    @classmethod
    def now(cls, tz=None):
        value = cls.fixed or datetime(2026, 4, 29, 10, 0, tzinfo=market_utils.KST)
        if tz is not None and value.tzinfo is None:
            return value.replace(tzinfo=tz)
        return value


def _bare_bot() -> trading_bot.TradingBot:
    bot = object.__new__(trading_bot.TradingBot)
    bot.pending_orders = []
    bot._save_pending_orders = Mock()
    bot._reconcile_broker_open_orders = Mock(return_value={})
    bot._flag_execution_issue = Mock()
    return bot


class PendingOrderUpsertTests(unittest.TestCase):
    def test_add_pending_order_preserves_distinct_order_numbers_for_same_ticker(self) -> None:
        bot = _bare_bot()
        bot.pending_orders = [
            {"market": "KR", "ticker": "006340", "order_no": "0007603600", "qty": 10}
        ]

        trading_bot.TradingBot._add_pending_order(
            bot,
            {"market": "KR", "ticker": "006340", "order_no": "0008146000", "qty": 10},
        )

        order_nos = sorted(o["order_no"] for o in bot.pending_orders)
        self.assertEqual(order_nos, ["0007603600", "0008146000"])
        self.assertTrue(all(o.get("duplicate_ticker_pending") for o in bot.pending_orders))
        bot._reconcile_broker_open_orders.assert_called_once()
        bot._save_pending_orders.assert_called_once()

    def test_add_pending_order_without_order_no_only_replaces_temporary_row(self) -> None:
        bot = _bare_bot()
        bot.pending_orders = [
            {"market": "KR", "ticker": "047040", "order_no": "0008147800", "qty": 3},
            {"market": "KR", "ticker": "047040", "order_no": "", "qty": 1},
        ]

        trading_bot.TradingBot._add_pending_order(
            bot,
            {"market": "KR", "ticker": "047040", "qty": 2},
        )

        self.assertEqual(len(bot.pending_orders), 2)
        self.assertIn("0008147800", {o.get("order_no", "") for o in bot.pending_orders})
        temp_rows = [o for o in bot.pending_orders if not o.get("order_no")]
        self.assertEqual(len(temp_rows), 1)
        self.assertEqual(temp_rows[0]["qty"], 2)


class OrderUnknownRegistryTests(unittest.TestCase):
    def test_broker_only_open_order_blocks_market_and_tracks_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")

            state = registry.record_broker_open_order(
                market="KR",
                ticker="006340",
                order_no="0007603600",
                side="buy",
                qty=10,
                remaining_qty=10,
                reason="test",
            )

            self.assertTrue(state["blocked"])
            self.assertTrue(registry.should_block_market("KR"))
            order_state = registry.state["orders"]["KR:0007603600"]
            self.assertTrue(order_state["broker_open"])
            self.assertEqual(order_state["resolution"], "BROKER_ONLY_OPEN_ORDER")

    def test_cancel_requested_and_resolved_are_tracked_by_order_no(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")

            registry.record_cancel_requested(
                market="KR",
                ticker="047040",
                order_no="0008147800",
                qty=3,
                reason="cancel_above_after_ack",
            )
            order_state = registry.state["orders"]["KR:0008147800"]
            self.assertEqual(order_state["resolution"], "CANCEL_REQUESTED")
            self.assertEqual(order_state["cancel_attempts"], 1)
            self.assertTrue(order_state["cancel_requested_at"])

            registry.record_cancel_resolved(
                market="KR",
                ticker="047040",
                order_no="0008147800",
                resolution="CANCEL_CONFIRMED",
            )
            order_state = registry.state["orders"]["KR:0008147800"]
            self.assertEqual(order_state["resolution"], "CANCEL_CONFIRMED")
            self.assertTrue(order_state["resolved_at"])

    def test_session_open_auto_clear_removes_market_pause_but_keeps_known_open_ticker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")
            registry.record_unknown(market="KR", ticker="006340", execution_id="0007603600", detail="unknown")
            registry.record_unknown(market="KR", ticker="047040", execution_id="0008147800", detail="unknown")

            summary = registry.auto_clear_at_session_open(
                market="KR",
                broker_snapshot={
                    "missing": False,
                    "stale": False,
                    "error": "",
                    "positions": [],
                    "open_orders": [
                        {
                            "ticker": "006340",
                            "order_no": "0007603600",
                            "side": "buy",
                            "remaining_qty": 1,
                        }
                    ],
                    "today_fills": [],
                },
            )

            self.assertTrue(summary["market_pause_cleared"])
            self.assertFalse(registry.should_block_market("KR"))
            self.assertIn("006340", registry.state["paused_tickers"]["KR"])
            self.assertNotIn("047040", registry.state["paused_tickers"]["KR"])
            self.assertEqual(registry.state["orders"]["KR:0007603600"]["resolution"], "RESTORED_TO_PENDING")
            self.assertEqual(registry.state["orders"]["KR:0008147800"]["resolution"], "AUTO_CLEARED_NO_BROKER_EVIDENCE")

    def test_session_open_auto_clear_keeps_duplicate_open_orders_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = OrderUnknownEscalator(Path(tmp) / "unknown.json")
            registry.record_duplicate_open_orders(
                market="KR",
                ticker="006340",
                order_nos=["0007603600", "0008146000"],
                reason="duplicate",
            )

            summary = registry.auto_clear_at_session_open(
                market="KR",
                broker_snapshot={
                    "missing": False,
                    "stale": False,
                    "error": "",
                    "positions": [],
                    "open_orders": [
                        {"ticker": "006340", "order_no": "0007603600", "remaining_qty": 1},
                        {"ticker": "006340", "order_no": "0008146000", "remaining_qty": 1},
                    ],
                    "today_fills": [],
                },
            )

            self.assertEqual(summary["kept_unresolved"], 1)
            self.assertTrue(registry.should_block_market("KR"))
            self.assertIn("006340", registry.state["paused_tickers"]["KR"])


class BrokerOpenOrderReconcileTests(unittest.TestCase):
    def test_broker_only_open_order_uses_remaining_qty_when_order_qty_missing(self) -> None:
        bot = object.__new__(trading_bot.TradingBot)
        bot.pending_orders = []
        bot.v2_order_unknown = Mock()
        bot._flag_execution_issue = Mock()
        bot._broker_truth_market_snapshot = Mock(
            return_value={
                "missing": False,
                "stale": False,
                "error": "",
                "open_orders": [
                    {
                        "market": "US",
                        "ticker": "IBM",
                        "order_no": "0030262408",
                        "side": "sell",
                        "order_qty": 0,
                        "remaining_qty": 3,
                    }
                ],
            }
        )

        summary = trading_bot.TradingBot._reconcile_broker_open_orders(
            bot,
            "US",
            reason="test",
            force=True,
        )

        payload = summary["broker_only_open_orders"][0]
        self.assertEqual(payload["qty"], 3)
        self.assertEqual(payload["remaining_qty"], 3)
        bot.v2_order_unknown.record_broker_open_order.assert_called_once()
        self.assertEqual(bot.v2_order_unknown.record_broker_open_order.call_args.kwargs["qty"], 3)


class EquityReferenceTests(unittest.TestCase):
    def _bot_for_equity(self, market: str, base: float, broker_total: float, *, daily_pnl: float = 0.0):
        bot = object.__new__(trading_bot.TradingBot)
        bot._daily_baseline_by_market = {market: {"session_date": "2026-04-29", "base": base}}
        bot._broker_state = {
            market: {
                "trust_level": "trusted",
                "last_snapshot": {"total_krw": broker_total, "cash_krw": broker_total, "eval_krw": 0},
                "last_trusted_snapshot": {"total_krw": broker_total, "cash_krw": broker_total},
            }
        }
        bot.risk = SimpleNamespace(daily_pnl=daily_pnl, session_start_equity=base, positions=[])
        bot.current_market = market
        bot._recent_sell_proceeds_by_market = {"KR": [], "US": []}
        bot._current_session_date_str = lambda _market: "2026-04-29"
        bot._ticker_market = lambda ticker: infer_ticker_market(ticker, unknown="KR")
        return bot

    def test_kr_uses_internal_session_equity_when_broker_cash_is_stale(self) -> None:
        bot = self._bot_for_equity("KR", 1_000_000, 1_100_000, daily_pnl=-1_000)
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "006340",
                "qty": 10,
                "pnl": -1_000,
                "reason": "stop_loss",
                "session_date": "2026-04-29",
                "market": "KR",
            }
        ]
        bot.risk.positions = [
            {
                "ticker": "006340",
                "qty": 10,
                "entry": 10_000,
                "current_price": 10_100,
                "entry_session_date": "2026-04-29",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                ctx = trading_bot.TradingBot._market_equity_reference_context(bot, "KR")

        self.assertEqual(ctx["source"], "internal_session_equity")
        self.assertEqual(ctx["total_krw"], 1_000_000)
        self.assertTrue(ctx["lag_suspected"])

    def test_realized_pnl_ignores_other_market_even_when_daily_pnl_is_combined(self) -> None:
        bot = self._bot_for_equity("KR", 1_000_000, 1_000_000, daily_pnl=-99_999)
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "006340",
                "qty": 10,
                "pnl": -1_000,
                "reason": "stop_loss",
                "session_date": "2026-04-29",
                "market": "KR",
            },
            {
                "side": "sell",
                "ticker": "SNAP",
                "qty": 12,
                "pnl": -8_000,
                "reason": "pre_session_sell",
                "session_date": "2026-04-29",
                "market": "US",
            },
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                realized = trading_bot.TradingBot._market_realized_pnl_krw(bot, "KR")

        self.assertEqual(realized, -1_000)

    def test_us_recent_sell_proceeds_adjusts_broker_lag_without_double_count(self) -> None:
        bot = self._bot_for_equity("US", 1_272_940, 1_167_233, daily_pnl=-1_676)
        bot.risk.all_trade_log = []
        trading_bot.TradingBot._note_recent_sell_proceeds(
            bot,
            "US",
            "SNAP",
            order_no="0030639869",
            proceeds_krw=105_530,
            reason="pre_session_sell",
        )

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"):
                ctx = trading_bot.TradingBot._market_equity_reference_context(bot, "US")
                ret = trading_bot.TradingBot._market_daily_return_pct(bot, "US")

        self.assertEqual(ctx["source"], "broker_current_sell_lag_adjusted")
        self.assertEqual(ctx["base_krw"], 1_272_940)
        self.assertEqual(ctx["adjustment_krw"], 105_530)
        self.assertGreater(ret, -1.0)

    def test_equity_only_halt_warning_includes_lag_context_without_halting(self) -> None:
        bot = self._bot_for_equity("US", 1_000_000, 900_000, daily_pnl=0)
        bot.risk.all_trade_log = []
        bot.risk.halted = False
        bot.risk.halt_reason = ""
        bot.risk.positions = [
            {
                "ticker": "SNAP",
                "qty": 1,
                "sell_confirmation_pending": True,
                "pending_sell_order_no": "0030639869",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(trading_bot, "DECISIONS_FILE", Path(tmp) / "decisions.jsonl"), patch.dict(
                trading_bot.HARD_RULES,
                {"max_daily_loss_pct": -8.0},
            ), self.assertLogs(trading_bot.log.name, level="WARNING") as caught:
                halted = trading_bot.TradingBot._check_market_halt(bot, "US")

        self.assertFalse(halted)
        self.assertFalse(bot.risk.halted)
        self.assertEqual(bot.risk.halt_reason, "")
        logs = "\n".join(caught.output)
        self.assertIn("equity breach only", logs)
        self.assertIn("broker_total=", logs)
        self.assertIn("internal=", logs)
        self.assertIn("adjustment=", logs)
        self.assertIn("fallback=", logs)
        self.assertIn("pending_sell=SNAP", logs)
        self.assertIn("0030639869", logs)


class NewBuyGateTests(unittest.TestCase):
    def _bot(self) -> trading_bot.TradingBot:
        bot = object.__new__(trading_bot.TradingBot)
        bot.v2 = None
        bot.v2_order_unknown = None
        return bot

    def _with_now(self, hour: int, minute: int):
        _FrozenDateTime.fixed = datetime(2026, 4, 29, hour, minute, tzinfo=market_utils.KST)
        return patch.multiple(
            market_utils,
            datetime=_FrozenDateTime,
        )

    def test_kr_new_buy_blocks_after_blackout_start_before_close(self) -> None:
        bot = self._bot()
        with self._with_now(15, 21), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ENTRY_BLACKOUT")

    def test_kr_new_buy_blocks_after_orderable_close(self) -> None:
        bot = self._bot()
        with self._with_now(15, 31), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "MARKET_CLOSED")

    def test_order_unknown_market_block_stops_new_buy(self) -> None:
        bot = self._bot()
        bot.v2_order_unknown = SimpleNamespace(
            should_block_global=Mock(return_value=False),
            should_block_market=Mock(return_value=True),
        )
        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}
        ):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "ORDER_UNKNOWN_UNRESOLVED")
        self.assertEqual(state["scope"], "market")

    def test_second_stop_cluster_blocks_new_buy_by_default(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 2}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 30, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": set()}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "momentum")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "STOP_CLUSTER_MARKET_BLOCK")
        self.assertEqual(state["scope"], "market")

    def test_first_stop_cluster_cooldown_blocks_normal_strategy(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 1}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"000660"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "momentum")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "STOP_CLUSTER_FIRST_STOP_COOLDOWN")
        self.assertEqual(state["scope"], "market")

    def test_first_stop_cluster_cooldown_blocks_kr_recovery_micro_by_default(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 1}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"000660"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
                "KR_RECOVERY_MICRO_ENABLED": "",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "RECOVERY_MICRO")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "STOP_CLUSTER_FIRST_STOP_COOLDOWN")
        self.assertEqual(state["details"]["kr_recovery_micro_gate"]["reason"], "kr_recovery_micro_disabled")

    def test_first_stop_cluster_cooldown_allows_recovery_micro(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 1}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"000660"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
                "KR_RECOVERY_MICRO_ENABLED": "true",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "RECOVERY_MICRO")

        self.assertTrue(state["allowed"])
        self.assertFalse(state["blocked"])
        self.assertEqual(
            state["details"]["stop_cluster_exemption"],
            "RECOVERY_MICRO_FIRST_STOP_COOLDOWN",
        )

    def test_recovery_micro_still_blocks_same_stopped_ticker(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 1}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"005930"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "RECOVERY_MICRO")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "SAME_DAY_REENTRY_AFTER_STOP")
        self.assertEqual(state["scope"], "ticker")

    def test_recovery_micro_still_blocks_second_stop_cluster(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 2}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"000660"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "30",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "RECOVERY_MICRO")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "STOP_CLUSTER_MARKET_BLOCK")
        self.assertEqual(state["scope"], "market")

    def test_zero_first_stop_freeze_allows_normal_flow_without_recovery_exemption(self) -> None:
        bot = self._bot()
        bot._daily_sl_count = {"KR": 1}
        bot._daily_sl_last_at = {"KR": datetime(2026, 4, 29, 9, 55, tzinfo=market_utils.KST)}
        bot._v2_same_day_stop_tickers = {"KR": {"000660"}}

        with self._with_now(10, 0), patch.object(trading_bot, "datetime", _FrozenDateTime), patch.dict(
            os.environ,
            {
                "STOP_CLUSTER_FIRST_FREEZE_MINUTES": "0",
                "STOP_CLUSTER_HARD_BLOCK_COUNT": "2",
                "STOP_CLUSTER_DISASTER_BLOCK_COUNT": "3",
            },
        ), patch.dict(market_utils.HARD_RULES, {"no_new_entry_min": 10, "close_before_min": 10}):
            state = trading_bot.TradingBot._new_buy_block_state(bot, "KR", "005930", "momentum")

        self.assertTrue(state["allowed"])
        self.assertNotIn("stop_cluster_exemption", state["details"])


class StopClusterDedupeTests(unittest.TestCase):
    def test_duplicate_stop_event_does_not_increment_count(self) -> None:
        bot = object.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot._daily_sl_count = {"US": 0}
        bot._daily_sl_last_at = {"US": None}
        bot._daily_sl_event_keys = set()
        bot._v2_same_day_stop_tickers = {"US": set()}
        bot._current_session_date_str = lambda market: "2026-05-05"

        first = trading_bot.TradingBot._note_stop_loss_event(
            bot,
            "US",
            "EAT",
            "stop_loss",
            order_no="ord-1",
            qty=1,
            pnl_krw=-5000,
            pnl_pct=-1.2,
        )
        duplicate = trading_bot.TradingBot._note_stop_loss_event(
            bot,
            "US",
            "EAT",
            "stop_loss",
            order_no="ord-1",
            qty=1,
            pnl_krw=-5000,
            pnl_pct=-1.2,
        )
        second = trading_bot.TradingBot._note_stop_loss_event(
            bot,
            "US",
            "CYTK",
            "stop_loss",
            order_no="ord-2",
            qty=1,
            pnl_krw=-3000,
            pnl_pct=-0.8,
        )

        self.assertEqual(first, 1)
        self.assertEqual(duplicate, 1)
        self.assertEqual(second, 2)
        self.assertEqual(bot._daily_sl_count["US"], 2)


class RecoveryMicroTests(unittest.TestCase):
    def test_kr_recovery_micro_adjustment_disabled_by_default(self) -> None:
        bot = object.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot.enable_recovery_micro = True
        bot.recovery_micro_paper_only = False
        bot.recovery_micro_allowed_markets = {"KR", "US"}
        bot.recovery_micro_allowed_modes = {"MODERATE_BULL"}
        bot.recovery_micro_min_entry_priority = 0.70
        bot.recovery_micro_max_daily_trades = 1
        bot.recovery_micro_max_open_positions = 1
        bot._market_open_elapsed_min = lambda market, now_dt=None: 45.0

        with patch.dict(os.environ, {"KR_RECOVERY_MICRO_ENABLED": ""}, clear=False):
            result = trading_bot.TradingBot._recovery_micro_adjustment(
                bot,
                market="KR",
                ticker="005930",
                mode="MODERATE_BULL",
                source_strategy="momentum",
                entry_priority_score=0.72,
                qty=10,
                risk_price_krw=70_000,
                original_order_cost_krw=700_000,
                available_budget_krw=500_000,
                cash_krw=500_000,
                daily_stop_count=1,
                realized_daily_pnl_pct=-0.5,
            )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "kr_recovery_micro_disabled")

    def test_recovery_micro_caps_size_after_first_stop(self) -> None:
        bot = object.__new__(trading_bot.TradingBot)
        bot.is_paper = False
        bot.enable_recovery_micro = True
        bot.recovery_micro_paper_only = False
        bot.recovery_micro_allowed_markets = {"US"}
        bot.recovery_micro_allowed_modes = {"MODERATE_BULL"}
        bot.recovery_micro_min_entry_priority = 0.70
        bot.recovery_micro_max_order_krw_us = 180_000
        bot.recovery_micro_max_order_krw_kr = 150_000
        bot.recovery_micro_max_daily_trades = 1
        bot.recovery_micro_max_open_positions = 1
        bot.risk = SimpleNamespace(positions=[])
        bot.pending_orders = []
        bot.decision_event_log = []
        bot._v2_same_day_stop_tickers = {"US": set()}
        bot._ticker_market = lambda ticker: "US"
        bot._market_close_anchor_dt = lambda market: datetime(2026, 5, 6, 5, 0, tzinfo=market_utils.KST)

        result = trading_bot.TradingBot._recovery_micro_adjustment(
            bot,
            market="US",
            ticker="CYTK",
            mode="MODERATE_BULL",
            source_strategy="momentum",
            entry_priority_score=0.72,
            qty=10,
            risk_price_krw=140_000,
            original_order_cost_krw=1_400_000,
            available_budget_krw=500_000,
            cash_krw=500_000,
            daily_stop_count=1,
            realized_daily_pnl_pct=-0.5,
        )

        self.assertTrue(result["allowed"])
        self.assertEqual(result["adjusted_qty"], 1)
        self.assertEqual(result["adjusted_order_cost_krw"], 140_000)
        self.assertEqual(result["source_strategy"], "momentum")
        self.assertTrue(result["recovery_micro_no_carry"])
        self.assertEqual(result["recovery_micro_hard_loss_pct"], 1.2)
        self.assertTrue(result["recovery_micro_force_exit_at"])


if __name__ == "__main__":
    unittest.main()
