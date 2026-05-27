from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST, resolve_session_date_str
from audit.candidate_counterfactual_store import CandidateCounterfactualStore
from runtime_paths import get_runtime_path


MINUTE_COLUMNS = ["ts", "open", "high", "low", "close", "volume", "source", "collected_at"]
DEFAULT_COUNTERFACTUAL_STATUSES = ("TRIGGERED", "PENDING", "PRICE_PENDING", "OUTCOME_PARTIAL")
FetchFunc = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class MinuteCollectionResult:
    ticker: str
    market: str
    status: str
    row_count: int
    provider: str = ""
    source: str = ""
    first_ts: str = ""
    last_ts: str = ""
    file_path: str = ""
    written: bool = False
    session_dates: dict[str, int] | None = None
    observed_dates: dict[str, int] | None = None
    non_60s_gap_count: int = 0
    max_gap_seconds: int = 0
    error: str = ""


def normalize_market(market: str) -> str:
    key = str(market or "").strip().upper()
    if key not in {"KR", "US"}:
        raise ValueError(f"unsupported market: {market}")
    return key


def normalize_ticker(market: str, ticker: str) -> str:
    market_key = normalize_market(market)
    text = str(ticker or "").strip()
    return text.upper() if market_key == "US" else text.zfill(6)


def minute_csv_path(price_root: str | Path, market: str, ticker: str) -> Path:
    market_key = normalize_market(market)
    market_dir = market_key.lower()
    ticker_key = normalize_ticker(market_key, ticker)
    return Path(price_root) / "minute" / market_dir / f"{market_dir}_{ticker_key}.csv"


def load_env_file(env_path: str | Path) -> bool:
    path = Path(env_path)
    if not path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")
        return True
    load_dotenv(dotenv_path=path, override=True)
    return True


def _format_ts(value: Any) -> str:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return ""
    if ts.tzinfo is None:
        ts = ts.tz_localize(KST)
    else:
        ts = ts.tz_convert(KST)
    return ts.isoformat()


def normalize_minute_frame(frame: pd.DataFrame, *, source: str = "") -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    work = frame.copy()
    columns = {str(col).lower(): col for col in work.columns}
    if "ts" not in columns:
        if "datetime" in columns:
            work["ts"] = work[columns["datetime"]]
        elif "date" in columns:
            work["ts"] = work[columns["date"]]
    for name in ("open", "high", "low", "close", "volume"):
        if name not in work.columns and name in columns:
            work[name] = work[columns[name]]
    if "source" not in work.columns:
        work["source"] = source
    work["ts"] = pd.to_datetime(work["ts"], errors="coerce")
    for name in ("open", "high", "low", "close", "volume"):
        if name not in work.columns:
            work[name] = 0.0 if name == "volume" else pd.NA
        work[name] = pd.to_numeric(work[name], errors="coerce")
    work = work.dropna(subset=["ts", "open", "high", "low", "close"])
    if work.empty:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    work["volume"] = work["volume"].fillna(0.0)
    work["source"] = work["source"].fillna(source).astype(str)
    work["ts"] = work["ts"].map(_format_ts)
    work["collected_at"] = datetime.now(KST).isoformat(timespec="seconds")
    work = work[MINUTE_COLUMNS].drop_duplicates("ts", keep="last").sort_values("ts")
    return work.reset_index(drop=True)


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    try:
        frame = pd.read_csv(path, encoding="utf-8-sig")
    except Exception:
        return pd.DataFrame(columns=MINUTE_COLUMNS)
    for column in MINUTE_COLUMNS:
        if column not in frame.columns:
            frame[column] = ""
    return frame[MINUTE_COLUMNS]


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _effective_provider_for_market(market: str, provider: str = "") -> str:
    market_key = normalize_market(market)
    raw = str(provider or "").strip().lower()
    if raw in {"", "auto"}:
        raw = str(os.getenv(f"INTRADAY_EVIDENCE_PROVIDER_{market_key}", "") or "").strip().lower()
    return raw


