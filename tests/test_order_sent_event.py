# -*- coding: utf-8 -*-
"""ORDER_SENT lifecycle 이벤트 계약 테스트 (2026-08-22).

배경: `lifecycle/quality.py`가 **FILLED은 있는데 ORDER_SENT가 없으면 DIRTY**로 판정하는데,
이 이벤트를 발행하는 코드가 한 곳도 없었다(08-01 이후 실측: FILLED 11 · ORDER_SENT 0).
그 결과 코호트 10건이 예외 없이 `FILLED_WITHOUT_ORDER_SENT` → learning_allowed=0.
**30건이 다 차도 학습·판정에 쓸 수 있는 건이 0건**이었다.

`_add_pending_order` 한 곳에 붙인 이유: us_swing(micro_probe)·kr_fallen·PathA가 모두
이 경로를 지난다. 주문 경로마다 따로 넣으면 하나를 빠뜨린다.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from lifecycle.quality import evaluate_decision_quality
from trading_bot import TradingBot


class _Bot:
    def __init__(self, v2=True):
        self.v2 = object() if v2 else None
        self.events: list[dict] = []
        self._emit_order_sent_event = TradingBot._emit_order_sent_event.__get__(self)

    def _v2_record_lifecycle_event(self, event_type, market, ticker, **kw):
        self.events.append({"event_type": event_type, "market": market, "ticker": ticker, **kw})

    def _v2_decision_id_for_ticker(self, market, ticker):
        return f"dec_20260822_{market}_{ticker}_stub"


class OrderSentEmitTests(unittest.TestCase):
    def test_emits_order_sent_with_decision_id(self):
        bot = _Bot()
        bot._emit_order_sent_event(
            {"order_no": "0031236487", "qty": 9, "raw_price": 55.115,
             "strategy": "micro_probe", "source_strategy": "us_swing_5d"},
            "US", "SEI",
        )
        self.assertEqual(len(bot.events), 1)
        ev = bot.events[0]
        self.assertEqual(ev["event_type"], "ORDER_SENT")
        self.assertEqual(ev["ticker"], "SEI")
        self.assertEqual(ev["decision_id"], "dec_20260822_US_SEI_stub")
        self.assertEqual(ev["payload"]["order_no"], "0031236487")
        self.assertEqual(ev["payload"]["qty"], 9)
        self.assertEqual(ev["payload"]["source_strategy"], "us_swing_5d")

    def test_prefers_order_supplied_decision_id(self):
        bot = _Bot()
        bot._emit_order_sent_event(
            {"order_no": "1", "v2_decision_id": "dec_real_id", "qty": 1}, "US", "AVAV")
        self.assertEqual(bot.events[0]["decision_id"], "dec_real_id")

    def test_same_order_no_emits_once(self):
        """_add_pending_order는 주문 갱신 때도 호출된다 — 중복 발행 금지."""
        bot = _Bot()
        for _ in range(3):
            bot._emit_order_sent_event({"order_no": "SAME", "qty": 1}, "US", "SEI")
        self.assertEqual(len(bot.events), 1)

    def test_no_order_no_is_skipped(self):
        bot = _Bot()
        bot._emit_order_sent_event({"order_no": "", "qty": 1}, "US", "SEI")
        self.assertEqual(bot.events, [])

    def test_v2_disabled_is_noop(self):
        bot = _Bot(v2=False)
        bot._emit_order_sent_event({"order_no": "1", "qty": 1}, "US", "SEI")
        self.assertEqual(bot.events, [])

    def test_failure_does_not_raise(self):
        """기록 계층 전용 — 실패해도 주문 흐름을 막지 않는다."""
        bot = _Bot()
        bot._v2_record_lifecycle_event = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        bot._emit_order_sent_event({"order_no": "X", "qty": 1}, "US", "SEI")  # 예외가 나오면 실패


class QualityGradeTests(unittest.TestCase):
    """ORDER_SENT가 붙으면 실제로 DIRTY를 벗어나는지 — 수리의 목적 자체."""

    def _events(self, types):
        return [{"event_type": t, "payload": {}} for t in types]

    def test_filled_without_order_sent_is_dirty(self):
        q = evaluate_decision_quality(self._events(
            ["CLAUDE_TRADE_READY", "FILLED", "FORWARD_MEASURED", "CLOSED"]))
        self.assertIn("FILLED_WITHOUT_ORDER_SENT", q.reasons)
        self.assertFalse(q.learning_allowed)

    def test_with_order_sent_becomes_clean(self):
        q = evaluate_decision_quality(self._events(
            ["CLAUDE_TRADE_READY", "ORDER_SENT", "FILLED", "FORWARD_MEASURED", "CLOSED"]))
        self.assertEqual(q.reasons, ())
        self.assertTrue(q.learning_allowed, "ORDER_SENT가 붙으면 학습 가능해야 한다")


if __name__ == "__main__":
    unittest.main()
