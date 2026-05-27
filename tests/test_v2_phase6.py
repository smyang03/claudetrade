from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision.registry import DecisionRegistry
from interface import v2_ops_summary
from interface.v2_ops_summary import V2_DASHBOARD_TABS, V2_TELEGRAM_COMMANDS, build_v2_ops_summary
from interface.v2_telegram import handle_v2_command
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from lifecycle.validation import V2PhaseValidator


ROOT = Path(__file__).resolve().parent.parent


class _Risk:
    def __init__(self):
        self.cash = 1_000_000
        self.positions = []
        self.daily_pnl = 0
        self.halted = False
        self.halt_reason = ""

    def equity(self):
        return self.cash

    def daily_return(self):
        return 0.0


class _Bot:
    def __init__(self):
        self.risk = _Risk()


class V2Phase6Tests(unittest.TestCase):
    def test_operator_tabs_and_telegram_commands_are_fixed(self):
        self.assertEqual(
            list(V2_DASHBOARD_TABS),
            [
                "Account",
                "System Health",
                "Claude Picks",
                "B플랜 실시간",
                "Lifecycle",
                "Positions",
                "Brain",
                "Daily Review",
            ],
        )
        for command in (
            "/status", "/health", "/picks", "/positions", "/errors", "/halt", "/resume", "/panic",
            "/brain_pending", "/buy_capacity", "/capacity", "/pathb_status", "/pathb_on", "/pathb_off", "/pathb_kill",
            "/pathb_closeall",
        ):
            self.assertIn(command, V2_TELEGRAM_COMMANDS)

    def test_ops_summary_uses_lifecycle_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_phase6",
                payload={
                    "selection_meta": {
                        "price_targets": {
                            "005930": {
                                "buy_zone_low": 52_000,
                                "buy_zone_high": 52_500,
                                "sell_target": 54_500,
                                "stop_loss": 51_000,
                                "confidence": 0.7,
                            }
                        }
                    }
                },
            )
            store.create_path_run(
                path_run_id="pathb_1",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                status="WAITING",
                plan={
                    "buy_zone_low": 52_000,
                    "buy_zone_high": 52_500,
                    "sell_target": 54_500,
                    "stop_loss": 51_000,
                    "confidence": 0.7,
                },
            )
            store.create_path_run(
                path_run_id="pathb_closed",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="000660",
                status="CLOSED",
                plan={
                    "buy_zone_low": 120_000,
                    "buy_zone_high": 121_000,
                    "sell_target": 125_000,
                    "stop_loss": 118_000,
                    "confidence": 0.8,
                    "actual_entry_price": 120_500,
                    "actual_exit_price": 125_000,
                    "filled_qty": 1,
                    "pnl_pct": 3.73,
                    "close_reason": "CLOSED_CLAUDE_PRICE_TARGET",
                    "rationale": "support pullback",
                    "entry_basis_tags": ["support"],
                },
            )
            store.create_path_run(
                path_run_id="pathb_unknown",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="051910",
                status="ORDER_UNKNOWN",
                plan={
                    "order_unknown_resolution": "session_end_unresolved",
                    "session_end_unresolved": True,
                    "manual_reconciliation_required": True,
                },
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-04-26",
                "ticker": "005930",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain_phase6",
            }
            store.append(LifecycleEvent(event_type="ORDER_UNKNOWN", execution_id="exec1", **base))
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    execution_id="exec_a",
                    position_id="pos_a",
                    reason_code="CLOSED_TRAILING_STOP",
                    payload={"pnl_pct": 1.25, "pnl_krw": 1250, "close_reason": "CLOSED_TRAILING_STOP"},
                    **base,
                )
            )
            summary = build_v2_ops_summary(
                store=store,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
            )

        self.assertEqual(summary["system_health"]["order_unknown_count"], 1)
        self.assertEqual(len(summary["claude_picks"]), 1)
        self.assertEqual(summary["claude_picks"][0]["path_a"], "timing_adapter")
        self.assertEqual(summary["claude_picks"][0]["path_b"], "claude_price")
        self.assertEqual(summary["path_b_live"]["waiting"], 1)
        self.assertEqual(summary["path_b_live"]["active"][0]["ticker"], "005930")
        self.assertEqual(summary["path_b_live"]["metrics"]["target_hits"], 1)
        self.assertEqual(summary["path_b_live"]["metrics"]["target_hit_rate_pct"], 100.0)
        self.assertEqual(summary["path_b_live"]["metrics"]["currency"], "KRW")
        self.assertIn("pnl", summary["path_b_live"]["charts"])
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_a"]["closed"], 1)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_b"]["closed"], 1)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_a"]["avg_pnl_pct"], 1.25)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_b"]["avg_pnl_pct"], 3.73)
        self.assertEqual(summary["lifecycle"]["event_counts"]["ORDER_UNKNOWN"], 1)
        self.assertTrue(summary["path_b_live"]["order_unknown"][0]["manual_reconciliation_required"])

    def test_ops_summary_counts_carried_pathb_close_in_today_comparison(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="US",
                    runtime_mode="live",
                    session_date="2026-05-11",
                    ticker="TEVA",
                    decision_id="dec_20260430_US_TEVA",
                    execution_id="sell_1",
                    position_id="pos_US_TEVA",
                    prompt_version="v2",
                    brain_snapshot_id="brain_phase6",
                    reason_code="CLOSED_USER_MANUAL",
                    payload={
                        "close_reason": "CLOSED_USER_MANUAL",
                        "entry_route": "path_b",
                        "path_run_id": "path_20260430_US_TEVA_claude_price_23c822da",
                        "path_type": "claude_price",
                        "pnl_krw": 1764.2989,
                        "pnl_pct": 1.6836,
                    },
                )
            )

            summary = build_v2_ops_summary(
                store=store,
                market="US",
                runtime_mode="live",
                session_date="2026-05-11",
            )

        comparison = summary["path_b_live"]["path_comparison"]
        self.assertEqual(comparison["path_a"]["closed"], 0)
        self.assertEqual(comparison["path_b"]["closed"], 1)
        self.assertEqual(comparison["path_b"]["avg_pnl_pct"], 1.6836)
        self.assertEqual(comparison["path_b"]["realized_pnl_value"], 1764.2989)

    def test_pathb_ops_summary_derives_expired_reason_from_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            decision_id = "dec_20260527_KR_049080"
            store.create_path_run(
                path_run_id="path_expired",
                decision_id=decision_id,
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-05-27",
                ticker="049080",
                status="EXPIRED",
                plan={
                    "entry_order_price": 1000,
                    "actual_entry_price": 1003,
                },
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-05-27",
                "ticker": "049080",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain_phase6",
            }
            store.append(
                LifecycleEvent(
                    event_type="SAFETY_BLOCKED",
                    reason_code="KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED",
                    occurred_at="2026-05-27T09:30:00+09:00",
                    **base,
                )
            )
            store.append(
                LifecycleEvent(
                    event_type="CLAUDE_PRICE_EXPIRED",
                    occurred_at="2026-05-27T09:45:00+09:00",
                    **base,
                )
            )

            summary = build_v2_ops_summary(
                store=store,
                market="KR",
                runtime_mode="live",
                session_date="2026-05-27",
            )

        ops = summary["path_b_live"]["ops_summary"]
        self.assertEqual(ops["join_contract"], "path_run_id_then_decision_market_ticker")
        self.assertEqual(ops["expired_reason_counts"]["KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED"], 1)
        run = summary["path_b_live"]["recent"][0]
        self.assertEqual(run["ops_reason"], "KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED")
        self.assertEqual(run["latest_gate_reason"], "KR_PATHB_RISK_ORIGIN_CONFIRMATION_REQUIRED")
        self.assertEqual(run["entry_slippage_bps"], 30.0)

    def test_pathb_ops_summary_exposes_order_latency_and_fill_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            decision_id = "dec_20260527_US_NVDA"
            store.create_path_run(
                path_run_id="path_filled",
                decision_id=decision_id,
                path_type="claude_price",
                market="US",
                runtime_mode="live",
                session_date="2026-05-27",
                ticker="NVDA",
                status="FILLED",
                plan={
                    "entry_order_price": 100.0,
                    "actual_entry_price": 100.25,
                    "order_unknown_reconcile_attempts": 2,
                },
            )
            base = {
                "market": "US",
                "runtime_mode": "live",
                "session_date": "2026-05-27",
                "ticker": "NVDA",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain_phase6",
                "payload": {"path_run_id": "path_filled", "path_type": "claude_price"},
            }
            store.append(LifecycleEvent(event_type="ORDER_SENT", occurred_at="2026-05-27T09:30:00+09:00", **base))
            store.append(LifecycleEvent(event_type="ORDER_ACKED", occurred_at="2026-05-27T09:30:03+09:00", **base))
            store.append(
                LifecycleEvent(
                    event_type="FILLED",
                    occurred_at="2026-05-27T09:30:08+09:00",
                    payload={"path_run_id": "path_filled", "path_type": "claude_price", "side": "buy"},
                    **{key: value for key, value in base.items() if key != "payload"},
                )
            )

            summary = build_v2_ops_summary(
                store=store,
                market="US",
                runtime_mode="live",
                session_date="2026-05-27",
            )

        run = summary["path_b_live"]["recent"][0]
        self.assertEqual(run["sent_to_ack_latency_sec"], 3)
        self.assertEqual(run["sent_to_fill_latency_sec"], 8)
        self.assertEqual(run["entry_slippage_bps"], 25.0)
        self.assertEqual(run["order_unknown_reconcile_attempts"], 2)

    def test_pathb_pnl_chart_keeps_individual_trade_pnl_next_to_cumulative(self):
        charts = v2_ops_summary._path_b_charts(
            [
                {
                    "ticker": "F",
                    "status": "CLOSED",
                    "plan": {"pnl_pct": 3.0324, "close_reason": "CLOSED_CLAUDE_PRICE_TARGET"},
                },
                {
                    "ticker": "IREN",
                    "status": "CLOSED",
                    "plan": {"pnl_pct": -2.0008, "close_reason": "CLOSED_LOSS_CAP"},
                },
            ],
            metrics={},
        )

        self.assertEqual(charts["pnl"]["labels"], ["F", "IREN"])
        self.assertEqual(charts["pnl"]["data"], [3.0324, 1.0316])
        self.assertEqual(charts["pnl"]["point_pnl_pct"], [3.0324, -2.0008])
        self.assertEqual(
            charts["pnl"]["point_close_reasons"],
            ["CLOSED_CLAUDE_PRICE_TARGET", "CLOSED_LOSS_CAP"],
        )

    def test_pathb_metrics_marks_us_native_amounts_as_usd(self):
        metrics = v2_ops_summary._path_b_metrics(
            [
                {
                    "market": "US",
                    "status": "CLOSED",
                    "plan": {
                        "actual_entry_price": 60.0,
                        "filled_qty": 3,
                        "pnl_pct": -2.0,
                    },
                }
            ]
        )

        self.assertEqual(metrics["currency"], "USD")
        self.assertEqual(metrics["deployed_value"], 180.0)
        self.assertEqual(metrics["realized_pnl_value"], -3.6)

    def test_pathb_selection_summary_exposes_compact_validation_state(self):
        selection_meta = {
            "watchlist": ["AAPL", "MSFT"],
            "trade_ready": [],
            "recommended_strategy": {"AAPL": "opening_range_pullback"},
            "candidate_actions": [{"ticker": "AAPL", "action": "WATCH"}],
            "_selection_raw_schema": "compact",
            "_selection_schema_version": "selection_compact.v1",
            "_selection_stop_reason": "max_tokens",
            "_candidate_actions_source": "compact_candidate_actions_v1",
            "_candidate_actions_missing_contract": True,
            "_fallback_mode": "selection_truncated",
            "_compact_validation": {
                "errors": ["candidate_actions_coverage_incomplete", "stop_reason_max_tokens"],
                "warnings": ["MSFT:missing_strategy"],
            },
        }

        with patch.object(
            v2_ops_summary,
            "_load_judgment_record",
            return_value={"selection_meta": selection_meta, "universe_tickers": ["AAPL", "MSFT"]},
        ):
            selection = v2_ops_summary._path_b_selection_snapshot(
                market="US",
                runtime_mode="live",
                session_date="2026-05-12",
                pathb_runs=[],
                config={"enabled": True},
                control={"enabled": True},
            )

        self.assertEqual(selection["selection_raw_schema"], "compact")
        self.assertEqual(selection["selection_schema_version"], "selection_compact.v1")
        self.assertEqual(selection["selection_stop_reason"], "max_tokens")
        self.assertTrue(selection["candidate_actions_missing_contract"])
        self.assertEqual(selection["counts"]["candidate_actions"], 1)
        self.assertEqual(selection["counts"]["missing_strategy"], 1)
        self.assertEqual(selection["counts"]["compact_validation_errors"], 2)
        self.assertIn("CANDIDATE_ACTIONS_MISSING_CONTRACT", selection["no_plan_reasons"])
        self.assertIn("SELECTION_TRUNCATED", selection["no_plan_reasons"])
        self.assertEqual(selection["no_plan_primary_reason"], "NO_TRADE_READY")
        self.assertTrue(selection["no_plan_action_required"])
        self.assertIn("watch_only", selection["no_plan_summary"])

    def test_pathb_selection_summary_uses_candidate_action_price_targets(self):
        selection_meta = {
            "watchlist": ["ZS", "RBLX"],
            "trade_ready": [],
            "candidate_actions": [
                {
                    "ticker": "ZS",
                    "market": "US",
                    "action": "PULLBACK_WAIT",
                    "reason": "WAIT_FOR_PULLBACK",
                    "price_targets": {
                        "buy_zone_low": 168.5,
                        "buy_zone_high": 175.5,
                        "sell_target": 180.0,
                        "stop_loss": 166.0,
                        "confidence": 0.52,
                    },
                },
                {
                    "ticker": "RBLX",
                    "market": "US",
                    "action": "PULLBACK_WAIT",
                    "price_targets": {
                        "buy_zone_low": 44.5,
                        "buy_zone_high": 48.5,
                        "sell_target": 51.0,
                        "stop_loss": 43.0,
                        "confidence": 0.48,
                    },
                },
            ],
        }

        with patch.object(
            v2_ops_summary,
            "_load_judgment_record",
            return_value={"selection_meta": selection_meta, "universe_tickers": ["ZS", "RBLX"]},
        ):
            selection = v2_ops_summary._path_b_selection_snapshot(
                market="US",
                runtime_mode="live",
                session_date="2026-05-18",
                pathb_runs=[],
                config={"enabled": True},
                control={"enabled": True},
            )

        rows = {row["ticker"]: row for row in selection["watch_rows"]}
        self.assertEqual(selection["counts"]["price_targets"], 2)
        self.assertEqual(rows["ZS"]["buy_zone_low"], 168.5)
        self.assertEqual(rows["ZS"]["sell_target"], 180.0)
        self.assertEqual(rows["ZS"]["stop_loss"], 166.0)
        self.assertEqual(rows["ZS"]["confidence"], 0.52)
        self.assertFalse(selection["no_plan_action_required"])

    def test_pathb_execution_capacity_uses_broker_orderable_cash(self):
        broker_truth = {
            "markets": {
                "KR": {
                    "account_summary": {"orderable_cash": 1_941_524},
                    "positions": [],
                    "open_orders": [],
                }
            }
        }
        config = {
            "fixed_order_krw": 500_000,
            "fixed_order_krw_by_market": {"KR": 500_000},
            "min_order_krw_by_market": {"KR": 50_000},
            "max_positions": 15,
            "max_daily_entries": 40,
            "daily_entry_cap_by_market": {"KR": 40},
            "usd_krw": 1400,
        }

        capacity = v2_ops_summary._path_b_execution_capacity(
            broker_truth,
            config,
            [],
            markets=["KR"],
            session_date="2026-05-21",
        )

        self.assertEqual(capacity["KR"]["max_affordable_fixed_orders"], 3)
        self.assertTrue(capacity["KR"]["min_order_possible"])
        self.assertFalse(capacity["KR"]["daily_cap_cash_feasible"])

    def test_pathb_execution_capacity_applies_analyst_gross_exposure_cap(self):
        broker_truth = {
            "markets": {
                "US": {
                    "account_summary": {"orderable_cash": 1_000},
                    "positions": [{"ticker": "IONQ"}],
                    "open_orders": [],
                },
                "KR": {
                    "account_summary": {"orderable_cash": 2_000_000},
                    "positions": [{"ticker": "005930"}],
                    "open_orders": [],
                },
            }
        }
        config = {
            "fixed_order_krw": 300_000,
            "fixed_order_krw_by_market": {"KR": 300_000, "US": 300_000},
            "min_order_krw_by_market": {"KR": 50_000, "US": 50_000},
            "max_positions": 15,
            "max_daily_entries": 40,
            "daily_entry_cap_by_market": {"KR": 40, "US": 40},
            "usd_krw": 1500,
        }

        capacity = v2_ops_summary._path_b_execution_capacity(
            broker_truth,
            config,
            [],
            markets=["US", "KR"],
            session_date="2026-05-22",
            consensus_by_market={
                "US": {"new_buy_permission": "selective", "max_gross_exposure_pct": 40},
                "KR": {"new_buy_permission": "selective", "max_gross_exposure_pct": 50},
            },
            equity_context_by_market={
                "US": {"total_krw": 3_400_000, "position_krw": 1_900_000, "source": "test"},
                "KR": {"total_krw": 3_000_000, "position_krw": 1_000_000, "source": "test"},
            },
        )

        self.assertEqual(capacity["US"]["today_buy_capacity_krw"], 0)
        self.assertIn("ANALYST_MAX_GROSS_EXPOSURE_REACHED", capacity["US"]["capacity_block_reasons"])
        self.assertEqual(capacity["KR"]["gross_exposure_remaining_krw"], 500_000)
        self.assertEqual(capacity["KR"]["today_buy_capacity_krw"], 500_000)
        self.assertEqual(capacity["KR"]["today_entry_capacity_orders"], 1)

    def test_pathb_execution_capacity_can_use_manual_gross_exposure_cap(self):
        broker_truth = {
            "markets": {
                "US": {
                    "account_summary": {"orderable_cash": 1_000},
                    "positions": [{"ticker": "IONQ"}],
                    "open_orders": [],
                },
            }
        }
        config = {
            "fixed_order_krw": 300_000,
            "fixed_order_krw_by_market": {"US": 300_000},
            "min_order_krw_by_market": {"US": 50_000},
            "max_positions": 15,
            "max_daily_entries": 40,
            "daily_entry_cap_by_market": {"US": 40},
            "usd_krw": 1500,
            "analyst_gross_exposure_cap_mode_by_market": {"US": "manual"},
            "analyst_gross_exposure_cap_pct_by_market": {"US": 60},
        }

        capacity = v2_ops_summary._path_b_execution_capacity(
            broker_truth,
            config,
            [],
            markets=["US"],
            session_date="2026-05-22",
            consensus_by_market={
                "US": {"new_buy_permission": "selective", "max_gross_exposure_pct": 40},
            },
            equity_context_by_market={
                "US": {"total_krw": 3_400_000, "position_krw": 1_900_000, "source": "test"},
            },
        )

        self.assertEqual(capacity["US"]["max_gross_exposure_pct"], 60)
        self.assertEqual(capacity["US"]["analyst_max_gross_exposure_pct"], 40)
        self.assertEqual(capacity["US"]["gross_cap_mode"], "manual")
        self.assertEqual(capacity["US"]["gross_cap_source"], "manual_config")
        self.assertNotIn("ANALYST_MAX_GROSS_EXPOSURE_REACHED", capacity["US"]["capacity_block_reasons"])
        self.assertEqual(capacity["US"]["gross_exposure_remaining_krw"], 140_000)

    def test_buy_capacity_telegram_command_reports_capacity_snapshot(self):
        payload = {
            "path_b_live": {
                "execution_capacity": {
                    "US": {
                        "position_exposure_krw": 1_900_000,
                        "gross_exposure_cap_krw": 1_360_000,
                        "gross_exposure_pct": 55.8,
                        "max_gross_exposure_pct": 40,
                        "gross_exposure_remaining_krw": 0,
                        "orderable_cash_krw": 1_500_000,
                        "today_buy_capacity_krw": 0,
                        "today_entry_capacity_orders": 0,
                        "today_fixed_order_capacity_krw": 0,
                        "capacity_block_reasons": ["ANALYST_MAX_GROSS_EXPOSURE_REACHED"],
                    }
                }
            }
        }

        with patch("interface.v2_telegram.build_v2_ops_summary", return_value=payload):
            response = handle_v2_command("/buy_capacity", _Bot())

        self.assertIn("Buy Capacity", response)
        self.assertIn("US", response)
        self.assertIn("ANALYST_MAX_GROSS_EXPOSURE_REACHED", response)

    def test_pathb_readiness_distinguishes_overnight_allowed_states(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": False, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 1,
                "open_orders": 0,
            }
        }

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "inactive", "reason": "after_close"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=[],
            )
            no_position = dict(truth)
            no_position["US"] = {**truth["US"], "positions": 0}
            idle = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=no_position,
                execution_capacity=capacity,
                pathb_runs=[],
            )

        self.assertEqual(readiness["state"], "HOLDING_OVERNIGHT")
        self.assertEqual(idle["state"], "IDLE_MARKET_CLOSED_OVERNIGHT_ALLOWED")

    def test_pathb_readiness_closed_market_reports_stale_truth_as_warning(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": False, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {"US": {"trusted": False, "fresh": False, "positions": 0, "open_orders": 0}}

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "inactive", "reason": "after_close"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=[],
            )

        self.assertEqual(readiness["state"], "IDLE_MARKET_CLOSED_OVERNIGHT_ALLOWED")
        self.assertIn("BROKER_TRUTH_STALE_WARNING", readiness["known_blockers"])
        self.assertEqual(readiness["broker_truth_warning"], "stale_or_untrusted")

    def test_pathb_readiness_active_market_keeps_stale_truth_hard_block(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 2, "price_targets": 2}}
        config = {"enabled": True, "intraday_only": False, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {"US": {"trusted": False, "fresh": False, "positions": 0, "open_orders": 0}}

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=[],
            )

        self.assertEqual(readiness["state"], "BLOCKED_BROKER_TRUTH")
        self.assertTrue(readiness["operator_action_required"])

    def test_pathb_readiness_missing_intraday_uses_effective_config(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 0,
                "open_orders": 0,
            }
        }

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "inactive", "reason": "after_close"},
        ), patch.object(
            v2_ops_summary,
            "_path_b_config",
            return_value={"intraday_only": False},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=[],
                runtime_mode="live",
            )

        self.assertFalse(readiness["pathb_intraday_only"])
        self.assertEqual(readiness["state"], "IDLE_MARKET_CLOSED_OVERNIGHT_ALLOWED")

    def test_pathb_readiness_reports_wait_only_live_plan_as_active(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 0,
                "open_orders": 0,
            }
        }
        pathb_runs = [
            {
                "market": "US",
                "status": "WAITING",
                "plan": {
                    "registration_scope": "candidate_actions_wait_only",
                    "origin_action": "PULLBACK_WAIT",
                },
            }
        ]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "WAITING_QUOTE_OR_BUY_ZONE")
        self.assertEqual(readiness["registered_live_plans"], 1)
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 1)

    def test_pathb_readiness_blocks_entry_waiting_plan_when_cash_insufficient(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": False}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 0,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "US", "status": "WAITING", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "BLOCKED_AFFORDABILITY")
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 1)
        self.assertTrue(readiness["operator_action_required"])

    def test_pathb_readiness_ignores_inactive_registered_plan(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": True}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 0,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "US", "status": "CANCELLED", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "IDLE_NO_TRADE_READY")
        self.assertEqual(readiness["registered_live_plans"], 1)
        self.assertEqual(readiness["active_live_plans"], 0)
        self.assertEqual(readiness["entry_waiting_plans"], 0)

    def test_pathb_readiness_does_not_block_filled_plan_on_entry_cash(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": False}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 1,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "US", "status": "FILLED", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "WAITING_QUOTE_OR_BUY_ZONE")
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 0)
        self.assertFalse(readiness["operator_action_required"])

    def test_pathb_readiness_counts_sell_partial_filled_as_active(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {"enabled": True, "intraday_only": True, "market_live_enabled": {"US": True}}
        control = {"enabled": True}
        capacity = {"US": {"min_order_possible": False}}
        truth = {
            "US": {
                "trusted": True,
                "fresh": True,
                "positions": 1,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "US", "status": "SELL_PARTIAL_FILLED", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="US",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "WAITING_QUOTE_OR_BUY_ZONE")
        self.assertEqual(readiness["registered_live_plans"], 1)
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 0)
        self.assertFalse(readiness["operator_action_required"])

    def test_pathb_readiness_does_not_apply_kr_confirmation_gate_to_filled_plan(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {
            "enabled": True,
            "intraday_only": True,
            "market_live_enabled": {"KR": True},
            "source": {
                "runtime_snapshot": {
                    "effective": {
                        "KR_DAILY_ENTRY_CAP": "40",
                        "KR_CONFIRMATION_GATE_ENABLED": "false",
                        "KR_CONFIRMATION_GATE_SHADOW": "true",
                        "KR_CONFIRMATION_GATE_MODE": "",
                    }
                }
            },
        }
        control = {"enabled": True}
        capacity = {"KR": {"min_order_possible": True}}
        truth = {
            "KR": {
                "trusted": True,
                "fresh": True,
                "positions": 1,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "KR", "status": "FILLED", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="KR",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "WAITING_QUOTE_OR_BUY_ZONE")
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 0)
        self.assertFalse(readiness["operator_action_required"])

    def test_pathb_readiness_blocks_kr_entry_waiting_when_confirmation_gate_not_ready(self):
        selection = {"counts": {"watchlist": 15, "applied_trade_ready": 0, "price_targets": 0}}
        config = {
            "enabled": True,
            "intraday_only": True,
            "market_live_enabled": {"KR": True},
            "source": {
                "runtime_snapshot": {
                    "effective": {
                        "KR_DAILY_ENTRY_CAP": "40",
                        "KR_CONFIRMATION_GATE_ENABLED": "false",
                        "KR_CONFIRMATION_GATE_SHADOW": "true",
                        "KR_CONFIRMATION_GATE_MODE": "",
                    }
                }
            },
        }
        control = {"enabled": True}
        capacity = {"KR": {"min_order_possible": True}}
        truth = {
            "KR": {
                "trusted": True,
                "fresh": True,
                "positions": 0,
                "open_orders": 0,
            }
        }
        pathb_runs = [{"market": "KR", "status": "HIT", "plan": {"origin_action": "PULLBACK_WAIT"}}]

        with patch.object(
            v2_ops_summary,
            "_path_b_market_session_state",
            return_value={"state": "active", "reason": "regular_session"},
        ):
            readiness = v2_ops_summary._path_b_execution_readiness(
                market="KR",
                session_date="2026-05-21",
                selection=selection,
                config=config,
                control=control,
                broker_truth={},
                live_truth_verdict=truth,
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
            )

        self.assertEqual(readiness["state"], "BLOCKED_CONFIRMATION_GATE")
        self.assertEqual(readiness["active_live_plans"], 1)
        self.assertEqual(readiness["entry_waiting_plans"], 1)
        self.assertTrue(readiness["operator_action_required"])

    def test_telegram_v2_halt_resume_and_health(self):
        bot = _Bot()
        health = handle_v2_command("/health", bot)
        self.assertIn("ORDER_UNKNOWN", health)

        halt = handle_v2_command("/halt", bot)
        self.assertIn("HALT", halt)
        self.assertTrue(bot.risk.halted)
        self.assertEqual(bot.risk.halt_reason, "manual_halt")

        resume = handle_v2_command("/resume", bot)
        self.assertIn("RESUME", resume)
        self.assertFalse(bot.risk.halted)

    def test_phase6_gate_runs_cumulatively(self):
        report = V2PhaseValidator(ROOT).validate(6)

        self.assertTrue(report["ok"], report)
        self.assertEqual([phase["phase"] for phase in report["phases"]], [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
