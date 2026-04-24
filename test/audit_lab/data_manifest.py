"""Historical data quality manifest for audit-lab simulations."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path

import pandas as pd

from .adapters import available_tickers
from .config import PRICE_DIR, TRUSTED_START_BY_MARKET


@dataclass(frozen=True)
class TickerDataQuality:
    market: str
    ticker: str
    rows: int
    start_date: str
    end_date: str
    missing_ohlcv_pct: float
    duplicate_dates: int
    trusted_start: str
    adjusted_price: str
    delisted_included: bool
    survivorship_bias: str
    quality_grade: str


def _grade(rows: int, start_date: str, trusted_start: str, missing_pct: float, duplicate_dates: int) -> str:
    if rows <= 0:
        return "missing"
    if start_date <= trusted_start and missing_pct <= 1.0 and duplicate_dates == 0:
        return "high"
    if missing_pct <= 5.0:
        return "medium"
    return "low"


def inspect_price_file(market: str, ticker: str, price_dir: Path = PRICE_DIR) -> TickerDataQuality:
    market_u = str(market or "").upper()
    market_l = market_u.lower()
    trusted_start = TRUSTED_START_BY_MARKET.get(market_u, "2015-01-01")
    path = price_dir / market_l / f"{market_l}_{ticker}.csv"
    if not path.exists():
        return TickerDataQuality(market_u, ticker, 0, "", "", 100.0, 0, trusted_start, "unknown", False, "known_risk", "missing")

    df = pd.read_csv(path)
    rows = len(df)
    if rows == 0 or "date" not in df.columns:
        return TickerDataQuality(market_u, ticker, rows, "", "", 100.0, 0, trusted_start, "unknown", False, "known_risk", "missing")

    dates = pd.to_datetime(df["date"], errors="coerce")
    start_date = str(dates.min().date()) if dates.notna().any() else ""
    end_date = str(dates.max().date()) if dates.notna().any() else ""
    ohlcv_cols = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
    total_cells = max(rows * max(len(ohlcv_cols), 1), 1)
    missing_cells = int(df[ohlcv_cols].isna().sum().sum()) if ohlcv_cols else total_cells
    missing_pct = round(missing_cells / total_cells * 100.0, 3)
    duplicate_dates = int(df["date"].duplicated().sum())
    grade = _grade(rows, start_date or "9999-12-31", trusted_start, missing_pct, duplicate_dates)
    return TickerDataQuality(
        market=market_u,
        ticker=ticker,
        rows=rows,
        start_date=start_date,
        end_date=end_date,
        missing_ohlcv_pct=missing_pct,
        duplicate_dates=duplicate_dates,
        trusted_start=trusted_start,
        adjusted_price="unknown",
        delisted_included=False,
        survivorship_bias="known_risk",
        quality_grade=grade,
    )


def build_manifest(market: str, price_dir: Path = PRICE_DIR, ticker_limit: int = 0) -> dict:
    markets = ["KR", "US"] if str(market).upper() == "ALL" else [str(market).upper()]
    rows: list[TickerDataQuality] = []
    for mkt in markets:
        for ticker in available_tickers(mkt, price_dir=price_dir, limit=ticker_limit):
            rows.append(inspect_price_file(mkt, ticker, price_dir=price_dir))

    by_market = {}
    for mkt in markets:
        mrows = [r for r in rows if r.market == mkt]
        by_market[mkt] = {
            "tickers": len(mrows),
            "high": sum(1 for r in mrows if r.quality_grade == "high"),
            "medium": sum(1 for r in mrows if r.quality_grade == "medium"),
            "low": sum(1 for r in mrows if r.quality_grade == "low"),
            "missing": sum(1 for r in mrows if r.quality_grade == "missing"),
            "trusted_start": TRUSTED_START_BY_MARKET.get(mkt, ""),
            "survivorship_bias": "known_risk",
            "delisted_included": False,
        }
    return {"summary": by_market, "tickers": [asdict(r) for r in rows]}
