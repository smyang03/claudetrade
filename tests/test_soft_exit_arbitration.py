from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from lifecycle.event_store import EventStore
from risk_manager import RiskManager
from trading_bot import TradingBot


def _bot_stub() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot.selection_meta = {"KR": {}, "US": {}}
    bot.usd_krw_rate = 1.0
    bot.today_judgment = {"consensus": {"mode": "MILD_BULL"}}
    bot.soft_exit_arbitration_enabled = True
    bot.soft_exit_reference_buffer_pct = 0.005
    bot.soft_exit_min_mfe_pct = 2.5
    bot.soft_exit_cooldown_sec = 600
    bot.soft_exit_max_reviews = 2
    bot._current_session_date_str = lambda market: "2026-04-30"  # type: ignore[method-assign]
    bot._save_positions = lambda: None  # type: ignore[method-assign]
    bot._record_decision_event = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot._lookup_ticker_name = lambda ticker, market: ""  # type: ignore[method-assign]
    bot._normalize_position_metadata = lambda pos, *args, **kwargs: pos  # type: ignore[method-assign]
    return bot


def _stx_position(**overrides):
    pos = {
        "ticker": "STX",
        "entry": 654.92,
        "qty": 1,
        "current_price": 658.01,
        "display_avg_price": 654.92,
        "display_current_price": 658.01,
        "display_currency": "USD",
        "strategy": "gap_pullback",
        "pathb_reference_target": 665.0,
        "pathb_reference_stop": 640.0,
        "pathb_reference_confidence": 0.55,
        "peak_pnl_pct": 3.775,
        "held_days": 0,
        "max_hold": 1,
    }
    pos.update(overrides)
    return pos


