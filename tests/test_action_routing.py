from __future__ import annotations

import unittest

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
