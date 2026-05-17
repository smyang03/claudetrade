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
            "/brain_pending", "/pathb_status", "/pathb_on", "/pathb_off", "/pathb_kill",
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
