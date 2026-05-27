from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from decision.claude_price_plan import make_price_plan
from execution.claude_price_sell_manager import ExitSignal
from lifecycle.event_store import EventStore
from runtime.pathb_runtime import KST, PathBRuntime
from runtime.pathb_reasons import normalize_pathb_decision_exit_reason
from runtime.exit_lifecycle import reason_family
from runtime.v2_lifecycle_runtime import v2_close_reason
from trading_bot import TradingBot


class _Risk:
    def __init__(self) -> None:
        self.positions: list[dict] = []


class _Bot:
    def __init__(self) -> None:
        self.risk = _Risk()
        self.usd_krw_rate = 1350.0
        self.price_cache = {}
        self.price_cache_raw = {}
        self.today_judgment = {"digest_prompt": ""}
        self.session_active = True
        self.current_market = "US"
        self.v2 = SimpleNamespace(brain_snapshot_ids={"US": "brain_us", "KR": "brain_kr"})
        self.saved_positions = False

    def _current_session_date_str(self, market: str) -> str:
        return "2026-05-13"

    def _save_positions(self) -> None:
        self.saved_positions = True

    def _build_intraday_context(self, market: str) -> str:
        return ""

    def _advisor_pos(self, pos: dict, market: str) -> dict:
        return dict(pos)

    def _minutes_to_close(self, market: str) -> float:
        return 120.0


def _plan(*, market: str = "US"):
    return make_price_plan(
        decision_id=f"dec_{market}",
        ticker="HALO" if market == "US" else "005930",
        market=market,
        session_date="2026-05-13",
        buy_zone_low=68.5 if market == "US" else 98.0,
        buy_zone_high=71.5 if market == "US" else 101.0,
        sell_target=74.0 if market == "US" else 110.0,
        stop_loss=67.0 if market == "US" else 95.0,
        hold_days=1,
        confidence=0.72,
    )


def _runtime_with_plan(tmp: str, *, market: str = "US", status: str = "FILLED"):
    bot = _Bot()
    bot.current_market = market
    store = EventStore(Path(tmp) / "events.db")
    runtime = PathBRuntime(bot, is_paper=False, store=store)
    plan = _plan(market=market)
    store.create_path_run(
        path_run_id=plan.path_run_id,
        decision_id=plan.decision_id,
        path_type="claude_price",
        market=plan.market,
        runtime_mode=runtime.mode,
        session_date=plan.session_date,
        ticker=plan.ticker,
        status=status,
        plan={**plan.to_dict(), "actual_entry_price": 70.34 if market == "US" else 100.0},
    )
    return runtime, bot, store, plan


