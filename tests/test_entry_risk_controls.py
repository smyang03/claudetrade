from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from execution.safety_gate import SafetyContext, SafetyGate
import risk_manager as risk_module
from risk_manager import RiskManager
from runtime.v2_lifecycle_runtime import V2LifecycleRuntime
from trading_bot import TradingBot


def _safety_ctx(**overrides) -> SafetyContext:
    base = {
        "market": "US",
        "runtime_mode": "live",
        "ticker": "AAPL",
        "price_krw": 200_000,
        "qty": 1,
        "order_cost_krw": 200_000,
        "cash_krw": 1_000_000,
        "min_order_krw": 0,
        "market_open": True,
        "broker_trust_level": "trusted",
    }
    base.update(overrides)
    return SafetyContext(**base)


class EntryRiskControlTests(unittest.TestCase):
    def test_v2_daily_cap_can_be_split_by_market(self) -> None:
        runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)

        with patch.dict(
            os.environ,
            {"KR_DAILY_ENTRY_CAP": "2", "US_DAILY_ENTRY_CAP": "1", "V2_MAX_DAILY_ENTRIES": "9"},
            clear=False,
        ):
            self.assertEqual(runtime.max_daily_entries("KR"), 2)
            self.assertEqual(runtime.max_daily_entries("US"), 1)
            self.assertEqual(runtime.max_daily_entries("JP"), 9)

    def test_v2_daily_cap_defaults_to_two_for_supported_markets(self) -> None:
        runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)

        with patch.dict(
            os.environ,
            {
                "KR_DAILY_ENTRY_CAP": "",
                "US_DAILY_ENTRY_CAP": "",
                "V2_MAX_DAILY_ENTRIES": "",
                "MAX_DAILY_ENTRIES": "",
            },
            clear=False,
        ):
            self.assertEqual(runtime.max_daily_entries("KR"), 2)
            self.assertEqual(runtime.max_daily_entries("US"), 2)
            self.assertIsNone(runtime.max_daily_entries("JP"))

    def test_market_risk_shadow_keeps_market_scoped_snapshot(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(
            cash=1_000_000,
            max_order_krw=200_000,
            positions=[
                {"ticker": "005930", "qty": 1, "entry": 70_000},
                {"ticker": "AAPL", "qty": 1, "entry": 200_000},
            ],
            trade_log=[
                {"side": "buy", "ticker": "005930"},
                {"side": "buy", "ticker": "AAPL"},
            ],
            all_trade_log=[
                {"side": "buy", "ticker": "005930"},
                {"side": "buy", "ticker": "AAPL"},
            ],
            halted=False,
            halt_reason="",
            session_start_equity=1_000_000,
        )
        bot._market_realized_pnl_krw = lambda market: -10_000 if market == "KR" else 5_000  # type: ignore[method-assign]
        bot._market_session_start_equity_krw = lambda market: 500_000  # type: ignore[method-assign]
        bot._market_equity_reference_context = lambda market: {"cash_krw": 300_000 if market == "KR" else 700_000}  # type: ignore[method-assign]

        with patch.dict(os.environ, {"ENABLE_MARKET_RISK_SHADOW": "true"}, clear=False):
            TradingBot._init_market_risk_shadow(
                bot,
                init_cash_krw=500_000,
                us_cash_init_krw=500_000,
                max_order_krw=200_000,
            )

        kr_status = TradingBot._market_risk_shadow_status(bot, "KR")
        us_status = TradingBot._market_risk_shadow_status(bot, "US")
        self.assertTrue(kr_status["enabled"])
        self.assertEqual(kr_status["risk_source"], "market_shadow")
        self.assertEqual(kr_status["position_count"], 1)
        self.assertEqual(us_status["position_count"], 1)
        self.assertEqual(kr_status["daily_pnl_krw"], -10000)
        self.assertEqual(us_status["daily_pnl_krw"], 5000)

    def test_us_broker_sync_quarantine_blocks_degraded_safety_context(self) -> None:
        with patch.dict(os.environ, {"US_BROKER_SYNC_QUARANTINE_ENABLED": "true"}, clear=False):
            decision = SafetyGate().evaluate(_safety_ctx(broker_trust_level="degraded"))

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "BROKER_SYNC_QUARANTINE")
        self.assertEqual(decision.details["broker_trust_level"], "degraded")
        self.assertEqual(decision.details["policy_name"], "us_broker_trust_quarantine")

    def test_trading_bot_new_buy_gate_blocks_us_broker_quarantine(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.v2 = None
        bot.v2_order_unknown = None
        bot.risk = SimpleNamespace(positions=[])
        bot._broker_state = {"US": {"trust_level": "degraded"}}
        bot._is_order_allowed_now = lambda market: True  # type: ignore[method-assign]
        bot._in_entry_blackout = lambda market: False  # type: ignore[method-assign]
        bot._daily_stop_cluster_state = lambda market, ticker="": {"blocked": False}  # type: ignore[method-assign]
        bot._analyst_new_buy_block_state = lambda market: {"blocked": False}  # type: ignore[method-assign]
        bot._v2_order_unknown_block_state = lambda market, ticker: {"blocked": False}  # type: ignore[method-assign]

        with patch.dict(os.environ, {"US_BROKER_SYNC_QUARANTINE_ENABLED": "true"}, clear=False):
            state = bot._new_buy_block_state("US", "AAPL", "sector_play")

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "BROKER_SYNC_QUARANTINE")
        self.assertEqual(state["details"]["broker_trust_level"], "degraded")
        self.assertEqual(state["details"]["policy_name"], "us_broker_trust_quarantine")

    def test_plan_a_broker_state_reason_uses_canonical_quarantine_code(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(positions=[])
        bot._broker_state = {"US": {"trust_level": "degraded"}}

        with patch.dict(os.environ, {"US_BROKER_SYNC_QUARANTINE_ENABLED": "true"}, clear=False):
            ok, reason = bot._entry_allowed_by_broker_state("US")

        self.assertFalse(ok)
        self.assertEqual(reason, "BROKER_SYNC_QUARANTINE")

    def test_plana_mfe_breakeven_creates_exit_candidate(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "005930",
                "entry": 10_000.0,
                "qty": 1,
                "current_price": 10_005.0,
                "strategy": "momentum",
                "tp": 11_000.0,
                "sl": 9_500.0,
                "peak_pnl_pct": 3.0,
                "trough_pnl_pct": 0.0,
            }
        ]

        with patch.dict(
            os.environ,
            {
                "PLANA_MFE_BREAKEVEN_ENABLED": "true",
                "PLANA_MFE_BREAKEVEN_TRIGGER_PCT": "2.5",
                "PLANA_MFE_BREAKEVEN_BUFFER_PCT": "0.001",
            },
            clear=False,
        ):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "mfe_breakeven")
        self.assertAlmostEqual(candidates[0]["mfe_breakeven_price"], 10_010.0)
        self.assertEqual(candidates[0]["effective_stop_price"], candidates[0]["mfe_breakeven_price"])

    def test_plana_mfe_breakeven_does_not_override_loss_cap(self) -> None:
        with patch.dict(risk_module.HARD_RULES, {"max_single_loss_pct": -2.0}):
            risk = RiskManager(init_cash=1_000_000)
            risk.reset_daily_state(override_base=1_000_000)
            risk.positions = [
                {
                    "ticker": "005930",
                    "entry": 10_000.0,
                    "qty": 1,
                    "current_price": 9_700.0,
                    "strategy": "momentum",
                    "tp": 11_000.0,
                    "sl": 9_500.0,
                    "peak_pnl_pct": 3.0,
                    "trough_pnl_pct": -3.0,
                }
            ]

            with patch.dict(
                os.environ,
                {
                    "PLANA_MFE_BREAKEVEN_ENABLED": "true",
                    "PLANA_MFE_BREAKEVEN_TRIGGER_PCT": "2.5",
                    "PLANA_MFE_BREAKEVEN_BUFFER_PCT": "0.001",
                },
                clear=False,
            ):
                candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "loss_cap")

    def test_plana_mfe_breakeven_uses_avg_price_when_entry_missing(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "005930",
                "entry": 0.0,
                "avg_price": 10_000.0,
                "qty": 1,
                "current_price": 10_005.0,
                "strategy": "momentum",
                "tp": 11_000.0,
                "sl": 9_500.0,
                "peak_pnl_pct": 3.0,
                "trough_pnl_pct": 0.0,
            }
        ]

        with patch.dict(
            os.environ,
            {
                "PLANA_MFE_BREAKEVEN_ENABLED": "true",
                "PLANA_MFE_BREAKEVEN_TRIGGER_PCT": "2.5",
                "PLANA_MFE_BREAKEVEN_BUFFER_PCT": "0.001",
            },
            clear=False,
        ):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "mfe_breakeven")
        self.assertAlmostEqual(candidates[0]["mfe_breakeven_price"], 10_010.0)

    def test_plana_mfe_breakeven_skips_pathb_position_without_run_id(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "005930",
                "entry": 10_000.0,
                "qty": 1,
                "current_price": 10_005.0,
                "strategy": "claude_price",
                "path_type": "claude_price",
                "tp": 11_000.0,
                "sl": 9_500.0,
                "peak_pnl_pct": 3.0,
                "trough_pnl_pct": 0.0,
            }
        ]

        with patch.dict(
            os.environ,
            {
                "PLANA_MFE_BREAKEVEN_ENABLED": "true",
                "PLANA_MFE_BREAKEVEN_TRIGGER_PCT": "2.5",
                "PLANA_MFE_BREAKEVEN_BUFFER_PCT": "0.001",
            },
            clear=False,
        ):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates, [])

    def test_plana_mfe_breakeven_still_manages_legacy_strategy_claude_price_without_path_type(self) -> None:
        risk = RiskManager(init_cash=1_000_000)
        risk.reset_daily_state(override_base=1_000_000)
        risk.positions = [
            {
                "ticker": "005930",
                "entry": 10_000.0,
                "qty": 1,
                "current_price": 10_005.0,
                "strategy": "claude_price",
                "tp": 11_000.0,
                "sl": 9_500.0,
                "peak_pnl_pct": 3.0,
                "trough_pnl_pct": 0.0,
            }
        ]

        with patch.dict(
            os.environ,
            {
                "PLANA_MFE_BREAKEVEN_ENABLED": "true",
                "PLANA_MFE_BREAKEVEN_TRIGGER_PCT": "2.5",
                "PLANA_MFE_BREAKEVEN_BUFFER_PCT": "0.001",
            },
            clear=False,
        ):
            candidates = risk.get_exit_candidates()

        self.assertEqual(candidates[0]["reason"], "mfe_breakeven")


if __name__ == "__main__":
    unittest.main()
