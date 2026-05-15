from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

REQUIRED_PRICE_COLUMNS = ("date", "open", "high", "low", "close", "volume")
NUMERIC_PRICE_COLUMNS = ("open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class PriceCsvLoadResult:
    path: str
    market: str
    ticker: str
    status: str
    detail: str
    rows: int = 0
    first_date: str = ""
    last_date: str = ""
    expected_last_date: str = ""
    calendar_source: str = ""
    errors: tuple[str, ...] = ()


def price_csv_identity(path: Path, market: str = "", ticker: str = "") -> tuple[str, str]:
    market_key = (market or path.parent.name or "").upper()
    prefix = f"{market_key.lower()}_"
    stem = path.stem
    ticker_key = ticker or (stem[len(prefix) :] if stem.lower().startswith(prefix) else stem)
    return market_key, ticker_key


def _normalize_timestamp(value: Any) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def expected_trading_days(
    market: str,
    start_dt: pd.Timestamp,
    end_dt: pd.Timestamp,
) -> tuple[list[pd.Timestamp], str]:
    start = _normalize_timestamp(start_dt)
    end = _normalize_timestamp(end_dt)
    if start > end:
        return [], "empty"
    market_key = str(market or "").upper()
    try:
        import exchange_calendars as ec

        calendar = ec.get_calendar("XKRX" if market_key == "KR" else "XNYS")
        sessions = calendar.sessions_in_range(start, end)
        return [pd.Timestamp(day).tz_localize(None).normalize() for day in sessions], "exchange_calendars"
    except Exception:
        days = [
            pd.Timestamp(day).normalize()
            for day in pd.date_range(start, end, freq="D")
            if day.weekday() < 5
        ]
        return days, "weekday_fallback"


def expected_last_trading_day(
    market: str,
    end_dt: pd.Timestamp | datetime | str,
    *,
    now: datetime | None = None,
) -> tuple[pd.Timestamp | None, str]:
    """Return the latest completed daily-bar session expected in local KST operations.

    US daily bars are expected through the previous US session when the local KST
    date is used as the collection end date. This avoids treating a four-day-old
    CSV as fresh just because it is within a fixed grace window.
    """
    market_key = str(market or "").upper()
    end = _normalize_timestamp(end_dt)
    if market_key == "US":
        if now is None:
            tz = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None
            now = datetime.now(tz)
        now_ts = pd.Timestamp(now)
        if now_ts.tzinfo is not None:
            now_ts = now_ts.tz_convert(None)
        local_today = pd.Timestamp(now.date()).normalize()
        close_buffer_hour_kst = int(7)
        latest_completed_local = local_today - pd.Timedelta(days=2 if now.time() < time(close_buffer_hour_kst, 0) else 1)
        end = min(end, latest_completed_local)
    sessions, source = expected_trading_days(market_key, end - pd.Timedelta(days=21), end)
    if sessions:
        return sessions[-1], source
    return None, source


def _date_series(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, errors="coerce", utc=True)
    return parsed.dt.tz_convert(None).dt.normalize()


def _finite_at_least(value: Any, minimum: float, *, inclusive: bool) -> bool:
    try:
        number = float(value)
    except Exception:
        return False
    if not math.isfinite(number):
        return False
    return number >= minimum if inclusive else number > minimum


def normalize_price_frame(
    df: pd.DataFrame,
    start_dt: pd.Timestamp | None = None,
    end_dt: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame, tuple[str, ...]]:
    errors: list[str] = []
    if df is None or df.empty:
        return pd.DataFrame(), ("empty_frame",)

    rename = {col: str(col).strip().lower() for col in df.columns}
    out = df.rename(columns=rename).copy()
    missing = [col for col in REQUIRED_PRICE_COLUMNS if col not in out.columns]
    if missing:
        return pd.DataFrame(), (f"missing_columns:{','.join(missing)}",)

    out = out[list(REQUIRED_PRICE_COLUMNS)].copy()
    dates = _date_series(out["date"])
    bad_dates = int(dates.isna().sum())
    if bad_dates:
        errors.append(f"invalid_date_rows:{bad_dates}")
    out["date"] = dates

    for col in NUMERIC_PRICE_COLUMNS:
        values = pd.to_numeric(out[col], errors="coerce")
        bad_values = int(values.isna().sum())
        if bad_values:
            errors.append(f"invalid_{col}_rows:{bad_values}")
        out[col] = values

    valid = out["date"].notna()
    for col in ("open", "high", "low", "close"):
        valid &= out[col].apply(lambda value: _finite_at_least(value, 0.0, inclusive=False))
    valid &= out["volume"].apply(lambda value: _finite_at_least(value, 0.0, inclusive=True))
    dropped = int((~valid).sum())
    if dropped:
        errors.append(f"dropped_invalid_rows:{dropped}")
    out = out[valid].copy()

    if out.empty:
        return pd.DataFrame(), tuple(errors or ["no_valid_rows"])

    if start_dt is not None:
        out = out[out["date"] >= _normalize_timestamp(start_dt)]
    if end_dt is not None:
        out = out[out["date"] <= _normalize_timestamp(end_dt)]
    if out.empty:
        return pd.DataFrame(), tuple(errors or ["outside_requested_window"])

    duplicate_count = int(out["date"].duplicated().sum())
    if duplicate_count:
        errors.append(f"duplicate_date_rows:{duplicate_count}")

    out = out.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out, tuple(errors)


def load_price_csv_frame(
    path: Path,
    market: str = "",
    ticker: str = "",
    *,
    expected_last_date: pd.Timestamp | None = None,
) -> tuple[pd.DataFrame | None, PriceCsvLoadResult]:
    market_key, ticker_key = price_csv_identity(path, market, ticker)
    if not path.exists():
        return None, PriceCsvLoadResult(
            path=str(path),
            market=market_key,
            ticker=ticker_key,
            status="missing_csv",
            detail="CSV file is missing",
        )
    try:
        raw = pd.read_csv(path, dtype={"date": str})
    except Exception as exc:
        return None, PriceCsvLoadResult(
            path=str(path),
            market=market_key,
            ticker=ticker_key,
            status="malformed_csv",
            detail=f"read_error:{exc}",
            errors=(f"read_error:{exc}",),
        )

    clean, errors = normalize_price_frame(raw)
    if clean.empty or errors:
        detail = ";".join(errors or ("no_valid_rows",))
        return None, PriceCsvLoadResult(
            path=str(path),
            market=market_key,
            ticker=ticker_key,
            status="malformed_csv",
            detail=detail,
            errors=errors or ("no_valid_rows",),
        )

    first_date = str(clean["date"].iloc[0])
    last_date = str(clean["date"].iloc[-1])
    expected_str = ""
    if expected_last_date is not None:
        expected = _normalize_timestamp(expected_last_date)
        expected_str = expected.strftime("%Y-%m-%d")
        if _normalize_timestamp(last_date) < expected:
            return clean, PriceCsvLoadResult(
                path=str(path),
                market=market_key,
                ticker=ticker_key,
                status="stale_csv",
                detail=f"last_date={last_date} expected_last_date={expected_str}",
                rows=len(clean),
                first_date=first_date,
                last_date=last_date,
                expected_last_date=expected_str,
            )

    return clean, PriceCsvLoadResult(
        path=str(path),
        market=market_key,
        ticker=ticker_key,
        status="ok",
        detail=f"rows={len(clean)} last_date={last_date}",
        rows=len(clean),
        first_date=first_date,
        last_date=last_date,
        expected_last_date=expected_str,
    )


def quarantine_bad_price_csv(path: Path, reason: str = "") -> Path | None:
    if not path.exists():
        return None
    price_root = path.parents[1] if len(path.parents) > 1 else path.parent
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target_dir = price_root / "_bad" / datetime.now().strftime("%Y%m%d")
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_reason = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in reason[:40])
    suffix = f".{stamp}.{safe_reason}.bad.csv" if safe_reason else f".{stamp}.bad.csv"
    target = target_dir / f"{path.stem}{suffix}"
    try:
        shutil.copy2(path, target)
        return target
    except Exception:
        return None


