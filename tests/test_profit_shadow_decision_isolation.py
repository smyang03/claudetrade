from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from lifecycle.event_store import EventStore
from tools.profit_path_forward_monitor import _load_predictions


class ProfitShadowDecisionIsolationTests(unittest.TestCase):
    def test_shadow_observation_does_not_create_canonical_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            observation_id = store.append_profit_evidence_shadow(
                market="KR",
                runtime_mode="live",
                session_date="2026-07-13",
                ticker="005930",
                payload={
                    "strategy": "momentum",
                    "evidence": {
                        "model_version": "shadow-v1",
                        "decision_ts": "2026-07-13T00:40:00+00:00",
                        "path_name": "immediate",
                        "ood": False,
                        "feature_snapshot": {f"f{i}": i for i in range(8)},
                    },
                },
            )
            self.assertGreater(observation_id, 0)
            with store.connect() as con:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM v2_decisions").fetchone()[0], 0)
                self.assertEqual(con.execute("SELECT COUNT(*) FROM lifecycle_events").fetchone()[0], 0)
                predictions = _load_predictions(con, "KR")
            self.assertEqual(len(predictions), 1)
            self.assertLess(int(predictions.iloc[0]["event_id"]), 0)
            self.assertEqual(predictions.iloc[0]["ticker"], "005930")


if __name__ == "__main__":
    unittest.main()
