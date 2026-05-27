from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from config.v2 import V2Config
from execution.path_arbiter import PathExecutionArbiter, SameDayReentryGuard, build_late_entry_payload
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


class PathExecutionArbiterTests(unittest.TestCase):
    def _store(self, tmp: str) -> EventStore:
        return EventStore(Path(tmp) / "events.db")

    def _path_run(self, store: EventStore, status: str, *, plan: dict | None = None) -> None:
        store.create_path_run(
            path_run_id=f"path_{status.lower()}",
            decision_id="dec1",
            path_type="claude_price",
            market="KR",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker="005930",
            status=status,
            plan=plan or {"buy_zone_low": 52000, "buy_zone_high": 52500},
        )

    def test_pathb_order_unknown_blocks_same_ticker_path_a(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._path_run(store, "ORDER_UNKNOWN")

            decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                current_price=53000,
                strategy="momentum",
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, "PATHB_ORDER_UNKNOWN_SAME_TICKER")

    def test_pathb_buy_and_sell_in_progress_block_path_a(self) -> None:
        for status, reason in (
            ("HIT", "PATHB_ORDER_IN_PROGRESS"),
            ("ORDER_SENT", "PATHB_ORDER_IN_PROGRESS"),
            ("ORDER_ACKED", "PATHB_ORDER_IN_PROGRESS"),
            ("PARTIAL_FILLED", "PATHB_ORDER_IN_PROGRESS"),
            ("SELL_SENT", "PATHB_SELL_IN_PROGRESS"),
            ("SELL_ACKED", "PATHB_SELL_IN_PROGRESS"),
            ("SELL_PARTIAL_FILLED", "PATHB_SELL_IN_PROGRESS"),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                store = self._store(tmp)
                self._path_run(store, status)

                decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    current_price=53000,
                    strategy="momentum",
                )

                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason_code, reason)

    def test_waiting_pathb_does_not_block_but_records_price_chase_shadow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._path_run(store, "WAITING", plan={"buy_zone_low": 52000, "buy_zone_high": 52500})

            decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                current_price=53200,
                strategy="momentum",
            )

            self.assertTrue(decision.allowed)
            self.assertTrue(decision.shadow["pathb_waiting_price_chase"])
            self.assertEqual(decision.shadow["pathb_waiting_strategy"], "momentum")

    def test_waiting_pathb_records_same_ticker_shadow_even_inside_zone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._path_run(store, "WAITING", plan={"buy_zone_low": 52000, "buy_zone_high": 52500})

            decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                current_price=52200,
                strategy="momentum",
            )

            self.assertTrue(decision.allowed)
            self.assertTrue(decision.shadow["pathb_waiting_same_ticker"])
            self.assertEqual(decision.shadow["pathb_waiting_shadow_reason"], "PATHB_WAITING_SAME_TICKER_SHADOW")
            self.assertNotIn("pathb_waiting_price_chase", decision.shadow)

    def test_cancel_if_open_above_is_shadow_not_hard_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._path_run(
                store,
                "CANCELLED",
                plan={
                    "buy_zone_low": 52000,
                    "buy_zone_high": 52500,
                    "cancel_if_open_above": 53500,
                    "cancel_reason": "cancel_if_open_above",
                },
            )

            decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                current_price=54000,
                strategy="momentum",
            )

            self.assertTrue(decision.allowed)
            self.assertTrue(decision.shadow["pathb_cancel_price_chase"])
            self.assertEqual(decision.shadow["pathb_cancel_reason"], "cancel_if_open_above")

    def test_pathb_filled_is_left_to_already_holding_safety_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self._path_run(store, "FILLED")

            decision = PathExecutionArbiter(store).evaluate_path_a_entry(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                current_price=53000,
            )

            self.assertTrue(decision.allowed)

    def test_same_day_reentry_cooldown_blocks_recent_closed_event(self) -> None:
        now = datetime(2026, 4, 27, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            closed_event_id = store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec1",
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    reason_code="CLOSED_CLAUDE_PRICE_TARGET",
                    occurred_at=(now - timedelta(minutes=30)).isoformat(),
                    payload={"close_reason": "CLOSED_CLAUDE_PRICE_TARGET"},
                )
            )

            decision = SameDayReentryGuard(store, V2Config(kr_reentry_cooldown_minutes=120)).evaluate(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                now=now,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, "SAME_DAY_REENTRY_COOLDOWN")
            self.assertEqual(decision.shadow["same_day_reentry_closed_event_id"], closed_event_id)
            self.assertEqual(decision.shadow["same_day_reentry_closed_decision_id"], "dec1")

    def test_same_day_reentry_allows_after_cooldown_and_ignores_broker_sync(self) -> None:
        now = datetime(2026, 4, 27, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec1",
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    reason_code="CLOSED_TRAILING_STOP",
                    occurred_at=(now - timedelta(minutes=150)).isoformat(),
                    payload={"close_reason": "CLOSED_TRAILING_STOP"},
                )
            )

            decision = SameDayReentryGuard(store, V2Config(kr_reentry_cooldown_minutes=120)).evaluate(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                now=now,
            )
            self.assertTrue(decision.allowed)
            self.assertTrue(decision.shadow["same_day_reentry"])

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec1",
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    reason_code="CLOSED_BROKER_SYNC",
                    occurred_at=(now - timedelta(minutes=5)).isoformat(),
                    payload={"close_reason": "CLOSED_BROKER_SYNC"},
                )
            )

            decision = SameDayReentryGuard(store).evaluate(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                now=now,
            )
            self.assertTrue(decision.allowed)

    def test_broker_sync_close_does_not_hide_recent_real_close(self) -> None:
        now = datetime(2026, 4, 27, 4, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec1",
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    reason_code="CLOSED_TRAILING_STOP",
                    occurred_at=(now - timedelta(minutes=30)).isoformat(),
                    payload={"close_reason": "CLOSED_TRAILING_STOP"},
                )
            )
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    market="KR",
                    runtime_mode="live",
                    session_date="2026-04-27",
                    ticker="005930",
                    decision_id="dec2",
                    prompt_version="v2",
                    brain_snapshot_id="brain1",
                    reason_code="CLOSED_BROKER_SYNC",
                    occurred_at=(now - timedelta(minutes=5)).isoformat(),
                    payload={"close_reason": "CLOSED_BROKER_SYNC"},
                )
            )

            decision = SameDayReentryGuard(store, V2Config(kr_reentry_cooldown_minutes=120)).evaluate(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                now=now,
            )

            self.assertFalse(decision.allowed)
            self.assertEqual(decision.reason_code, "SAME_DAY_REENTRY_COOLDOWN")

    def test_late_entry_payload_is_observational(self) -> None:
        payload = build_late_entry_payload(
            entry_elapsed_min=185,
            change_pct_at_entry=21,
            from_high_pct=-0.5,
            selected_reason="late momentum",
            arbiter_shadow={"pathb_waiting_price_chase": True, "pathb_waiting_price_chase_pct": 1.2},
        )

        self.assertGreater(payload["late_entry_score"], 0)
        self.assertTrue(payload["pathb_waiting_price_chase"])
        self.assertEqual(payload["selected_reason"], "late momentum")


if __name__ == "__main__":
    unittest.main()
