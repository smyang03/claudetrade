"""장 사이 시간대 봇 자기복구 — broker stale만 남으면 기동은 허용한다.

2026-08-04 실측 결함 회귀 테스트. 봇 기동 허용과 진입 허용은 분리된 판정이며,
stale 예외는 기동에만 적용되고 market_gates(진입)는 닫힌 채로 유지되어야 한다.
"""

from __future__ import annotations

import unittest

from tools.live_guardian import GuardianFinding, broker_stale_is_only_blocker


def _finding(name: str) -> GuardianFinding:
    return GuardianFinding(name=name, status="WARN", classification="hard_fail", detail=name)


class BrokerStaleOnlyBlockerTests(unittest.TestCase):
    def test_stale_only_allows_launch(self) -> None:
        hard_fail = [
            _finding("broker_truth.kr_stale_state"),
            _finding("broker_truth.us_stale_state"),
        ]
        self.assertTrue(broker_stale_is_only_blocker(hard_fail))

    def test_other_blocker_present_blocks_launch(self) -> None:
        hard_fail = [
            _finding("broker_truth.kr_stale_state"),
            _finding("runtime.order_unknown"),
        ]
        self.assertFalse(broker_stale_is_only_blocker(hard_fail))

    def test_empty_hard_fail_is_not_a_stale_exception(self) -> None:
        # blocker가 없으면 정상 경로에서 이미 기동이 허용된다.
        self.assertFalse(broker_stale_is_only_blocker([]))

    def test_non_stale_broker_truth_finding_still_blocks(self) -> None:
        hard_fail = [_finding("broker_truth.pathb_conflict")]
        self.assertFalse(broker_stale_is_only_blocker(hard_fail))


if __name__ == "__main__":
    unittest.main()
