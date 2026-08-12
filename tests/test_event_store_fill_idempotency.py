from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


def _fill_event(**overrides) -> LifecycleEvent:
    base = dict(
        event_type="FILLED",
        market="KR",
        runtime_mode="live",
        session_date="2026-08-13",
        ticker="018880",
        decision_id="dec_20260813_KR_018880_test0001",
        prompt_version="v1",
        brain_snapshot_id="brain_test",
        execution_id="0006642200",
        payload={"order_no": "0006642200"},
    )
    base.update(overrides)
    return LifecycleEvent(**base)


class FillIdempotencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EventStore(Path(self._tmp.name) / "events.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_duplicate_filled_same_execution_is_deduped(self) -> None:
        # 실측 사례(2026-07-23 KR 018880): 두 emit 지점이 같은 체결에 FILLED를
        # 각각 기록 — 두 번째 append는 새 행을 만들지 않아야 한다.
        first_id = self.store.append(_fill_event())
        second_id = self.store.append(_fill_event(payload={"price": 3655.0}))
        self.assertEqual(first_id, second_id)
        self.assertEqual(self.store.count_events("FILLED"), 1)

    def test_duplicate_filled_merges_missing_payload_keys(self) -> None:
        self.store.append(_fill_event(payload={"order_no": "0006642200"}))
        self.store.append(_fill_event(payload={"price": 3655.0, "order_no": "IGNORED"}))
        events = self.store.events_for_decision("dec_20260813_KR_018880_test0001")
        self.assertEqual(len(events), 1)
        payload = events[0]["payload"]
        self.assertEqual(payload["order_no"], "0006642200")  # 기존 값 우선
        self.assertEqual(payload["price"], 3655.0)  # 없던 키만 보충

    def test_filled_without_execution_id_is_not_deduped(self) -> None:
        # execution_id가 없으면 같은 체결인지 판별 불가 → dedupe 금지
        self.store.append(_fill_event(execution_id=None, payload={}))
        self.store.append(_fill_event(execution_id=None, payload={}))
        self.assertEqual(self.store.count_events("FILLED"), 2)

    def test_different_execution_ids_both_recorded(self) -> None:
        self.store.append(_fill_event(execution_id="0006642200"))
        self.store.append(_fill_event(execution_id="0006642201"))
        self.assertEqual(self.store.count_events("FILLED"), 2)

    def test_partial_filled_is_not_deduped(self) -> None:
        # 분할체결은 같은 주문에 여러 번이 정상
        self.store.append(_fill_event(event_type="PARTIAL_FILLED"))
        self.store.append(_fill_event(event_type="PARTIAL_FILLED"))
        self.assertEqual(self.store.count_events("PARTIAL_FILLED"), 2)

    def test_append_many_dedupes_within_batch(self) -> None:
        ids = self.store.append_many([_fill_event(), _fill_event(payload={"price": 3655.0})])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(self.store.count_events("FILLED"), 1)


if __name__ == "__main__":
    unittest.main()