def _parse_kst_timestamp(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        ts = pd.Timestamp(value)
    except Exception:
        return None
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        ts = ts.tz_localize(KST)
    else:
        ts = ts.tz_convert(KST)
    return ts


def _latest_source_for_existing(existing: pd.DataFrame) -> str:
    if existing is None or existing.empty or "ts" not in existing.columns or "source" not in existing.columns:
        return ""
    work = existing[["ts", "source"]].copy()
    work["ts"] = pd.to_datetime(work["ts"], errors="coerce", utc=True)
    work = work.dropna(subset=["ts"]).sort_values("ts")
    if work.empty:
        return ""
    return str(work.iloc[-1]["source"] or "").strip().lower()


def _incremental_start_at(
    *,
    market: str,
    ticker: str,
    price_root: str | Path,
    provider: str,
    start_at: str,
    end_at: str,
) -> str:
    market_key = normalize_market(market)
    if market_key != "US":
        return start_at
    if _effective_provider_for_market(market_key, provider) != "kis":
        return start_at
    if not _env_bool("US_INTRADAY_KIS_INCREMENTAL_ENABLED", True):
        return start_at
    path = minute_csv_path(price_root, market_key, ticker)
    existing = _read_existing(path)
    if existing.empty or "ts" not in existing.columns:
        return start_at
    if "kis" not in _latest_source_for_existing(existing):
        return start_at
    values = pd.to_datetime(existing["ts"], errors="coerce", utc=True).dropna()
    if values.empty:
        return start_at
    latest = values.max().tz_convert(KST)
    next_start = latest + pd.Timedelta(minutes=1)
    original_start = _parse_kst_timestamp(start_at)
    if original_start is not None and next_start <= original_start:
        return start_at
    end_ts = _parse_kst_timestamp(end_at)
    if end_ts is not None and next_start > end_ts:
        return next_start.isoformat()
    return next_start.isoformat()


def _source_summary(frame: pd.DataFrame) -> str:
    if frame is None or frame.empty or "source" not in frame.columns:
        return ""
    values = sorted({str(item).strip() for item in frame["source"].tolist() if str(item).strip()})
    return ",".join(values)


def write_minute_csv(
    frame: pd.DataFrame,
    *,
    price_root: str | Path,
    market: str,
    ticker: str,
    append: bool = True,
) -> tuple[Path, int]:
    normalized = normalize_minute_frame(frame)
    path = minute_csv_path(price_root, market, ticker)
    path.parent.mkdir(parents=True, exist_ok=True)
    if append:
        existing = _read_existing(path)
        parts = [item for item in (existing, normalized) if item is not None and not item.empty]
        combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=MINUTE_COLUMNS)
        combined = combined.drop_duplicates("ts", keep="last").sort_values("ts")
    else:
        combined = normalized
    combined.to_csv(path, index=False, encoding="utf-8-sig", columns=MINUTE_COLUMNS)
    return path, int(len(combined))


def summarize_minute_frame(frame: pd.DataFrame, *, market: str) -> dict[str, Any]:
    normalized = normalize_minute_frame(frame)
    if normalized.empty:
        return {
            "row_count": 0,
            "first_ts": "",
            "last_ts": "",
            "session_dates": {},
            "observed_dates": {},
            "non_60s_gap_count": 0,
            "max_gap_seconds": 0,
        }
    ts_values = pd.to_datetime(normalized["ts"], errors="coerce").dropna().sort_values()
    gaps = ts_values.diff().dropna().dt.total_seconds().astype(int).tolist()
    session_dates = Counter(resolve_session_date_str(market, ts.to_pydatetime()) for ts in ts_values)
    observed_dates = Counter(ts.strftime("%Y-%m-%d") for ts in ts_values)
    return {
        "row_count": int(len(normalized)),
        "first_ts": str(normalized.iloc[0]["ts"]),
        "last_ts": str(normalized.iloc[-1]["ts"]),
        "session_dates": dict(sorted(session_dates.items())),
        "observed_dates": dict(sorted(observed_dates.items())),
        "non_60s_gap_count": int(sum(1 for gap in gaps if gap != 60)),
        "max_gap_seconds": int(max(gaps) if gaps else 0),
    }


