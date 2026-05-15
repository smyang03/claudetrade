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
from runtime.v2_lifecycle_runtime import V2LifecycleRuntime


class _DummyLifecycleBot:
    _mode = "live"

    def __init__(self) -> None:
        self.selection_meta = {"KR": {}}

    def _current_session_date_str(self, market: str) -> str:
        return "2026-05-11"


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

    def test_trade_ready_batch_payload_includes_ticker_origin(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            ids = registry.register_trade_ready_batch(
                market="US",
                runtime_mode="live",
                session_date="2026-05-11",
                tickers=["GXO"],
                prompt_version="v2",
                brain_snapshot_id="brain_1",
                selection_meta={
                    "trade_ready": ["GXO"],
                    "_pathb_wait_origins": {
                        "GXO": {
                            "origin_action": "PULLBACK_WAIT",
                            "origin_route": "pathb_wait_only",
                            "not_patha_trade_ready": True,
                        }
                    },
                },
            )

            events = store.events_for_decision(ids["GXO"])
            payload = events[0]["payload"]
            self.assertEqual(payload["ticker_origin"]["origin_action"], "PULLBACK_WAIT")
            self.assertTrue(payload["ticker_origin"]["not_patha_trade_ready"])

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

    def test_runtime_recovers_decision_id_for_executed_path_a_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)
            runtime.bot = _DummyLifecycleBot()
            runtime.enabled = True
            runtime.registry = DecisionRegistry(store)
            runtime.decision_ids = {"KR": {}}
            runtime.brain_snapshot_ids = {"KR": "brain_test"}
            runtime.brain_snapshot_store = None

            runtime.record_event(
                "ORDER_SENT",
                "KR",
                "067170",
                execution_id="buy-1",
                payload={"strategy": "momentum", "entry_route": "plan_a", "qty": 47},
            )
            decision = store.find_decision(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-11",
                ticker="067170",
            )
            self.assertIsNotNone(decision)
            decision_id = str(decision["decision_id"])

            runtime.record_event(
                "CLOSED",
                "KR",
                "067170",
                execution_id="sell-1",
                position_id="pos_KR_067170_buy-1",
                reason_code="CLOSED_PROFIT_FLOOR",
                payload={"raw_reason": "profit_floor", "qty": 47},
            )

            event_types = [event["event_type"] for event in store.events_for_decision(decision_id)]
            self.assertEqual(event_types, ["CLAUDE_TRADE_READY", "ORDER_SENT", "CLOSED"])
            self.assertEqual(runtime.decision_id_for_ticker("KR", "067170"), decision_id)

    def test_runtime_injects_path_type_for_pathb_lifecycle_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="US",
                runtime_mode="live",
                session_date="2026-05-11",
                ticker="STM",
                prompt_version="v2",
                brain_snapshot_id="brain_test",
            )
            runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)
            runtime.bot = _DummyLifecycleBot()
            runtime.enabled = True
            runtime.registry = registry
            runtime.decision_ids = {"US": {"STM": decision_id}}
            runtime.brain_snapshot_ids = {"US": "brain_test"}
            runtime.brain_snapshot_store = None

            runtime.record_event(
                "ORDER_ACKED",
                "US",
                "STM",
                decision_id=decision_id,
                execution_id="buy-1",
                payload={"entry_route": "path_b", "path_run_id": "path_20260511_US_STM_claude_price"},
            )
            runtime.record_event(
                "FILLED",
                "US",
                "STM",
                decision_id=decision_id,
                execution_id="buy-1",
                payload={"fill_price_native": 61.35},
            )

            events = store.events_for_decision(decision_id)
            filled = events[-1]

        self.assertEqual(filled["event_type"], "FILLED")
        self.assertEqual(filled["payload"]["entry_route"], "path_b")
        self.assertEqual(filled["payload"]["path_run_id"], "path_20260511_US_STM_claude_price")
        self.assertEqual(filled["payload"]["path_type"], "claude_price")

    def test_runtime_does_not_create_decision_from_close_only_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            runtime = V2LifecycleRuntime.__new__(V2LifecycleRuntime)
            runtime.bot = _DummyLifecycleBot()
            runtime.enabled = True
            runtime.registry = DecisionRegistry(store)
            runtime.decision_ids = {"KR": {}}
            runtime.brain_snapshot_ids = {"KR": "brain_test"}
            runtime.brain_snapshot_store = None

            runtime.record_event(
                "CLOSED",
                "KR",
                "067170",
                execution_id="sell-orphan",
                position_id="pos_KR_067170_legacy",
                reason_code="CLOSED_PROFIT_FLOOR",
                payload={"raw_reason": "profit_floor", "qty": 47},
            )

            decision = store.find_decision(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-11",
                ticker="067170",
            )
            self.assertIsNone(decision)
            self.assertEqual(store.count_events(), 0)

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

