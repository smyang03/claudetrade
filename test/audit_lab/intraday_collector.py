"""yfinance intraday collector for audit-lab simulations.

The collector stores 5m/15m data into the same manifest table as daily data,
using the timeframe column to keep datasets separate.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .config import MARKET_DATA_DB, YFINANCE_DATA_DIR
from .data_quality import normalize_ohlcv_frame, validate_ohlcv_frame
from .db import (
    complete_collection_run,
    connect,
    init_database,
    insert_quality_issues,
    insert_symbol_resolution,
    start_collection_run,
    upsert_ohlcv_manifest,
    utc_now,
)


DownloadFunc = Callable[..., pd.DataFrame]
ProgressFunc = Callable[[str], None]


@dataclass(frozen=True)
class IntradayCollectionResult:
    symbol: str
    market: str
    timeframe: str
    status: str
    file_path: str
    storage_format: str
    row_count: int
    start_date: str
    end_date: str
    missing_rate: float
    quality_grade: str
    attempts: int
    error_msg: str = ""


class YFinanceIntradayCollector:
    def __init__(
        self,
        *,
        data_dir: Path = YFINANCE_DATA_DIR,
        db_path: Path = MARKET_DATA_DB,
        downloader: DownloadFunc | None = None,
        sleep_seconds: float = 0.5,
        max_retries: int = 3,
        storage_format: str = "parquet",
        progress: ProgressFunc | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.db_path = Path(db_path)
        self.downloader = downloader
        self.sleep_seconds = float(sleep_seconds)
        self.max_retries = max(1, int(max_retries))
        self.storage_format = storage_format.lower()
        self.progress = progress or (lambda _message: None)

    def _download_once(self, symbol: str, *, period: str, interval: str, auto_adjust: bool) -> pd.DataFrame:
        if self.downloader is not None:
            return self.downloader(symbol=symbol, period=period, interval=interval, auto_adjust=auto_adjust)
        try:
            import yfinance as yf  # type: ignore
        except ImportError as exc:
            raise RuntimeError("yfinance package is not installed") from exc
        return yf.download(symbol, period=period, interval=interval, auto_adjust=auto_adjust, progress=False)

    def _download_with_retry(self, symbol: str, *, period: str, interval: str, auto_adjust: bool) -> tuple[pd.DataFrame, int, str]:
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = self._download_once(symbol, period=period, interval=interval, auto_adjust=auto_adjust)
                frame = normalize_ohlcv_frame(raw)
                if not frame.empty:
                    return frame, attempt, ""
                last_error = "empty DataFrame returned"
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.max_retries:
                time.sleep(self.sleep_seconds * attempt)
        return pd.DataFrame(), self.max_retries, last_error

    def _write_frame(self, frame: pd.DataFrame, *, market: str, symbol: str, timeframe: str) -> tuple[Path, str]:
        base_dir = self.data_dir / "intraday" / market.upper() / timeframe
        base_dir.mkdir(parents=True, exist_ok=True)
        if self.storage_format == "csv":
            path = base_dir / f"{symbol}.csv"
            frame.to_csv(path, index=False, encoding="utf-8-sig")
            return path, "csv"
        path = base_dir / f"{symbol}.parquet"
        try:
            frame.to_parquet(path, index=False)
            return path, "parquet"
        except Exception:
            fallback = base_dir / f"{symbol}.csv"
            frame.to_csv(fallback, index=False, encoding="utf-8-sig")
            return fallback, "csv"

    def collect_symbol(
        self,
        *,
        market: str,
        symbol: str,
        run_id: str,
        interval: str = "5m",
        period: str = "730d",
        auto_adjust: bool = True,
    ) -> IntradayCollectionResult:
        market_u = market.upper()
        frame, attempts, error = self._download_with_retry(
            symbol,
            period=period,
            interval=interval,
            auto_adjust=auto_adjust,
        )
        if frame.empty:
            with connect(self.db_path) as conn:
                insert_symbol_resolution(
                    conn,
                    raw_symbol=symbol,
                    resolved_symbol="",
                    market=market_u,
                    status="failed",
                    error_msg=error,
                )
            return IntradayCollectionResult(symbol, market_u, interval, "failed", "", "", 0, "", "", 100.0, "FAIL", attempts, error)

        quality = validate_ohlcv_frame(frame, symbol=symbol, market=market_u, timeframe=interval)
        path, actual_format = self._write_frame(frame, market=market_u, symbol=symbol, timeframe=interval)
        with connect(self.db_path) as conn:
            insert_symbol_resolution(conn, raw_symbol=symbol, resolved_symbol=symbol, market=market_u, status="ok")
            upsert_ohlcv_manifest(
                conn,
                {
                    "symbol": symbol,
                    "market": market_u,
                    "timeframe": interval,
                    "file_path": str(path),
                    "storage_format": actual_format,
                    "row_count": quality.row_count,
                    "start_date": quality.start_date,
                    "end_date": quality.end_date,
                    "missing_rate": quality.missing_rate,
                    "quality_grade": quality.quality_grade,
                    "adjusted_price": 1 if auto_adjust else 0,
                    "collected_at": utc_now(),
                    "run_id": run_id,
                },
            )
            conn.execute(
                "DELETE FROM data_quality_issues WHERE symbol = ? AND market = ? AND timeframe = ?",
                (symbol, market_u, interval),
            )
            insert_quality_issues(conn, quality.issues)

        return IntradayCollectionResult(
            symbol=symbol,
            market=market_u,
            timeframe=interval,
            status="ok",
            file_path=str(path),
            storage_format=actual_format,
            row_count=quality.row_count,
            start_date=quality.start_date,
            end_date=quality.end_date,
            missing_rate=quality.missing_rate,
            quality_grade=quality.quality_grade,
            attempts=attempts,
        )

    def collect(
        self,
        *,
        market: str,
        symbols: Iterable[str],
        interval: str = "5m",
        period: str = "730d",
        run_id: str | None = None,
        auto_adjust: bool = True,
    ) -> list[IntradayCollectionResult]:
        init_database(self.db_path)
        rows = list(dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip()))
        run_id = run_id or f"CI_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{interval}"
        with connect(self.db_path) as conn:
            start_collection_run(
                conn,
                run_id=run_id,
                market=market.upper(),
                timeframe=interval,
                symbols_requested=len(rows),
                notes=f"period={period}, interval={interval}, auto_adjust={auto_adjust}",
            )

        results: list[IntradayCollectionResult] = []
        for idx, symbol in enumerate(rows, start=1):
            self.progress(f"장중 수집 진행 | {idx}/{len(rows)} {market.upper()} {symbol} {interval}")
            results.append(
                self.collect_symbol(
                    market=market,
                    symbol=symbol,
                    run_id=run_id,
                    interval=interval,
                    period=period,
                    auto_adjust=auto_adjust,
                )
            )
            time.sleep(self.sleep_seconds)

        success = sum(1 for result in results if result.status == "ok")
        failed = len(results) - success
        with connect(self.db_path) as conn:
            complete_collection_run(
                conn,
                run_id=run_id,
                status="done" if failed == 0 else "partial",
                symbols_success=success,
                symbols_failed=failed,
                notes=f"success={success}, failed={failed}",
            )
        self.progress(f"장중 수집 완료 | 요청={len(results)} 성공={success} 실패={failed}")
        return results


def intraday_results_to_dicts(results: list[IntradayCollectionResult]) -> list[dict]:
    return [asdict(result) for result in results]
