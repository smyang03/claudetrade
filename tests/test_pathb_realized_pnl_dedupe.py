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

    def test_trade_log_without_order_no_dedupes_against_decision_with_order_no(self) -> None:
        session_date = "2026-05-12"
        bot = TradingBot.__new__(TradingBot)
        bot._current_session_date_str = lambda market: session_date
        bot._ticker_market = lambda ticker: "KR"
        bot.risk = type("Risk", (), {})()
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": "018880",
                "market": "KR",
                "session_date": session_date,
                "qty": 34,
                "price": 5904.0,
                "pnl": -26680.5055,
                "reason": "stop_loss",
            }
        ]

        with tempfile.TemporaryDirectory() as tmp:
            decisions_file = Path(tmp) / "decisions.jsonl"
            decisions_file.write_text(
                json.dumps(
                    {
                        "type": "closed",
                        "market": "KR",
                        "session_date": session_date,
                        "ticker": "018880",
                        "qty": 34,
                        "exit_price": 5904.0,
                        "pnl_krw": -26680.5055,
                        "exit_reason": "stop_loss",
                        "order_no": "0024806400",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "DECISIONS_FILE", decisions_file):
                self.assertEqual(TradingBot._market_realized_pnl_krw(bot, "KR"), -26680.5055)

    def test_kr_20260512_replay_realized_pnl_is_not_double_counted(self) -> None:
        session_date = "2026-05-12"
        rows = [
            ("012610", 25, 0.0, -1285.675, "stop_loss", "0006177100"),
            ("010170", 6, 0.0, -1543.98, "stop_loss", "0020925100"),
            ("018880", 34, 5904.0, -26680.5055, "stop_loss", "0024806400"),
        ]
        bot = TradingBot.__new__(TradingBot)
        bot._current_session_date_str = lambda market: session_date
        bot._ticker_market = lambda ticker: "KR"
        bot.risk = type("Risk", (), {})()
        bot.risk.all_trade_log = [
            {
                "side": "sell",
                "ticker": ticker,
                "market": "KR",
                "session_date": session_date,
                "qty": qty,
                "price": price,
                "pnl": pnl,
                "reason": reason,
            }
            for ticker, qty, price, pnl, reason, _order_no in rows
        ]

        with tempfile.TemporaryDirectory() as tmp:
            decisions_file = Path(tmp) / "decisions.jsonl"
            decisions_file.write_text(
                "\n".join(
                    json.dumps(
                        {
                            "type": "closed",
                            "market": "KR",
                            "session_date": session_date,
                            "ticker": ticker,
                            "qty": qty,
                            "exit_price": price,
                            "pnl_krw": pnl,
                            "exit_reason": reason,
                            "order_no": order_no,
                        },
                        ensure_ascii=False,
                    )
                    for ticker, qty, price, pnl, reason, order_no in rows
                ),
                encoding="utf-8",
            )

            with patch.object(trading_bot_module, "DECISIONS_FILE", decisions_file):
                self.assertAlmostEqual(TradingBot._market_realized_pnl_krw(bot, "KR"), -29510.1605)

    def test_canonical_session_trade_merge_dedupes_decision_fallback_rows(self) -> None:
        session_date = "2026-05-12"
        bot = TradingBot.__new__(TradingBot)
        bot._ticker_market = lambda ticker: "KR"
        trades = [
            {
                "side": "buy",
                "ticker": "010170",
                "qty": 6,
                "price": 25000.0,
                "pnl": 0.0,
                "reason": "",
                "date": session_date,
            },
            {
                "side": "sell",
                "ticker": "018880",
                "qty": 34,
                "price": 5904.0,
                "pnl": -26680.5055,
                "reason": "stop_loss",
                "date": session_date,
            },
            {
                "side": "sell",
                "ticker": "018880",
                "qty": 34,
                "price": 5904.0,
                "pnl": -26680.5055,
                "reason": "stop_loss",
                "order_no": "0024806400",
                "date": session_date,
                "source_kind": "decisions_fallback",
            },
        ]

        merged = TradingBot._canonicalize_session_trades(bot, trades, "KR", session_date)
        sells = [row for row in merged if row["side"] == "sell"]

        self.assertEqual(len(merged), 2)
        self.assertEqual(len(sells), 1)
        self.assertEqual(sells[0]["order_no"], "0024806400")

    def test_canonical_session_trade_side_aliases_and_legacy_unknown_are_stable(self) -> None:
        session_date = "2026-05-12"
        bot = TradingBot.__new__(TradingBot)
        bot._ticker_market = lambda ticker: "KR"

        alias_trades = [
            {"side": "BUY", "ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
            {"side": "매수", "ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
            {"side": "SELL", "ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
            {"side": "매도", "ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
        ]
        alias_merged = TradingBot._canonicalize_session_trades(bot, alias_trades, "KR", session_date)

        self.assertEqual(len(alias_merged), 2)
        self.assertEqual(
            {TradingBot._canonical_trade_side(row) for row in alias_merged},
            {"buy", "sell"},
        )

        legacy_trades = [
            {"ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
            {"ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
            {"side": "buy", "ticker": "005930", "qty": 1, "price": 70000.0, "pnl": 0.0, "reason": "", "date": session_date},
        ]
        legacy_merged = TradingBot._canonicalize_session_trades(bot, legacy_trades, "KR", session_date)

        self.assertEqual(len(legacy_merged), 2)
        self.assertEqual(TradingBot._canonical_trade_side(legacy_merged[0]), "unknown")


if __name__ == "__main__":
    unittest.main()
