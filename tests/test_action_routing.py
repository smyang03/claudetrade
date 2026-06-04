from __future__ import annotations

import unittest
from unittest.mock import patch

from runtime.action_routing import route_candidate_action


class ActionRoutingTests(unittest.TestCase):
    def test_hard_block_prevents_route(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY"},
            market="US",
            gate_final_action="HARD_BLOCK",
            gate_blocker="BROKER_UNTRUSTED",
        )

        self.assertEqual(decision.final_action, "HARD_BLOCK")
        self.assertIsNone(decision.route)

    def test_pullback_wait_requires_target(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "PULLBACK_WAIT", "price_targets": {}},
            market="US",
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "missing_pullback_target")

    def test_pullback_wait_hint_without_full_plan_stays_watch(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AAPL",
                "action": "PULLBACK_WAIT",
                "price_targets": {"entry_below": 180.0},
            },
            market="US",
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "missing_pullback_target")

    def test_pullback_wait_negative_context_stays_watch(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "IONQ",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "price_targets": {
                    "buy_zone_low": 45.5,
                    "buy_zone_high": 46.8,
                    "sell_target": 49.5,
                    "stop_loss": 43.8,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="US",
            execution_context={"momentum_state": "fade", "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "pullback_wait_blocked_negative_context")
        self.assertEqual(decision.runtime_gate_reason, "negative_pullback_context")
        self.assertEqual(decision.demoted_to, "WATCH")

    def test_pullback_wait_evidence_ceiling_defaults_to_shadow_without_route_change(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "GXO",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "price_targets": {
                    "buy_zone_low": 40.0,
                    "buy_zone_high": 41.0,
                    "sell_target": 44.0,
                    "stop_loss": 39.0,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="US",
            execution_context={
                "evidence_pack_ceiling_enabled": True,
                "evidence_data_state": "missing",
                "evidence_action_ceiling": "WATCH",
            },
        )

        self.assertEqual(decision.final_action, "PULLBACK_WAIT")
        self.assertEqual(decision.route, "PathB.wait")
        self.assertIn("pullback_wait_evidence_shadow", decision.warnings)
        gate = decision.runtime_gate["pullback_wait_evidence_gate"]
        self.assertTrue(gate["shadow_only"])
        self.assertFalse(gate["demoted_to_watch"])
        self.assertEqual(gate["mode"], "shadow")
        self.assertIn("evidence_missing", gate["reasons"])
        self.assertIn("evidence_ceiling_watch", gate["reasons"])

    def test_pullback_wait_evidence_ceiling_live_mode_demotes_to_watch(self) -> None:
        with patch.dict("os.environ", {"PULLBACK_WAIT_EVIDENCE_GATE_MODE": "live"}, clear=False):
            decision = route_candidate_action(
                {
                    "ticker": "GXO",
                    "action": "PULLBACK_WAIT",
                    "confidence": 0.72,
                    "price_targets": {
                        "buy_zone_low": 40.0,
                        "buy_zone_high": 41.0,
                        "sell_target": 44.0,
                        "stop_loss": 39.0,
                        "hold_days": 1,
                        "confidence": 0.72,
                    },
                },
                market="US",
                execution_context={
                    "evidence_pack_ceiling_enabled": True,
                    "evidence_data_state": "missing",
                    "evidence_action_ceiling": "WATCH",
                },
            )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertIsNone(decision.route)
        self.assertEqual(decision.reason, "pullback_wait_evidence_gate")
        self.assertEqual(decision.runtime_gate_reason, "pullback_wait_evidence_gate")
        self.assertIn("pullback_wait_evidence_gate", decision.warnings)
        gate = decision.runtime_gate["pullback_wait_evidence_gate"]
        self.assertFalse(gate["shadow_only"])
        self.assertTrue(gate["demoted_to_watch"])
        self.assertEqual(gate["mode"], "live")

    def test_kr_pullback_negative_context_records_healthy_shadow_without_route_change(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "208710",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "reason": "fade risk but recovered",
                "price_targets": {
                    "buy_zone_low": 4950,
                    "buy_zone_high": 5300,
                    "sell_target": 5700,
                    "stop_loss": 4800,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="KR",
            execution_context={
                "current_price": 5010,
                "momentum_state": "sustained",
                "data_quality": "minute_complete",
                "evidence_data_state": "confirmed",
                "evidence_fail_closed": False,
                "vi_active": False,
                "pullback_from_high_pct": -3.0,
                "repeated_failed_ready_count": 0,
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertIsNone(decision.route)
        self.assertFalse(decision.cancel_pathb)
        self.assertFalse(decision.suspend_pathb)
        self.assertNotIn("kr_healthy_pullback_shadow", decision.warnings)
        shadow = decision.runtime_gate["kr_healthy_pullback_shadow"]
        self.assertEqual(shadow["shadow_decision"], "accepted")
        self.assertTrue(shadow["would_have_pathb_wait"])
        self.assertFalse(shadow["pathb_wait_registration"])
        self.assertFalse(shadow["v2_path_run_created"])
        self.assertFalse(shadow["order_created"])

    def test_kr_pullback_overextended_shadow_requires_review(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "126730",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "reason": "fade risk",
                "price_targets": {
                    "buy_zone_low": 35000,
                    "buy_zone_high": 37000,
                    "sell_target": 39000,
                    "stop_loss": 34000,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="KR",
            execution_context={
                "current_price": 35200,
                "momentum_state": "overextended",
                "opening_range_break": True,
                "data_quality": "minute_complete",
                "evidence_data_state": "confirmed",
                "evidence_fail_closed": False,
                "vi_active": False,
                "pullback_from_high_pct": -3.56,
                "repeated_failed_ready_count": 0,
            },
        )

        shadow = decision.runtime_gate["kr_healthy_pullback_shadow"]
        self.assertEqual(shadow["shadow_decision"], "needs_review_overextended")
        self.assertFalse(shadow["would_have_pathb_wait"])
        self.assertIn("overextended_needs_review", shadow["reasons"])

    def test_buy_ready_can_cancel_pathb_when_confident_and_not_extended(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.8},
            market="US",
            pathb_waiting=True,
            overextended=False,
            data_quality="good",
        )

        self.assertEqual(decision.route, "PlanA.buy")
        self.assertTrue(decision.cancel_pathb)

    def test_buy_ready_missing_data_does_not_cancel_pathb(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.95},
            market="US",
            pathb_waiting=True,
            overextended=False,
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertFalse(decision.cancel_pathb)
        self.assertEqual(decision.reason, "pathb_waiting_kept_bad_data")
        self.assertEqual(decision.runtime_gate_reason, "data_quality")
        self.assertTrue(decision.runtime_gate["data_quality_missing"])

    def test_buy_ready_overextended_demotes_to_probe(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AMD", "action": "BUY_READY", "confidence": 0.86},
            market="US",
            execution_context={
                "market": "US",
                "ticker": "AMD",
                "momentum_state": "overextended",
                "ret_5m_pct": 4.7,
                "threshold_used": 3.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.route, "PlanA.probe")
        self.assertEqual(decision.reason, "buy_ready_demoted_overextended")
        self.assertEqual(decision.original_action, "BUY_READY")
        self.assertEqual(decision.demoted_to, "PROBE_READY")
        self.assertEqual(decision.runtime_gate_reason, "overextended")
        self.assertEqual(decision.runtime_gate["reason"], "overextended")
        self.assertEqual(decision.runtime_gate["ret_5m_pct"], 4.7)
        self.assertEqual(decision.runtime_gate["threshold_used"], 3.0)

    def test_buy_ready_above_cancel_price_is_chase_blocked(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AMD",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"cancel_if_open_above": 124.0},
            },
            market="US",
            execution_context={"current_price": 125.0, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "buy_ready_chase_blocked")
        self.assertEqual(decision.runtime_gate_reason, "chase_above_cancel")

    def test_kr_buy_ready_missing_price_cap_demotes_to_probe(self) -> None:
        decision = route_candidate_action(
            {"ticker": "005930", "action": "BUY_READY", "confidence": 0.9},
            market="KR",
            execution_context={"current_price": 70000, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.route, "PlanA.probe")
        self.assertEqual(decision.reason, "kr_buy_ready_missing_price_cap_demoted")
        self.assertEqual(decision.runtime_gate_reason, "missing_price_cap")
        self.assertTrue(decision.runtime_gate["entry_price_cap_missing"])
        self.assertIn("kr_missing_price_cap_demoted", decision.warnings)

    def test_us_buy_ready_missing_price_cap_remains_buy_ready(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.9},
            market="US",
            execution_context={"current_price": 180.0, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        self.assertEqual(decision.route, "PlanA.buy")
        self.assertTrue(decision.runtime_gate["entry_price_cap_missing"])

    def test_kr_buy_ready_with_cancel_if_open_above_does_not_missing_cap_demote(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"cancel_if_open_above": 71000.0},
            },
            market="KR",
            execution_context={"current_price": 70000.0, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        self.assertEqual(decision.route, "PlanA.buy")
        self.assertFalse(decision.runtime_gate["entry_price_cap_missing"])
        self.assertEqual(decision.runtime_gate["entry_price_cap"], 71000.0)

    def test_kr_buy_ready_above_entry_price_cap_is_blocked(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"max_entry_price": 69000.0},
            },
            market="KR",
            execution_context={"current_price": 70000.0, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "buy_ready_price_cap_exceeded")
        self.assertEqual(decision.runtime_gate_reason, "entry_price_cap_exceeded")

    def test_kr_buy_ready_overextended_still_demotes_before_missing_cap(self) -> None:
        decision = route_candidate_action(
            {"ticker": "005930", "action": "BUY_READY", "confidence": 0.9},
            market="KR",
            execution_context={
                "current_price": 70000.0,
                "momentum_state": "overextended",
                "ret_5m_pct": 4.2,
                "threshold_used": 3.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.reason, "buy_ready_demoted_overextended")
        self.assertEqual(decision.runtime_gate_reason, "overextended")

    def test_kr_buy_ready_missing_price_cap_keeps_pathb_waiting(self) -> None:
        decision = route_candidate_action(
            {"ticker": "005930", "action": "BUY_READY", "confidence": 0.95},
            market="KR",
            pathb_waiting=True,
            execution_context={
                "current_price": 70000.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertFalse(decision.cancel_pathb)
        self.assertEqual(decision.reason, "pathb_waiting_kept_missing_price_cap")
        self.assertEqual(decision.runtime_gate_reason, "missing_price_cap")

    def test_us_soft_gate_buy_zone_high_does_not_change_live_route(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AAPL",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"buy_zone_high": 100.0},
                "soft_gate_overrides": ["late_chase"],
            },
            market="US",
            execution_context={
                "soft_gate_override_validation_enabled": True,
                "soft_gates": ["late_chase"],
                "current_price": 105.0,
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "opening_range_break": True,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        self.assertEqual(decision.route, "PlanA.buy")
        self.assertEqual(decision.runtime_gate["entry_price_cap"], 100.0)
        self.assertTrue(decision.runtime_gate["soft_gate_override_validation"]["validated"])

    def test_kr_confirmation_score_payload_is_preserved(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"max_entry_price": 71000},
            },
            market="KR",
            execution_context={
                "current_price": 70000,
                "data_quality": "good",
                "kr_confirmation_gate_active": True,
                "kr_confirmation_confirmed": True,
                "kr_confirmation_state": "CONFIRMED",
                "kr_confirmation_gate_mode": "FAST_TRIGGER_WITH_HARD_VETO",
                "kr_confirmation_score": 2,
                "kr_confirmation_score_items": ["ret_3m_ok", "ret_5m_ok"],
                "kr_confirmation_threshold": 2,
                "kr_confirmation_fast_window_ok": True,
                "vi_active": False,
                "orderbook_support": True,
            },
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        self.assertEqual(decision.runtime_gate["kr_confirmation_score"], 2)
        self.assertEqual(decision.runtime_gate["kr_confirmation_score_items"], ["ret_3m_ok", "ret_5m_ok"])
        self.assertEqual(decision.runtime_gate["kr_confirmation_gate_mode"], "FAST_TRIGGER_WITH_HARD_VETO")
        self.assertTrue(decision.runtime_gate["kr_confirmation_fast_window_ok"])

    def test_kr_confirmation_live_does_not_block_pullback_wait(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "price_targets": {
                    "buy_zone_low": 69500,
                    "buy_zone_high": 70000,
                    "sell_target": 73000,
                    "stop_loss": 68000,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="KR",
            execution_context={
                "data_quality": "good",
                "kr_confirmation_gate_active": True,
                "kr_confirmation_confirmed": False,
                "kr_confirmation_state": "CONFIRMING",
                "kr_confirmation_reason": "kr_momentum_not_confirmed",
            },
        )

        self.assertEqual(decision.final_action, "PULLBACK_WAIT")
        self.assertEqual(decision.reason, "pullback_wait")
        self.assertEqual(decision.runtime_gate_reason, "")
        self.assertEqual(decision.demoted_to, "")
        self.assertEqual(decision.confirmation_state, "CONFIRMING")
        self.assertEqual(decision.confirmation_reason, "kr_momentum_not_confirmed")

    def test_kr_or_missing_at_high_demotes_buy_ready_to_probe(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "BUY_READY",
                "confidence": 0.9,
                "risk_tags": ["or_missing", "at_high"],
            },
            market="KR",
            execution_context={"data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.route, "PlanA.probe")
        self.assertEqual(decision.original_action, "BUY_READY")
        self.assertEqual(decision.demoted_to, "PROBE_READY")
        self.assertEqual(decision.runtime_gate_reason, "kr_risk_combo_gate")
        self.assertIn("kr_risk_combo_demoted", decision.warnings)

    def test_kr_or_missing_at_high_blocks_pullback_wait_without_confirmation(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "005930",
                "action": "PULLBACK_WAIT",
                "confidence": 0.72,
                "risk_tags": ["or_missing", "at_high"],
                "price_targets": {
                    "buy_zone_low": 69500,
                    "buy_zone_high": 70000,
                    "sell_target": 73000,
                    "stop_loss": 68000,
                    "hold_days": 1,
                    "confidence": 0.72,
                },
            },
            market="KR",
            execution_context={"data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "kr_risk_combo_confirmation_required")
        self.assertEqual(decision.runtime_gate_reason, "kr_risk_combo_gate")

    def test_us_risk_tags_do_not_apply_kr_combo_gate(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AAPL",
                "action": "BUY_READY",
                "confidence": 0.9,
                "risk_tags": ["or_missing", "at_high"],
            },
            market="US",
            execution_context={"data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "BUY_READY")
        self.assertEqual(decision.route, "PlanA.buy")

    def test_pathb_waiting_keeps_wait_when_buy_ready_is_inside_buy_zone(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "AAPL",
                "action": "BUY_READY",
                "confidence": 0.9,
                "price_targets": {"buy_zone_high": 101.0},
            },
            market="US",
            pathb_waiting=True,
            execution_context={"current_price": 100.5, "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertFalse(decision.cancel_pathb)
        self.assertEqual(decision.reason, "pathb_waiting_kept_inside_buy_zone")

    def test_pathb_waiting_keeps_wait_when_overextended(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AMD", "action": "BUY_READY", "confidence": 0.95},
            market="US",
            pathb_waiting=True,
            execution_context={
                "momentum_state": "overextended",
                "ret_5m_pct": 4.7,
                "threshold_used": 3.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertFalse(decision.cancel_pathb)
        self.assertEqual(decision.reason, "pathb_waiting_kept_overextended")

    def test_pathb_active_order_blocks_plana(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.9},
            market="US",
            pathb_active_order=True,
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "pathb_active_order_blocks_plana")

    def test_probe_ready_above_existing_pathb_zone_is_blocked(self) -> None:
        decision = route_candidate_action(
            {
                "ticker": "078150",
                "action": "PROBE_READY",
                "confidence": 0.58,
                "price_targets": {"buy_zone_high": 4660.0},
            },
            market="KR",
            pathb_waiting=True,
            execution_context={
                "current_price": 4655.0,
                "pathb_waiting_buy_zone_high": 4420.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "probe_blocked_above_pathb_zone")
        self.assertFalse(decision.cancel_pathb)
        self.assertEqual(decision.runtime_gate_reason, "above_pathb_buy_zone")

    def test_probe_ready_can_cancel_pathb_above_zone_when_confident(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "PROBE_READY", "confidence": 0.8},
            market="US",
            pathb_waiting=True,
            execution_context={
                "current_price": 105.0,
                "pathb_waiting_buy_zone_high": 101.0,
                "data_quality": "good",
            },
        )

        self.assertEqual(decision.final_action, "PROBE_READY")
        self.assertEqual(decision.reason, "probe_ready_cancels_pathb_above_zone")
        self.assertTrue(decision.cancel_pathb)

    def test_probe_ready_missing_data_does_not_cancel_pathb_above_zone(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "PROBE_READY", "confidence": 0.8},
            market="US",
            pathb_waiting=True,
            execution_context={
                "current_price": 105.0,
                "pathb_waiting_buy_zone_high": 101.0,
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "probe_blocked_above_pathb_zone")
        self.assertFalse(decision.cancel_pathb)
        self.assertTrue(decision.runtime_gate["data_quality_missing"])

    def test_buy_ready_minute_complete_can_cancel_pathb(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "BUY_READY", "confidence": 0.8},
            market="US",
            pathb_waiting=True,
            overextended=False,
            data_quality="minute_complete",
        )

        self.assertEqual(decision.route, "PlanA.buy")
        self.assertTrue(decision.cancel_pathb)

    def test_probe_ready_minute_complete_can_cancel_pathb_above_zone(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "PROBE_READY", "confidence": 0.8},
            market="US",
            pathb_waiting=True,
            execution_context={
                "current_price": 105.0,
                "pathb_waiting_buy_zone_high": 101.0,
                "data_quality": "minute_complete",
            },
        )

        self.assertEqual(decision.route, "PlanA.probe")
        self.assertTrue(decision.cancel_pathb)

    def test_watch_negative_context_suspends_pathb_shadow(self) -> None:
        decision = route_candidate_action(
            {"ticker": "KBI", "action": "WATCH", "reason": "fade 지속, 방향 미확인"},
            market="KR",
            pathb_waiting=True,
            execution_context={"momentum_state": "fade", "data_quality": "good"},
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "watch_keeps_pathb_waiting_hysteresis")
        self.assertFalse(decision.suspend_pathb)
        self.assertEqual(decision.runtime_gate_reason, "pathb_suspend_hysteresis")
        self.assertFalse(decision.cancel_pathb)

    def test_watch_negative_context_suspends_pathb_at_hysteresis_threshold(self) -> None:
        decision = route_candidate_action(
            {"ticker": "KBI", "action": "WATCH", "reason": "fade"},
            market="KR",
            pathb_waiting=True,
            execution_context={
                "momentum_state": "fade",
                "data_quality": "good",
                "pathb_wait_negative_watch_count": 3,
                "pathb_suspend_negative_watch_threshold": 3,
            },
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "watch_suspends_stale_pathb")
        self.assertTrue(decision.suspend_pathb)

    def test_avoid_suspends_pathb_shadow(self) -> None:
        decision = route_candidate_action(
            {"ticker": "KBI", "action": "AVOID"},
            market="KR",
            pathb_waiting=True,
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "claude_avoid")
        self.assertTrue(decision.suspend_pathb)

    def test_add_ready_requires_broker_and_local_position(self) -> None:
        decision = route_candidate_action(
            {"ticker": "AAPL", "action": "ADD_READY"},
            market="US",
            has_local_position=False,
            has_broker_position=True,
            add_enabled=True,
        )

        self.assertEqual(decision.final_action, "WATCH")
        self.assertEqual(decision.reason, "add_without_position")


if __name__ == "__main__":
    unittest.main()
