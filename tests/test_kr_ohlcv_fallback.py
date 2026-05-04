from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pandas as pd

import trading_bot


def _daily_frame(rows: int = 140) -> pd.DataFrame:
    dates = pd.date_range("2026-01-01", periods=rows, freq="D")
    close = pd.Series(range(100, 100 + rows), dtype="float64")
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1_000_000,
        }
    )


class KrOhlcvFallbackTests(unittest.TestCase):
    def _bot(self) -> trading_bot.TradingBot:
        bot = trading_bot.TradingBot.__new__(trading_bot.TradingBot)
        bot._token_for_market = lambda market: f"token-{market}"
        bot._hist_fill_enqueue = lambda ticker, market: None
        bot._last_data_insufficient_candidates = {"KR": [], "US": []}
        bot._data_insufficient_watch_tickers = {"KR": set(), "US": set()}
        return bot

    def test_kr_primary_exception_uses_yfinance_fallback(self) -> None:
        bot = self._bot()
        fallback = _daily_frame()

        with patch.dict(os.environ, {"KR_YFINANCE_DAILY_FALLBACK": "true"}), patch.object(
            trading_bot,
            "get_daily_ohlcv",
            side_effect=RuntimeError("kis rate limited"),
        ), patch.object(
            trading_bot,
            "_daily_ohlcv_kr_yf",
            return_value=fallback,
        ) as yf:
            df, usable, source = bot._fetch_signal_ready_ohlcv("005930", "KR", lookback_days=365)

        self.assertFalse(df.empty)
        self.assertGreaterEqual(usable, bot._MIN_SIGNAL_ROWS)
        self.assertEqual(source, "yfinance")
        yf.assert_called_once_with("005930", lookback_days=365)

    def test_history_filter_keeps_kr_candidate_when_primary_raises_and_yfinance_succeeds(self) -> None:
        bot = self._bot()
        fallback = _daily_frame()
        fetch_results: list[tuple[int, str]] = []

        def fetch_cached(ticker: str, market: str):
            df, usable, source = bot._fetch_signal_ready_ohlcv(ticker, market, lookback_days=365)
            fetch_results.append((usable, source))
            return df

        bot._get_ohlcv_cached = fetch_cached

        with patch.dict(os.environ, {"KR_YFINANCE_DAILY_FALLBACK": "true"}), patch.object(
            trading_bot,
            "get_daily_ohlcv",
            side_effect=RuntimeError("kis token expired"),
        ), patch.object(
            trading_bot,
            "_daily_ohlcv_kr_yf",
            return_value=fallback,
        ):
            filtered = bot._filter_candidates_by_history([{"ticker": "005930", "price": 120.0}], "KR")

        self.assertEqual([row["ticker"] for row in filtered], ["005930"])
        self.assertEqual(fetch_results[0][1], "yfinance")
        self.assertGreaterEqual(fetch_results[0][0], bot._MIN_SIGNAL_ROWS)


if __name__ == "__main__":
    unittest.main()
