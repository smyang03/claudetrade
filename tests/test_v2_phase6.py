from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from decision.registry import DecisionRegistry
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
        self.assertIn("pnl", summary["path_b_live"]["charts"])
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_a"]["closed"], 1)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_b"]["closed"], 1)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_a"]["avg_pnl_pct"], 1.25)
        self.assertEqual(summary["path_b_live"]["path_comparison"]["path_b"]["avg_pnl_pct"], 3.73)
        self.assertEqual(summary["lifecycle"]["event_counts"]["ORDER_UNKNOWN"], 1)

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
