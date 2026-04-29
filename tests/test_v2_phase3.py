from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from lifecycle.validation import V2PhaseValidator
from performance.decomposition import PerformanceDecomposer, decompose_decision_events
from review.daily_review import DailyReviewWriter


class V2Phase3Tests(unittest.TestCase):
    def _seed_store(self, path: Path) -> tuple[EventStore, str]:
        store = EventStore(path)
        decision_id = "dec_perf_1"
        store.create_decision(
            decision_id=decision_id,
            market="US",
            runtime_mode="live",
            session_date="2026-04-26",
            ticker="NVDA",
            prompt_version="v2",
            brain_snapshot_id="brain_perf",
        )
        base = {
            "market": "US",
            "runtime_mode": "live",
            "session_date": "2026-04-26",
            "ticker": "NVDA",
            "decision_id": decision_id,
            "prompt_version": "v2",
            "brain_snapshot_id": "brain_perf",
        }
        store.append(LifecycleEvent(event_type="CLAUDE_TRADE_READY", occurred_at="2026-04-26T00:00:00+00:00", **base))
        store.append(LifecycleEvent(event_type="FILLED", execution_id="exec1", position_id="pos1", occurred_at="2026-04-26T00:15:00+00:00", **base))
        store.append(
            LifecycleEvent(
                event_type="CLOSED",
                execution_id="exec1",
                position_id="pos1",
                occurred_at="2026-04-26T02:00:00+00:00",
                payload={"pnl_pct": 3.0, "pnl_krw": 1500, "mfe_pct": 6.0, "mae_pct": -2.0, "claude_cost_krw": 200},
                **base,
            )
        )
        store.append(
            LifecycleEvent(
                event_type="FORWARD_MEASURED",
                occurred_at="2026-04-29T00:00:00+00:00",
                payload={"forward_3d": 7.0, "benchmark_3d": 4.0},
                **base,
            )
        )
        return store, decision_id

    def test_decompose_decision_performance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, decision_id = self._seed_store(Path(tmp) / "events.db")
            perf = decompose_decision_events(store.events_for_decision(decision_id))
            self.assertEqual(perf.selection_alpha["3d"], 3.0)
            self.assertEqual(perf.entry_delay_minutes, 15.0)
            self.assertEqual(perf.exit_efficiency, 0.5)
            self.assertEqual(perf.net_pnl_after_claude_krw, 1300.0)

    def test_session_performance_and_daily_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._seed_store(Path(tmp) / "events.db")
            session = PerformanceDecomposer(store).session_performance(
                session_date="2026-04-26",
                runtime_mode="live",
                market="US",
            )
            self.assertEqual(session["actual_trade_result"]["total_pnl_krw"], 1500.0)
            self.assertEqual(session["selection_alpha"]["avg_3d"], 3.0)

            review = DailyReviewWriter(store, output_dir=Path(tmp) / "review").build_summary(
                session_date="2026-04-26",
                runtime_mode="live",
                market="US",
            )
            self.assertEqual(review["performance"]["net_pnl_after_claude"]["total_krw"], 1300.0)

    def test_phase3_gate_runs_cumulatively(self) -> None:
        report = V2PhaseValidator(Path(__file__).resolve().parent.parent).validate(3)
        self.assertTrue(report["ok"], report)
        self.assertEqual([phase["phase"] for phase in report["phases"]], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()

