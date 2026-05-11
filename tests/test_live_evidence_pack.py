from __future__ import annotations

import unittest

from runtime.live_evidence_pack import (
    attach_live_evidence_summary,
    build_live_evidence_pack,
    classify_live_evidence_state,
)


class LiveEvidencePackTests(unittest.TestCase):
    def test_classifies_missing_confirmation_fields(self) -> None:
        state, missing = classify_live_evidence_state(
            {
                "current_price": 100.0,
                "ret_3m_pct": None,
                "ret_5m_pct": None,
            }
        )

        self.assertEqual(state, "missing")
        self.assertIn("opening_range_break", missing)
        self.assertIn("vwap_distance_pct", missing)
        self.assertIn("volume_ratio_open", missing)

    def test_confirmed_pack_keeps_buy_ceiling(self) -> None:
        pack = build_live_evidence_pack(
            market="US",
            ticker="AAOI",
            features={
                "current_price": 183.0,
                "ret_3m_pct": 1.2,
                "ret_5m_pct": 0.5,
                "opening_range_break": True,
                "vwap_distance_pct": 0.2,
                "volume_ratio_open": 3.1,
                "data_quality": "good",
            },
            action={"ticker": "AAOI", "action": "BUY_READY"},
            route={"ticker": "AAOI", "final_action": "BUY_READY", "route": "PlanA.buy"},
        )

        self.assertEqual(pack["data_state"], "confirmed")
        self.assertEqual(pack["action_ceiling"], "BUY_READY")
        self.assertEqual(pack["decision_trace"]["execution_state"], "routed")
        self.assertEqual(pack["decision_trace"]["execution_owner"], "claude")
        self.assertFalse(pack["decision_trace"]["local_promotion_allowed"])

    def test_partial_pack_caps_to_probe_ready(self) -> None:
        pack = build_live_evidence_pack(
            market="KR",
            ticker="004710",
            features={
                "current_price": 14140,
                "ret_3m_pct": 0.3,
                "ret_5m_pct": 0.4,
                "data_quality": "first_observed",
            },
            action={"ticker": "004710", "action": "BUY_READY"},
        )

        self.assertEqual(pack["data_state"], "partial")
        self.assertEqual(pack["action_ceiling"], "PROBE_READY")
        self.assertIn("data_partial", pack["negative_evidence"])
        self.assertFalse(pack["risk_control_view"]["system_may_promote"])
        self.assertTrue(pack["risk_control_view"]["reask_claude_required_for_promotion"])

    def test_attach_summary_adds_counts_and_packs(self) -> None:
        meta = attach_live_evidence_summary(
            market="KR",
            selection_meta={
                "watchlist": ["004710"],
                "candidate_actions": [{"ticker": "004710", "action": "BUY_READY"}],
                "_post_open_features_by_ticker": {
                    "004710": {
                        "current_price": 14140,
                        "ret_3m_pct": 0.3,
                        "ret_5m_pct": 0.4,
                    }
                },
            },
        )

        evidence = meta["_live_evidence"]
        self.assertEqual(evidence["version"], "live_evidence_pack.v1")
        self.assertEqual(evidence["counts"]["total"], 1)
        self.assertEqual(evidence["packs"]["004710"]["ticker"], "004710")


if __name__ == "__main__":
    unittest.main()
