from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from tools.v2_daily_loop import build_checks, diff_start_config, reserve_forward_pending


class V2DailyLoopTests(unittest.TestCase):
    def test_reserve_forward_pending_marks_trade_ready_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )

            result = reserve_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
            )

            self.assertEqual(result["decision_count"], 1)
            self.assertEqual(result["reserved_count"], 1)
            event_types = [event["event_type"] for event in store.events_for_decision(decision_id)]
            self.assertIn("FORWARD_PENDING_DATA", event_types)

            second = reserve_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
            )
            self.assertEqual(second["reserved_count"], 0)
            self.assertEqual(second["skipped"][0]["reason"], "already_pending")

    def test_dry_run_does_not_append_forward_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="000660",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )

            result = reserve_forward_pending(
                store,
                session_date="2026-04-27",
                runtime_mode="live",
                markets=["KR"],
                dry_run=True,
            )

            self.assertEqual(result["reserved_count"], 1)
            event_types = [event["event_type"] for event in store.events_for_decision(decision_id)]
            self.assertNotIn("FORWARD_PENDING_DATA", event_types)

    def test_config_diff_and_checks(self) -> None:
        current = {
            "enabled_markets": ["KR", "US"],
            "disabled_markets": [],
            "KR_FIXED_ORDER_KRW": 100000,
            "US_FIXED_ORDER_KRW": 100000,
            "KR_MIN_ORDER_KRW": 100000,
            "US_MIN_ORDER_KRW": 100000,
            "KR_MAX_POSITIONS": 10,
            "US_MAX_POSITIONS": 10,
            "V2_MAX_DAILY_ENTRIES": 10,
            "brain_policy": "fresh_v2_reference_v1",
            "same_close_policy": "research_only_disallowed_for_live",
            "env_overrides": {
                "US_FIXED_ORDER_KRW": "100000",
                "PATHB_MAX_POSITIONS": "10",
                "PATHB_MAX_DAILY_ENTRIES": "10",
            },
        }
        self.assertEqual(diff_start_config(None, current)["status"], "NO_PREVIOUS_CONFIG")
        self.assertEqual(diff_start_config(current, current)["status"], "UNCHANGED")
        self.assertTrue(all(item["ok"] for item in build_checks(current, {"decision_count": 0})))


if __name__ == "__main__":
    unittest.main()
