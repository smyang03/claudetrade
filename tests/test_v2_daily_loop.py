from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from tools.v2_daily_loop import (
    _forward_measurement_complete,
    build_checks,
    diff_start_config,
    reserve_forward_pending,
    run_daily_loop,
)


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
            "KR_FIXED_ORDER_KRW": 200000,
            "US_FIXED_ORDER_KRW": 200000,
            "KR_MIN_ORDER_KRW": 50000,
            "US_MIN_ORDER_KRW": 50000,
            "KR_MAX_POSITIONS": 10,
            "US_MAX_POSITIONS": 10,
            "V2_MAX_DAILY_ENTRIES": 10,
            "brain_policy": "fresh_v2_reference_v1",
            "same_close_policy": "research_only_disallowed_for_live",
            "env_overrides": {
                "US_FIXED_ORDER_KRW": "200000",
                "PATHB_MAX_POSITIONS": "10",
                "PATHB_MAX_DAILY_ENTRIES": "10",
            },
        }
        self.assertEqual(diff_start_config(None, current)["status"], "NO_PREVIOUS_CONFIG")
        self.assertEqual(diff_start_config(current, current)["status"], "UNCHANGED")
        self.assertTrue(
            all(
                item["ok"]
                for item in build_checks(
                    current,
                    {"decision_count": 0},
                    {"measured_count": 0},
                    {"KR": {"selected": 0, "written": 0, "dry_run": True, "learning_allowed": 0}},
                )
            )
        )

    def test_forward_measurement_complete_uses_pending_due_horizons(self) -> None:
        events = [
            {"event_type": "FORWARD_PENDING_DATA", "payload": {"due_horizons": ["1d"]}},
            {"event_type": "FORWARD_MEASURED", "payload": {"measured_horizons": ["1d"]}},
        ]

        self.assertTrue(_forward_measurement_complete(events))

    def test_run_daily_loop_catches_up_forward_sessions_and_syncs_learning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = {
                "enabled_markets": ["KR", "US"],
                "disabled_markets": [],
                "KR_FIXED_ORDER_KRW": 200000,
                "US_FIXED_ORDER_KRW": 200000,
                "KR_MIN_ORDER_KRW": 50000,
                "US_MIN_ORDER_KRW": 50000,
                "KR_MAX_POSITIONS": 10,
                "US_MAX_POSITIONS": 10,
                "V2_MAX_DAILY_ENTRIES": 10,
                "brain_policy": "fresh_v2_reference_v1",
                "same_close_policy": "research_only_disallowed_for_live",
                "env_overrides": {
                    "US_FIXED_ORDER_KRW": "200000",
                    "PATHB_MAX_POSITIONS": "10",
                    "PATHB_MAX_DAILY_ENTRIES": "10",
                },
            }
            config_path = root / "v2_start_config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            price_dir = root / "data" / "price" / "kr"
            price_dir.mkdir(parents=True)
            (price_dir / "kr_005930.csv").write_text(
                "date,open,high,low,close,volume\n"
                "2026-05-08,100,101,99,100,1000\n"
                "2026-05-11,101,102,100,101,1000\n"
                "2026-05-12,102,103,101,102,1000\n"
                "2026-05-13,103,104,102,103,1000\n"
                "2026-05-14,104,105,103,104,1000\n"
                "2026-05-15,105,106,104,105,1000\n",
                encoding="utf-8-sig",
            )
            store = EventStore(root / "events.db")
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-05-08",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_kr",
            )

            payload = run_daily_loop(
                session_date="2026-05-10",
                runtime_mode="live",
                market="KR",
                config_path=config_path,
                dry_run=False,
                run_simulation=False,
                run_optimizer=False,
                store=store,
                root=root,
                output_dir=root / "reports",
                forward_lookback_days=3,
            )

            self.assertEqual(payload["forward_sessions"], ["2026-05-08", "2026-05-09", "2026-05-10"])
            self.assertEqual(payload["forward_pending"]["decision_count"], 1)
            self.assertEqual(payload["forward_measured"]["measured_count"], 1)
            self.assertEqual(payload["learning_sync"]["KR"]["written"], 1)
            self.assertEqual(payload["learning_sync"]["KR"]["learning_allowed"], 1)
            self.assertTrue(all(item["ok"] for item in payload["checks"]))
            events = store.events_for_decision(decision_id)
            self.assertTrue(_forward_measurement_complete(events))
            with closing(sqlite3.connect(root / "data" / "ml" / "decisions.db")) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT forward_complete, quality_grade, learning_allowed FROM v2_learning_performance WHERE v2_decision_id=?",
                    (decision_id,),
                ).fetchone()
            self.assertEqual(row["forward_complete"], 1)
            self.assertEqual(row["quality_grade"], "CLEAN")
            self.assertEqual(row["learning_allowed"], 1)


if __name__ == "__main__":
    unittest.main()


class SleeveClosedBackfillWiringTests(unittest.TestCase):
    """sleeve 청산 CLOSED 자동 주입 배선 (2026-08-17).

    라이브 청산 경로가 CLOSED를 발행하지 않아 정본 net(v2_canonical_performance)이
    비어 있었다. sync는 이벤트를 옮길 뿐 만들지 않으므로, 일일 루프가 sync **직전에**
    로그 기반 주입을 돌려야 그날 정본이 채워진다. 이 배선이 사라지면 판정 원장에
    조용히 구멍이 생기므로(CVI·MXL 실측) 호출 자체를 고정한다.
    """

    def test_daily_loop_calls_sleeve_backfill_before_sync(self) -> None:
        import inspect

        from tools import v2_daily_loop

        source = inspect.getsource(v2_daily_loop.run_daily_loop)
        self.assertIn("backfill_sleeve_closed(", source)
        self.assertLess(
            source.index("backfill_sleeve_closed("),
            source.index("sync_v2_learning_performance("),
            "backfill은 sync보다 먼저 실행돼야 그날 정본에 반영된다",
        )

    def test_backfill_helper_is_idempotent_and_reports_summary(self) -> None:
        from tools.backfill_sleeve_closed_events import backfill_sleeve_closed

        summary = backfill_sleeve_closed(dry_run=True, verbose=False)
        for key in ("scanned", "already", "pending", "written", "dry_run"):
            self.assertIn(key, summary)
        self.assertTrue(summary["dry_run"])
        self.assertEqual(summary["written"], 0)
