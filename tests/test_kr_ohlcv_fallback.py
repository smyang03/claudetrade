from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

import trading_bot


class _RuntimeConfig:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    def get_bool(self, key: str, default: bool = False) -> bool:
        return bool(self.values.get(key, default))

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.values.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.values.get(key, default))

    def get(self, key: str, default: object = "") -> object:
        return self.values.get(key, default)


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
        bot.runtime_config = _RuntimeConfig()
        bot.is_paper = False
        bot._token_for_market = lambda market: f"token-{market}"
        bot._hist_fill_enqueue = lambda ticker, market: None
        bot._current_session_date_str = lambda market: "2026-05-19"
        bot._candidate_audit_store_cache = None
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

    def test_history_filter_writes_shadow_audit_for_data_insufficient_candidate(self) -> None:
        bot = self._bot()
        bot.runtime_config.values.update({"ENABLE_CANDIDATE_AUDIT_LIVE": True})
        bot._get_ohlcv_cached = lambda ticker, market: _daily_frame(20)

        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "CANDIDATE_AUDIT_DB_PATH": str(Path(tmp) / "candidate_audit.db"),
                "DATA_INSUFFICIENT_WATCH_MIN_USABLE": "40",
            },
        ):
            filtered = bot._filter_candidates_by_history(
                [{"ticker": "439960", "price": 120.0}],
                "KR",
                phase="session_open",
            )

            conn = sqlite3.connect(Path(tmp) / "candidate_audit.db")
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM audit_candidate_rows WHERE ticker='439960'").fetchone()
                payload = json.loads(row["payload_json"])
            finally:
                conn.close()

        self.assertEqual(filtered, [])
        self.assertIsNotNone(row)
        self.assertEqual(row["classification"], "data_insufficient")
        self.assertEqual(row["source_file"], "trading_bot.screener_filter")
        self.assertEqual(row["in_prompt"], 0)
        self.assertEqual(row["data_quality"], "DATA_INSUFFICIENT_SHADOW")
        self.assertEqual(row["history_status"], "DATA_INSUFFICIENT")
        self.assertEqual(payload["phase"], "session_open")

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
