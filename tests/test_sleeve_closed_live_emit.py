"""sleeve 계약 청산의 라이브 CLOSED 발행 (2026-08-17 운영자 승인).

이전에는 라이브 청산 경로가 CLOSED lifecycle 이벤트를 남기지 않아 정본 원장
(v2_canonical_performance)이 비었고, 사후 backfill에 의존해 quality_grade=DIRTY로
쌓였다. 이 배선이 빠지면 판정 원장에 다시 구멍이 생기므로 계약을 테스트로 고정한다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from trading_bot import TradingBot


class _FakeV2:
    def __init__(self) -> None:
        self.enabled = True
        self.events: list[dict] = []

    def record_event(self, event_type, market, ticker, *, decision_id="", execution_id="",
                     position_id="", reason_code="", payload=None):
        self.events.append({
            "event_type": event_type, "market": market, "ticker": ticker,
            "decision_id": decision_id, "reason_code": reason_code, "payload": dict(payload or {}),
        })


def _bot(v2):
    bot = SimpleNamespace()
    bot.v2 = v2
    bot._current_session_date_str = lambda market: "2026-08-18"
    bot.SLEEVE_SOURCE_STRATEGIES = TradingBot.SLEEVE_SOURCE_STRATEGIES
    bot._record_sleeve_closed_event = TradingBot._record_sleeve_closed_event.__get__(bot)
    return bot


class SleeveClosedLiveEmitTests(unittest.TestCase):
    def test_us_swing_close_emits_closed_with_backfill_compatible_id(self) -> None:
        v2 = _FakeV2()
        _bot(v2)._record_sleeve_closed_event(
            {"ticker": "MXL", "source_strategy": "us_swing_5d"}, "US",
            "strategy_fixed_take_profit",
            {"ticker": "MXL", "pnl_pct": 12.46, "pnl_krw": 37053, "qty": 3, "exit_price": 78.73},
        )
        self.assertEqual(len(v2.events), 1)
        ev = v2.events[0]
        self.assertEqual(ev["event_type"], "CLOSED")
        # backfill 도구와 동일한 합성 ID 규약이어야 두 경로가 같은 건을 가리킨다
        self.assertEqual(ev["decision_id"], "sleeve_US_MXL_20260818")
        self.assertEqual(ev["reason_code"], "CLOSED_STRATEGY_FIXED_TAKE_PROFIT")
        self.assertEqual(ev["payload"]["pnl_pct"], 12.46)
        self.assertTrue(ev["payload"]["sleeve_contract"])
        self.assertEqual(ev["payload"]["emitted_by"], "live_exit_path")

    def test_kr_fallen_close_emits_closed(self) -> None:
        v2 = _FakeV2()
        _bot(v2)._record_sleeve_closed_event(
            {"ticker": "005930", "source_strategy": "kr_fallen_5d"}, "KR",
            "strategy_horizon_exit",
            {"ticker": "005930", "pnl_pct": -1.2, "pnl_krw": -3600, "qty": 3, "exit_price": 70000},
        )
        self.assertEqual(len(v2.events), 1)
        self.assertEqual(v2.events[0]["decision_id"], "sleeve_KR_005930_20260818")

    def test_non_sleeve_strategy_is_ignored(self) -> None:
        v2 = _FakeV2()
        _bot(v2)._record_sleeve_closed_event(
            {"ticker": "SCHG", "source_strategy": "us_schg_bil_trend_v1"}, "US",
            "rebalance", {"ticker": "SCHG", "pnl_pct": 1.0},
        )
        self.assertEqual(v2.events, [])

    def test_disabled_v2_is_safe(self) -> None:
        v2 = _FakeV2()
        v2.enabled = False
        _bot(v2)._record_sleeve_closed_event(
            {"ticker": "MXL", "source_strategy": "us_swing_5d"}, "US",
            "strategy_fixed_take_profit", {"ticker": "MXL", "pnl_pct": 12.46},
        )
        self.assertEqual(v2.events, [])


if __name__ == "__main__":
    unittest.main()
