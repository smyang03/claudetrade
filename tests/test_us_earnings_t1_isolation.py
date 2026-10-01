"""T1(US 실적 서프라이즈) 포지션을 운영 봇 일반 청산에서 격리 (2026-09-29).

계약:
- us_earnings_t1 출처 포지션은 봇이 청산하지 않는다(코어 ETF와 같은 취급) — 깊은 손실에서도 후보 없음.
- 브로커 동기화 때 출처가 비어 있어도 T1 원장 보유 목록에 있으면 us_earnings_t1로 복원한다.
- T1이 낸 주문번호는 '브로커 단독 미체결 주문' 이상으로 잡지 않는다.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import risk_manager
import trading_bot
from risk_manager import RiskManager, position_exit_contract


def _ledger(rows: list[dict]) -> Path:
    fd = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    for r in rows:
        fd.write(json.dumps(r) + "\n")
    fd.close()
    return Path(fd.name)


class T1ManifestTests(unittest.TestCase):
    def test_last_position_row_wins_and_orders_collected(self) -> None:
        p = _ledger([
            {"kind": "order", "ticker": "abc", "order_no": "111", "side": "buy"},
            {"kind": "position", "ticker": "ABC", "status": "OPEN"},
            {"kind": "position", "ticker": "XYZ", "status": "OPEN"},
            {"kind": "order", "ticker": "XYZ", "order_no": "222", "side": "sell"},
            {"kind": "position", "ticker": "XYZ", "status": "CLOSED"},
        ])
        m = risk_manager.us_earnings_t1_manifest(p)
        self.assertEqual(m["open_tickers"], frozenset({"ABC"}))
        self.assertEqual(m["order_nos"], frozenset({"111", "222"}))

    def test_missing_file_is_empty(self) -> None:
        m = risk_manager.us_earnings_t1_manifest(Path(tempfile.gettempdir()) / "no_such_t1.jsonl")
        self.assertEqual(m, {"open_tickers": frozenset(), "order_nos": frozenset()})


class T1ExitIsolationTests(unittest.TestCase):
    def test_source_is_isolated(self) -> None:
        self.assertIn("us_earnings_t1", risk_manager.ISOLATED_STRATEGY_SOURCES)
        contract = position_exit_contract({"ticker": "ABC", "source_strategy": "us_earnings_t1"})
        self.assertEqual(contract["owner"], "us_earnings_t1")
        self.assertTrue(contract["protected"])

    def test_bot_never_exits_even_at_deep_loss(self) -> None:
        rm = RiskManager.__new__(RiskManager)
        pos = {"ticker": "ABC", "source_strategy": "us_earnings_t1", "display_currency": "USD",
               "display_avg_price": 100.0, "display_current_price": 40.0, "current_price": 55000.0,
               "entry": 137000.0, "peak_pnl_pct": 30.0}
        isolated, candidate = rm._isolated_strategy_exit_candidate(pos)
        self.assertTrue(isolated)
        self.assertIsNone(candidate)


class T1AttributionTests(unittest.TestCase):
    def _bot(self):
        return SimpleNamespace(
            _current_session_date_str=lambda market: "2026-09-29",
            _recover_position_mfe_state=lambda pos, market, origin="": pos,
        )

    def test_broker_injected_us_position_gets_t1_source(self) -> None:
        manifest = {"open_tickers": frozenset({"ABC"}), "order_nos": frozenset()}
        with patch.object(trading_bot, "us_earnings_t1_manifest", return_value=manifest):
            pos = trading_bot.TradingBot._normalize_position_metadata(
                self._bot(), {"ticker": "ABC", "qty": 1, "source_strategy": ""}, "US",
                origin="broker_injected", integrity="protected", management_protected=True)
        self.assertEqual(pos["source_strategy"], "us_earnings_t1")
        self.assertEqual(pos["exit_owner"], "us_earnings_t1")

    def test_other_sources_and_tickers_untouched(self) -> None:
        manifest = {"open_tickers": frozenset({"ABC"}), "order_nos": frozenset()}
        with patch.object(trading_bot, "us_earnings_t1_manifest", return_value=manifest):
            swing = trading_bot.TradingBot._normalize_position_metadata(
                self._bot(), {"ticker": "ABC", "qty": 1, "source_strategy": "us_swing_5d"}, "US",
                origin="broker_recovered", integrity="trusted", management_protected=False)
            other = trading_bot.TradingBot._normalize_position_metadata(
                self._bot(), {"ticker": "SCHG", "qty": 5}, "US",
                origin="broker_injected", integrity="protected", management_protected=True)
            kr = trading_bot.TradingBot._normalize_position_metadata(
                self._bot(), {"ticker": "ABC", "qty": 1}, "KR",
                origin="broker_injected", integrity="protected", management_protected=True)
        self.assertEqual(swing["source_strategy"], "us_swing_5d")
        self.assertFalse(other.get("source_strategy"))
        self.assertFalse(kr.get("source_strategy"))


class T1OpenOrderReconcileTests(unittest.TestCase):
    def test_t1_order_not_flagged_as_broker_only(self) -> None:
        bot = object.__new__(trading_bot.TradingBot)
        bot.pending_orders = []
        bot.v2_order_unknown = Mock()
        bot._flag_execution_issue = Mock()
        bot._broker_truth_market_snapshot = Mock(return_value={
            "missing": False, "stale": False, "error": "",
            "open_orders": [
                {"market": "US", "ticker": "ABC", "order_no": "T1-111", "side": "buy", "remaining_qty": 1},
                {"market": "US", "ticker": "IBM", "order_no": "999", "side": "sell", "remaining_qty": 3},
            ],
        })
        manifest = {"open_tickers": frozenset(), "order_nos": frozenset({"T1-111"})}
        with patch.object(trading_bot, "us_earnings_t1_manifest", return_value=manifest):
            summary = trading_bot.TradingBot._reconcile_broker_open_orders(bot, "US", reason="test", force=True)
        self.assertEqual([o["order_no"] for o in summary["broker_only_open_orders"]], ["999"])
        bot._flag_execution_issue.assert_called_once()


if __name__ == "__main__":
    unittest.main()
