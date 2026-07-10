from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from decision.claude_price_plan import make_price_plan
from runtime import profit_evidence_gate as gate


NOW = datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc)


def valid_evidence(**overrides):
    payload = {
        "schema_version": "profit_evidence_v1",
        "model_version": "meta_us_claude_price_v1",
        "model_state": "PROBE",
        "decision_ts": (NOW - timedelta(minutes=10)).isoformat(),
        "p_target_before_stop_calibrated": 0.64,
        "expected_gross_pct": 1.20,
        "expected_cost_pct_p75": 0.55,
        "expected_net_pct": 0.60,
        "uncertainty": 0.18,
        "ood": False,
        "drift_state": "healthy",
        "validation_sample_n": 120,
        "validation_net_lcb_pct": 0.08,
        "calibration_ece": 0.06,
    }
    payload.update(overrides)
    return payload


class ProfitEvidenceGateTests(unittest.TestCase):
    def setUp(self) -> None:
        gate._SNAPSHOT_CACHE.clear()

    def test_off_allows_missing_evidence(self) -> None:
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "off"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="US", ticker="NVDA", strategy="path_b", evidence={}, now=NOW
            )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.passed)
        self.assertFalse(decision.would_block)

    def test_shadow_reports_would_block_but_allows(self) -> None:
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "shadow"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="KR", ticker="005930", strategy="momentum", evidence={}, now=NOW
            )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.passed)
        self.assertTrue(decision.would_block)
        self.assertIn("evidence_missing", decision.reasons)

    def test_enforce_missing_evidence_abstains(self) -> None:
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="US", ticker="NVDA", strategy="path_b", evidence={}, now=NOW
            )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason_code, "PROFIT_EVIDENCE_ABSTAIN")

    def test_valid_promoted_calibrated_evidence_passes(self) -> None:
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="US",
                ticker="NVDA",
                strategy="path_b",
                evidence=valid_evidence(),
                now=NOW,
            )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.reasons, ())

    def test_cost_understatement_and_optimistic_net_are_blocked(self) -> None:
        evidence = valid_evidence(expected_cost_pct_p75=0.10, expected_net_pct=1.16)
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="US", ticker="NVDA", strategy="path_b", evidence=evidence, now=NOW
            )
        self.assertFalse(decision.allowed)
        self.assertIn("cost_understated", decision.reasons)
        self.assertIn("net_math_inconsistent", decision.reasons)

    def test_stale_ood_drifted_model_is_blocked(self) -> None:
        evidence = valid_evidence(
            decision_ts=(NOW - timedelta(minutes=181)).isoformat(),
            ood=True,
            drift_state="degraded",
        )
        with patch.dict(os.environ, {"PROFIT_EVIDENCE_GATE_MODE": "enforce"}, clear=False):
            decision = gate.evaluate_profit_evidence(
                market="KR", ticker="005930", strategy="momentum", evidence=evidence, now=NOW
            )
        self.assertIn("evidence_stale", decision.reasons)
        self.assertIn("ood_or_ood_missing", decision.reasons)
        self.assertIn("drift_unhealthy_or_missing", decision.reasons)

    def test_market_path_override_has_highest_precedence(self) -> None:
        env = {
            "PROFIT_EVIDENCE_GATE_MODE": "off",
            "PROFIT_EVIDENCE_GATE_MODE_US": "shadow",
            "PROFIT_EVIDENCE_GATE_MODE_PATH_B": "shadow",
            "PROFIT_EVIDENCE_GATE_MODE_US_PATH_B": "enforce",
        }
        with patch.dict(os.environ, env, clear=False):
            self.assertEqual(gate.resolve_profit_evidence_mode("US", "path_b"), ("enforce", "PATH_B"))
            self.assertEqual(gate.resolve_profit_evidence_mode("KR", "momentum"), ("off", "PATH_A"))

    def test_snapshot_resolves_ticker_and_inherits_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            template = str(Path(tmp) / "profit_{market}.json")
            snapshot = {
                "schema_version": "profit_evidence_v1",
                "model_version": "snapshot_v1",
                "generated_at": NOW.isoformat(),
                "evidence_by_ticker": {"NVDA": valid_evidence(model_version="")},
            }
            Path(template.format(market="US")).write_text(json.dumps(snapshot), encoding="utf-8")
            with patch.dict(os.environ, {"PROFIT_EVIDENCE_SNAPSHOT_PATH": template}, clear=False):
                evidence, source = gate.resolve_profit_evidence(market="US", ticker="nvda")
        self.assertEqual(source, "snapshot")
        self.assertEqual(evidence["model_version"], "snapshot_v1")
        self.assertEqual(evidence["p_target_before_stop_calibrated"], 0.64)

    def test_price_plan_preserves_profit_evidence_contract(self) -> None:
        evidence = valid_evidence()
        plan = make_price_plan(
            decision_id="d1",
            ticker="NVDA",
            market="US",
            session_date="2026-07-10",
            buy_zone_low=100.0,
            buy_zone_high=101.0,
            sell_target=106.0,
            stop_loss=98.0,
            hold_days=1,
            confidence=0.8,
            profit_evidence=evidence,
        )
        self.assertEqual(plan.to_dict()["profit_evidence"]["model_version"], evidence["model_version"])


if __name__ == "__main__":
    unittest.main()
