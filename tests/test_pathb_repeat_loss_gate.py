"""Phase 3: 반복 손실 종목 멀티데이 쿨다운 게이트 테스트.

IREN(10회 -2.5%)/IONQ(5회 -9.1%)형 반복 적자 종목이 진입 즉시 역행(loss_cap MFE 중앙
+0.39%, 88% MFE<1%)하는 것을 막는다. lookback일 내 손실 max회 이상 + cooldown 미경과면 차단.
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from execution.path_arbiter import SameDayReentryGuard
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent


class RepeatLossGateTests(unittest.TestCase):
    def setUp(self):
        # 기본 정책을 명시적으로 고정(다른 테스트/환경 누수 방지).
        for k, v in {
            "PATHB_REPEAT_LOSS_GATE_ENABLED": "true",
            "PATHB_REPEAT_LOSS_LOOKBACK_DAYS": "10",
            "PATHB_REPEAT_LOSS_MAX": "3",
            "PATHB_REPEAT_LOSS_COOLDOWN_HOURS": "48",
        }.items():
            os.environ[k] = v

    def _store(self, tmp: str) -> EventStore:
        return EventStore(Path(tmp) / "events.db")

    def _close(self, store, ticker, occurred_at, pnl_pct, reason="CLOSED_LOSS_CAP", session="2026-06-09"):
        store.append(
            LifecycleEvent(
                event_type="CLOSED",
                market="US",
                runtime_mode="live",
                session_date=session,
                ticker=ticker,
                decision_id="d",
                prompt_version="v2",
                brain_snapshot_id="brain1",
                reason_code=reason,
                occurred_at=occurred_at.isoformat(),
                payload={"close_reason": reason, "pnl_pct": pnl_pct},
            )
        )

    def _eval(self, store, now, ticker="IREN"):
        return SameDayReentryGuard(store).evaluate(
            market="US", runtime_mode="live", session_date="2026-06-12", ticker=ticker, now=now
        )

    def test_three_losses_within_lookback_block(self):
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(3):
                self._close(store, "IREN", now - timedelta(days=i + 1, hours=1), -2.0)
            d = self._eval(store, now)
            self.assertFalse(d.allowed)
            self.assertEqual(d.reason_code, "REPEAT_LOSS_COOLDOWN")
            self.assertEqual(d.shadow["repeat_loss_count"], 3)

    def test_two_losses_pass(self):
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(2):
                self._close(store, "IREN", now - timedelta(days=i + 1, hours=1), -2.0)
            d = self._eval(store, now)
            self.assertTrue(d.allowed)

    def test_old_losses_pass_after_cooldown(self):
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            # 3회 손실이지만 마지막 손실이 3일 전(>48h cooldown)
            for i in range(3):
                self._close(store, "IREN", now - timedelta(days=i + 3, hours=1), -2.0)
            d = self._eval(store, now)
            self.assertTrue(d.allowed)

    def test_profit_closes_not_counted(self):
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(4):
                self._close(store, "IREN", now - timedelta(days=i + 1, hours=1), +1.5, reason="CLOSED_CLAUDE_PRICE_TARGET")
            d = self._eval(store, now)
            self.assertTrue(d.allowed)

    def test_disabled_gate_passes(self):
        os.environ["PATHB_REPEAT_LOSS_GATE_ENABLED"] = "false"
        now = datetime(2026, 6, 12, 15, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            for i in range(5):
                self._close(store, "IREN", now - timedelta(days=i + 1, hours=1), -2.0)
            d = self._eval(store, now)
            self.assertTrue(d.allowed)


if __name__ == "__main__":
    unittest.main()
