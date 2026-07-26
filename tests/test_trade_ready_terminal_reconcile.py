"""TRADE_READY 종결 catch-all 계약 검증.

배경: 실측(2026-07-13~24) CLAUDE_TRADE_READY 26종목 중 9건(34.6%)이 종결 이벤트 없이
사라져 "왜 안 샀는가"를 사후 추적할 수 없었다. session_close에서 미종결 건을
TRADE_READY_UNRESOLVED로 남긴다. 관측 전용이며 주문·게이트에 관여하지 않는다.

TradingBot 전체를 띄우지 않고 필요한 협력자만 stub으로 붙여 메서드를 직접 검증한다.
"""
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.models import LifecycleEventType, normalize_event_type  # noqa: E402


class _Store:
    def __init__(self, events):
        self._events = events
        self.calls = []

    def events_for_session(self, *, market=None, runtime_mode=None, session_date=None):
        self.calls.append((market, runtime_mode, session_date))
        return list(self._events)


class _Bot:
    """_reconcile_trade_ready_terminal이 실제로 쓰는 표면만 흉내낸다."""

    def __init__(self, events):
        store = _Store(events)
        self.v2 = types.SimpleNamespace(registry=types.SimpleNamespace(store=store))
        self.store = store
        self._mode = "live"
        self.recorded = []

    def _current_session_date_str(self, market):
        return "2026-07-24"

    def _v2_record_lifecycle_event(self, event_type, market, ticker, **kwargs):
        normalize_event_type(event_type)  # 미등록 타입이면 여기서 터진다
        self.recorded.append(
            {"event_type": event_type, "market": market, "ticker": ticker, **kwargs}
        )


def _ev(ticker, event_type, **kw):
    base = {
        "ticker": ticker,
        "event_type": event_type,
        "decision_id": kw.pop("decision_id", "dec_x"),
        "reason_code": kw.pop("reason_code", ""),
        "created_at": kw.pop("created_at", "2026-07-24T10:00:00"),
    }
    base.update(kw)
    return base


def _bind(bot):
    import trading_bot

    method = trading_bot.TradingBot._reconcile_trade_ready_terminal
    bot._TRADE_READY_TERMINAL_EVENTS = trading_bot.TradingBot._TRADE_READY_TERMINAL_EVENTS
    return types.MethodType(method, bot)


class TradeReadyTerminalReconcileTests(unittest.TestCase):
    def test_event_type_is_registered(self):
        self.assertEqual(
            normalize_event_type("TRADE_READY_UNRESOLVED"),
            LifecycleEventType.TRADE_READY_UNRESOLVED.value,
        )

    def test_silent_trade_ready_is_recorded(self):
        bot = _Bot([
            _ev("SMCI", "CLAUDE_TRADE_READY"),
            _ev("SMCI", "FORWARD_PENDING_DATA"),
        ])
        summary = _bind(bot)("US")
        self.assertEqual(summary["trade_ready"], 1)
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(summary["recorded"], 1)
        rec = bot.recorded[0]
        self.assertEqual(rec["event_type"], "TRADE_READY_UNRESOLVED")
        self.assertEqual(rec["reason_code"], "TRADE_READY_SILENT")
        self.assertEqual(rec["payload"]["kind"], "trade_ready_silent")
        self.assertTrue(rec["payload"]["observation_only"])
        self.assertEqual(rec["payload"]["last_event_type"], "FORWARD_PENDING_DATA")

    def test_plan_created_without_terminal_is_flagged_separately(self):
        bot = _Bot([
            _ev("215790", "CLAUDE_TRADE_READY"),
            _ev("215790", "CLAUDE_PRICE_PLAN_CREATED"),
            _ev("215790", "CLAUDE_PRICE_WAITING"),
            _ev("215790", "PATHB_SELECTION_RECONCILE"),
        ])
        summary = _bind(bot)("KR")
        self.assertEqual(summary["recorded"], 1)
        self.assertEqual(bot.recorded[0]["payload"]["kind"], "plan_created_no_terminal")

    def test_terminal_events_are_not_flagged(self):
        for terminal in ("FILLED", "ORDER_SENT", "SAFETY_BLOCKED",
                         "TRADE_READY_NO_SUBMIT", "CLAUDE_PRICE_EXPIRED",
                         "CLAUDE_PRICE_CANCELLED"):
            with self.subTest(terminal=terminal):
                bot = _Bot([
                    _ev("AAA", "CLAUDE_TRADE_READY"),
                    _ev("AAA", terminal),
                ])
                summary = _bind(bot)("US")
                self.assertEqual(summary["unresolved"], 0, terminal)
                self.assertEqual(bot.recorded, [])

    def test_idempotent_when_already_recorded(self):
        bot = _Bot([
            _ev("MBLY", "CLAUDE_TRADE_READY"),
            _ev("MBLY", "TRADE_READY_UNRESOLVED"),
        ])
        summary = _bind(bot)("US")
        self.assertEqual(summary["unresolved"], 1)
        self.assertEqual(summary["recorded"], 0)
        self.assertEqual(bot.recorded, [])

    def test_ticker_without_trade_ready_is_ignored(self):
        bot = _Bot([_ev("NOPE", "FORWARD_PENDING_DATA")])
        summary = _bind(bot)("US")
        self.assertEqual(summary["trade_ready"], 0)
        self.assertEqual(bot.recorded, [])

    def test_missing_store_returns_empty_summary(self):
        bot = _Bot([])
        bot.v2 = None
        summary = _bind(bot)("KR")
        self.assertEqual(summary["trade_ready"], 0)
        self.assertEqual(summary["recorded"], 0)

    def test_store_failure_does_not_raise(self):
        bot = _Bot([])

        def boom(**kwargs):
            raise RuntimeError("db locked")

        bot.v2.registry.store.events_for_session = boom
        summary = _bind(bot)("KR")  # 예외가 새어나오면 세션 마감이 깨진다
        self.assertEqual(summary["unresolved"], 0)

    def test_session_query_uses_runtime_mode_and_market(self):
        bot = _Bot([_ev("AAA", "CLAUDE_TRADE_READY"), _ev("AAA", "FILLED")])
        _bind(bot)("us")
        self.assertEqual(bot.store.calls, [("US", "live", "2026-07-24")])


if __name__ == "__main__":
    unittest.main()
