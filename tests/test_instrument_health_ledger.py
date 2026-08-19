# -*- coding: utf-8 -*-
"""계측 품질 저하 원장(instrument_health_events.jsonl) 기록 계약 테스트.

30건 코호트 판정 때 "계측 정상 구간만"으로 재검증할 수 있으려면, WS 무음 같은
강등 구간이 보유 종목과 함께 남아 있어야 한다. 로그 롤오버 후에는 복원 불가.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import trading_bot


class InstrumentHealthLedgerTests(unittest.TestCase):
    def _bot(self, positions):
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot.risk = SimpleNamespace(positions=positions)
        bot._ticker_market = lambda t: "KR" if str(t).isdigit() else "US"
        bot._current_session_date_str = lambda market: "2026-08-19"
        return bot

    def _run(self, bot, tmpdir, **kwargs):
        def fake_path(*parts):
            return Path(tmpdir).joinpath(*parts)

        with mock.patch.object(trading_bot, "get_runtime_path", fake_path):
            bot._record_instrument_degradation(**kwargs)
        ledger = Path(tmpdir) / "data" / "shadow" / "instrument_health_events.jsonl"
        if not ledger.exists():
            return []
        return [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]

    def test_records_degradation_window_with_affected_holdings(self):
        import tempfile

        bot = self._bot([{"ticker": "AXTI"}, {"ticker": "WIX"}, {"ticker": "031330"}])
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._run(
                bot, tmp, market="US", kind="ws_tick_silence",
                duration_sec=656.0, detail="restart_ok subs=33",
            )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["schema_version"], "instrument_health_event_v1")
        self.assertEqual(row["market"], "US")
        self.assertEqual(row["kind"], "ws_tick_silence")
        self.assertEqual(row["duration_sec"], 656.0)
        # US 보유만 담기고 KR 종목은 섞이지 않는다
        self.assertEqual(sorted(row["affected_holdings"]), ["AXTI", "WIX"])
        self.assertEqual(row["exit_path_during"], "rest_holding_price_refresh")
        # 구간 복원이 가능해야 한다(시작 < 종료)
        self.assertLess(row["started_at"], row["ended_at"])

    def test_no_holdings_writes_nothing(self):
        import tempfile

        bot = self._bot([{"ticker": "031330"}])  # KR만 보유 → US 강등은 판정 무관
        with tempfile.TemporaryDirectory() as tmp:
            rows = self._run(
                bot, tmp, market="US", kind="ws_tick_silence", duration_sec=656.0,
            )
        self.assertEqual(rows, [])

    def test_never_raises_when_ledger_write_fails(self):
        bot = self._bot([{"ticker": "AXTI"}])

        def boom(*_a, **_k):
            raise OSError("disk full")

        with mock.patch.object(trading_bot, "get_runtime_path", boom):
            bot._record_instrument_degradation(
                market="US", kind="ws_tick_silence", duration_sec=10.0
            )  # 매매를 막으면 안 된다


if __name__ == "__main__":
    unittest.main()
