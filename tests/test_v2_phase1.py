from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from config.v2 import DEFAULT_V2_CONFIG, SAFETY_REASON_CODES
from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEventType
from lifecycle.quality import evaluate_decision_quality, live_clean_learning_allowed
from lifecycle.validation import V2PhaseValidator
from review.daily_review import DailyReviewWriter


class V2Phase1Tests(unittest.TestCase):
    def test_trade_ready_issues_decision_id_and_persists_after_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "events.db"
            store = EventStore(db_path)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="NVDA",
                prompt_version="v2",
                brain_snapshot_id="brain_1",
            )

            self.assertTrue(decision_id.startswith("dec_20260426_US_NVDA_"))
            reopened = EventStore(db_path)
            events = reopened.events_for_decision(decision_id)
            self.assertEqual([event["event_type"] for event in events], ["CLAUDE_TRADE_READY"])

    def test_safety_and_timing_events_are_recordable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_1",
            )
            registry.record_event(
                event_type=LifecycleEventType.SAFETY_PASSED,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                decision_id=decision_id,
                prompt_version="v2",
                brain_snapshot_id="brain_1",
            )
            registry.record_event(
                event_type=LifecycleEventType.TIMING_EXPIRED,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                decision_id=decision_id,
                prompt_version="v2",
                brain_snapshot_id="brain_1",
                payload={"forward_measure_required": True},
            )

            event_types = [event["event_type"] for event in store.events_for_decision(decision_id)]
            self.assertIn("SAFETY_PASSED", event_types)
            self.assertIn("TIMING_EXPIRED", event_types)

    def test_quality_rules_block_dirty_and_paper_learning(self) -> None:
        dirty = evaluate_decision_quality(
            [
                {"event_type": "CLAUDE_TRADE_READY"},
                {"event_type": "FILLED"},
                {"event_type": "FORWARD_MEASURED"},
            ]
        )
        self.assertEqual(dirty.grade.value, "DIRTY")
        self.assertFalse(dirty.learning_allowed)
        self.assertFalse(live_clean_learning_allowed(runtime_mode="paper", quality="CLEAN", forward_complete=True))
        self.assertTrue(live_clean_learning_allowed(runtime_mode="live", quality="CLEAN", forward_complete=True))

    def test_daily_review_writes_json_and_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_1",
            )
            registry.record_event(
                event_type=LifecycleEventType.SAFETY_BLOCKED,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                decision_id=decision_id,
                prompt_version="v2",
                brain_snapshot_id="brain_1",
                reason_code="INSUFFICIENT_CASH",
            )

            paths = DailyReviewWriter(store, output_dir=Path(tmp) / "review").write(
                session_date="2026-04-26",
                runtime_mode="live",
                market="KR",
            )
            self.assertTrue(Path(paths["json"]).exists())
            self.assertTrue(Path(paths["markdown"]).exists())

    def test_phase1_gate_passes_cumulatively_for_phase1(self) -> None:
        report = V2PhaseValidator(Path(__file__).resolve().parent.parent).validate(1)
        self.assertTrue(report["ok"], report)

    def test_v2_config_has_required_defaults(self) -> None:
        self.assertEqual(DEFAULT_V2_CONFIG.kr_fixed_order_krw, 100_000)
        self.assertIn("ORDER_UNKNOWN_UNRESOLVED", SAFETY_REASON_CODES)


if __name__ == "__main__":
    unittest.main()

