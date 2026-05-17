from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phase1_trainer import price_collector
from runtime.price_csv_health import (
    load_price_csv_frame,
    normalize_price_frame,
    price_csv_freshness_status,
    price_csv_health_summary,
    quarantine_bad_price_csv,
)


FetchFn = Callable[[str, pd.Timestamp, pd.Timestamp], pd.DataFrame]


def _price_path(root: Path, market: str, ticker: str) -> Path:
    market_key = str(market or "").upper()
    return root / "data" / "price" / market_key.lower() / f"{market_key.lower()}_{ticker}.csv"


def _issues_for_result(result: Any, freshness: dict[str, Any] | None) -> list[str]:
    issues: list[str] = []
    if result.status != "ok":
        issues.append(result.status)
    if freshness and not freshness.get("fresh", True):
        issues.append("stale_csv")
    if result.errors:
        issues.extend(str(item) for item in result.errors)
    if result.latest_flat_ohlc_zero_volume:
        issues.append("latest_flat_ohlc_zero_volume")
    elif result.flat_ohlc_zero_volume_rows:
        issues.append("flat_ohlc_zero_volume_rows")
    if result.too_few_rows:
        issues.append("too_few_rows")
    return sorted(set(issues))


def inspect_ticker(root: Path, market: str, ticker: str) -> dict[str, Any]:
    path = _price_path(root, market, ticker)
    _frame, result = load_price_csv_frame(path, market, ticker)
    freshness = price_csv_freshness_status(market, result.last_date) if result.last_date else None
    issues = _issues_for_result(result, freshness)
    return {
        "market": str(market or "").upper(),
        "ticker": ticker,
        "path": str(path),
        "status": result.status,
        "detail": result.detail,
        "rows": result.rows,
        "last_date": result.last_date,
        "freshness": freshness,
        "quality": {
            "flat_ohlc_rows": result.flat_ohlc_rows,
            "zero_volume_rows": result.zero_volume_rows,
            "flat_ohlc_zero_volume_rows": result.flat_ohlc_zero_volume_rows,
            "latest_flat_ohlc_zero_volume": result.latest_flat_ohlc_zero_volume,
            "too_few_rows": result.too_few_rows,
            "min_rows": result.min_rows,
        },
        "errors": list(result.errors),
        "warnings": list(result.warnings),
        "issues": issues,
        "needs_repair": bool(issues),
    }


def _candidate_tickers(root: Path, market: str) -> list[str]:
    summary = price_csv_health_summary(root, market)
    tickers: set[str] = set()
    quality_tickers = summary.get("quality_tickers") if isinstance(summary.get("quality_tickers"), dict) else {}
    for values in quality_tickers.values():
        for ticker in values or []:
            ticker_key = str(ticker or "").strip()
            if ticker_key:
                tickers.add(ticker_key)
    for status, rows in (summary.get("samples") or {}).items():
        for row in rows or []:
            quality = row.get("quality") if isinstance(row, dict) else {}
            quality_problem = False
            if isinstance(quality, dict):
                quality_problem = any(
                    bool(quality.get(key))
                    for key in (
                        "flat_ohlc_rows",
                        "zero_volume_rows",
                        "flat_ohlc_zero_volume_rows",
                        "latest_flat_ohlc_zero_volume",
                        "too_few_rows",
                    )
                )
            if status != "ok" or quality_problem:
                ticker = str(row.get("ticker") or "").strip()
                if ticker:
                    tickers.add(ticker)
    return sorted(tickers)


def _default_fetcher(market: str) -> FetchFn:
    market_key = str(market or "").upper()
    return price_collector.fetch_us_daily_yfinance if market_key == "US" else price_collector.fetch_kr_daily_yfinance


def _blocking_repair_issues(result: Any, freshness: dict[str, Any] | None) -> list[str]:
    issues: list[str] = []
    if result.status != "ok":
        issues.append(result.status)
    if freshness and not freshness.get("fresh", True):
        issues.append("stale_csv")
    if result.errors:
        issues.extend(str(item) for item in result.errors)
    if result.latest_flat_ohlc_zero_volume:
        issues.append("latest_flat_ohlc_zero_volume")
    if result.too_few_rows:
        issues.append("too_few_rows")
    return sorted(set(issues))


