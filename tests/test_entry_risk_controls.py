from __future__ import annotations

import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from execution.safety_gate import SafetyContext, SafetyGate
import risk_manager as risk_module
from risk_manager import RiskManager
from runtime.market_resolver import infer_ticker_market
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
    def test_kr_one_share_over_budget_allows_when_cash_covers_price(self) -> None:
        bot = TradingBot.__new__(TradingBot)

        with patch.dict(
            os.environ,
            {"KR_ALLOW_ONE_SHARE_OVER_BUDGET": "true", "KR_ONE_SHARE_OVER_BUDGET_MAX_KRW": ""},
            clear=False,
        ):
            decision = TradingBot._one_share_over_budget_adjustment(
                bot,
                market="KR",
                price_krw=1_416_000,
                qty=0,
                order_budget_krw=110_000,
                available_budget_krw=3_121_305,
                cash_krw=3_121_305,
                strategy="kr_sector_play",
            )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["adjusted_qty"], 1)
        self.assertEqual(decision["adjusted_order_cost_krw"], 1_416_000)
        self.assertGreater(decision["oversize_ratio"], 1.0)

    def test_kr_one_share_over_budget_still_requires_cash(self) -> None:
        bot = TradingBot.__new__(TradingBot)

        decision = TradingBot._one_share_over_budget_adjustment(
            bot,
            market="KR",
            price_krw=1_416_000,
            qty=0,
            order_budget_krw=110_000,
            available_budget_krw=3_121_305,
            cash_krw=500_000,
            strategy="kr_sector_play",
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "insufficient_cash")

    def test_us_one_share_after_early_gate_allows_pre_gate_budget_case(self) -> None:
        decision = TradingBot._plan_a_us_one_share_after_gate_adjustment(
            market="US",
            price_krw=310_000,
            qty=0,
            original_budget_krw=450_000,
            effective_budget_krw=225_000,
            available_budget_krw=1_000_000,
            cash_krw=1_000_000,
            early_gate_applied=True,
        )

        self.assertTrue(decision["allowed"])
        self.assertEqual(decision["adjusted_qty"], 1)
        self.assertEqual(decision["reason"], "one_share_allowed_after_early_gate")
        self.assertTrue(decision["can_buy_1_share"])

    def test_us_one_share_after_early_gate_blocks_above_pre_gate_budget(self) -> None:
        decision = TradingBot._plan_a_us_one_share_after_gate_adjustment(
            market="US",
            price_krw=790_000,
            qty=0,
            original_budget_krw=450_000,
            effective_budget_krw=225_000,
            available_budget_krw=1_000_000,
            cash_krw=1_000_000,
            early_gate_applied=True,
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "HIGH_PRICE_BUDGET_BLOCK")
        self.assertFalse(decision["can_buy_1_share"])

    def test_us_one_share_after_early_gate_still_requires_cash(self) -> None:
        decision = TradingBot._plan_a_us_one_share_after_gate_adjustment(
            market="US",
            price_krw=310_000,
            qty=0,
            original_budget_krw=450_000,
            effective_budget_krw=225_000,
            available_budget_krw=1_000_000,
            cash_krw=200_000,
            early_gate_applied=True,
        )

        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["reason"], "insufficient_cash")

    def test_kr_sector_play_confirmation_blocks_missing_minute_evidence(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_post_open_features_by_ticker = {"KR": {}, "US": {}}

        with patch.dict(os.environ, {"KR_SECTOR_PLAY_CONFIRMATION_GATE_ENABLED": "true"}, clear=False):
            state = bot._kr_sector_play_confirmation_gate("003670", 246_500, {"etf": "305720", "etf_chg": 0.61})

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "kr_sector_play_intraday_unconfirmed")

    def test_kr_sector_play_confirmation_allows_minute_confirmed_strength(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "003670": {
                    "data_quality": "minute_complete",
                    "current_price": 246_500,
                    "ret_3m_pct": 0.4,
                    "ret_5m_pct": 0.8,
                    "opening_range_high": 245_000,
                    "opening_range_break": True,
                    "vwap": 245_800,
                    "vwap_distance_pct": 0.3,
                    "volume_ratio_open": 1.3,
                    "momentum_state": "continuation",
                    "sector_relative_strength_pct": 0.2,
                }
            },
            "US": {},
        }

        with patch.dict(os.environ, {"KR_SECTOR_PLAY_CONFIRMATION_GATE_ENABLED": "true"}, clear=False):
            state = bot._kr_sector_play_confirmation_gate("003670", 246_500, {"etf": "305720", "etf_chg": 0.61})

        self.assertTrue(state["allowed"])
        self.assertEqual(state["reason"], "kr_sector_play_confirmed")
        self.assertTrue(state["confirmation_checks"]["volume_ok"])

    def test_kr_sector_play_confirmation_blocks_intraday_weakness(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "003670": {
                    "data_quality": "minute_complete",
                    "current_price": 240_500,
                    "ret_3m_pct": -0.6,
                    "opening_range_high": 246_000,
                    "vwap": 244_000,
                    "vwap_distance_pct": -1.4,
                    "volume_ratio_open": 1.4,
                    "momentum_state": "fade",
                }
            },
            "US": {},
        }

        with patch.dict(os.environ, {"KR_SECTOR_PLAY_CONFIRMATION_GATE_ENABLED": "true"}, clear=False):
            state = bot._kr_sector_play_confirmation_gate("003670", 240_500, {"etf": "305720", "etf_chg": 0.61})

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "kr_sector_play_intraday_weak")

    def test_kr_sector_play_confirmation_blocks_missing_volume(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "003670": {
                    "data_quality": "minute_complete",
                    "current_price": 246_500,
                    "ret_3m_pct": 0.4,
                    "opening_range_break": True,
                    "vwap_distance_pct": 0.3,
                    "momentum_state": "continuation",
                }
            },
            "US": {},
        }

        with patch.dict(os.environ, {"KR_SECTOR_PLAY_CONFIRMATION_GATE_ENABLED": "true"}, clear=False):
            state = bot._kr_sector_play_confirmation_gate("003670", 246_500, {"etf": "305720", "etf_chg": 0.61})

        self.assertFalse(state["allowed"])
        self.assertEqual(state["reason"], "kr_sector_play_volume_unconfirmed")

    def test_kr_sector_play_confirmation_detail_includes_skip_evidence(self) -> None:
        detail = TradingBot._kr_sector_play_confirmation_detail(
            {
                "data_quality": "minute_complete",
                "feature_present": True,
                "confirmation_checks": {
                    "volume_ok": False,
                    "opening_range_break": True,
                    "vwap_reclaim": False,
                    "momentum_ok": True,
                    "relative_ok": True,
                },
            }
        )

        for token in (
            "sector_play_gate:",
            "data_quality=minute_complete",
            "feature_present=True",
            "volume_ok=False",
            "opening_range_break=True",
            "vwap_reclaim=False",
            "momentum_ok=True",
            "relative_ok=True",
        ):
            self.assertIn(token, detail)

    def test_kr_sector_play_confirmation_can_be_disabled(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot._last_post_open_features_by_ticker = {"KR": {}, "US": {}}

        with patch.dict(os.environ, {"KR_SECTOR_PLAY_CONFIRMATION_GATE_ENABLED": "false"}, clear=False):
            state = bot._kr_sector_play_confirmation_gate("003670", 246_500, {"etf": "305720", "etf_chg": 0.61})

        self.assertTrue(state["allowed"])
        self.assertFalse(state["enabled"])

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

    def test_v2_daily_entry_count_merges_patha_and_pathb_without_double_count(self) -> None:
        class _PathB:
            def daily_entry_run_ids(self, market: str) -> set[str]:
                self.seen_market = market
                return {"run_pending", "run_orphan"} if market == "KR" else set()

        runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)
        runtime.bot = SimpleNamespace(
            risk=SimpleNamespace(
                trade_log=[
                    {"side": "buy", "ticker": "005930"},
                    {"side": "buy", "ticker": "AAPL"},
                ]
            ),
            pending_orders=[
                {"market": "KR", "ticker": "078150", "pathb_path_run_id": "run_pending"},
                {"market": "US", "ticker": "MSFT"},
            ],
            pathb=_PathB(),
            _ticker_market=lambda ticker: infer_ticker_market(ticker, unknown="KR"),
        )

        self.assertEqual(runtime.daily_entry_count("KR"), 3)
        self.assertEqual(runtime.daily_entry_count("US"), 2)

    def test_v2_ensure_decision_id_records_fallback_metrics_in_selection_meta(self) -> None:
        class _Store:
            def find_decision(self, **kwargs):
                return None

        class _Registry:
            store = _Store()

            def register_trade_ready(self, **kwargs):
                self.kwargs = kwargs
                return "decision_012610"

        bot = SimpleNamespace(
            _mode="live",
            selection_meta={"KR": {}},
            _current_session_date_str=lambda market: "2026-05-12",
        )
        runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)
        runtime.bot = bot
        runtime.enabled = True
        runtime.registry = _Registry()
        runtime.decision_ids = {"KR": {}, "US": {}}
        runtime.brain_snapshot_ids = {"KR": "brain_kr", "US": ""}
        runtime.brain_snapshot_store = None

        decision_id = runtime.ensure_decision_id(
            "KR",
            "012610",
            strategy_hint="momentum",
            payload={"registration_source": "execution_lifecycle_fallback"},
        )

        self.assertEqual(decision_id, "decision_012610")
        meta = bot.selection_meta["KR"]
        self.assertEqual(meta["_decision_id_fallback_count"], 1)
        self.assertEqual(meta["_decision_id_fallback_tickers"], ["012610"])
        self.assertEqual(meta["_decision_id_fallback_sources"], ["execution_lifecycle_fallback"])
        self.assertEqual(meta["v2_decision_ids"]["012610"], "decision_012610")

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
        self.assertEqual(kr_status["event_count"], 1)
        self.assertEqual(kr_status["entry_count"], 1)
        self.assertEqual(kr_status["closed_count"], 0)

    def test_us_broker_sync_quarantine_blocks_degraded_safety_context(self) -> None:
        with patch.dict(os.environ, {"US_BROKER_SYNC_QUARANTINE_ENABLED": "true"}, clear=False):
            decision = SafetyGate().evaluate(_safety_ctx(broker_trust_level="degraded"))

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "BROKER_SYNC_QUARANTINE")
        self.assertEqual(decision.details["broker_trust_level"], "degraded")
        self.assertEqual(decision.details["policy_name"], "us_broker_trust_quarantine")

    def test_safety_gate_uses_position_market_for_hyphenated_us_ticker(self) -> None:
        decision = SafetyGate().evaluate(
            _safety_ctx(
                ticker="BRK-B",
                positions=[{"ticker": "BRK-B", "market": "US", "qty": 1}],
            )
        )

        self.assertFalse(decision.passed)
        self.assertEqual(decision.reason_code, "ALREADY_HOLDING")

    def test_risk_manager_uses_position_market_for_hyphenated_us_ticker(self) -> None:
        risk = RiskManager(init_cash=1_000_000, market="US")
        risk.positions = [{"ticker": "BRK-B", "market": "US", "qty": 1, "current_price": 10.0}]

        with patch.dict(risk_module.HARD_RULES, {"max_pyramid": 1}, clear=False):
            ok, reason = risk.can_open("BRK-B", 10.0, market="US")

        self.assertFalse(ok)
        self.assertEqual(reason, "already holding")

    def test_trading_bot_market_position_count_uses_position_market(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(positions=[{"ticker": "BRK-B", "market": "US", "qty": 1}])

        self.assertEqual(TradingBot._market_position_count(bot, "US"), 1)
        self.assertEqual(TradingBot._market_position_count(bot, "KR"), 0)

    def test_trading_bot_positions_for_market_filters_display_positions(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.risk = SimpleNamespace(
            positions=[
                {"ticker": "005930", "market": "KR", "qty": 2},
                {"ticker": "EL", "market": "US", "qty": 1},
                {"ticker": "SOFI", "qty": 16, "display_currency": "USD"},
                {"ticker": "000660", "market": "KR", "qty": 0},
            ]
        )

        kr_positions = TradingBot._positions_for_market(bot, "KR")
        us_positions = TradingBot._positions_for_market(bot, "US")

        self.assertEqual([p["ticker"] for p in kr_positions], ["005930"])
        self.assertEqual([p["ticker"] for p in us_positions], ["EL", "SOFI"])

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