class PathBProfitProtectionTests(unittest.TestCase):
    def test_market_open_for_advisor_ignores_current_market_when_session_time_open(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("runtime.pathb_runtime._is_trading_day", return_value=True):
            runtime, bot, _store, _plan = _runtime_with_plan(tmp, market="US")
            bot.current_market = "KR"
            now = datetime.now(KST)
            runtime._session_date = lambda market: now.date().isoformat()  # type: ignore[method-assign]
            runtime._minutes_to_close = lambda market: 120.0  # type: ignore[method-assign]
            runtime._advisor_market_open_close = lambda market, session_date, now_dt: (  # type: ignore[method-assign]
                now_dt - timedelta(minutes=30),
                now_dt + timedelta(minutes=120),
            )

            self.assertTrue(runtime._market_open_for_advisor("US"))

    def test_market_open_for_advisor_rejects_rollover_minutes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("runtime.pathb_runtime._is_trading_day", return_value=True):
            runtime, _bot, _store, _plan = _runtime_with_plan(tmp, market="US")
            now = datetime.now(KST)
            runtime._session_date = lambda market: now.date().isoformat()  # type: ignore[method-assign]
            runtime._minutes_to_close = lambda market: 1437.0  # type: ignore[method-assign]
            runtime._advisor_market_open_close = lambda market, session_date, now_dt: (  # type: ignore[method-assign]
                now_dt - timedelta(minutes=30),
                now_dt + timedelta(minutes=120),
            )

            self.assertFalse(runtime._market_open_for_advisor("US"))

    def test_apply_general_hold_advice_creates_protective_hold_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            runtime._minutes_to_close = lambda market: 120.0  # type: ignore[method-assign]
            pos = {
                "ticker": "HALO",
                "qty": 1,
                "display_avg_price": 70.34,
                "display_current_price": 71.16,
                "pathb_path_run_id": plan.path_run_id,
            }
            advice = {
                "action": "HOLD",
                "protective_stop": 70.80,
                "hard_stop": 69.01,
                "valid_for_min": 15,
                "confidence": 0.72,
                "reason": "protect open profit",
            }

            result = runtime.apply_general_hold_advice_policy(pos, "US", advice, 71.16)

            self.assertTrue(result["updated"])
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["mode"], "protective_hold")
            self.assertEqual(policy["protective_stop"], 70.80)
            self.assertLessEqual(policy["hard_stop"], policy["protective_stop"])

    def test_intraday_hold_valid_minutes_floor_is_20_for_normal_profit_hold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            runtime._minutes_to_close = lambda market: 120.0  # type: ignore[method-assign]
            pos = {
                "ticker": "HALO",
                "qty": 1,
                "display_avg_price": 70.34,
                "display_current_price": 71.16,
                "decision_stage": "INTRADAY_REVIEW",
                "pathb_path_run_id": plan.path_run_id,
            }
            advice = {
                "action": "HOLD",
                "decision_stage": "INTRADAY_REVIEW",
                "protective_stop": 70.20,
                "hard_stop": 69.01,
                "valid_for_min": 10,
                "confidence": 0.72,
                "reason": "protect open profit",
            }

            result = runtime.apply_general_hold_advice_policy(pos, "US", advice, 71.16)

            self.assertTrue(result["updated"])
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["valid_for_min"], 20)

    def test_intraday_hold_valid_minutes_keeps_10_for_stop_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            runtime._minutes_to_close = lambda market: 120.0  # type: ignore[method-assign]
            pos = {
                "ticker": "HALO",
                "qty": 1,
                "display_avg_price": 70.34,
                "display_current_price": 71.16,
                "decision_stage": "INTRADAY_REVIEW",
                "pathb_path_run_id": plan.path_run_id,
            }
            advice = {
                "action": "HOLD",
                "decision_stage": "INTRADAY_REVIEW",
                "hold_mode": "stop_recovery",
                "protective_stop": 70.20,
                "hard_stop": 69.01,
                "valid_for_min": 10,
                "confidence": 0.72,
                "reason": "watch recovery",
            }

            result = runtime.apply_general_hold_advice_policy(pos, "US", advice, 71.16)

            self.assertTrue(result["updated"])
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["valid_for_min"], 10)

    def test_apply_general_hold_advice_preserves_sellability_quarantine(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            pos = {
                "ticker": "HALO",
                "qty": 1,
                "display_avg_price": 70.34,
                "display_current_price": 71.16,
                "pathb_path_run_id": plan.path_run_id,
                "sellable_qty_untrusted": True,
                "manual_reconcile_required": True,
                "pathb_sell_state": "sellable_qty_reject_no_open_order",
            }
            advice = {
                "action": "HOLD",
                "protective_stop": 70.80,
                "hard_stop": 69.01,
                "valid_for_min": 15,
                "confidence": 0.72,
                "reason": "protect open profit",
            }

            result = runtime.apply_general_hold_advice_policy(pos, "US", advice, 71.16)

            self.assertFalse(result["updated"])
            self.assertEqual(result["reason"], "sellable_qty_untrusted")
            self.assertTrue(result["preserved_execution_uncertainty"])
            self.assertNotIn("auto_sell_policy", store.find_path_run(plan.path_run_id)["plan"])
            self.assertTrue(pos["sellable_qty_untrusted"])
            self.assertTrue(pos["manual_reconcile_required"])
            self.assertEqual(pos["pathb_sell_state"], "sellable_qty_reject_no_open_order")

    def test_apply_general_sell_advice_creates_forced_sell_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            pos = {
                "ticker": "HALO",
                "qty": 1,
                "display_avg_price": 70.34,
                "display_current_price": 71.16,
                "pathb_path_run_id": plan.path_run_id,
            }
            advice = {
                "action": "SELL",
                "valid_for_min": 10,
                "confidence": 0.74,
                "reason": "thesis weakened",
            }

            result = runtime.apply_general_hold_advice_policy(pos, "US", advice, 71.16)

            self.assertTrue(result["updated"])
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["mode"], "forced_sell")
            self.assertEqual(policy["close_reason"], "CLOSED_CLAUDE_SELL")
            self.assertEqual(policy["reason"], "thesis weakened")

    def test_forced_sell_policy_emits_sell_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "forced_sell",
                        "close_reason": "CLOSED_CLAUDE_SELL",
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )

            result = runtime._evaluate_pathb_auto_sell_policy(plan, {"ticker": "HALO"}, 71.20)

            self.assertEqual(result["action"], "sell")
            self.assertEqual(result["signal"].reason, "policy_forced_sell")
            self.assertEqual(result["signal"].close_reason, "CLOSED_CLAUDE_SELL")

    def test_protective_hold_sells_on_protective_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 70.80,
                        "hard_stop": 69.01,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            pos = {"ticker": "HALO", "display_avg_price": 70.34, "pathb_path_run_id": plan.path_run_id}

            result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 70.75)

            self.assertEqual(result["action"], "sell")
            self.assertEqual(result["signal"].reason, "policy_protective_stop")
            self.assertEqual(result["signal"].close_reason, "CLOSED_CLAUDE_PRICE_STOP")

    def test_protective_hold_rechecks_on_price_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 70.80,
                        "hard_stop": 69.01,
                        "reask_if_price_above": 72.50,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )

            result = runtime._evaluate_pathb_auto_sell_policy(plan, {"ticker": "HALO"}, 72.60)

            self.assertEqual(result["action"], "recheck")
            self.assertEqual(result["reason"], "policy_price_above_trigger")

    def test_protective_hold_sells_on_hard_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 70.80,
                        "hard_stop": 69.01,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            pos = {"ticker": "HALO", "display_avg_price": 70.34, "pathb_path_run_id": plan.path_run_id}

            result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 69.00)

            self.assertEqual(result["action"], "sell")
            self.assertEqual(result["signal"].reason, "policy_hard_stop")
            self.assertEqual(result["signal"].close_reason, "CLOSED_HARD_STOP")

    def test_protective_hold_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 70.80,
                        "hard_stop": 69.01,
                        "valid_until": (datetime.now(KST) - timedelta(minutes=1)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )

            result = runtime._evaluate_pathb_auto_sell_policy(plan, {"ticker": "HALO"}, 71.00)

            self.assertEqual(result["action"], "proceed")
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["status"], "expired")

    def test_protective_hold_releases_when_trailing_catches_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="US")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 70.80,
                        "hard_stop": 69.01,
                        "valid_until": (datetime.now(KST) + timedelta(minutes=10)).isoformat(timespec="seconds"),
                    }
                },
                merge_plan=True,
            )
            pos = {"ticker": "HALO", "sl": 71.00 * 1350.0, "pathb_path_run_id": plan.path_run_id}

            result = runtime._evaluate_pathb_auto_sell_policy(plan, pos, 71.20)

            self.assertEqual(result["action"], "proceed")
            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]
            self.assertEqual(policy["status"], "released")

    def test_profit_ladder_uses_mfe_not_current_pnl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PATHB_LADDER_MIN_HOLD_SEC": "0"}, clear=False):
            runtime, _bot, _store, plan = _runtime_with_plan(tmp, market="KR")
            pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 3.2, "pathb_path_run_id": plan.path_run_id}

            signal = runtime._pathb_profit_ladder_signal(plan, pos, 101.50, "KR")

            self.assertIsNotNone(signal)
            self.assertEqual(signal.reason, "profit_ladder")
            self.assertEqual(signal.close_reason, "CLOSED_PROFIT_LADDER")

    def test_profit_ladder_skips_when_protective_hold_is_tighter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {"PATHB_LADDER_MIN_HOLD_SEC": "0"}, clear=False):
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="KR")
            store.update_path_run(
                plan.path_run_id,
                plan={
                    "auto_sell_policy": {
                        "status": "active",
                        "mode": "protective_hold",
                        "protective_stop": 103.0,
                    }
                },
                merge_plan=True,
            )
            pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 3.2, "pathb_path_run_id": plan.path_run_id}

            self.assertIsNone(runtime._pathb_profit_ladder_signal(plan, pos, 101.50, "KR"))

    def test_closed_profit_ladder_bypasses_sell_review(self) -> None:
        runtime = PathBRuntime.__new__(PathBRuntime)
        signal = ExitSignal(True, "profit_ladder", "CLOSED_PROFIT_LADDER", 101.5, "run1")

        with patch.dict("os.environ", {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "false"}, clear=False):
            result = runtime._run_pathb_sell_review_gate(_plan(market="KR"), {}, signal)

        self.assertTrue(result["allowed"])
        self.assertTrue(result["bypassed"])
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_PROFIT_LADDER"), "profit_ladder")
        self.assertEqual(v2_close_reason("profit_ladder"), "CLOSED_PROFIT_LADDER")
        self.assertEqual(reason_family("profit_ladder"), "profit_ladder")

    def test_closed_profit_ladder_calls_sell_review_when_review_all_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ",
            {"CLAUDE_REVIEW_ALL_AUTOMATED_SELLS": "true"},
            clear=False,
        ):
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="KR")
            signal = ExitSignal(True, "profit_ladder", "CLOSED_PROFIT_LADDER", 101.5, plan.path_run_id)

            with patch("minority_report.hold_advisor.ask", return_value={"action": "SELL", "confidence": 0.8}) as ask:
                result = runtime._run_pathb_sell_review_gate(plan, {}, signal)

            run = store.find_path_run(plan.path_run_id)
            self.assertTrue(result["allowed"])
            self.assertFalse(result.get("bypassed", False))
            ask.assert_called_once()
            self.assertEqual(run["plan"]["auto_sell_review_action"], "SELL")
            self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_PROFIT_LADDER"), "profit_ladder")

    def test_profit_protection_review_respects_cooldown(self) -> None:
        env = {
            "PATHB_PROFIT_REVIEW_ENABLED": "true",
            "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0",
            "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "600",
            "PATHB_LADDER_MIN_HOLD_SEC": "0",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env, clear=False):
            runtime, _bot, _store, plan = _runtime_with_plan(tmp, market="KR")
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
            pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            with patch("minority_report.hold_advisor.ask", return_value={"action": "HOLD", "confidence": 0.5}) as ask:
                first = runtime._maybe_trigger_profit_protection_review(plan, pos, 102.50, "KR")
                second = runtime._maybe_trigger_profit_protection_review(plan, pos, 102.60, "KR")

        self.assertTrue(first["triggered"])
        self.assertFalse(second["triggered"])
        self.assertEqual(second["reason"], "cooldown")
        self.assertEqual(ask.call_count, 1)

    def test_profit_protection_review_skips_hold_advisor_when_market_closed(self) -> None:
        env = {
            "PATHB_PROFIT_REVIEW_ENABLED": "true",
            "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0",
            "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "0",
            "PATHB_LADDER_MIN_HOLD_SEC": "0",
        }
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env, clear=False):
            runtime, _bot, _store, plan = _runtime_with_plan(tmp, market="KR")
            runtime._market_open_for_advisor = lambda market: False  # type: ignore[method-assign]
            pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            with patch("minority_report.hold_advisor.ask") as ask:
                result = runtime._maybe_trigger_profit_protection_review(plan, pos, 102.50, "KR")

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "market_closed")
        ask.assert_not_called()

    def test_profit_protection_review_sell_creates_forced_sell_policy(self) -> None:
        env = {
            "PATHB_PROFIT_REVIEW_ENABLED": "true",
            "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0",
            "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "0",
            "PATHB_LADDER_MIN_HOLD_SEC": "0",
        }
        advice = {"action": "SELL", "confidence": 0.8, "reason": "give back risk"}
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env, clear=False):
            runtime, _bot, store, plan = _runtime_with_plan(tmp, market="KR")
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
            pos = {"ticker": "005930", "entry": 100.0, "peak_pnl_pct": 3.0, "pathb_path_run_id": plan.path_run_id}
            with patch("minority_report.hold_advisor.ask", return_value=advice):
                result = runtime._maybe_trigger_profit_protection_review(plan, pos, 102.50, "KR")

            policy = store.find_path_run(plan.path_run_id)["plan"]["auto_sell_policy"]

        self.assertTrue(result["triggered"])
        self.assertEqual(result["reason"], "forced_sell_policy")
        self.assertEqual(policy["mode"], "forced_sell")
        self.assertEqual(policy["close_reason"], "CLOSED_CLAUDE_SELL")

    def test_profit_protection_review_aborts_when_verified_price_below_floor(self) -> None:
        """TSM 유형 회귀: scan stale 가격으로 gate 통과했지만 verified 가격으로 재확인 시 ladder floor
        아래에 있으면 hold_advisor 호출 없이 too_close_to_ladder_floor 반환."""
        env = {
            "PATHB_PROFIT_REVIEW_ENABLED": "true",
            "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0",
            "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "0",
            "PATHB_LADDER_MIN_HOLD_SEC": "0",
            "PATHB_LADDER_TIER2_PCT": "2.0",
            "PATHB_LADDER_TIER2_FLOOR_BUFFER_PCT": "0.005",
            "PATHB_PROFIT_REVIEW_LADDER_BUFFER_PCT": "0.003",
        }
        # entry=100, peak=3% → tier2, floor=100*1.005=100.5, threshold=100.5*1.003≈100.80
        # stale scan price=103.0 → gate 통과처럼 보임
        # verified price=100.4 → floor threshold(100.80) 아래 → aborted
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env, clear=False):
            runtime, _bot, _store, plan = _runtime_with_plan(tmp, market="KR")
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: 100.4
            )
            pos = {
                "ticker": "005930",
                "entry": 100.0,
                "peak_pnl_pct": 3.0,
                "pathb_path_run_id": plan.path_run_id,
            }
            with patch("minority_report.hold_advisor.ask") as ask:
                result = runtime._maybe_trigger_profit_protection_review(plan, pos, 103.0, "KR")

        self.assertFalse(result["triggered"])
        self.assertEqual(result["reason"], "too_close_to_ladder_floor")
        ask.assert_not_called()

    def test_profit_protection_review_uses_verified_price_when_scan_price_stale(self) -> None:
        """verified 가격이 scan 가격보다 낮지만 여전히 floor 위이면 그 가격으로 hold_advisor 호출."""
        env = {
            "PATHB_PROFIT_REVIEW_ENABLED": "true",
            "PATHB_PROFIT_REVIEW_TIMEOUT_SEC": "0",
            "PATHB_PROFIT_REVIEW_COOLDOWN_SEC": "0",
            "PATHB_LADDER_MIN_HOLD_SEC": "0",
            "PATHB_LADDER_TIER2_PCT": "2.0",
            "PATHB_LADDER_TIER2_FLOOR_BUFFER_PCT": "0.005",
            "PATHB_PROFIT_REVIEW_LADDER_BUFFER_PCT": "0.003",
        }
        # entry=100, peak=3% → floor≈100.5, threshold≈100.80
        # stale scan=103.0, verified=102.0 → 102.0 > 100.80 → gate 통과, verified 가격으로 hold_advisor 호출
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", env, clear=False):
            runtime, _bot, _store, plan = _runtime_with_plan(tmp, market="KR")
            runtime._market_open_for_advisor = lambda market: True  # type: ignore[method-assign]
            runtime._current_native_price_for_exit = (  # type: ignore[method-assign]
                lambda market, ticker, pos: 102.0
            )
            pos = {
                "ticker": "005930",
                "entry": 100.0,
                "peak_pnl_pct": 3.0,
                "pathb_path_run_id": plan.path_run_id,
            }
            advice = {"action": "HOLD", "confidence": 0.6, "reason": "above floor"}
            with patch("minority_report.hold_advisor.ask", return_value=advice) as ask:
                result = runtime._maybe_trigger_profit_protection_review(plan, pos, 103.0, "KR")

        self.assertTrue(result["triggered"])
        ask.assert_called_once()
        # hold_advisor에 넘긴 pos의 current_price가 verified 가격(102.0)인지 확인
        call_pos = ask.call_args[0][0]
        self.assertAlmostEqual(float(call_pos.get("current_price", 0)), 102.0, places=1)

    def test_trading_bot_bridge_uses_non_trailing_pathb_position(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0
        bot.price_cache = {}
        bot.price_cache_raw = {"HALO": 71.16}
        pos = {
            "ticker": "HALO",
            "qty": 1,
            "display_avg_price": 70.34,
            "display_current_price": 71.16,
            "pathb_path_run_id": "run_halo",
            "trailing": False,
        }
        bot.risk = SimpleNamespace(positions=[pos])
        bot.pathb = SimpleNamespace(apply_general_hold_advice_policy=Mock(return_value={"updated": True}))
        advice = {"action": "HOLD", "protective_stop": 70.80}

        result = bot._apply_pathb_hold_advice_bridge(pos, "US", advice)

        self.assertTrue(result["updated"])
        bot.pathb.apply_general_hold_advice_policy.assert_called_once()
        args = bot.pathb.apply_general_hold_advice_policy.call_args.args
        self.assertIs(args[0], pos)
        self.assertEqual(args[1], "US")
        self.assertEqual(args[2], advice)
        self.assertAlmostEqual(args[3], 71.16)

    def test_trading_bot_bridge_accepts_sell_advice_for_pathb_position(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.usd_krw_rate = 1350.0
        bot.price_cache = {}
        bot.price_cache_raw = {"HALO": 71.16}
        pos = {
            "ticker": "HALO",
            "qty": 1,
            "display_avg_price": 70.34,
            "display_current_price": 71.16,
            "pathb_path_run_id": "run_halo",
        }
        bot.risk = SimpleNamespace(positions=[pos])
        bot.pathb = SimpleNamespace(apply_general_hold_advice_policy=Mock(return_value={"updated": True}))
        advice = {"action": "SELL", "reason": "exit"}

        result = bot._apply_pathb_hold_advice_bridge(pos, "US", advice)

        self.assertTrue(result["updated"])
        bot.pathb.apply_general_hold_advice_policy.assert_called_once()


if __name__ == "__main__":
    unittest.main()
