"""Import externally collected intraday OHLCV files into the audit DB."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

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


@dataclass(frozen=True)
class IntradayImportResult:
    symbol: str
    market: str
    timeframe: str
    status: str
    source_path: str
    file_path: str
    storage_format: str
    row_count: int
    start_date: str
    end_date: str
    missing_rate: float
    quality_grade: str
    error_msg: str = ""


def _read_frame(path: Path) -> pd.DataFrame:
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)


def _write_frame(frame: pd.DataFrame, *, data_dir: Path, market: str, symbol: str, timeframe: str, storage_format: str) -> tuple[Path, str]:
    root = data_dir / "intraday" / market.upper() / timeframe
    root.mkdir(parents=True, exist_ok=True)
    fmt = storage_format.lower()
    if fmt == "csv":
        path = root / f"{symbol}.csv"
        frame.to_csv(path, index=False, encoding="utf-8-sig")
        return path, "csv"
    path = root / f"{symbol}.parquet"
    try:
        frame.to_parquet(path, index=False)
        return path, "parquet"
    except Exception:
        fallback = root / f"{symbol}.csv"
        frame.to_csv(fallback, index=False, encoding="utf-8-sig")
        return fallback, "csv"


def _symbol_from_path(path: Path) -> str:
    return path.stem.strip().upper()


def discover_intraday_files(input_path: Path) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        return [path]
    if not path.exists():
        return []
    files: list[Path] = []
    for pattern in ("*.csv", "*.parquet"):
        files.extend(sorted(path.glob(pattern)))
    return files


def import_intraday_file(
    source_path: Path,
    *,
    market: str,
    timeframe: str,
    db_path: Path = MARKET_DATA_DB,
    data_dir: Path = YFINANCE_DATA_DIR,
    storage_format: str = "parquet",
    run_id: str,
    symbol: str = "",
) -> IntradayImportResult:
    market_u = market.upper()
    source = Path(source_path)
    resolved_symbol = (symbol or _symbol_from_path(source)).upper()
    try:
        raw = _read_frame(source)
        frame = normalize_ohlcv_frame(raw)
        if frame.empty:
            raise ValueError("empty or invalid OHLCV frame")
        quality = validate_ohlcv_frame(frame, symbol=resolved_symbol, market=market_u, timeframe=timeframe)
        path, actual_format = _write_frame(
            frame,
            data_dir=Path(data_dir),
            market=market_u,
            symbol=resolved_symbol,
            timeframe=timeframe,
            storage_format=storage_format,
        )
        with connect(db_path) as conn:
            insert_symbol_resolution(conn, raw_symbol=resolved_symbol, resolved_symbol=resolved_symbol, market=market_u, status="ok")
            upsert_ohlcv_manifest(
                conn,
                {
                    "symbol": resolved_symbol,
                    "market": market_u,
                    "timeframe": timeframe,
                    "file_path": str(path),
                    "storage_format": actual_format,
                    "row_count": quality.row_count,
                    "start_date": quality.start_date,
                    "end_date": quality.end_date,
                    "missing_rate": quality.missing_rate,
                    "quality_grade": quality.quality_grade,
                    "adjusted_price": 1,
                    "collected_at": utc_now(),
                    "run_id": run_id,
                },
            )
            conn.execute(
                "DELETE FROM data_quality_issues WHERE symbol = ? AND market = ? AND timeframe = ?",
                (resolved_symbol, market_u, timeframe),
            )
            insert_quality_issues(conn, quality.issues)
        return IntradayImportResult(
            symbol=resolved_symbol,
            market=market_u,
            timeframe=timeframe,
            status="ok",
            source_path=str(source),
            file_path=str(path),
            storage_format=actual_format,
            row_count=quality.row_count,
            start_date=quality.start_date,
            end_date=quality.end_date,
            missing_rate=quality.missing_rate,
            quality_grade=quality.quality_grade,
        )
    except Exception as exc:
        with connect(db_path) as conn:
            insert_symbol_resolution(
                conn,
                raw_symbol=resolved_symbol,
                resolved_symbol="",
                market=market_u,
                status="failed",
                error_msg=str(exc),
            )
        return IntradayImportResult(
            symbol=resolved_symbol,
            market=market_u,
            timeframe=timeframe,
            status="failed",
            source_path=str(source),
            file_path="",
            storage_format="",
            row_count=0,
            start_date="",
            end_date="",
            missing_rate=100.0,
            quality_grade="FAIL",
            error_msg=str(exc),
        )


def import_intraday_files(
    files: Iterable[Path],
    *,
    market: str,
    timeframe: str,
    db_path: Path = MARKET_DATA_DB,
    data_dir: Path = YFINANCE_DATA_DIR,
    storage_format: str = "parquet",
    run_id: str | None = None,
) -> list[IntradayImportResult]:
    paths = list(files)
    init_database(db_path)
    run_id = run_id or f"II_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{timeframe}"
    with connect(db_path) as conn:
        start_collection_run(
            conn,
            run_id=run_id,
            market=market.upper(),
            timeframe=timeframe,
            symbols_requested=len(paths),
            notes="manual intraday file import",
        )
    results = [
        import_intraday_file(
            path,
            market=market,
            timeframe=timeframe,
            db_path=db_path,
            data_dir=data_dir,
            storage_format=storage_format,
            run_id=run_id,
        )
        for path in paths
    ]
    success = sum(1 for row in results if row.status == "ok")
    failed = len(results) - success
    with connect(db_path) as conn:
        complete_collection_run(
            conn,
            run_id=run_id,
            status="done" if failed == 0 else "partial",
            symbols_success=success,
            symbols_failed=failed,
            notes=f"success={success}, failed={failed}",
        )
    return results


def import_results_to_dicts(results: list[IntradayImportResult]) -> list[dict]:
    return [asdict(result) for result in results]
