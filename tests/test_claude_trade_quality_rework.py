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
from runtime.adaptive_live_condition import build_adaptive_live_condition
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
                "price_targets": {"max_entry_price": 5800.0},
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

    def test_soft_gate_override_allows_ret3_only_inside_opening_grace(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"max_entry_price": 5800.0},
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "market": "KR",
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": 0.2,
                "ret_5m_pct": None,
                "market_open_elapsed_min": 3.0,
                "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN": 5.0,
                "opening_range_break": True,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        validation = decision.runtime_gate["soft_gate_override_validation"]
        self.assertTrue(validation["checks"]["ret3_only_grace_used"])

    def test_kr_soft_gate_override_fails_missing_entry_price_cap(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "market": "KR",
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "opening_range_break": True,
                "current_price": 5700.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        validation = decision.runtime_gate["soft_gate_override_validation"]
        self.assertFalse(validation["validated"])
        self.assertFalse(validation["checks"]["entry_price_cap_ok"])
        self.assertIn("entry_price_cap_missing", validation["failed_checks"])

    def test_soft_gate_override_rejects_ret3_only_outside_opening_grace(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"max_entry_price": 5800.0},
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "market": "KR",
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": 0.2,
                "ret_5m_pct": None,
                "market_open_elapsed_min": 8.0,
                "SOFT_GATE_ALLOW_RET3_ONLY_FIRST_MIN": 5.0,
                "opening_range_break": True,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertFalse(decision.runtime_gate["soft_gate_override_validation"]["checks"]["ret3_only_grace_used"])

    def test_evidence_ceiling_runs_before_soft_gate_override(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "market": "KR",
                "evidence_pack_ceiling_enabled": True,
                "evidence_data_state": "missing",
                "evidence_action_ceiling": "WATCH",
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": 1.0,
                "ret_5m_pct": 1.0,
                "opening_range_break": True,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.runtime_gate_reason, "evidence_action_ceiling")
        self.assertNotIn("soft_gate_override_validation", decision.runtime_gate)

    def test_fail_closed_evidence_reason_is_preserved_in_runtime_gate(self) -> None:
        decision = route_candidate_action(
            {"ticker": "018880", "action": "BUY_READY", "confidence": 0.9},
            market="KR",
            execution_context={
                "market": "KR",
                "evidence_pack_ceiling_enabled": True,
                "evidence_data_state": "missing",
                "evidence_action_ceiling": "WATCH",
                "evidence_fail_closed": True,
                "evidence_fail_closed_reason": "coverage_below_threshold",
                "evidence_provider": "kis",
                "evidence_requested": 30,
                "evidence_complete": 17,
                "evidence_coverage_ratio": 0.5667,
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "data_fail_closed_watch_only")
        self.assertEqual(decision.runtime_gate_reason, "coverage_below_threshold")
        self.assertTrue(decision.runtime_gate["evidence_fail_closed"])
        self.assertEqual(decision.runtime_gate["evidence_provider"], "kis")

    def test_partial_evidence_demotes_buy_to_probe_then_validates_soft_override(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "018880",
                "action": "BUY_READY",
                "confidence": 0.9,
                "soft_gate_overrides": ["late_chase"],
            },
            market="KR",
            execution_context={
                "market": "KR",
                "evidence_pack_ceiling_enabled": True,
                "evidence_data_state": "partial",
                "evidence_action_ceiling": "PROBE_READY",
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "ret_3m_pct": 1.0,
                "ret_5m_pct": 1.0,
                "opening_range_break": True,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.demoted_to, "PROBE_READY")
        self.assertEqual(decision.runtime_gate_reason, "evidence_action_ceiling")
        self.assertTrue(decision.runtime_gate["soft_gate_override_validation"]["validated"])

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

    def test_exit_lifecycle_live_allowlist_ignores_unknown_env_reasons(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig(
            {
                "EXIT_LIFECYCLE_ALLOWLIST_LIVE_ENABLED": True,
                "EXIT_LIFECYCLE_LIVE_REASONS": "hard_loss,unknown_force_exit,trail_exit",
            }
        )

        allowlist = TradingBot._exit_lifecycle_live_allowlist(bot)

        self.assertIn("hard_loss", allowlist)
        self.assertIn("trail_exit", allowlist)
        self.assertNotIn("unknown_force_exit", allowlist)

    def test_adaptive_live_condition_thresholds_are_market_configurable(self) -> None:
        meta = {
            "watchlist": ["005930"],
            "_post_open_features_by_ticker": {
                "005930": {
                    "current_price": 70000,
                    "ret_3m_pct": 0.9,
                    "ret_5m_pct": 0.1,
                    "opening_range_break": True,
                    "volume_ratio_open": 2.0,
                    "vwap_distance_pct": 0.1,
                    "momentum_state": "early_strength",
                    "data_quality": "good",
                }
            },
        }

        base = build_adaptive_live_condition(market="KR", selection_meta=meta, consensus_mode="AGGRESSIVE")
        with patch.dict(os.environ, {"ADAPTIVE_LIVE_R3_MIN_KR": "1.5"}, clear=False):
            stricter = build_adaptive_live_condition(market="KR", selection_meta=meta, consensus_mode="AGGRESSIVE")

        self.assertEqual(base["decisions"]["005930"]["suggested_claude_action"], "PROBE_READY")
        self.assertEqual(stricter["decisions"]["005930"]["suggested_claude_action"], "")
        self.assertEqual(stricter["thresholds"]["r3_min"], 1.5)

    def test_hold_advisor_soft_cache_uses_ttl_and_move_thresholds(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig(
            {
                "HOLD_ADVISOR_SOFT_CACHE_ENABLED": True,
                "HOLD_ADVISOR_SOFT_CACHE_TTL_SEC": 60,
                "HOLD_ADVISOR_SOFT_CACHE_PRICE_MOVE_MAX_PCT": 0.2,
                "HOLD_ADVISOR_SOFT_CACHE_PNL_MOVE_MAX_PCT": 0.15,
            }
        )
        bot._minutes_to_close = lambda market: 999.0  # type: ignore[method-assign]
        bot._current_session_date_str = lambda market: "2026-05-12"  # type: ignore[method-assign]
        cand = {"ticker": "005930", "entry": 100.0, "entry_session_date": "2026-05-12"}
        payload = {"auto_sell_review_action": "HOLD", "reason": "hold"}
        advice = {"next_review_min": 15}

        TradingBot._hold_advisor_soft_cache_put(bot, cand, "KR", "profit_floor", 100.0, payload, advice)
        key = TradingBot._hold_advisor_soft_cache_key(bot, cand, "KR", "profit_floor")

        self.assertLessEqual(bot._hold_advisor_soft_cache[key]["ttl_sec"], 60)
        self.assertEqual(
            TradingBot._hold_advisor_soft_cache_get(bot, cand, "KR", "profit_floor", 100.1),
            payload,
        )
        self.assertIsNone(TradingBot._hold_advisor_soft_cache_get(bot, cand, "KR", "profit_floor", 100.3))
        self.assertGreaterEqual(bot._hold_advisor_soft_cache_stats["invalidated"], 1)

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

    def test_order_time_late_entry_gate_allows_missing_preorder_metrics_with_warning(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig({"KR_LATE_ENTRY_EXEC_GATE_ENABLED": True})
        bot.selection_meta = {"KR": {}}
        bot._v2_same_day_stop_tickers = {"KR": set()}
        bot._last_post_open_features_by_ticker = {"KR": {}}
        bot._candidate_entry_timing_context = lambda *args, **kwargs: {
            "candidate_age_min": 20.0,
            "entry_timing_snapshot": {},
        }

        gate = TradingBot._kr_late_entry_order_time_gate(bot, "018880", current_price=5730.0)

        self.assertTrue(gate["allowed"])
        self.assertEqual(gate["reason"], "order_time_late_entry_metrics_unresolved_allow")
        self.assertEqual(gate["metrics_missing"], ["price_change_candidate_to_order_pct"])
        self.assertIn("kr_late_entry_metrics_missing", gate["warnings"])
        self.assertIn("kr_order_time_price_cap_missing", gate["warnings"])
        self.assertTrue(gate["entry_price_cap_missing"])

    def test_order_time_late_entry_gate_derives_chase_from_detected_price(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.runtime_config = _RuntimeConfig({"KR_LATE_ENTRY_EXEC_GATE_ENABLED": True})
        bot.selection_meta = {"KR": {}}
        bot._v2_same_day_stop_tickers = {"KR": set()}
        bot._last_post_open_features_by_ticker = {"KR": {}}
        bot._candidate_entry_timing_context = lambda *args, **kwargs: {
            "candidate_age_min": 130.0,
            "entry_timing_snapshot": {
                "candidate_price_at_detected": 100.0,
            },
        }

        gate = TradingBot._kr_late_entry_order_time_gate(bot, "018880", current_price=106.0)

        self.assertFalse(gate["allowed"])
        self.assertEqual(gate["reason"], "kr_late_chase_order_time_block")
        self.assertEqual(gate["price_change_candidate_to_order_pct"], 6.0)
        self.assertEqual(gate["price_change_source"], "snapshot_candidate_price_at_detected")

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

    def test_adaptive_live_condition_requests_claude_rejudgment_for_early_probe_shadow(self) -> None:
        result = build_adaptive_live_condition(
            market="US",
            consensus_mode="MODERATE_BULL",
            selection_meta={
                "watchlist": ["AAOI", "CRCL"],
                "_post_open_features_by_ticker": {
                    "AAOI": {
                        "ret_3m_pct": 3.36,
                        "ret_5m_pct": 0.0,
                        "ret_10m_pct": -0.78,
                        "opening_range_break": True,
                        "pullback_from_high_pct": -1.88,
                        "momentum_state": "early_probe_only",
                        "data_quality": "first_observed",
                    },
                    "CRCL": {
                        "ret_3m_pct": 0.9,
                        "ret_5m_pct": 0.9,
                        "ret_10m_pct": 4.13,
                        "opening_range_break": True,
                        "pullback_from_high_pct": -1.59,
                        "momentum_state": "early_probe_only",
                        "data_quality": "first_observed",
                    },
                },
            },
        )

        self.assertEqual(result["decisions"]["AAOI"]["action"], "REASK_CLAUDE")
        self.assertEqual(result["decisions"]["AAOI"]["suggested_claude_action"], "PROBE_READY")
        self.assertTrue(result["decisions"]["AAOI"]["non_executable"])
        self.assertFalse(result["decisions"]["AAOI"]["local_promotion_allowed"])
        self.assertEqual(result["decisions"]["CRCL"]["action"], "REASK_CLAUDE")
        self.assertEqual(set(result["reask_claude_shadow"]), {"AAOI", "CRCL"})
        self.assertEqual(set(result["suggested_probe_ready_shadow"]), {"AAOI", "CRCL"})
        self.assertEqual(result["probe_ready_shadow"], [])
        self.assertTrue(result["shadow_only"])

    def test_adaptive_live_condition_late_mover_reasks_with_micro_probe_suggestion(self) -> None:
        result = build_adaptive_live_condition(
            market="US",
            consensus_mode="MODERATE_BULL",
            selection_meta={
                "watchlist": ["NVTS"],
                "_post_open_features_by_ticker": {
                    "NVTS": {
                        "ret_30m_pct": 16.29,
                        "opening_range_break": True,
                        "pullback_from_high_pct": -1.8,
                        "from_open_high_pct": 25.6,
                        "momentum_state": "late_mover",
                        "data_quality": "first_observed",
                    }
                },
            },
        )

        self.assertEqual(result["decisions"]["NVTS"]["action"], "REASK_CLAUDE")
        self.assertEqual(result["decisions"]["NVTS"]["suggested_claude_action"], "MICRO_PROBE")
        self.assertEqual(result["decisions"]["NVTS"]["action_ceiling"], "MICRO_PROBE")
        self.assertEqual(result["reask_claude_shadow"], ["NVTS"])
        self.assertEqual(result["suggested_micro_probe_shadow"], ["NVTS"])
        self.assertEqual(result["micro_probe_shadow"], [])

    def test_adaptive_live_condition_blocks_fade_and_risk_off(self) -> None:
        fade_result = build_adaptive_live_condition(
            market="US",
            consensus_mode="MODERATE_BULL",
            selection_meta={
                "watchlist": ["APLD"],
                "_post_open_features_by_ticker": {
                    "APLD": {
                        "ret_3m_pct": 5.0,
                        "opening_range_break": True,
                        "pullback_from_high_pct": -1.0,
                        "momentum_state": "fade",
                        "data_quality": "first_observed",
                    }
                },
            },
        )
        risk_off_result = build_adaptive_live_condition(
            market="US",
            consensus_mode="DEFENSIVE",
            selection_meta={
                "watchlist": ["CRCL"],
                "_post_open_features_by_ticker": {
                    "CRCL": {
                        "ret_3m_pct": 0.9,
                        "ret_5m_pct": 0.9,
                        "ret_10m_pct": 4.1,
                        "opening_range_break": True,
                        "pullback_from_high_pct": -1.5,
                        "momentum_state": "early_probe_only",
                        "data_quality": "first_observed",
                    }
                },
            },
        )

        self.assertEqual(fade_result["decisions"]["APLD"]["action"], "WATCH")
        self.assertIn("fade", fade_result["decisions"]["APLD"]["blockers"])
        self.assertEqual(risk_off_result["decisions"]["CRCL"]["action"], "WATCH")
        self.assertIn("risk_off_regime", risk_off_result["decisions"]["CRCL"]["blockers"])

    def test_adaptive_live_condition_marks_kr_fade_recovered_shadow_without_reask(self) -> None:
        result = build_adaptive_live_condition(
            market="KR",
            consensus_mode="MODERATE_BULL",
            selection_meta={
                "watchlist": ["036540"],
                "_post_open_features_by_ticker": {
                    "036540": {
                        "current_price": 10360,
                        "ret_3m_pct": 6.84,
                        "ret_5m_pct": 6.50,
                        "opening_range_break": True,
                        "vwap_distance_pct": 2.39,
                        "volume_ratio_open": 64.73,
                        "pullback_from_high_pct": -5.04,
                        "momentum_state": "fade",
                        "data_quality": "minute_complete",
                    }
                },
            },
        )

        decision = result["decisions"]["036540"]
        self.assertEqual(decision["action"], "WATCH")
        self.assertEqual(decision["action_ceiling"], "WATCH")
        self.assertFalse(decision["claude_reask"])
        self.assertTrue(decision["fade_recovered_shadow"])
        self.assertEqual(decision["fade_recovered_suggested_action"], "PROBE_READY")
        self.assertEqual(result["fade_recovered_shadow"], ["036540"])
        self.assertEqual(result["counts"]["fade_recovered_shadow"], 1)

    def test_route_layer_does_not_promote_watch_or_avoid_with_strong_evidence(self) -> None:
        strong_context = {
            "current_price": 183.0,
            "ret_3m_pct": 3.0,
            "ret_5m_pct": 1.0,
            "opening_range_break": True,
            "vwap_reclaim": True,
            "volume_ratio_open": 4.0,
            "data_quality": "good",
        }

        watch_decision = route_candidate_action(
            {"ticker": "AAOI", "action": "WATCH", "confidence": 0.99},
            market="US",
            execution_context=strong_context,
        )
        avoid_decision = route_candidate_action(
            {"ticker": "AAOI", "action": "AVOID", "confidence": 0.99},
            market="US",
            execution_context=strong_context,
        )

        self.assertEqual(watch_decision.final_action, "WATCH")
        self.assertIsNone(watch_decision.route)
        self.assertEqual(avoid_decision.final_action, "WATCH")
        self.assertIsNone(avoid_decision.route)

    def test_route_layer_treats_reask_claude_as_non_executable(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AAOI",
                "action": "REASK_CLAUDE",
                "suggested_claude_action": "PROBE_READY",
                "confidence": 0.99,
            },
            market="US",
            execution_context={
                "current_price": 183.0,
                "ret_3m_pct": 3.0,
                "ret_5m_pct": 1.0,
                "opening_range_break": True,
                "vwap_reclaim": True,
                "volume_ratio_open": 4.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertIsNone(decision.route)
        self.assertEqual(decision.reason, "watch")

    def test_session_runtime_safety_summary_attaches_ops_metrics_and_alerts(self) -> None:
        bot = TradingBot.__new__(TradingBot)
        bot.is_paper = False
        bot.runtime_config = _RuntimeConfig({
            "DECISION_ID_FALLBACK_ALERT_RATE": 0.2,
            "DECISION_ID_FALLBACK_ALERT_MIN_COUNT": 1,
            "EXIT_BYPASS_ALERT_RATE": 0.3,
        })
        bot.selection_meta = {
            "KR": {
                "trade_ready": ["005930", "000660", "035420", "012610"],
                "_decision_id_fallback_count": 1,
                "_decision_id_fallback_tickers": ["012610"],
                "_decision_id_fallback_sources": ["execution_lifecycle_fallback"],
            }
        }
        bot._hold_advisor_soft_cache_stats = {"hit": 1, "miss": 2, "expired": 0, "invalidated": 1}
        bot._exit_lifecycle_bypass_stats = {
            "KR": {"attempts": 2, "bypass_count": 1, "reason_counts": {"hard_loss": 1}}
        }
        lifecycle_report = {
            "session_date": "2026-05-12",
            "market": "KR",
            "runtime_mode": "live",
            "gap_count": 1,
            "severity_counts": {"HIGH": 1},
            "gaps": [{"ticker": "012610", "event_type": "FILLED", "severity": "HIGH"}],
        }

        summary = bot._build_session_runtime_safety_summary(
            "KR",
            "2026-05-12",
            lifecycle_gap_report=lifecycle_report,
        )
        ops = bot._attach_runtime_safety_to_ops_snapshot({"metrics": {}, "triggers": {}}, summary)

        self.assertEqual(summary["decision_id_fallback"]["ratio_pct"], 25.0)
        self.assertEqual(summary["hold_advisor_soft_cache"]["requests"], 4)
        self.assertEqual(summary["exit_lifecycle_bypass"]["ratio_pct"], 50.0)
        self.assertTrue(ops["triggers"]["decision_id_fallback_seen"])
        self.assertTrue(ops["triggers"]["lifecycle_high_gap_seen"])
        self.assertTrue(ops["triggers"]["low_hold_advisor_cache_hit_rate"])
        self.assertTrue(ops["triggers"]["high_exit_lifecycle_bypass_ratio"])
        with patch("trading_bot.system_alert") as alert:
            bot._emit_session_runtime_safety_alerts("KR", summary)
        self.assertEqual(alert.call_count, 2)


if __name__ == "__main__":
    unittest.main()
