from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credit_tracker
from minority_report import analysts
from audit.candidate_audit_store import CandidateAuditStore
from bot.candidate_policy import normalize_selection_result
from runtime.action_routing import route_candidate_action
from runtime.candidate_actions import candidate_actions_from_response
from runtime.exit_lifecycle import decide_exit_lifecycle, exit_lifecycle_bypass_allowed
from trading_bot import TradingBot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str, default=None):
        return self.values.get(key, default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))


class ClaudeTradeQualityReworkTests(unittest.TestCase):
    def test_candidate_actions_v2_missing_why_not_watch_demotes_ready(self) -> None:
        actions = candidate_actions_from_response(
            {
                "candidate_actions": [
                    {
                        "schema_version": "candidate_actions.v2",
                        "ticker": "018880",
                        "action": "BUY_READY",
                        "confidence": 0.8,
                    }
                ]
            },
            market="KR",
            created_at="2026-05-11T10:00:00",
        )

        self.assertEqual(actions[0]["action"], "WATCH")
        self.assertIn("v2_missing_why_not_watch_demoted", actions[0]["warnings"])

    def test_v2_selection_result_does_not_auto_promote_one_list_output(self) -> None:
        with patch.dict(os.environ, {"CANDIDATE_ACTIONS_V2_ENABLED": "true"}, clear=False):
            meta = normalize_selection_result(
                {"tickers": ["018880"]},
                [{"ticker": "018880"}],
                "KR",
            )

        self.assertEqual(meta["watchlist"], ["018880"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertFalse(meta["_legacy_auto_ready_promoted"])

    def test_soft_gate_override_requires_evidence(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": None,
                "ret_5m_pct": None,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.runtime_gate_reason, "soft_gate_override_failed")

    def test_recovery_micro_loss_cap_maps_to_hard_loss_bypass(self) -> None:
        decision = decide_exit_lifecycle(
            {"ticker": "012610", "recovery_micro_no_carry": True},
            exit_candidate={
                "ticker": "012610",
                "reason": "loss_cap",
                "recovery_micro_exit_trigger": "recovery_micro_hard_loss",
            },
            claude_vote="HOLD",
        )

        self.assertEqual(decision.reason, "hard_loss")
        self.assertFalse(decision.claude_override_allowed)
        self.assertTrue(exit_lifecycle_bypass_allowed(decision.to_dict()))

    def test_candidate_audit_store_migrates_v2_columns(self) -> None:
        path = Path(tempfile.gettempdir()) / f"candidate_audit_rework_{os.getpid()}.db"
        for suffix in ("", "-wal", "-shm"):
            try:
                Path(str(path) + suffix).unlink()
            except FileNotFoundError:
                pass
        try:
            store = CandidateAuditStore(path)
            store.upsert_candidate(
                {
                    "call_id": "call_1",
                    "runtime_mode": "live",
                    "market": "KR",
                    "session_date": "2026-05-11",
                    "ticker": "018880",
                    "payload": {
                        "schema_version": "candidate_actions.v2",
                        "legacy_auto_ready_promoted": True,
                        "soft_gates": ["late_chase"],
                    },
                }
            )
            with sqlite3.connect(path) as conn:
                columns = {row[1] for row in conn.execute("PRAGMA table_info(audit_candidate_rows)")}
                row = conn.execute(
                    "SELECT schema_version, legacy_auto_ready_promoted, soft_gates FROM audit_candidate_rows"
                ).fetchone()

            self.assertIn("schema_version", columns)
            self.assertEqual(row[0], "candidate_actions.v2")
            self.assertEqual(row[1], 1)
            self.assertIn("late_chase", row[2])
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    Path(str(path) + suffix).unlink()
                except OSError:
                    pass

    def test_credit_tracker_throttle_blocks_optional_at_hard_cap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            usage_path = Path(tmp) / "usage.json"
            with patch.object(credit_tracker, "USAGE_PATH", usage_path), patch.dict(
                os.environ,
                {
                    "CLAUDE_BUDGET_THROTTLE_ENABLED": "true",
                    "CLAUDE_DAILY_BUDGET_USD": "0.001",
                    "CLAUDE_BUDGET_HARD_EXIT_EXEMPT": "true",
                },
                clear=False,
            ):
                credit_tracker.record(1_000_000, 1_000_000, "seed", model="claude-sonnet-test")
                optional = credit_tracker.throttle_state(label="select_tickers")
                hard_exit = credit_tracker.throttle_state(label="auto_exit", hard_exit=True)

        self.assertFalse(optional["allowed"])
        self.assertEqual(optional["tier"], "hard_cap")
        self.assertTrue(hard_exit["allowed"])
        self.assertEqual(hard_exit["tier"], "hard_exit_exempt")

    def test_empty_selection_meta_fallback_is_watch_only_in_v2(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig(
            {
                "CANDIDATE_ACTIONS_V2_ENABLED": True,
                "ENABLE_CLAUDE_CANDIDATE_ACTIONS": False,
                "ENABLE_ACTION_ROUTING": False,
            }
        )
        bot.selection_meta = {"KR": {}, "US": {}}
        bot.selection_stages = {"KR": {}, "US": {}}
        bot.trade_ready_tickers = {"KR": [], "US": []}
        bot.today_judgment = {"market": "KR"}
        bot._record_candidate_funnel_snapshot = lambda *args, **kwargs: None
        bot._v2_register_trade_ready = lambda market, meta: {}
        bot.pathb = None

        with patch("trading_bot.get_last_selection_meta", return_value={}):
            meta = TradingBot._apply_selection_meta(bot, "KR", ["018880"], mode="MODERATE_BULL")

        self.assertEqual(meta["watchlist"], ["018880"])
        self.assertEqual(meta["trade_ready"], [])
        self.assertEqual(meta["_fallback_mode"], "empty_selection_meta_watch_only")

    def test_order_time_late_entry_gate_blocks_unconfirmed_stale_chase(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig({"KR_LATE_ENTRY_EXEC_GATE_ENABLED": True})
        bot.selection_meta = {"KR": {}}
        bot._v2_same_day_stop_tickers = {"KR": set()}
        bot._last_post_open_features_by_ticker = {"KR": {}}
        bot._candidate_entry_timing_context = lambda *args, **kwargs: {
            "candidate_age_min": 130.0,
            "entry_timing_snapshot": {
                "candidate_to_order_delay_min": 130.0,
                "price_change_candidate_to_order_pct": 11.7,
            },
        }

        gate = TradingBot._kr_late_entry_order_time_gate(bot, "018880", current_price=5730.0)

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "kr_late_chase_order_time_block")

    def test_order_time_late_entry_gate_allows_confirmed_continuation(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig(
            {
                "KR_LATE_ENTRY_EXEC_GATE_ENABLED": True,
                "KR_LATE_ENTRY_FRESH_MIN_RET_3M_PCT": 0.0,
                "KR_LATE_ENTRY_FRESH_MIN_RET_5M_PCT": 0.0,
            }
        )
        bot.selection_meta = {"KR": {}}
        bot._v2_same_day_stop_tickers = {"KR": set()}
        bot._last_post_open_features_by_ticker = {
            "KR": {
                "018880": {
                    "ret_3m_pct": 0.4,
                    "ret_5m_pct": 0.8,
                    "opening_range_break": True,
                    "vwap_distance_pct": 0.3,
                    "volume_ratio_open": 2.4,
                }
            }
        }
        bot._candidate_entry_timing_context = lambda *args, **kwargs: {
            "candidate_age_min": 130.0,
            "entry_timing_snapshot": {
                "candidate_to_order_delay_min": 130.0,
                "price_change_candidate_to_order_pct": 11.7,
            },
        }

        gate = TradingBot._kr_late_entry_order_time_gate(bot, "018880", current_price=5730.0)

        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "order_time_late_entry_allowed")
        self.assertTrue(gate["fresh_confirmed"])

    def test_order_time_late_entry_gate_blocks_max_entry_price(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig({"KR_LATE_ENTRY_EXEC_GATE_ENABLED": True})
        bot._v2_same_day_stop_tickers = {"KR": set()}

        gate = TradingBot._kr_late_entry_order_time_gate(
            bot,
            "018880",
            current_price=5730.0,
            max_entry_price=5600.0,
        )

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "max_entry_price_exceeded")

    def test_tuning_feedback_contract_is_structured_and_bounded(self) -> None:
        with patch.dict(os.environ, {"TUNING_FEEDBACK_CONTRACT_ENABLED": "true"}, clear=False):
            section, meta = analysts._build_tuning_feedback_contract(
                "KR",
                [
                    {
                        "negative_evidence": {"risk_tags": ["late_chase"]},
                        "risk_control_view": {"action_ceiling": "WATCH"},
                    }
                ],
                {"ids": ["lesson_1"]},
            )

        self.assertIn("Tuning feedback contract", section)
        self.assertEqual(meta["rule_version"], "kr_selection_feedback.v1")
        self.assertEqual(meta["similar_past_failures"][0]["pattern"], "late_chase_after_watch")
        self.assertIn("lesson_1", meta["active_lesson_ids"])


if __name__ == "__main__":
    unittest.main()
