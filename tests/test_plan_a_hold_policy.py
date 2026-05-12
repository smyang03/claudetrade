from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import risk_manager as risk_module
from minority_report import hold_advisor
from risk_manager import RiskManager
from trading_bot import TradingBot


def _future(minutes: int = 10) -> str:
    return (datetime.now(risk_module.KST) + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _past(minutes: int = 10) -> str:
    return (datetime.now(risk_module.KST) - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _kr_position(**overrides):
    pos = {
        "ticker": "010170",
        "entry": 100.0,
        "qty": 1,
        "current_price": 100.5,
        "strategy": "momentum",
        "tp": 110.0,
        "sl": 94.0,
        "held_days": 0,
        "max_hold": 3,
        "peak_pnl_pct": 3.0,
        "trough_pnl_pct": 0.0,
        "trailing": True,
        "trail_sl": 101.0,
    }
    pos.update(overrides)
    return pos


def _us_position(**overrides):
    pos = {
        "ticker": "QCOM",
        "entry": 235_804.5,
        "qty": 1,
        "current_price": 237_735.0,
        "display_currency": "USD",
        "display_avg_price": 174.67,
        "display_current_price": 176.1,
        "strategy": "momentum",
        "tp": 250_000.0,
        "sl": 226_000.0,
        "tp_pct": 0.06,
        "sl_pct": 0.04,
        "held_days": 0,
        "max_hold": 3,
        "peak_pnl_pct": 3.0,
        "trough_pnl_pct": 0.0,
    }
    pos.update(overrides)
    return pos


def _policy(mode: str = "profit_pullback", **overrides):
    policy = {
        "version": 1,
        "status": "active",
        "source": "hold_advisor",
        "mode": mode,
        "created_at": datetime.now(risk_module.KST).isoformat(timespec="seconds"),
        "valid_until": _future(10),
        "reask_after_at": _future(5),
        "signal_reason": "trail_stop",
        "policy_currency": "KRW",
        "created_price": 100.5,
        "peak_price": 103.0,
        "entry_price": 100.0,
        "original_stop_loss": 94.0,
        "original_take_profit": 110.0,
        "revised_sell_target": 110.0,
        "protective_stop": 99.5,
        "hard_stop": 0.0,
        "recover_above": 0.0,
        "recovery_watch_min": 0,
        "reask_drawdown_from_peak_pct": 0.0,
        "recheck_count": 0,
        "max_rechecks": 2,
        "last_recheck_at": "",
        "last_recheck_reason": "",
        "next_review_min": 5,
        "invalid_if": "breaks protective stop",
        "confidence": 0.7,
        "reason": "protect profit",
    }
    policy.update(overrides)
    return policy


def _bot() -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    pos = _us_position(display_current_price=106.0, display_avg_price=100.0, current_price=106.0, entry=100.0)
    bot.risk = SimpleNamespace(positions=[pos])
    bot.price_cache_raw = {"QCOM": 106.0}
    bot.price_cache = {"QCOM": 106.0}
    bot.pending_orders = []
    bot.today_judgment = {"digest_prompt": ""}
    bot.usd_krw_rate = 1350.0
    bot._sell_fail_at = {}
    bot._sell_fail_meta = {}
    bot._SELL_FAIL_COOLDOWN_SEC = 60
    bot._build_intraday_context = lambda market: ""  # type: ignore[method-assign]
    bot._advisor_pos = lambda pos, market: pos  # type: ignore[method-assign]
    bot._record_decision_event = Mock()  # type: ignore[method-assign]
    bot._save_positions = Mock()  # type: ignore[method-assign]
    bot._write_funnel_event = Mock()  # type: ignore[method-assign]
    bot._note_sell_failure = Mock()  # type: ignore[method-assign]
    bot._compute_order_price = lambda side, market, price: float(price)  # type: ignore[method-assign]
    bot._token_for_market = lambda market: "token"  # type: ignore[method-assign]
    bot._has_pending_sell_confirmation = lambda ticker, market: False  # type: ignore[method-assign]
    bot._reconcile_pending_sell_confirmations = Mock()  # type: ignore[method-assign]
    return bot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object]):
        self.values = values

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


