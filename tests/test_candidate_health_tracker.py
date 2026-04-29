from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from bot.candidate_health import CandidateHealthTracker, compact_session_date


class CandidateHealthTrackerTests(unittest.TestCase):
    def _tracker(self, market: str = "KR") -> tuple[CandidateHealthTracker, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "candidate_health.json"
        return CandidateHealthTracker(market, "2026-04-29", path=path), path

    def test_persists_raw_fields_only(self) -> None:
        tracker, path = self._tracker()
        tracker.update_selection(
            watchlist=["058430"],
            trade_ready=["058430"],
            price_by_ticker={"058430": 10010},
            phase="session_open",
            now=datetime(2026, 4, 29, 9, 5),
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        rec = payload["tickers"]["058430"]
        self.assertEqual(rec["first_ready_price"], 10010.0)
        self.assertNotIn("health_state", rec)
        self.assertNotIn("current_vs_first_ready_pct", rec)
        self.assertGreaterEqual(rec["seen_count"], rec["ready_count"])

    def test_strong_ready_state_is_derived(self) -> None:
        tracker, _ = self._tracker()
        base = datetime(2026, 4, 29, 9, 0)
        tracker.update_selection(
            watchlist=["006340"],
            trade_ready=["006340"],
            price_by_ticker={"006340": 10000},
            now=base,
        )
        tracker.update_selection(
            watchlist=["006340"],
            trade_ready=["006340"],
            price_by_ticker={"006340": 10300},
            now=base + timedelta(minutes=5),
        )

        state = tracker.state_for("006340")
        self.assertEqual(state["health_state"], "STRONG_READY")
        self.assertEqual(state["current_vs_first_ready_pct"], 3.0)

    def test_weakening_ready_state_is_derived(self) -> None:
        tracker, _ = self._tracker()
        base = datetime(2026, 4, 29, 9, 0)
        for idx, price in enumerate([10000, 9900, 9780]):
            tracker.update_selection(
                watchlist=["058430"],
                trade_ready=["058430"],
                price_by_ticker={"058430": price},
                now=base + timedelta(minutes=idx * 5),
            )

        state = tracker.state_for("058430")
        self.assertEqual(state["health_state"], "WEAKENING_READY")
        self.assertLessEqual(state["mae_pct"], -2.0)
        self.assertFalse(state["recovered_first_ready"])

    def test_failed_ready_state_is_derived(self) -> None:
        tracker, _ = self._tracker()
        base = datetime(2026, 4, 29, 9, 0)
        for idx, price in enumerate([10000, 9850, 9650]):
            tracker.update_selection(
                watchlist=["002780"],
                trade_ready=["002780"],
                price_by_ticker={"002780": price},
                now=base + timedelta(minutes=idx * 5),
            )

        self.assertEqual(tracker.state_for("002780")["health_state"], "FAILED_READY")

    def test_recovered_first_ready_after_drawdown(self) -> None:
        tracker, _ = self._tracker()
        base = datetime(2026, 4, 29, 9, 0)
        for idx, price in enumerate([10000, 9800, 10050]):
            tracker.update_selection(
                watchlist=["001440"],
                trade_ready=["001440"],
                price_by_ticker={"001440": price},
                now=base + timedelta(minutes=idx * 5),
            )

        state = tracker.state_for("001440")
        self.assertTrue(state["recovered_first_ready"])
        self.assertEqual(state["current_vs_first_ready_pct"], 0.5)

    def test_watch_strengthening_and_watch_weak_are_derived(self) -> None:
        tracker, _ = self._tracker()
        base = datetime(2026, 4, 29, 9, 0)
        tracker.update_selection(
            watchlist=["417200", "098460"],
            trade_ready=[],
            price_by_ticker={"417200": 10000, "098460": 10000},
            now=base,
        )
        tracker.update_selection(
            watchlist=["417200", "098460"],
            trade_ready=[],
            price_by_ticker={"417200": 10250, "098460": 9750},
            now=base + timedelta(minutes=5),
        )

        self.assertEqual(tracker.state_for("417200")["health_state"], "WATCH_STRENGTHENING")
        self.assertEqual(tracker.state_for("098460")["health_state"], "WATCH_WEAK")

    def test_compact_session_date(self) -> None:
        self.assertEqual(compact_session_date("2026-04-29"), "20260429")


if __name__ == "__main__":
    unittest.main()

