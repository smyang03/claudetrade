from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from lifecycle.event_store import EventStore
from lifecycle.models import PathType, make_path_run_id


class PathBFoundationTests(unittest.TestCase):
    def test_make_path_run_id_contains_path_type(self) -> None:
        path_run_id = make_path_run_id(PathType.CLAUDE_PRICE, "KR", "2026-04-27", "005930")
        self.assertTrue(path_run_id.startswith("path_20260427_KR_005930_claude_price_"))

    def test_path_run_crud_and_active_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            store.create_path_run(
                path_run_id="path_a",
                decision_id="dec1",
                path_type="timing_adapter",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                status="FILLED",
                plan={"a": 1},
            )
            store.create_path_run(
                path_run_id="path_b",
                decision_id="dec1",
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                status="WAITING",
                plan={"b": 2},
            )
            store.create_path_run(
                path_run_id="path_closed",
                decision_id="dec1",
                path_type="claude_price",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                status="CLOSED",
                plan={},
            )

            store.update_path_run("path_b", status="HIT", plan={"b": 3})

            self.assertEqual(store.find_path_run("path_b")["status"], "HIT")
            self.assertEqual(store.find_path_run("path_b")["plan"]["b"], 3)
            active = store.active_path_runs_for_ticker(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
            )
            self.assertEqual({item["path_run_id"] for item in active}, {"path_a", "path_b"})
            session = store.path_runs_for_session(market="KR", runtime_mode="live", session_date="2026-04-27")
            self.assertEqual(len(session), 3)


if __name__ == "__main__":
    unittest.main()