class PlanAHoldPolicyRiskTests(unittest.TestCase):
    def test_profit_pullback_policy_inside_suppresses_trail_stop(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(auto_sell_policy=_policy())]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce", "US_PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])

    def test_policy_protective_stop_creates_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=99.4, auto_sell_policy=_policy())]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce", "US_PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_protective_stop")
        self.assertEqual(candidates[0]["effective_stop_price"], 99.5)
        self.assertEqual(candidates[0]["auto_sell_policy_mode"], "profit_pullback")

    def test_mfe_breakeven_is_not_suppressed_by_active_policy(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=100.05, auto_sell_policy=_policy())]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce", "US_PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "mfe_breakeven")

    def test_expired_policy_falls_back_to_existing_exit_logic(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        pos = _kr_position(auto_sell_policy=_policy(valid_until=_past(1), reask_after_at=_past(1)))
        risk.positions = [pos]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce", "US_PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(pos["auto_sell_policy"]["status"], "expired")
        self.assertEqual(candidates[0]["reason"], "trail_stop")

    def test_us_policy_uses_native_currency_prices(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="US")
        risk.reset_daily_state(override_base=1_000_000)
        pos = _us_position(
            display_current_price=176.1,
            current_price=237_735.0,
            auto_sell_policy=_policy(
                policy_currency="USD",
                created_price=178.79,
                peak_price=180.0,
                entry_price=174.67,
                protective_stop=176.2,
                revised_sell_target=190.0,
                original_stop_loss=167.68,
            ),
        )
        risk.positions = [pos]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce", "US_PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_protective_stop")
        self.assertEqual(candidates[0]["policy_currency"], "USD")
        self.assertAlmostEqual(candidates[0]["effective_stop_price"], 176.2)
        self.assertGreater(candidates[0]["exit_price"], 1000.0)

    def test_pending_sell_suppresses_policy_evaluation(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=99.4,
                sell_confirmation_pending=True,
                auto_sell_policy=_policy(),
            )
        ]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])

    def test_resolved_pending_sell_allows_future_exit_candidates(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=99.4,
                pending_sell_status="resolved",
                pending_sell_order_no="hist-123",
            )
        ]

        candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "trail_stop")

    def test_policy_recheck_limit_creates_sell_without_review(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=102.0,
                trail_sl=101.0,
                auto_sell_policy=_policy(
                    reask_after_at=_past(1),
                    recheck_count=2,
                    max_rechecks=2,
                ),
            )
        ]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_recheck_limit_sell")
        self.assertEqual(candidates[0]["auto_sell_policy_recheck_reason"], "reask_after_due")

    def test_target_extension_revised_target_creates_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=111.0,
                auto_sell_policy=_policy(
                    mode="target_extension",
                    protective_stop=99.5,
                    revised_sell_target=110.0,
                ),
            )
        ]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_revised_target")
        self.assertEqual(candidates[0]["auto_sell_policy_mode"], "target_extension")

    def test_stop_recovery_hard_stop_creates_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            _kr_position(
                current_price=98.8,
                auto_sell_policy=_policy(
                    mode="stop_recovery",
                    protective_stop=0.0,
                    revised_sell_target=0.0,
                    hard_stop=99.0,
                    recover_above=102.0,
                ),
            )
        ]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_hard_stop")
        self.assertEqual(candidates[0]["effective_stop_price"], 99.0)

    def test_stop_recovery_recovered_falls_back_without_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        pos = _kr_position(
            current_price=102.5,
            auto_sell_policy=_policy(
                mode="stop_recovery",
                protective_stop=0.0,
                revised_sell_target=0.0,
                hard_stop=98.0,
                recover_above=102.0,
            ),
        )
        risk.positions = [pos]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])
        self.assertEqual(pos["auto_sell_policy"]["status"], "recovered")

    def test_shadow_mode_does_not_enforce_active_policy(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=99.4, auto_sell_policy=_policy())]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "shadow", "KR_PLANA_HOLD_POLICY_MODE": "shadow"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "trail_stop")

    def test_market_specific_policy_mode_enforces_kr_when_global_shadow(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=99.4, auto_sell_policy=_policy())]

        with patch.dict(
            os.environ,
            {
                "PLANA_HOLD_POLICY_MODE": "shadow",
                "KR_PLANA_HOLD_POLICY_MODE": "enforce",
            },
        ):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_protective_stop")

    def test_risk_manager_uses_runtime_config_policy_mode_for_live_consistency(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="KR")
        risk.runtime_config = _RuntimeConfig({"KR_PLANA_HOLD_POLICY_MODE": "enforce"})
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [_kr_position(current_price=99.4, auto_sell_policy=_policy())]

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "shadow"}):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "policy_protective_stop")


