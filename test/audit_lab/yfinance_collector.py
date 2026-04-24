"""yfinance based market-data collector with explicit retry and QA records."""

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
    upsert_symbol_master,
    utc_now,
)
from .universe import UniverseMember, normalize_raw_symbol


DownloadFunc = Callable[..., pd.DataFrame]
ProgressFunc = Callable[[str], None]


@dataclass(frozen=True)
class CollectionResult:
    raw_symbol: str
    resolved_symbol: str
    market: str
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


class YFinanceDailyCollector:
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
            raise RuntimeError("yfinance 패키지가 설치되어 있지 않음") from exc
        return yf.download(symbol, period=period, interval=interval, auto_adjust=auto_adjust, progress=False)

    def _download_with_retry(self, symbol: str, *, period: str, interval: str, auto_adjust: bool) -> tuple[pd.DataFrame, int, str]:
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                raw = self._download_once(symbol, period=period, interval=interval, auto_adjust=auto_adjust)
                frame = normalize_ohlcv_frame(raw)
                if not frame.empty:
                    return frame, attempt, ""
                last_error = "빈 DataFrame 반환"
            except Exception as exc:
                last_error = str(exc)
            if attempt < self.max_retries:
                time.sleep(self.sleep_seconds * attempt)
        return pd.DataFrame(), self.max_retries, last_error

    @staticmethod
    def candidate_symbols(raw_symbol: str, market: str) -> list[str]:
        raw = normalize_raw_symbol(raw_symbol, market)
        if not raw:
            return []
        if market.upper() == "KR":
            if raw.endswith((".KS", ".KQ")):
                return [raw]
            return [f"{raw}.KS", f"{raw}.KQ"]
        return [raw]

    def _write_frame(self, frame: pd.DataFrame, *, market: str, symbol: str) -> tuple[Path, str]:
        base_dir = self.data_dir / "daily" / market.upper()
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

    def collect_member(
        self,
        member: UniverseMember,
        *,
        run_id: str,
        period: str = "max",
        interval: str = "1d",
        auto_adjust: bool = True,
    ) -> CollectionResult:
        market = member.market.upper()
        raw = normalize_raw_symbol(member.raw_symbol, market)
        attempts_total = 0
        last_error = ""
        for candidate in self.candidate_symbols(raw, market):
            frame, attempts, error = self._download_with_retry(
                candidate,
                period=period,
                interval=interval,
                auto_adjust=auto_adjust,
            )
            attempts_total += attempts
            if frame.empty:
                last_error = error or "빈 DataFrame 반환"
                continue
            quality = validate_ohlcv_frame(frame, symbol=candidate, market=market, timeframe="daily")
            path, actual_format = self._write_frame(frame, market=market, symbol=candidate)
            result = CollectionResult(
                raw_symbol=raw,
                resolved_symbol=candidate,
                market=market,
                status="ok",
                file_path=str(path),
                storage_format=actual_format,
                row_count=quality.row_count,
                start_date=quality.start_date,
                end_date=quality.end_date,
                missing_rate=quality.missing_rate,
                quality_grade=quality.quality_grade,
                attempts=attempts_total,
            )
            with connect(self.db_path) as conn:
                upsert_symbol_master(
                    conn,
                    symbol=candidate,
                    raw_symbol=raw,
                    market=market,
                    universe_group=member.universe_group,
                    universe_sources=list(member.sources),
                    is_active=1,
                )
                insert_symbol_resolution(conn, raw_symbol=raw, resolved_symbol=candidate, market=market, status="ok")
                upsert_ohlcv_manifest(
                    conn,
                    {
                        "symbol": candidate,
                        "market": market,
                        "timeframe": "daily",
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
                    (candidate, market, "daily"),
                )
                insert_quality_issues(conn, quality.issues)
            return result

        with connect(self.db_path) as conn:
            insert_symbol_resolution(
                conn,
                raw_symbol=raw,
                resolved_symbol="",
                market=market,
                status="failed",
                error_msg=last_error,
            )
        return CollectionResult(
            raw_symbol=raw,
            resolved_symbol="",
            market=market,
            status="failed",
            file_path="",
            storage_format="",
            row_count=0,
            start_date="",
            end_date="",
            missing_rate=100.0,
            quality_grade="FAIL",
            attempts=attempts_total,
            error_msg=last_error,
        )

    def collect(
        self,
        members: Iterable[UniverseMember],
        *,
        run_id: str | None = None,
        period: str = "max",
        interval: str = "1d",
        auto_adjust: bool = True,
    ) -> list[CollectionResult]:
        init_database(self.db_path)
        rows = list(members)
        run_id = run_id or f"CR_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        market_label = "ALL" if len({m.market for m in rows}) > 1 else (rows[0].market if rows else "ALL")
        with connect(self.db_path) as conn:
            start_collection_run(
                conn,
                run_id=run_id,
                market=market_label,
                timeframe="daily",
                symbols_requested=len(rows),
                notes=f"period={period}, interval={interval}, auto_adjust={auto_adjust}",
            )

        results: list[CollectionResult] = []
        for idx, member in enumerate(rows, start=1):
            self.progress(f"일봉 수집 진행 | {idx}/{len(rows)} {member.market} {member.raw_symbol}")
            results.append(
                self.collect_member(member, run_id=run_id, period=period, interval=interval, auto_adjust=auto_adjust)
            )
            time.sleep(self.sleep_seconds)

        success = sum(1 for row in results if row.status == "ok")
        failed = len(results) - success
        status = "done" if failed == 0 else "partial"
        with connect(self.db_path) as conn:
            complete_collection_run(
                conn,
                run_id=run_id,
                status=status,
                symbols_success=success,
                symbols_failed=failed,
                notes=f"성공={success}, 실패={failed}",
            )
        self.progress(f"일봉 수집 완료 | 요청={len(results)} 성공={success} 실패={failed}")
        return results


def results_to_dicts(results: list[CollectionResult]) -> list[dict]:
    return [asdict(result) for result in results]
