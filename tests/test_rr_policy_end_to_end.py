from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config.v2 import V2Config
from decision.claude_price_plan import make_price_plan
from execution.claude_price_adapter import ClaudePriceAdapter
from execution.safety_gate import PathBSafetyGate, SafetyContext
from lifecycle.event_store import EventStore
from minority_report.prompt_contracts import price_plan_contract
from runtime.pathb_runtime import PathBRuntime


def _plan(*, target: float = 111.5):
    return make_price_plan(
        decision_id="dec_rr",
        ticker="005930",
        market="KR",
        session_date="2026-07-13",
        buy_zone_low=99.0,
        buy_zone_high=100.0,
        sell_target=target,
        stop_loss=90.0,
        hold_days=2,
        confidence=0.7,
    )


def _ctx() -> SafetyContext:
    return SafetyContext(
        market="KR",
        runtime_mode="live",
        ticker="005930",
        price_krw=100.0,
        qty=1_000,
        order_cost_krw=100_000.0,
        cash_krw=1_000_000.0,
        min_order_krw=50_000.0,
        market_open=True,
        broker_trust_level="trusted",
    )


class RewardRiskEndToEndTests(unittest.TestCase):
    def test_prompt_uses_same_market_policy(self) -> None:
        with patch.dict(
            "os.environ",
            {"PATHB_MIN_REWARD_RISK_KR": "1.1", "PATHB_MIN_REWARD_RISK_US": "1.5"},
            clear=False,
        ):
            self.assertIn("for KR is 1.1", price_plan_contract("KR"))
            self.assertIn("for US is 1.5", price_plan_contract("US"))

    def test_registration_persists_threshold_and_submit_reuses_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            "os.environ", {"PATHB_CONSISTENT_REWARD_RISK": "true"}, clear=False
        ):
            store = EventStore(Path(tmp) / "events.db")
            plan = _plan()  # RR=1.15 under the consistent zone-high definition.
            ClaudePriceAdapter(store).register_plan(
                plan,
                runtime_mode="live",
                brain_snapshot_id="brain",
                min_reward_risk=1.1,
            )
            run = store.find_path_run(plan.path_run_id)
            self.assertEqual(run["plan"]["validated_min_reward_risk"], 1.1)
            self.assertEqual(run["plan"]["reward_risk_policy_version"], "pathb_rr_v2")

            runtime = PathBRuntime.__new__(PathBRuntime)
            reloaded = runtime._plan_from_run(run)
            self.assertIsNotNone(reloaded)
            # A stricter current setting must not retroactively cancel a plan
            # accepted under 1.1.
            decision = PathBSafetyGate(V2Config()).evaluate(
                _ctx(), plan=reloaded, min_reward_risk=1.5
            )
            self.assertTrue(decision.passed, decision)

    def test_reload_is_structural_for_legacy_plan_without_policy_metadata(self) -> None:
        legacy = _plan(target=105.0).to_dict()  # RR below any live entry threshold.
        legacy.pop("validated_min_reward_risk", None)
        legacy.pop("reward_risk_policy_version", None)
        runtime = PathBRuntime.__new__(PathBRuntime)
        reloaded = runtime._plan_from_run(
            {
                "decision_id": legacy["decision_id"],
                "market": legacy["market"],
                "session_date": legacy["session_date"],
                "ticker": legacy["ticker"],
                "plan": legacy,
            }
        )
        self.assertIsNotNone(reloaded)


if __name__ == "__main__":
    unittest.main()