def _atomic_write_verified(path: Path, market: str, ticker: str, frame: pd.DataFrame, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> dict[str, Any]:
    clean, errors = normalize_price_frame(frame, start_dt, end_dt)
    if clean.empty:
        return {"ok": False, "error": "no_valid_rows", "errors": list(errors)}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.repair.tmp")
    clean.to_csv(tmp_path, index=False, encoding="utf-8-sig")
    verified, verify_result = load_price_csv_frame(tmp_path, market, ticker)
    if verified is None or verify_result.status != "ok":
        quarantine_path = quarantine_bad_price_csv(tmp_path, verify_result.detail)
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return {
            "ok": False,
            "error": "verification_failed",
            "detail": verify_result.detail,
            "quarantine": str(quarantine_path or ""),
        }
    freshness = price_csv_freshness_status(market, verify_result.last_date) if verify_result.last_date else None
    blocking_issues = _blocking_repair_issues(verify_result, freshness)
    if blocking_issues:
        quarantine_path = quarantine_bad_price_csv(tmp_path, ";".join(blocking_issues))
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return {
            "ok": False,
            "error": "quality_verification_failed",
            "issues": blocking_issues,
            "freshness": freshness,
            "quarantine": str(quarantine_path or ""),
        }
    tmp_path.replace(path)
    return {
        "ok": True,
        "rows": len(clean),
        "first_date": str(clean["date"].iloc[0]),
        "last_date": str(clean["date"].iloc[-1]),
        "normalized_warnings": list(errors),
    }


def repair_price_csv_quality(
    *,
    market: str,
    tickers: list[str] | None = None,
    root: Path = ROOT,
    lookback_days: int = 420,
    apply: bool = False,
    force: bool = False,
    fetcher: FetchFn | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    selected = [str(item).strip().upper() if market_key == "US" else str(item).strip() for item in (tickers or []) if str(item).strip()]
    if not selected:
        selected = _candidate_tickers(root, market_key)
    provider_available = True
    try:
        import yfinance  # noqa: F401
    except Exception:
        provider_available = False

    end_dt = pd.Timestamp(date.today())
    start_dt = pd.Timestamp(date.today() - timedelta(days=int(lookback_days)))
    fetch = fetcher or _default_fetcher(market_key)
    results: list[dict[str, Any]] = []
    for ticker in selected:
        before = inspect_ticker(root, market_key, ticker)
        item: dict[str, Any] = {
            **before,
            "provider": "yfinance",
            "provider_available": provider_available,
            "applied": False,
        }
        if not apply:
            results.append(item)
            continue
        if not force and not before.get("needs_repair"):
            item["skipped"] = "no_quality_issue"
            results.append(item)
            continue
        fetched = fetch(ticker, start_dt, end_dt) if provider_available or fetcher else pd.DataFrame()
        if fetched.empty:
            item["repair"] = {"ok": False, "error": "provider_returned_no_rows"}
            results.append(item)
            continue
        write_result = _atomic_write_verified(_price_path(root, market_key, ticker), market_key, ticker, fetched, start_dt, end_dt)
        item["repair"] = write_result
        item["applied"] = bool(write_result.get("ok"))
        item["after"] = inspect_ticker(root, market_key, ticker) if write_result.get("ok") else None
        results.append(item)
    return {
        "ok": True,
        "dry_run": not apply,
        "market": market_key,
        "count": len(results),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect or repair stale and low-quality price CSV files.")
    parser.add_argument("--market", required=True, choices=["KR", "US"])
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--lookback-days", type=int, default=420)
    parser.add_argument("--force", action="store_true")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True)
    group.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    payload = repair_price_csv_quality(
        market=args.market,
        tickers=args.ticker,
        root=Path(args.root),
        lookback_days=args.lookback_days,
        apply=bool(args.apply),
        force=bool(args.force),
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