def fetch_intraday_frame(
    *,
    market: str,
    ticker: str,
    session_date: str,
    start_at: str = "",
    end_at: str = "",
    provider: str = "",
    request_timeout: float | None = None,
    fetcher: FetchFunc | None = None,
) -> pd.DataFrame:
    market_key = normalize_market(market)
    ticker_key = normalize_ticker(market_key, ticker)
    if fetcher is not None:
        return fetcher(
            ticker=ticker_key,
            market=market_key,
            session_date=session_date,
            start_at=start_at or None,
            end_at=end_at or None,
            provider=provider,
            request_timeout=request_timeout,
        )
    import kis_api

    return kis_api.get_intraday_candles(
        ticker_key,
        market=market_key,
        session_date=session_date,
        start_at=start_at or None,
        end_at=end_at or None,
        provider=provider,
        request_timeout=request_timeout,
    )


def collect_one(
    *,
    market: str,
    ticker: str,
    session_date: str,
    price_root: str | Path,
    provider: str = "",
    start_at: str = "",
    end_at: str = "",
    request_timeout: float | None = None,
    write: bool = True,
    fetcher: FetchFunc | None = None,
) -> MinuteCollectionResult:
    market_key = normalize_market(market)
    ticker_key = normalize_ticker(market_key, ticker)
    try:
        fetch_start_at = start_at
        if write:
            fetch_start_at = _incremental_start_at(
                market=market_key,
                ticker=ticker_key,
                price_root=price_root,
                provider=provider,
                start_at=start_at,
                end_at=end_at,
            )
        raw = fetch_intraday_frame(
            market=market_key,
            ticker=ticker_key,
            session_date=session_date,
            start_at=fetch_start_at,
            end_at=end_at,
            provider=provider,
            request_timeout=request_timeout,
            fetcher=fetcher,
        )
        normalized = normalize_minute_frame(raw, source=provider)
        summary = summarize_minute_frame(normalized, market=market_key)
        path = minute_csv_path(price_root, market_key, ticker_key)
        row_count = int(summary["row_count"])
        source = _source_summary(normalized)
        written = False
        file_path = ""
        if write and row_count > 0:
            path, written_count = write_minute_csv(normalized, price_root=price_root, market=market_key, ticker=ticker_key)
            row_count = written_count
            combined = _read_existing(path)
            summary = summarize_minute_frame(combined, market=market_key)
            source = _source_summary(combined)
            written = True
            file_path = str(path)
        return MinuteCollectionResult(
            ticker=ticker_key,
            market=market_key,
            status="ok" if summary["row_count"] else "empty",
            row_count=row_count,
            provider=_effective_provider_for_market(market_key, provider) or provider,
            source=source,
            first_ts=str(summary["first_ts"]),
            last_ts=str(summary["last_ts"]),
            file_path=file_path if written else str(path),
            written=written,
            session_dates=summary["session_dates"],
            observed_dates=summary["observed_dates"],
            non_60s_gap_count=int(summary["non_60s_gap_count"]),
            max_gap_seconds=int(summary["max_gap_seconds"]),
        )
    except Exception as exc:
        return MinuteCollectionResult(
            ticker=ticker_key,
            market=market_key,
            status="failed",
            row_count=0,
            provider=_effective_provider_for_market(market_key, provider) or provider,
            file_path=str(minute_csv_path(price_root, market_key, ticker_key)),
            error=str(exc),
        )


def counterfactual_tickers(
    db_path: str | Path,
    *,
    market: str,
    session_date: str = "",
    statuses: Iterable[str] = DEFAULT_COUNTERFACTUAL_STATUSES,
) -> list[str]:
    path = Path(db_path)
    if not path.exists():
        return []
    market_key = normalize_market(market)
    status_list = [str(status).strip().upper() for status in statuses if str(status).strip()]
    where = ["market=?"]
    params: list[Any] = [market_key]
    if session_date:
        where.append("session_date=?")
        params.append(str(session_date)[:10])
    if status_list:
        placeholders = ",".join("?" for _ in status_list)
        where.append(f"status IN ({placeholders})")
        params.extend(status_list)
    sql = f"""
        SELECT DISTINCT ticker
        FROM candidate_counterfactual_paths
        WHERE {' AND '.join(where)}
        ORDER BY ticker
    """
    CandidateCounterfactualStore(path)
    with closing(sqlite3.connect(str(path))) as conn:
        return [normalize_ticker(market_key, row[0]) for row in conn.execute(sql, params).fetchall() if row[0]]


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _default_session_date(market: str) -> str:
    return resolve_session_date_str(normalize_market(market), datetime.now(KST))


