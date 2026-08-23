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


class _FakeStore:
    """진입 decision_id 조회 스텁. entry_id=""면 '진입행 없음'."""

    def __init__(self, entry_id: str = "", *, raises: bool = False) -> None:
        self.entry_id, self.raises = entry_id, raises
        self.calls: list[dict] = []

    def open_entry_decision_id(self, *, market, runtime_mode, ticker):
        self.calls.append({"market": market, "runtime_mode": runtime_mode, "ticker": ticker})
        if self.raises:
            raise RuntimeError("db locked")
        return self.entry_id


def _bot(v2, store=None):
    bot = SimpleNamespace()
    bot.v2 = v2
    if store is not None:
        v2.registry = SimpleNamespace(store=store)
    bot._mode = "live"
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
        # 진입행을 못 찾으면 backfill 도구와 동일한 합성 ID로 폴백한다
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

    def test_inherits_entry_decision_id_when_found(self) -> None:
        """진입행이 있으면 그 decision_id를 이어받는다 (2026-08-21).

        합성 ID를 쓰면 진입행은 closed=0, 청산행은 filled=0으로 남아 양쪽이
        DIRTY(CLOSED_WITHOUT_FILL)가 되고 30건 판정의 품질 게이트에 걸린다.
        """
        v2 = _FakeV2()
        store = _FakeStore("dec_20260814_US_MXL_d477a053")
        _bot(v2, store)._record_sleeve_closed_event(
            {"ticker": "MXL", "source_strategy": "us_swing_5d"}, "US",
            "strategy_fixed_take_profit",
            {"ticker": "MXL", "pnl_pct": 12.46, "pnl": 37053},
        )
        self.assertEqual(v2.events[0]["decision_id"], "dec_20260814_US_MXL_d477a053")
        self.assertEqual(
            store.calls,
            [{"market": "US", "runtime_mode": "live", "ticker": "MXL"}],
        )

    def test_kr_entry_lookup_uses_raw_ticker(self) -> None:
        v2 = _FakeV2()
        store = _FakeStore("dec_20260819_KR_031330_abc12345")
        _bot(v2, store)._record_sleeve_closed_event(
            {"ticker": "031330", "source_strategy": "kr_fallen_5d"}, "KR",
            "strategy_horizon_exit", {"ticker": "031330", "pnl_pct": 3.1, "pnl": 9300},
        )
        self.assertEqual(v2.events[0]["decision_id"], "dec_20260819_KR_031330_abc12345")
        self.assertEqual(store.calls[0]["ticker"], "031330")

    def test_lookup_failure_falls_back_to_synthetic_id(self) -> None:
        """조회가 터져도 청산 기록 자체는 남는다 — 최악이 수리 이전 상태다."""
        v2 = _FakeV2()
        _bot(v2, _FakeStore(raises=True))._record_sleeve_closed_event(
            {"ticker": "MXL", "source_strategy": "us_swing_5d"}, "US",
            "strategy_fixed_take_profit", {"ticker": "MXL", "pnl_pct": 12.46},
        )
        self.assertEqual(len(v2.events), 1)
        self.assertEqual(v2.events[0]["decision_id"], "sleeve_US_MXL_20260818")


class DelayedFillCloseTests(unittest.TestCase):
    """지연 체결 청산 경로도 CLOSED를 발행한다 (2026-08-23, Codex 리뷰 P1-5).

    브로커가 매도를 접수만 하고 즉시 체결 확인이 없으면 직접 청산 경로는 pending으로
    빠져나가고, 나중에 `_close_position_from_pending_sell`이 포지션을 닫는다. 이 경로에는
    CLOSED 발행이 없어서 정본 원장에 진입행만 남고 영원히 "보유중"·DIRTY가 됐다.
    지금까지 안 물린 건 sleeve 청산이 전부 즉시 확인이었기 때문이다 — 잠재 결함이었다.
    """

    @staticmethod
    def _pending_bot(v2, *, closed_result):
        bot = SimpleNamespace()
        bot.v2 = v2
        bot._mode = "live"
        bot.SLEEVE_SOURCE_STRATEGIES = TradingBot.SLEEVE_SOURCE_STRATEGIES
        bot._current_session_date_str = lambda market: "2026-08-18"
        bot._price_to_krw = lambda price, market: float(price) * 1400.0
        bot._session_closed_tickers = {}
        bot.risk = SimpleNamespace(close_position=lambda *a, **kw: closed_result)
        bot.decision_events = []
        bot._record_decision_event = lambda *a, **kw: bot.decision_events.append(kw)
        bot._note_recent_sell_proceeds = lambda *a, **kw: None
        bot._record_sleeve_closed_event = TradingBot._record_sleeve_closed_event.__get__(bot)
        bot._close_position_from_pending_sell = (
            TradingBot._close_position_from_pending_sell.__get__(bot)
        )
        return bot

    def test_full_close_emits_closed(self) -> None:
        v2 = _FakeV2()
        closed = {"ticker": "MXL", "pnl_pct": 12.46, "pnl": 37053, "qty": 8,
                  "exit_price": 105_000.0, "source_strategy": "us_swing_5d"}
        bot = self._pending_bot(v2, closed_result=closed)
        result = bot._close_position_from_pending_sell(
            {"ticker": "MXL", "qty": 8, "source_strategy": "us_swing_5d", "entry": 93_646.0},
            "US", qty=8, price_native=75.0, reason="strategy_fixed_take_profit",
            order_no="123", broker_fill_source="broker_fills", resolution="filled",
            broker_fill_confirmed=True,
        )
        self.assertIsNotNone(result)
        closed_events = [e for e in v2.events if e["event_type"] == "CLOSED"]
        self.assertEqual(len(closed_events), 1)
        self.assertEqual(closed_events[0]["payload"]["pnl_pct"], 12.46)

    def test_partial_close_does_not_emit_closed(self) -> None:
        """부분 청산은 CLOSED가 아니다 — 포지션이 아직 열려 있다."""
        v2 = _FakeV2()
        closed = {"ticker": "MXL", "pnl_pct": 3.0, "pnl": 1000, "qty": 3,
                  "exit_price": 105_000.0, "source_strategy": "us_swing_5d"}
        bot = self._pending_bot(v2, closed_result=closed)
        bot.risk.close_position_qty = lambda *a, **kw: closed
        bot._close_position_from_pending_sell(
            {"ticker": "MXL", "qty": 8, "source_strategy": "us_swing_5d", "entry": 93_646.0},
            "US", qty=3, price_native=75.0, reason="strategy_fixed_take_profit",
            order_no="123", broker_fill_source="broker_fills", resolution="partial",
            broker_fill_confirmed=True,
        )
        self.assertEqual([e for e in v2.events if e["event_type"] == "CLOSED"], [])


if __name__ == "__main__":
    unittest.main()
