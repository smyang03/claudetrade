"""KR 브리지 핸드오프 이력 원장 회귀 테스트 (2026-08-06).

status 파일은 매 호출마다 덮어써서 마지막 결과만 남는다. 브리지는 진입창 동안
2분마다 호출되므로 창이 끝나면 그 안의 판정 이력이 사라진다. 첫 실주문 이후
"왜 안 샀는가"를 사후 복원하려면 상태 전이가 원장에 남아야 한다.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import runtime.kr_fallen_order_bridge as bridge


def _payload(status: str, reason: str = "", results=None, session: str = "2026-08-06") -> dict:
    return {
        "schema_version": "kr_fallen_execution_status_v1",
        "generated_at": "2026-08-06T09:10:00+09:00",
        "session_date": session,
        "active_rule": "R2",
        "last_result": {"status": status, "reason": reason, **({"results": results} if results else {})},
    }


class HandoffHistoryTests(unittest.TestCase):
    def _run(self, payloads: list[dict]) -> list[dict]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(bridge, "get_runtime_path",
                              side_effect=lambda *parts, **_: root.joinpath(*parts)):
                for payload in payloads:
                    bridge._append_history(payload)
            path = root / "data" / "shadow" / "kr_fallen_handoff_history.jsonl"
            if not path.exists():
                return []
            return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_state_transitions_are_recorded(self) -> None:
        rows = self._run([
            _payload("SKIPPED", "no_rule_candidates"),
            _payload("EVALUATED", "", results=[{"ticker": "005930", "status": "SUBMITTED"}]),
            _payload("SKIPPED", "outside_entry_window"),
        ])
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["last_result"]["results"][0]["ticker"], "005930")

    def test_repeated_identical_state_is_collapsed(self) -> None:
        # 진입창 동안 2분마다 같은 판정이 반복돼도 한 줄만 남아야 한다.
        rows = self._run([_payload("SKIPPED", "no_rule_candidates")] * 8)
        self.assertEqual(len(rows), 1)

    def test_reason_change_creates_new_row(self) -> None:
        rows = self._run([
            _payload("SKIPPED", "no_rule_candidates"),
            _payload("SKIPPED", "no_rule_candidates"),
            _payload("SKIPPED", "outside_entry_window"),
            _payload("SKIPPED", "outside_entry_window"),
        ])
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["last_result"]["reason"] for r in rows],
                         ["no_rule_candidates", "outside_entry_window"])

    def test_new_session_creates_new_row_even_if_same_reason(self) -> None:
        rows = self._run([
            _payload("SKIPPED", "no_rule_candidates", session="2026-08-06"),
            _payload("SKIPPED", "no_rule_candidates", session="2026-08-07"),
        ])
        self.assertEqual(len(rows), 2)

    def test_write_failure_does_not_raise(self) -> None:
        # 이력 기록 실패가 핸드오프 본류를 막으면 안 된다.
        with patch.object(bridge, "get_runtime_path", side_effect=OSError("disk full")):
            bridge._append_history(_payload("SKIPPED", "no_rule_candidates"))


if __name__ == "__main__":
    unittest.main()