class SoftExitArbitrationTests(unittest.TestCase):
    def test_entry_reference_metadata_uses_cancelled_pathb_and_selection_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            adapter = ClaudePriceAdapter(store)
            plan = make_price_plan(
                decision_id="dec_stx",
                ticker="STX",
                market="US",
                session_date="2026-04-30",
                buy_zone_low=650.0,
                buy_zone_high=652.0,
                sell_target=665.0,
                stop_loss=640.0,
                hold_days=1,
                confidence=0.55,
                cancel_if_open_above=660.0,
            )
            path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
            adapter.cancel_plan(path_run_id, reason="cancel_if_open_above", runtime_mode="live", brain_snapshot_id="brain1")

            bot = _bot_stub()
            bot.pathb = SimpleNamespace(store=store, mode="live")
            bot.selection_meta["US"] = {
                "price_targets": {
                    "STX": {"sell_target": 672.0, "stop_loss": 641.0, "confidence": 0.6}
                }
            }

            meta = bot._entry_reference_metadata("US", "STX")

            self.assertEqual(meta["pathb_reference_target"], 665.0)
            self.assertEqual(meta["pathb_reference_stop"], 640.0)
            self.assertEqual(meta["pathb_reference_status"], "cancel_if_open_above")
            self.assertEqual(meta["pathb_reference_path_run_id"], path_run_id)
            self.assertEqual(meta["selection_reference_target"], 672.0)

    def test_make_position_copies_reference_metadata_from_order(self) -> None:
        bot = _bot_stub()
        order = {
            "ticker": "STX",
            "market": "US",
            "qty": 1,
            "raw_price": 654.92,
            "tp_pct": 0.02,
            "sl_pct": 0.02,
            "pathb_reference_target": 665.0,
            "selection_reference_target": 672.0,
        }

        pos = bot._make_position_from_broker(order, {"avg_price": 654.92, "eval_price": 658.01})

        self.assertEqual(pos["pathb_reference_target"], 665.0)
        self.assertEqual(pos["selection_reference_target"], 672.0)

    def test_profit_floor_hold_sets_floor_count_and_cooldown(self) -> None:
        bot = _bot_stub()
        pos = _stx_position()
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: {  # type: ignore[method-assign]
            "action": "HOLD",
            "confidence": 0.8,
            "protective_stop": 656.0,
            "reason": "target still valid",
        }
        cand = {
            **pos,
            "reason": "profit_floor",
            "exit_price": 658.01,
            "profit_floor_price": 658.19,
            "effective_stop_price": 640.0,
            "loss_cap_price": 635.0,
            "position_mfe_pct": 3.775,
        }

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertTrue(deferred)
        self.assertEqual(pos["soft_exit_review_count"], 1)
        self.assertEqual(pos["soft_exit_review_action"], "HOLD")
        self.assertGreaterEqual(pos["soft_exit_floor_price"], 658.19)
        self.assertTrue(pos["soft_exit_review_cooldown_until"])

    def test_quick_exit_fallback_defers_soft_sell(self) -> None:
        bot = _bot_stub()
        pos = _stx_position()
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: {  # type: ignore[method-assign]
            "action": "SELL",
            "fallback": True,
            "reason": "timeout",
        }
        cand = {**pos, "reason": "profit_floor", "exit_price": 658.01, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertTrue(deferred)
        self.assertEqual(cand["soft_exit_review_action"], "HOLD")
        self.assertTrue(cand["soft_exit_review_fallback"])
        self.assertEqual(pos["soft_exit_review_count"], 1)

    def test_pathb_reference_beats_selection_reference_for_arbitration(self) -> None:
        bot = _bot_stub()
        pos = _stx_position(selection_reference_target=672.0, selection_reference_stop=641.0)
        bot.risk = SimpleNamespace(positions=[pos])
        seen_payload = {}

        def _quick(payload):
            seen_payload.update(payload)
            return {
                "action": "SELL",
                "fallback": False,
                "reason": "respect soft exit",
            }

        bot._call_quick_exit_check = _quick  # type: ignore[method-assign]
        cand = {**pos, "reason": "profit_floor", "exit_price": 658.01, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertFalse(deferred)
        self.assertEqual(seen_payload["reference_target"], 665.0)
        self.assertEqual(cand["soft_exit_reference_source"], "pathb_reference")

    def test_trail_stop_is_eligible_for_arbitration(self) -> None:
        bot = _bot_stub()
        pos = _stx_position()
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: {  # type: ignore[method-assign]
            "action": "HOLD",
            "confidence": 0.7,
            "protective_stop": 657.0,
            "reason": "target remains above",
        }
        cand = {
            **pos,
            "reason": "trail_stop",
            "exit_price": 658.01,
            "effective_stop_price": 657.5,
            "position_mfe_pct": 3.775,
        }

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertTrue(deferred)
        self.assertEqual(pos["soft_exit_deferred_reason"], "trail_stop")

    def test_max_reviews_limit_bypasses_quick_check(self) -> None:
        bot = _bot_stub()
        pos = _stx_position(soft_exit_review_count=2)
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: self.fail("quick check must not run")  # type: ignore[method-assign]
        cand = {**pos, "reason": "profit_floor", "exit_price": 658.01, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertFalse(deferred)

    def test_cooldown_bypasses_quick_check(self) -> None:
        bot = _bot_stub()
        pos = _stx_position(
            soft_exit_review_cooldown_until=(datetime.now() + timedelta(minutes=5)).isoformat(timespec="seconds")
        )
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: self.fail("quick check must not run")  # type: ignore[method-assign]
        cand = {**pos, "reason": "profit_floor", "exit_price": 658.01, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertFalse(deferred)

    def test_missing_reference_target_bypasses_quick_check(self) -> None:
        bot = _bot_stub()
        pos = _stx_position(
            pathb_reference_target=0.0,
            pathb_reference_stop=0.0,
            selection_reference_target=0.0,
        )
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: self.fail("quick check must not run")  # type: ignore[method-assign]
        cand = {**pos, "reason": "profit_floor", "exit_price": 658.01, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertFalse(deferred)

    def test_current_price_at_or_below_entry_bypasses_quick_check(self) -> None:
        bot = _bot_stub()
        pos = _stx_position(current_price=654.0, display_current_price=654.0)
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: self.fail("quick check must not run")  # type: ignore[method-assign]
        cand = {**pos, "reason": "profit_floor", "exit_price": 654.0, "position_mfe_pct": 3.775}

        deferred = bot._try_soft_exit_arbitration(cand, "US")

        self.assertFalse(deferred)

    def test_hard_loss_cap_bypasses_arbitration(self) -> None:
        bot = _bot_stub()
        pos = _stx_position()
        bot.risk = SimpleNamespace(positions=[pos])
        bot._call_quick_exit_check = lambda payload: self.fail("quick check must not run")  # type: ignore[method-assign]

        deferred = bot._try_soft_exit_arbitration({**pos, "reason": "loss_cap"}, "US")

        self.assertFalse(deferred)

    def test_soft_exit_floor_price_is_direct_risk_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "STX",
                "entry": 100.0,
                "qty": 1,
                "current_price": 100.4,
                "strategy": "gap_pullback",
                "tp": 110.0,
                "sl": 90.0,
                "held_days": 0,
                "max_hold": 1,
                "peak_pnl_pct": 3.0,
                "soft_exit_floor_price": 101.0,
            }
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "soft_exit_floor_price")
        self.assertTrue(candidates[0]["soft_exit_floor_triggered"])

    def test_kr_soft_exit_floor_price_uses_krw_path(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "005930",
                "entry": 70_000.0,
                "qty": 1,
                "current_price": 70_400.0,
                "strategy": "gap_pullback",
                "tp": 76_000.0,
                "sl": 68_000.0,
                "held_days": 0,
                "max_hold": 1,
                "peak_pnl_pct": 3.0,
                "soft_exit_floor_price": 70_500.0,
            }
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["ticker"], "005930")
        self.assertEqual(candidates[0]["reason"], "soft_exit_floor_price")
        self.assertEqual(candidates[0]["soft_exit_floor_price"], 70_500.0)


if __name__ == "__main__":
    unittest.main()
