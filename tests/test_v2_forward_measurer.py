from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from tools.v2_forward_measurer import calculate_forward_returns, measure_forward_pending


class V2ForwardMeasurerTests(unittest.TestCase):
    def test_calculate_forward_returns_from_close_prices(self) -> None:
        rows = [
            {"date": "2026-04-27", "close": "100"},
            {"date": "2026-04-28", "close": "103"},
            {"date": "2026-04-29", "close": "98"},
            {"date": "2026-04-30", "close": "110"},
        ]

        result = calculate_forward_returns(rows, "2026-04-27", [1, 3, 5])

        self.assertEqual(result["measured_horizons"], [1, 3])
        self.assertEqual(result["forward_returns"]["1d"], 3.0)
        self.assertEqual(result["forward_returns"]["3d"], 10.0)

    def test_measure_forward_pending_appends_idempotent_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "price"
            kr_dir = price_dir / "kr"
            kr_dir.mkdir(parents=True)
            (kr_dir / "kr_005930.csv").write_text(
                "date,open,high,low,close,volume\n"
                "2026-04-27,100,101,99,100,1000\n"
                "2026-04-28,103,104,102,103,1000\n"
                "2026-04-29,105,106,104,105,1000\n"
                "2026-04-30,110,111,109,110,1000\n"
                "2026-05-01,120,121,119,120,1000\n"
                "2026-05-04,125,126,124,125,1000\n",
                encoding="utf-8",
            )
            store = EventStore(root / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )

            result = measure_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
                price_dir=price_dir,
            )

            self.assertEqual(result["measured_count"], 1)
            events = store.events_for_decision(decision_id)
            measured = [event for event in events if event["event_type"] == "FORWARD_MEASURED"]
            self.assertEqual(len(measured), 1)
            self.assertTrue(measured[0]["payload"]["complete"])
            self.assertEqual(measured[0]["payload"]["forward_returns"]["1d"], 3.0)

            second = measure_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
                price_dir=price_dir,
            )
            self.assertEqual(second["measured_count"], 0)
            self.assertEqual(second["skipped"][0]["reason"], "already_measured")

    def test_dry_run_does_not_append_forward_measured(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            price_dir = root / "price"
            kr_dir = price_dir / "kr"
            kr_dir.mkdir(parents=True)
            (kr_dir / "kr_000660.csv").write_text(
                "date,open,high,low,close,volume\n"
                "2026-04-27,100,101,99,100,1000\n"
                "2026-04-28,101,102,100,101,1000\n",
                encoding="utf-8",
            )
            store = EventStore(root / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="000660",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )

            result = measure_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
                price_dir=price_dir,
                dry_run=True,
            )

            self.assertEqual(result["measured_count"], 1)
            event_types = [event["event_type"] for event in store.events_for_decision(decision_id)]
            self.assertNotIn("FORWARD_MEASURED", event_types)


if __name__ == "__main__":
    unittest.main()
