from __future__ import annotations

import math
import os
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
    warnings: tuple[str, ...] = ()
    samples: tuple[dict[str, Any], ...] = ()
    flat_ohlc_rows: int = 0
    zero_volume_rows: int = 0
    flat_ohlc_zero_volume_rows: int = 0
    latest_flat_ohlc_zero_volume: bool = False
    too_few_rows: bool = False
    min_rows: int = 0


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


def _kst_now(now: datetime | pd.Timestamp | str | None = None) -> datetime:
    tz = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None
    if now is None:
        return datetime.now(tz)
    if isinstance(now, pd.Timestamp):
        value = now.to_pydatetime()
    elif isinstance(now, str):
        value = pd.Timestamp(now).to_pydatetime()
    else:
        value = now
    if tz is not None and value.tzinfo is not None:
        return value.astimezone(tz)
    return value


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
    now_dt = _kst_now(now)
    local_today = pd.Timestamp(now_dt.date()).normalize()
    if market_key == "KR":
        latest_completed_local = local_today - pd.Timedelta(days=1 if now_dt.time() < time(16, 0) else 0)
        end = min(end, latest_completed_local)
    elif market_key == "US":
        latest_completed_local = local_today - pd.Timedelta(days=2 if now_dt.time() < time(7, 0) else 1)
        end = min(end, latest_completed_local)
    sessions, source = expected_trading_days(market_key, end - pd.Timedelta(days=21), end)
    if sessions:
        return sessions[-1], source
    return None, source


def price_csv_freshness_threshold(market: str, calendar_source: str) -> int:
    market_key = str(market or "").upper()
    if calendar_source == "exchange_calendars":
        return 2
    if market_key == "KR":
        # Weekday fallback cannot see KR holidays; this protects long Seollal/Chuseok breaks.
        return 7
    return 3


def price_csv_min_rows(market: str) -> int:
    market_key = str(market or "").upper()
    env_key = "PRICE_CSV_MIN_ROWS_US" if market_key == "US" else "PRICE_CSV_MIN_ROWS_KR"
    try:
        return max(0, int(os.getenv(env_key, "30") or 30))
    except Exception:
        return 30


def _quality_metrics(df: pd.DataFrame, *, min_rows: int = 0) -> dict[str, Any]:
    if df is None or df.empty:
        return {
            "flat_ohlc_rows": 0,
            "zero_volume_rows": 0,
            "flat_ohlc_zero_volume_rows": 0,
            "latest_flat_ohlc_zero_volume": False,
            "too_few_rows": bool(min_rows > 0),
            "min_rows": int(min_rows or 0),
        }
    work = df.copy()
    for col in NUMERIC_PRICE_COLUMNS:
        work[col] = pd.to_numeric(work[col], errors="coerce")
    flat = (
        (work["open"] == work["high"])
        & (work["high"] == work["low"])
        & (work["low"] == work["close"])
    )
    zero_volume = work["volume"] == 0
    flat_zero = flat & zero_volume
    return {
        "flat_ohlc_rows": int(flat.sum()),
        "zero_volume_rows": int(zero_volume.sum()),
        "flat_ohlc_zero_volume_rows": int(flat_zero.sum()),
        "latest_flat_ohlc_zero_volume": bool(len(work) > 0 and bool(flat_zero.iloc[-1])),
        "too_few_rows": bool(int(min_rows or 0) > 0 and len(work) < int(min_rows or 0)),
        "min_rows": int(min_rows or 0),
    }


def price_csv_freshness_status(
    market: str,
    last_date: pd.Timestamp | datetime | str,
    *,
    now: datetime | pd.Timestamp | str | None = None,
) -> dict[str, Any]:
    market_key = "US" if str(market or "").upper() == "US" else "KR"
    try:
        last = _normalize_timestamp(last_date)
    except Exception as exc:
        return {
            "fresh": False,
            "market": market_key,
            "last_date": str(last_date or ""),
            "latest_completed": "",
            "missing_sessions": 999,
            "threshold": price_csv_freshness_threshold(market_key, "invalid_last_date"),
            "calendar_source": "invalid_last_date",
            "error": str(exc),
        }

    now_dt = _kst_now(now)
    latest_completed, latest_source = expected_last_trading_day(
        market_key,
        pd.Timestamp(now_dt.date()),
        now=now_dt,
    )
    last_str = last.strftime("%Y-%m-%d")
    if latest_completed is None:
        threshold = price_csv_freshness_threshold(market_key, latest_source)
        return {
            "fresh": True,
            "market": market_key,
            "last_date": last_str,
            "latest_completed": "",
            "missing_sessions": 0,
            "threshold": threshold,
            "calendar_source": latest_source,
        }

    latest = _normalize_timestamp(latest_completed)
    if last >= latest:
        threshold = price_csv_freshness_threshold(market_key, latest_source)
        return {
            "fresh": True,
            "market": market_key,
            "last_date": last_str,
            "latest_completed": latest.strftime("%Y-%m-%d"),
            "missing_sessions": 0,
            "threshold": threshold,
            "calendar_source": latest_source,
        }

    missing_days, calendar_source = expected_trading_days(
        market_key,
        last + pd.Timedelta(days=1),
        latest,
    )
    threshold = price_csv_freshness_threshold(market_key, calendar_source)
    missing_count = len(missing_days)
    return {
        "fresh": missing_count <= threshold,
        "market": market_key,
        "last_date": last_str,
        "latest_completed": latest.strftime("%Y-%m-%d"),
        "missing_sessions": missing_count,
        "threshold": threshold,
        "calendar_source": calendar_source,
        "missing_dates": [pd.Timestamp(day).strftime("%Y-%m-%d") for day in missing_days[:10]],
    }


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


