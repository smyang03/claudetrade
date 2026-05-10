from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from bot.kr_index_cache import load_kr_index_history, normalize_board


def _index_frame(days: int = 40) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=days, freq="D"),
            "close": [100 + idx for idx in range(days)],
            "high": [101 + idx for idx in range(days)],
            "volume": [0 for _ in range(days)],
        }
    )


class KrIndexCacheTests(unittest.TestCase):
    def test_normalize_board(self) -> None:
        self.assertEqual(normalize_board("KOSDAQ"), "KOSDAQ")
        self.assertEqual(normalize_board("1001"), "KOSDAQ")
        self.assertEqual(normalize_board("KOSPI"), "KOSPI")

    def test_load_kr_index_history_fetches_and_caches(self) -> None:
        calls = []

        def fetch(board: str, lookback_days: int) -> pd.DataFrame:
            calls.append((board, lookback_days))
            return _index_frame(50)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "kospi.json"
            first = load_kr_index_history("KOSPI", lookback_days=30, path=path, fetch_fn=fetch)
            second = load_kr_index_history("KOSPI", lookback_days=30, path=path, fetch_fn=fetch)

        self.assertEqual(len(first), 30)
        self.assertEqual(len(second), 30)
        self.assertEqual(calls, [("KOSPI", 30)])

    def test_returns_empty_when_fetch_fails_and_no_cache(self) -> None:
        def fetch(_board: str, _lookback_days: int) -> pd.DataFrame:
            raise RuntimeError("network unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            frame = load_kr_index_history("KOSDAQ", path=Path(tmp) / "kosdaq.json", fetch_fn=fetch)

        self.assertTrue(frame.empty)


if __name__ == "__main__":
    unittest.main()
