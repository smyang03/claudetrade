from __future__ import annotations

import csv
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from tools.collect_counterfactual_minutes import (
    collect_one,
    counterfactual_tickers,
    minute_csv_path,
    write_minute_csv,
)


def test_write_minute_csv_uses_standard_path_and_deduplicates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        first = pd.DataFrame(
            {
                "ts": ["2026-05-19T09:31:00", "2026-05-19T09:30:00"],
                "open": [101, 100],
                "high": [102, 101],
                "low": [100, 99],
                "close": [101, 100],
                "volume": [20, 10],
                "source": ["kis", "kis"],
            }
        )
        path, count = write_minute_csv(first, price_root=root, market="KR", ticker="5930")
        assert path == root / "minute" / "kr" / "kr_005930.csv"
        assert count == 2

        second = pd.DataFrame(
            {
                "ts": ["2026-05-19T09:31:00+09:00", "2026-05-19T09:32:00+09:00"],
                "open": [111, 112],
                "high": [112, 113],
                "low": [110, 111],
                "close": [111, 112],
                "volume": [21, 22],
                "source": ["kis", "kis"],
            }
        )
        _, count = write_minute_csv(second, price_root=root, market="KR", ticker="005930")

        with path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        assert count == 3
        assert rows[0]["ts"] == "2026-05-19T09:30:00+09:00"
        assert rows[1]["ts"] == "2026-05-19T09:31:00+09:00"
        assert rows[1]["close"] == "111"
        assert set(["ts", "open", "high", "low", "close", "volume"]).issubset(rows[0])


def test_collect_one_no_write_reports_spacing_and_dates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        def fetcher(**_kwargs) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "ts": [
                        "2026-05-19T09:00:00+09:00",
                        "2026-05-19T09:01:00+09:00",
                        "2026-05-19T09:03:00+09:00",
                    ],
                    "open": [100, 101, 103],
                    "high": [101, 102, 104],
                    "low": [99, 100, 102],
                    "close": [100, 101, 103],
                    "volume": [10, 11, 13],
                    "source": ["unit", "unit", "unit"],
                }
            )

        result = collect_one(
            market="KR",
            ticker="005930",
            session_date="2026-05-19",
            price_root=root,
            write=False,
            fetcher=fetcher,
        )

        assert result.status == "ok"
        assert result.row_count == 3
        assert result.non_60s_gap_count == 1
        assert result.max_gap_seconds == 120
        assert result.observed_dates == {"2026-05-19": 3}
        assert result.session_dates == {"2026-05-19": 3}
        assert not minute_csv_path(root, "KR", "005930").exists()


def test_collect_one_us_kis_uses_existing_latest_ts_for_incremental_fetch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minute_csv(
            pd.DataFrame(
                {
                    "ts": ["2026-05-13T22:30:00+09:00", "2026-05-13T22:31:00+09:00"],
                    "open": [100, 101],
                    "high": [101, 102],
                    "low": [99, 100],
                    "close": [100, 101],
                    "volume": [10, 11],
                    "source": ["kis_us_intraday", "kis_us_intraday"],
                }
            ),
            price_root=root,
            market="US",
            ticker="TSLA",
        )
        seen: dict[str, str] = {}

        def fetcher(**kwargs) -> pd.DataFrame:
            seen["start_at"] = str(kwargs.get("start_at") or "")
            return pd.DataFrame(
                {
                    "ts": ["2026-05-13T22:32:00+09:00"],
                    "open": [102],
                    "high": [103],
                    "low": [101],
                    "close": [102],
                    "volume": [12],
                    "source": ["kis_us_intraday"],
                }
            )

        result = collect_one(
            market="US",
            ticker="TSLA",
            session_date="2026-05-13",
            price_root=root,
            provider="kis",
            start_at="2026-05-13T22:30:00+09:00",
            end_at="2026-05-13T22:35:00+09:00",
            write=True,
            fetcher=fetcher,
        )

        assert seen["start_at"] == "2026-05-13T22:32:00+09:00"
        assert result.status == "ok"
        assert result.row_count == 3
        assert result.provider == "kis"
        assert result.source == "kis_us_intraday"
        assert result.first_ts == "2026-05-13T22:30:00+09:00"
        assert result.last_ts == "2026-05-13T22:32:00+09:00"


def test_collect_one_us_kis_full_fetches_when_existing_source_is_not_kis() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        write_minute_csv(
            pd.DataFrame(
                {
                    "ts": ["2026-05-13T22:30:00+09:00", "2026-05-13T22:31:00+09:00"],
                    "open": [100, 101],
                    "high": [101, 102],
                    "low": [99, 100],
                    "close": [100, 101],
                    "volume": [10, 11],
                    "source": ["yfinance_intraday", "yfinance_intraday"],
                }
            ),
            price_root=root,
            market="US",
            ticker="TSLA",
        )
        seen: dict[str, str] = {}

        def fetcher(**kwargs) -> pd.DataFrame:
            seen["start_at"] = str(kwargs.get("start_at") or "")
            return pd.DataFrame(
                {
                    "ts": ["2026-05-13T22:30:00+09:00", "2026-05-13T22:31:00+09:00"],
                    "open": [110, 111],
                    "high": [111, 112],
                    "low": [109, 110],
                    "close": [110, 111],
                    "volume": [20, 21],
                    "source": ["kis_us_intraday", "kis_us_intraday"],
                }
            )

        result = collect_one(
            market="US",
            ticker="TSLA",
            session_date="2026-05-13",
            price_root=root,
            provider="kis",
            start_at="2026-05-13T22:30:00+09:00",
            end_at="2026-05-13T22:35:00+09:00",
            write=True,
            fetcher=fetcher,
        )

        assert seen["start_at"] == "2026-05-13T22:30:00+09:00"
        assert result.row_count == 2
        assert result.source == "kis_us_intraday"


def test_counterfactual_tickers_filters_market_date_and_status() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        store = CandidateCounterfactualStore(db)
        for ticker, market, session_date, status in [
            ("aapl", "US", "2026-05-19", "TRIGGERED"),
            ("AAPL", "US", "2026-05-19", "PENDING"),
            ("MSFT", "US", "2026-05-19", "DATA_MISSING"),
            ("005930", "KR", "2026-05-19", "TRIGGERED"),
            ("TSLA", "US", "2026-05-20", "TRIGGERED"),
        ]:
            store.upsert_path(
                {
                    "runtime_mode": "live",
                    "session_date": session_date,
                    "market": market,
                    "ticker": ticker,
                    "known_at": f"{session_date}T09:35:00+09:00",
                    "signal_time": f"{session_date}T09:35:00+09:00",
                    "path_name": f"immediate_{ticker}_{status}",
                    "status": status,
                }
            )

        tickers = counterfactual_tickers(db, market="US", session_date="2026-05-19", statuses=["TRIGGERED", "PENDING"])
        assert tickers == ["AAPL"]


def test_counterfactual_tickers_initializes_missing_counterfactual_table() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        conn = sqlite3.connect(str(db))
        try:
            conn.execute("CREATE TABLE candidate_audit_rows (id INTEGER PRIMARY KEY)")
            conn.commit()
        finally:
            conn.close()

        tickers = counterfactual_tickers(db, market="US", session_date="2026-05-19")

        assert tickers == []
