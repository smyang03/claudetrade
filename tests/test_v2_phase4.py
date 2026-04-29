from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from learning.approval_queue import BrainApprovalQueue
from learning.brain_snapshot import BrainSnapshotStore
from learning.candidate_builder import BrainCandidateBuilder
from learning.patterns import grade_pattern, mark_expired_patterns, prompt_eligible_patterns
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from lifecycle.quality_marker import DataQualityMarker
from lifecycle.validation import V2PhaseValidator


class V2Phase4Tests(unittest.TestCase):
    def test_brain_snapshot_hash_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = BrainSnapshotStore(Path(tmp))
            snapshot = store.create_snapshot(
                prompt_version="v2",
                market="US",
                session_date="2026-04-26",
                runtime_mode="live",
                patterns=[{"pattern": "x", "sample_count": 30}],
            )
            loaded = store.load(snapshot.brain_snapshot_id)
            self.assertEqual(loaded["brain_hash"], snapshot.brain_hash)
            self.assertEqual(loaded["brain_snapshot_id"], snapshot.brain_snapshot_id)

    def test_patterns_grade_expire_and_prompt_filter(self) -> None:
        self.assertEqual(grade_pattern(0), "observation_only")
        self.assertEqual(grade_pattern(10), "weak_reference")
        self.assertEqual(grade_pattern(30), "trusted_candidate")
        self.assertEqual(grade_pattern(50), "operating_principle_candidate")
        expired = mark_expired_patterns(
            [{"sample_count": 20, "last_verified_at": "2026-01-01"}],
            as_of=date(2026, 4, 26),
        )
        self.assertEqual(expired[0]["quality"], "SUSPECT")
        self.assertEqual(
            len(
                prompt_eligible_patterns(
                    [
                        {"grade": "weak_reference", "quality": "CLEAN"},
                        {"grade": "weak_reference", "quality": "CLEAN"},
                        {"grade": "weak_reference", "quality": "CLEAN"},
                        {"grade": "weak_reference", "quality": "CLEAN"},
                        {"grade": "trusted_candidate", "quality": "CLEAN"},
                        {"grade": "observation_only", "quality": "CLEAN"},
                    ]
                )
            ),
            4,
        )

    def test_approval_queue_accepts_only_clean_live_forward_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            queue = BrainApprovalQueue(Path(tmp) / "queue.jsonl")
            self.assertTrue(queue.submit(candidate={"id": "ok"}, runtime_mode="live", data_quality="CLEAN", forward_complete=True))
            self.assertFalse(queue.submit(candidate={"id": "paper"}, runtime_mode="paper", data_quality="CLEAN", forward_complete=True))
            self.assertFalse(queue.submit(candidate={"id": "dirty"}, runtime_mode="live", data_quality="DIRTY", forward_complete=True))
            self.assertEqual(len(queue.read_all()), 1)

    def test_quality_marker_and_candidate_builder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            decision_id = "dec_learning"
            store.create_decision(
                decision_id=decision_id,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain1",
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-04-26",
                "ticker": "005930",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain1",
            }
            store.append(LifecycleEvent(event_type="CLAUDE_TRADE_READY", **base))
            store.append(LifecycleEvent(event_type="ORDER_SENT", execution_id="exec1", **base))
            store.append(LifecycleEvent(event_type="FILLED", execution_id="exec1", position_id="pos1", **base))
            store.append(LifecycleEvent(event_type="CLOSED", execution_id="exec1", position_id="pos1", payload={"pnl_pct": 1.0}, **base))
            store.append(LifecycleEvent(event_type="FORWARD_MEASURED", payload={"forward_3d": 2.0, "benchmark_3d": 1.0}, **base))
            self.assertEqual(DataQualityMarker(store).mark_decision(decision_id), "CLEAN")
            candidate = BrainCandidateBuilder().build_candidate(store.events_for_decision(decision_id))
            self.assertIsNotNone(candidate)
            self.assertEqual(candidate["data_quality"], "CLEAN")
            self.assertTrue(candidate["learning_allowed"])

    def test_phase4_gate_runs_cumulatively(self) -> None:
        report = V2PhaseValidator(Path(__file__).resolve().parent.parent).validate(4)
        self.assertTrue(report["ok"], report)
        self.assertEqual([phase["phase"] for phase in report["phases"]], [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()