def price_csv_health_summary(
    root: Path,
    market: str,
    *,
    expected_date: pd.Timestamp | None = None,
    include_tickers: list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    price_dir = root / "data" / "price" / market_key.lower()
    expected, source = (expected_date, "provided") if expected_date is not None else expected_last_trading_day(
        market_key,
        pd.Timestamp(datetime.now().date()),
    )
    files = sorted(price_dir.glob(f"{market_key.lower()}_*.csv")) if price_dir.exists() else []
    by_ticker = {price_csv_identity(path, market_key)[1]: path for path in files}
    if include_tickers:
        for ticker in include_tickers:
            by_ticker.setdefault(str(ticker), price_dir / f"{market_key.lower()}_{ticker}.csv")

    total = len(by_ticker)
    counts = {"ok": 0, "missing_csv": 0, "malformed_csv": 0, "stale_csv": 0}
    fresh_count = 0
    samples: dict[str, list[dict[str, Any]]] = {key: [] for key in counts}
    last_dates: list[str] = []

    for ticker, path in sorted(by_ticker.items()):
        _df, result = load_price_csv_frame(path, market_key, ticker, expected_last_date=expected)
        counts[result.status] = counts.get(result.status, 0) + 1
        if result.status == "ok":
            fresh_count += 1
        if result.last_date:
            last_dates.append(result.last_date)
        bucket = samples.setdefault(result.status, [])
        if len(bucket) < 30:
            bucket.append(
                {
                    "ticker": ticker,
                    "path": str(path),
                    "detail": result.detail,
                    "last_date": result.last_date,
                }
            )

    fresh_ratio = (fresh_count / total) if total else 0.0
    expected_str = expected.strftime("%Y-%m-%d") if expected is not None else ""
    return {
        "market": market_key,
        "price_dir": str(price_dir),
        "total": total,
        "fresh_count": fresh_count,
        "fresh_ratio": fresh_ratio,
        "counts": counts,
        "expected_last_date": expected_str,
        "calendar_source": source,
        "oldest_last_date": min(last_dates) if last_dates else "",
        "newest_last_date": max(last_dates) if last_dates else "",
        "samples": samples,
    }