class PlanAHoldPolicyBotTests(unittest.TestCase):
    def test_hold_advisor_coerces_hold_mode(self) -> None:
        vote = hold_advisor._coerce_vote(
            {"action": "HOLD", "hold_mode": "profit_pullback", "confidence": 0.8},
            decision_stage="AUTO_SELL_REVIEW",
        )

        self.assertEqual(vote["hold_mode"], "profit_pullback")

    def test_hold_review_stores_valid_policy(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "trail_stop"}

        advice = {
            "action": "HOLD",
            "hold_mode": "profit_pullback",
            "confidence": 0.8,
            "protective_stop": 104.0,
            "revised_sell_target": 112.0,
            "next_review_min": 5,
            "invalid_if": "loses 104",
            "reason": "trend intact",
        }
        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}), patch(
            "minority_report.hold_advisor.ask", return_value=advice
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertEqual(pos["auto_sell_policy"]["mode"], "profit_pullback")
        self.assertEqual(pos["auto_sell_policy"]["policy_currency"], "USD")
        self.assertEqual(pos["auto_sell_policy"]["protective_stop"], 104.0)
        self.assertTrue(pos["auto_sell_policy_recreate_block_until"])

    def test_missing_hold_mode_rejects_policy_but_keeps_hold_cooldown(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "trail_stop"}

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "confidence": 0.8,
                "protective_stop": 104.0,
                "next_review_min": 5,
                "reason": "missing mode",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertEqual(pos["auto_sell_policy_reject_reason"], "hold_mode_missing")
        self.assertNotIn("auto_sell_policy", pos)
        self.assertTrue(pos["auto_sell_review_cooldown_until"])

    def test_policy_recheck_invalid_new_policy_keeps_existing_and_increments_count(self) -> None:
        bot = _bot()
        existing = _policy(
            policy_currency="USD",
            created_price=106.0,
            peak_price=108.0,
            entry_price=100.0,
            original_stop_loss=99.0,
            revised_sell_target=112.0,
            protective_stop=104.0,
            recheck_count=1,
            max_rechecks=2,
        )
        bot.risk.positions[0]["auto_sell_policy"] = existing
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "policy_recheck"}

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "confidence": 0.8,
                "next_review_min": 5,
                "reason": "still hold but invalid policy",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "policy_recheck", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertEqual(pos["auto_sell_policy"]["protective_stop"], 104.0)
        self.assertEqual(pos["auto_sell_policy"]["recheck_count"], 2)
        self.assertEqual(pos["auto_sell_policy_last_reject_reason"], "hold_mode_missing")

    def test_policy_recreate_cooldown_blocks_new_policy_without_price_move(self) -> None:
        bot = _bot()
        bot.risk.positions[0]["auto_sell_policy_recreate_block_until"] = _future(3)
        bot.risk.positions[0]["auto_sell_policy_last_created_price"] = 106.0
        cand = {**bot.risk.positions[0], "exit_price": 106.1, "reason": "trail_stop"}

        with patch.dict(
            os.environ,
            {
                "PLANA_HOLD_POLICY_MODE": "enforce",
                "PLANA_HOLD_POLICY_RECREATE_MIN_PRICE_MOVE_PCT": "0.5",
            },
        ), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "hold_mode": "profit_pullback",
                "confidence": 0.8,
                "protective_stop": 104.0,
                "revised_sell_target": 112.0,
                "next_review_min": 5,
                "reason": "valid but too soon",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.1)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertNotIn("auto_sell_policy", pos)
        self.assertEqual(pos["auto_sell_policy_reject_reason"], "policy_recreate_cooldown")

    def test_loss_deferral_is_rejected_in_enforce_path(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "trail_stop"}

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "hold_mode": "loss_deferral",
                "confidence": 0.8,
                "next_review_min": 5,
                "reason": "defer loss",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertNotIn("auto_sell_policy", pos)
        self.assertEqual(pos["auto_sell_policy_reject_reason"], "loss_deferral_not_enforceable")

    def test_protective_stop_at_or_above_current_is_rejected(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "trail_stop"}

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "enforce"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "hold_mode": "profit_pullback",
                "confidence": 0.8,
                "protective_stop": 106.0,
                "revised_sell_target": 112.0,
                "next_review_min": 5,
                "reason": "bad stop",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertNotIn("auto_sell_policy", pos)
        self.assertEqual(pos["auto_sell_policy_reject_reason"], "protective_stop_not_below_current")

    def test_policy_mode_off_keeps_hold_without_creating_policy(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 106.0, "reason": "trail_stop"}

        with patch.dict(os.environ, {"PLANA_HOLD_POLICY_MODE": "off", "US_PLANA_HOLD_POLICY_MODE": "off"}), patch(
            "minority_report.hold_advisor.ask",
            return_value={
                "action": "HOLD",
                "hold_mode": "profit_pullback",
                "confidence": 0.8,
                "protective_stop": 104.0,
                "revised_sell_target": 112.0,
                "next_review_min": 5,
                "reason": "valid but off",
            },
        ):
            review = bot._run_auto_sell_review_gate(cand, "US", "trail_stop", current_native=106.0)

        pos = bot.risk.positions[0]
        self.assertFalse(review["allowed"])
        self.assertNotIn("auto_sell_policy", pos)
        self.assertEqual(review["auto_sell_policy_mode"], "off")

    def test_policy_stop_reaches_broker_without_hold_advisor_recheck(self) -> None:
        bot = _bot()
        cand = {**bot.risk.positions[0], "exit_price": 104.0, "reason": "policy_protective_stop"}
        bot.price_cache_raw["QCOM"] = 104.0

        with patch("minority_report.hold_advisor.ask") as advisor, patch(
            "trading_bot.precheck_order", return_value={"ok": False, "msg": "test stop"}
        ) as precheck:
            ok = bot._execute_sell(cand, "US", reason="policy_protective_stop")

        self.assertFalse(ok)
        advisor.assert_not_called()
        precheck.assert_called_once()

    def test_policy_stop_like_registration_depends_on_realized_pnl(self) -> None:
        bot = _bot()

        self.assertFalse(bot._closed_decision_is_stop_like({"reason": "policy_protective_stop", "pnl_pct": 1.0}))
        self.assertTrue(bot._closed_decision_is_stop_like({"reason": "policy_protective_stop", "pnl_pct": -0.1}))
        self.assertTrue(bot._closed_decision_is_stop_like({"reason": "policy_hard_stop", "pnl_pct": 1.0}))


if __name__ == "__main__":
    unittest.main()