def validate_ohlc_logic(df: pd.DataFrame) -> tuple[pd.DataFrame, tuple[str, ...], list[dict[str, Any]]]:
    if df is None or df.empty:
        return df, (), []
    samples: list[dict[str, Any]] = []
    bad_rows = 0
    for _, row in df.iterrows():
        violations: list[str] = []
        values: dict[str, float] = {}
        for col in NUMERIC_PRICE_COLUMNS:
            try:
                values[col] = float(row.get(col))
            except Exception:
                values[col] = float("nan")
        open_v = values["open"]
        high_v = values["high"]
        low_v = values["low"]
        close_v = values["close"]
        volume_v = values["volume"]
        if not _finite_at_least(open_v, 0.0, inclusive=False):
            violations.append("open_non_positive")
        if not _finite_at_least(high_v, 0.0, inclusive=False):
            violations.append("high_non_positive")
        if not _finite_at_least(low_v, 0.0, inclusive=False):
            violations.append("low_non_positive")
        if not _finite_at_least(close_v, 0.0, inclusive=False):
            violations.append("close_non_positive")
        if not _finite_at_least(volume_v, 0.0, inclusive=True):
            violations.append("volume_negative")
        if high_v < open_v:
            violations.append("high_lt_open")
        if high_v < close_v:
            violations.append("high_lt_close")
        if high_v < low_v:
            violations.append("high_lt_low")
        if low_v > open_v:
            violations.append("low_gt_open")
        if low_v > close_v:
            violations.append("low_gt_close")
        if violations:
            bad_rows += 1
            if len(samples) < 30:
                date_value = row.get("date", "")
                if isinstance(date_value, pd.Timestamp):
                    date_value = date_value.strftime("%Y-%m-%d")
                samples.append(
                    {
                        "date": str(date_value),
                        "open": open_v,
                        "high": high_v,
                        "low": low_v,
                        "close": close_v,
                        "violation": ",".join(violations),
                    }
                )
    errors = (f"ohlc_logic_error_rows:{bad_rows}",) if bad_rows else ()
    return df, errors, samples


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

    _checked, ohlc_errors, _ohlc_samples = validate_ohlc_logic(out)
    errors.extend(ohlc_errors)

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
    min_rows: int | None = None,
) -> tuple[pd.DataFrame | None, PriceCsvLoadResult]:
    market_key, ticker_key = price_csv_identity(path, market, ticker)
    min_rows_value = price_csv_min_rows(market_key) if min_rows is None else int(min_rows or 0)
    if not path.exists():
        return None, PriceCsvLoadResult(
            path=str(path),
            market=market_key,
            ticker=ticker_key,
            status="missing_csv",
            detail="CSV file is missing",
            min_rows=min_rows_value,
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
            min_rows=min_rows_value,
        )

    normalized_columns = {str(col).strip().lower() for col in raw.columns}
    extra_columns = tuple(sorted(col for col in normalized_columns if col not in REQUIRED_PRICE_COLUMNS))
    warnings = (f"extra_columns:{','.join(extra_columns)}",) if extra_columns else ()
    clean, errors = normalize_price_frame(raw)
    metrics = _quality_metrics(clean, min_rows=min_rows_value)
    if clean.empty or errors:
        detail = ";".join(errors or ("no_valid_rows",))
        samples: tuple[dict[str, Any], ...] = ()
        first_date = ""
        last_date = ""
        if clean is not None and not clean.empty:
            first_date = str(clean["date"].iloc[0])
            last_date = str(clean["date"].iloc[-1])
            _checked, _ohlc_errors, ohlc_samples = validate_ohlc_logic(clean)
            samples = tuple(ohlc_samples)
        return None, PriceCsvLoadResult(
            path=str(path),
            market=market_key,
            ticker=ticker_key,
            status="malformed_csv",
            detail=detail,
            rows=len(clean) if clean is not None else 0,
            first_date=first_date,
            last_date=last_date,
            errors=errors or ("no_valid_rows",),
            warnings=warnings,
            samples=samples,
            **metrics,
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
                warnings=warnings,
                **metrics,
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
        warnings=warnings,
        **metrics,
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
    counts = {
        "ok": 0,
        "missing_csv": 0,
        "malformed_csv": 0,
        "stale_csv": 0,
        "extra_columns_csv": 0,
        "ohlc_logic_error_csv": 0,
        "ohlc_logic_error_rows": 0,
        "latest_ohlc_logic_error_csv": 0,
        "flat_ohlc_rows": 0,
        "zero_volume_rows": 0,
        "flat_ohlc_zero_volume_csv": 0,
        "flat_ohlc_zero_volume_rows": 0,
        "latest_flat_ohlc_zero_volume_csv": 0,
        "too_few_rows_csv": 0,
    }
    fresh_count = 0
    samples: dict[str, list[dict[str, Any]]] = {key: [] for key in counts}
    last_dates: list[str] = []
    quality_tickers: dict[str, list[str]] = {
        "ohlc_logic_error": [],
        "latest_ohlc_logic_error": [],
        "latest_flat_ohlc_zero_volume": [],
        "too_few_rows": [],
    }

    policy_sources: dict[str, int] = {}
    for ticker, path in sorted(by_ticker.items()):
        _df, result = load_price_csv_frame(path, market_key, ticker)
        status = result.status
        detail = result.detail
        freshness: dict[str, Any] | None = None
        if any(str(item).startswith("extra_columns:") for item in result.warnings):
            counts["extra_columns_csv"] += 1
        counts["flat_ohlc_rows"] += int(result.flat_ohlc_rows or 0)
        counts["zero_volume_rows"] += int(result.zero_volume_rows or 0)
        if result.flat_ohlc_zero_volume_rows:
            counts["flat_ohlc_zero_volume_csv"] += 1
            counts["flat_ohlc_zero_volume_rows"] += int(result.flat_ohlc_zero_volume_rows)
        if result.latest_flat_ohlc_zero_volume:
            counts["latest_flat_ohlc_zero_volume_csv"] += 1
            quality_tickers["latest_flat_ohlc_zero_volume"].append(ticker)
        if result.too_few_rows:
            counts["too_few_rows_csv"] += 1
            quality_tickers["too_few_rows"].append(ticker)
        ohlc_error_rows = 0
        for error in result.errors:
            if str(error).startswith("ohlc_logic_error_rows:"):
                counts["ohlc_logic_error_csv"] += 1
                quality_tickers["ohlc_logic_error"].append(ticker)
                try:
                    ohlc_error_rows += int(str(error).split(":", 1)[1])
                except Exception:
                    pass
        if ohlc_error_rows:
            counts["ohlc_logic_error_rows"] += ohlc_error_rows
            expected_match = expected.strftime("%Y-%m-%d") if expected is not None else ""
            if any(sample.get("date") in {expected_match, result.last_date} for sample in result.samples):
                counts["latest_ohlc_logic_error_csv"] += 1
                quality_tickers["latest_ohlc_logic_error"].append(ticker)
        if status == "ok" and result.last_date:
            freshness = price_csv_freshness_status(market_key, result.last_date)
            policy_sources[str(freshness.get("calendar_source") or "")] = (
                policy_sources.get(str(freshness.get("calendar_source") or ""), 0) + 1
            )
            if not freshness.get("fresh"):
                status = "stale_csv"
                detail = (
                    f"last_date={freshness.get('last_date')} "
                    f"latest_completed={freshness.get('latest_completed')} "
                    f"missing_sessions={freshness.get('missing_sessions')} "
                    f"threshold={freshness.get('threshold')} "
                    f"calendar={freshness.get('calendar_source')}"
                )
        counts[status] = counts.get(status, 0) + 1
        if status == "ok":
            fresh_count += 1
        if result.last_date:
            last_dates.append(result.last_date)
        bucket = samples.setdefault(status, [])
        if len(bucket) < 30:
            sample = {
                "ticker": ticker,
                "path": str(path),
                "detail": detail,
                "last_date": result.last_date,
            }
            if freshness is not None:
                sample["freshness"] = freshness
            if result.errors:
                sample["errors"] = list(result.errors)
            if result.warnings:
                sample["warnings"] = list(result.warnings)
            if result.samples:
                sample["samples"] = list(result.samples)
            quality = {
                "flat_ohlc_rows": result.flat_ohlc_rows,
                "zero_volume_rows": result.zero_volume_rows,
                "flat_ohlc_zero_volume_rows": result.flat_ohlc_zero_volume_rows,
                "latest_flat_ohlc_zero_volume": result.latest_flat_ohlc_zero_volume,
                "too_few_rows": result.too_few_rows,
                "min_rows": result.min_rows,
            }
            if any(quality.values()):
                sample["quality"] = quality
            bucket.append(sample)

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
        "freshness_policy_sources": policy_sources,
        "min_rows": price_csv_min_rows(market_key),
        "oldest_last_date": min(last_dates) if last_dates else "",
        "newest_last_date": max(last_dates) if last_dates else "",
        "quality_tickers": quality_tickers,
        "samples": samples,
    }
