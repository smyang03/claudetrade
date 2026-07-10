from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]


def _universe(db_path: Path) -> list[str]:
    con = sqlite3.connect(db_path)
    try:
        values = {
            str(row[0]).strip().upper()
            for row in con.execute(
                "SELECT DISTINCT ticker FROM decisions WHERE market='US' AND data_source='backfill' AND ticker IS NOT NULL"
            )
            if str(row[0]).strip()
        }
    finally:
        con.close()
    return sorted(values | {"SPY", "QQQ", "IWM"})


def _ticker_frame(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if isinstance(raw.columns, pd.MultiIndex):
        if ticker not in raw.columns.get_level_values(0):
            return pd.DataFrame()
        frame = raw[ticker].copy()
    else:
        frame = raw.copy()
    frame = frame.reset_index()
    rename = {str(column): str(column).strip().lower().replace(" ", "_") for column in frame.columns}
    frame = frame.rename(columns=rename)
    required = ["date", "open", "high", "low", "close", "volume"]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return frame[required].dropna(subset=["date", "open", "high", "low", "close"]).drop_duplicates("date")


def _atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def collect(tickers: list[str], *, output_dir: Path, period: str, chunk_size: int) -> dict[str, Any]:
    import yfinance as yf

    success: dict[str, int] = {}
    failed: dict[str, str] = {}
    for start in range(0, len(tickers), max(1, chunk_size)):
        chunk = tickers[start: start + max(1, chunk_size)]
        try:
            raw = yf.download(
                chunk,
                period=period,
                interval="1d",
                auto_adjust=True,
                repair=True,
                progress=False,
                threads=False,
                group_by="ticker",
            )
        except Exception as exc:
            for ticker in chunk:
                failed[ticker] = f"batch_error:{exc}"
            continue
        for ticker in chunk:
            frame = _ticker_frame(raw, ticker)
            if frame.empty:
                failed[ticker] = "empty_or_schema_invalid"
                continue
            _atomic_csv(frame, output_dir / f"us_{ticker}.csv")
            success[ticker] = int(len(frame))
    return {
        "requested": len(tickers),
        "success": len(success),
        "failed": failed,
        "rows": int(sum(success.values())),
        "min_rows": min(success.values()) if success else 0,
        "median_rows": float(pd.Series(list(success.values())).median()) if success else 0.0,
        "max_rows": max(success.values()) if success else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect isolated 2-year Yahoo daily research cache for US anchors")
    parser.add_argument("--db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--output-dir", default=str(ROOT / "data" / "analysis" / "us_yahoo_2y"))
    parser.add_argument("--period", default="2y")
    parser.add_argument("--chunk-size", type=int, default=25)
    args = parser.parse_args()
    tickers = _universe(Path(args.db))
    report = collect(tickers, output_dir=Path(args.output_dir), period=args.period, chunk_size=args.chunk_size)
    report.update(
        {
            "output_dir": args.output_dir,
            "period": args.period,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "source": "yfinance Yahoo adjusted OHLCV repair=true",
            "authority": "research_only",
        }
    )
    manifest = Path(args.output_dir) / "collection_manifest.json"
    manifest.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
