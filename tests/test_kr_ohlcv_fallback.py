from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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
        bot._ohlcv_cache = {}
        bot._ohlcv_cache_time = {}
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

    def test_get_ohlcv_cached_uses_fresh_csv_without_on_demand(self) -> None:
        bot = self._bot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "kr" / "kr_005930.csv"
            path.parent.mkdir(parents=True)
            _daily_frame(140).to_csv(path, index=False)

            bot._fetch_signal_ready_ohlcv = Mock(side_effect=AssertionError("fresh CSV must not fetch on-demand"))
            with patch.object(trading_bot, "__file__", str(root / "trading_bot.py")), patch(
                "runtime.price_csv_health.price_csv_freshness_status",
                return_value={"fresh": True},
            ):
                df = bot._get_ohlcv_cached("005930", "KR", ttl_min=0)

        self.assertEqual(len(df), 140)
        bot._fetch_signal_ready_ohlcv.assert_not_called()

    def test_get_ohlcv_cached_fetches_when_csv_has_insufficient_rows_even_if_fresh(self) -> None:
        bot = self._bot()
        fallback = _daily_frame(140)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "kr" / "kr_005930.csv"
            path.parent.mkdir(parents=True)
            _daily_frame(30).to_csv(path, index=False)

            bot._fetch_signal_ready_ohlcv = Mock(return_value=(fallback, bot._MIN_SIGNAL_ROWS, "test"))
            with patch.object(trading_bot, "__file__", str(root / "trading_bot.py")), patch(
                "runtime.price_csv_health.price_csv_freshness_status",
                return_value={"fresh": True},
            ):
                df = bot._get_ohlcv_cached("005930", "KR", ttl_min=0)

        self.assertGreaterEqual(len(df), 140)
        bot._fetch_signal_ready_ohlcv.assert_called_once()

    def test_get_ohlcv_cached_fetches_when_csv_is_stale(self) -> None:
        bot = self._bot()
        fallback = _daily_frame(150)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "data" / "price" / "kr" / "kr_005930.csv"
            path.parent.mkdir(parents=True)
            _daily_frame(140).to_csv(path, index=False)

            bot._fetch_signal_ready_ohlcv = Mock(return_value=(fallback, bot._MIN_SIGNAL_ROWS, "test"))
            with patch.object(trading_bot, "__file__", str(root / "trading_bot.py")), patch(
                "runtime.price_csv_health.price_csv_freshness_status",
                return_value={
                    "fresh": False,
                    "last_date": "2026-05-12",
                    "latest_completed": "2026-05-15",
                    "missing_sessions": 3,
                    "threshold": 2,
                    "calendar_source": "exchange_calendars",
                },
            ):
                df = bot._get_ohlcv_cached("005930", "KR", ttl_min=0)

        self.assertGreaterEqual(len(df), 150)
        bot._fetch_signal_ready_ohlcv.assert_called_once()


if __name__ == "__main__":
    unittest.main()
