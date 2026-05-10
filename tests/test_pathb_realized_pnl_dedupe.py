from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import trading_bot as trading_bot_module
from runtime.pathb_reasons import normalize_pathb_decision_exit_reason
from trading_bot import TradingBot


class PathBRealizedPnlDedupeTests(unittest.TestCase):
    def test_pathb_reason_pairs_dedupe_across_trade_log_and_decisions(self) -> None:
        session_date = "2026-05-07"
        closes = [
            ("AAA", 1, -100.0, "CLOSED_LOSS_CAP", "loss_cap"),
            ("BBB", 2, -200.0, "CLOSED_HARD_STOP", "hard_stop"),
            ("CCC", 3, -300.0, "CLOSED_TIMEOUT", "closed_timeout"),
        ]
        bot = TradingBot.__new__(TradingBot)
        bot._current_session_date_str = lambda market: session_date
        bot._ticker_market = lambda ticker: "US"
        bot.risk = type("Risk", (), {})()
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": ticker,
                "market": "US",
                "session_date": session_date,
                "qty": qty,
                "pnl": pnl,
                "reason": close_reason,
            }
            for ticker, qty, pnl, close_reason, _decision_reason in closes
        ]

        with tempfile.TemporaryDirectory() as tmp:
            decisions_file = Path(tmp) / "decisions.jsonl"
            rows = [
                {
                    "type": "closed",
                    "market": "US",
                    "session_date": session_date,
                    "ticker": ticker,
                    "qty": qty,
                    "pnl_krw": pnl,
                    "exit_reason": decision_reason,
                }
                for ticker, qty, pnl, _close_reason, decision_reason in closes
            ]
            decisions_file.write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows),
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "DECISIONS_FILE", decisions_file):
                self.assertEqual(TradingBot._market_realized_pnl_krw(bot, "US"), -600.0)

    def test_pathb_reason_normalizer_matches_decision_fallback(self) -> None:
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_LOSS_CAP"), "loss_cap")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_HARD_STOP"), "hard_stop")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_CLAUDE_PRICE_STOP"), "claude_price_stop")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_MFE_BREAKEVEN"), "mfe_breakeven")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_PROFIT_FLOOR"), "profit_floor")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_TRAILING_STOP"), "trail_stop")
        self.assertEqual(normalize_pathb_decision_exit_reason("CLOSED_TIMEOUT"), "closed_timeout")
        self.assertEqual(normalize_pathb_decision_exit_reason(""), "pathb_closed")

    def test_pathb_path_run_id_dedupes_trade_log_and_decision_record(self) -> None:
        session_date = "2026-05-07"
        bot = TradingBot.__new__(TradingBot)
        bot._current_session_date_str = lambda market: session_date
        bot._ticker_market = lambda ticker: "US"
        bot.risk = type("Risk", (), {})()
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "IREN",
                "market": "US",
                "session_date": session_date,
                "qty": 2,
                "pnl": -123.0,
                "reason": "CLOSED_LOSS_CAP",
                "pathb_path_run_id": "pathb-iren-1",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            decisions_file = Path(tmp) / "decisions.jsonl"
            decisions_file.write_text(
                json.dumps(
                    {
                        "type": "closed",
                        "market": "US",
                        "session_date": session_date,
                        "ticker": "IREN",
                        "qty": 2,
                        "pnl_krw": -123.0,
                        "exit_reason": "loss_cap",
                        "pathb_path_run_id": "pathb-iren-1",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "DECISIONS_FILE", decisions_file):
                self.assertEqual(TradingBot._market_realized_pnl_krw(bot, "US"), -123.0)


if __name__ == "__main__":
    unittest.main()