def _default_tickers(market: str) -> list[str]:
    return ["005930"] if normalize_market(market) == "KR" else ["AAPL"]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect standard 1m CSVs for counterfactual outcome backfills.")
    parser.add_argument("--market", required=True, choices=["KR", "US", "kr", "us"])
    parser.add_argument("--tickers", default="", help="comma-separated tickers; defaults to smoke ticker")
    parser.add_argument("--from-counterfactual-db", action="store_true", help="read distinct tickers from candidate counterfactual DB")
    parser.add_argument("--db-path", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD; defaults to current session by market")
    parser.add_argument("--statuses", default=",".join(DEFAULT_COUNTERFACTUAL_STATUSES))
    parser.add_argument("--price-root", default=str(get_runtime_path("data", "price")))
    parser.add_argument("--env", default="", help="dotenv file to load before importing kis_api")
    parser.add_argument("--provider", default="", help="KR defaults to configured KIS; US defaults to configured/yfinance")
    parser.add_argument("--start-at", default="")
    parser.add_argument("--end-at", default="")
    parser.add_argument("--max-tickers", type=int, default=0)
    parser.add_argument("--request-timeout", type=float, default=10.0)
    parser.add_argument("--sleep-sec", type=float, default=0.3)
    parser.add_argument("--smoke", action="store_true", help="single-page/no-write smoke by default")
    parser.add_argument("--write", action="store_true", help="write CSVs even in --smoke mode")
    parser.add_argument("--no-write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    market = normalize_market(args.market)
    if args.env:
        load_env_file(args.env)
    if args.smoke and market == "KR":
        os.environ.setdefault("KR_INTRADAY_KIS_MAX_PAGES", "1")
    session_date = str(args.date or _default_session_date(market))[:10]
    tickers = _split_csv(args.tickers)
    if args.from_counterfactual_db:
        tickers.extend(
            counterfactual_tickers(
                args.db_path,
                market=market,
                session_date=session_date,
                statuses=_split_csv(args.statuses),
            )
        )
    if not tickers:
        tickers = _default_tickers(market)
    tickers = list(dict.fromkeys(normalize_ticker(market, ticker) for ticker in tickers))
    if args.smoke:
        tickers = tickers[:1]
    if args.max_tickers and args.max_tickers > 0:
        tickers = tickers[: args.max_tickers]
    write = bool(args.write or (not args.no_write and not args.smoke))

    results: list[MinuteCollectionResult] = []
    for idx, ticker in enumerate(tickers):
        results.append(
            collect_one(
                market=market,
                ticker=ticker,
                session_date=session_date,
                price_root=args.price_root,
                provider=args.provider,
                start_at=args.start_at,
                end_at=args.end_at,
                request_timeout=args.request_timeout,
                write=write,
            )
        )
        if idx < len(tickers) - 1 and args.sleep_sec > 0:
            time.sleep(args.sleep_sec)

    payload = {
        "ok": all(item.status in {"ok", "empty"} for item in results),
        "market": market,
        "session_date": session_date,
        "write": write,
        "smoke": bool(args.smoke),
        "price_root": str(args.price_root),
        "result_count": len(results),
        "status_counts": dict(sorted(Counter(item.status for item in results).items())),
        "results": [asdict(item) for item in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"market={market} session_date={session_date} write={write} results={len(results)}")
        for item in results:
            print(
                f"{item.market} {item.ticker} status={item.status} rows={item.row_count} "
                f"first={item.first_ts} last={item.last_ts} written={item.written} file={item.file_path}"
            )
            if item.error:
                print(f"  error={item.error}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
