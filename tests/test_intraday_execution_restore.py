from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from trading_bot import TradingBot


def _bot(session_date: str) -> TradingBot:
    bot = TradingBot.__new__(TradingBot)
    bot._daily_sl_count = {"KR": 0, "US": 0}
    bot._daily_sl_last_at = {"KR": None, "US": None}
    bot._daily_sl_event_keys = set()
    bot._session_closed_tickers = {"KR": set(), "US": set()}
    bot._v2_same_day_stop_tickers = {"KR": set(), "US": set()}
    bot._current_session_date_str = lambda market: session_date
    return bot


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )


class IntradayExecutionRestoreTests(unittest.TestCase):
    def test_restore_us_closed_tickers_and_dedupes_order_stop_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            _write_jsonl(
                decisions_path,
                [
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "EAT",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T22:30:20+09:00",
                        "exit_reason": "stop_loss",
                        "order_no": "0030650645",
                        "qty": 1,
                        "pnl_krw": -1234.41,
                        "pnl_pct": -1.21,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "EAT",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T22:30:23+09:00",
                        "exit_reason": "stop_loss",
                        "order_no": "0030650645",
                        "qty": 1,
                        "pnl_krw": -1234.09,
                        "pnl_pct": -1.19,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "EAT",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T22:58:47+09:00",
                        "exit_reason": "loss_cap",
                        "order_no": "0030699267",
                        "qty": 1,
                        "pnl_krw": -1510,
                        "pnl_pct": -1.43,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "CRCL",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T23:10:00+09:00",
                        "exit_reason": "claude_sell_target",
                        "order_no": "0030700001",
                        "qty": 1,
                        "pnl_krw": 2700,
                        "pnl_pct": 2.1,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "NOEV",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T23:11:00+09:00",
                        "exit_reason": "stop_loss",
                    },
                    {
                        "type": "closed",
                        "market": "KR",
                        "ticker": "005930",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T09:30:00+09:00",
                        "exit_reason": "stop_loss",
                        "order_no": "KR-1",
                    },
                ],
            )
            bot = _bot("2026-05-05")

            with patch("trading_bot.DECISIONS_FILE", decisions_path):
                summary = bot._restore_intraday_execution_state_from_decisions("US")

        self.assertEqual(summary["closed_tickers"], ["CRCL", "EAT"])
        self.assertEqual(summary["restored_stop_count"], 2)
        self.assertEqual(summary["skipped_no_broker_evidence_count"], 1)
        self.assertEqual(summary["skipped_no_broker_evidence_tickers"], ["NOEV"])
        self.assertEqual(bot._daily_sl_count["US"], 2)
        self.assertEqual(bot._session_closed_tickers["US"], {"CRCL", "EAT"})
        self.assertEqual(bot._v2_same_day_stop_tickers["US"], {"EAT"})
        self.assertIsNotNone(bot._daily_sl_last_at["US"])
        self.assertEqual(bot._daily_sl_last_at["US"].isoformat(), "2026-05-05T22:58:47+09:00")

    def test_restore_fallback_key_uses_minute_and_integer_pnl_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            _write_jsonl(
                decisions_path,
                [
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "NBIS",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T22:31:05+09:00",
                        "exit_reason": "trail_stop",
                        "broker_fill_confirmed": True,
                        "qty": 7,
                        "pnl_krw": -12372.32,
                        "pnl_pct": -0.8,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "NBIS",
                        "session_date": "2026-05-05",
                        "timestamp": "2026-05-05T22:31:44+09:00",
                        "exit_reason": "trail_stop",
                        "broker_fill_confirmed": True,
                        "qty": 7,
                        "pnl_krw": -12372.12,
                        "pnl_pct": -0.81,
                    },
                ],
            )
            bot = _bot("2026-05-05")

            with patch("trading_bot.DECISIONS_FILE", decisions_path):
                summary = bot._restore_intraday_execution_state_from_decisions("US")

        self.assertEqual(summary["closed_tickers"], ["NBIS"])
        self.assertEqual(summary["restored_stop_count"], 1)
        self.assertEqual(bot._daily_sl_count["US"], 1)
        self.assertEqual(bot._v2_same_day_stop_tickers["US"], {"NBIS"})

    def test_restore_kr_state_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            decisions_path = Path(tmp) / "decisions.jsonl"
            _write_jsonl(
                decisions_path,
                [
                    {
                        "type": "closed",
                        "market": "KR",
                        "ticker": "005930",
                        "session_date": "2026-05-06",
                        "timestamp": "2026-05-06T09:30:00+09:00",
                        "exit_reason": "stop_loss",
                        "order_no": "KR-STOP-1",
                        "qty": 3,
                        "pnl_krw": -9000,
                    },
                    {
                        "type": "closed",
                        "market": "US",
                        "ticker": "EAT",
                        "session_date": "2026-05-06",
                        "timestamp": "2026-05-06T22:30:00+09:00",
                        "exit_reason": "stop_loss",
                        "order_no": "US-STOP-1",
                    },
                ],
            )
            bot = _bot("2026-05-06")

            with patch("trading_bot.DECISIONS_FILE", decisions_path):
                summary = bot._restore_intraday_execution_state_from_decisions("KR")

        self.assertEqual(summary["closed_tickers"], ["005930"])
        self.assertEqual(summary["restored_stop_count"], 1)
        self.assertEqual(bot._daily_sl_count["KR"], 1)
        self.assertEqual(bot._daily_sl_count["US"], 0)
        self.assertEqual(bot._v2_same_day_stop_tickers["KR"], {"005930"})


if __name__ == "__main__":
    unittest.main()
