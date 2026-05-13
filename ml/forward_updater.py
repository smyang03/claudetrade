"""
Populate forward_1d / forward_3d / forward_5d in decisions.db from price CSVs.

This updater is idempotent:
- it revisits rows where any forward column is still NULL
- it preserves already-populated values
- it only fills values that can be computed from currently available CSV history
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).parent.parent
_PRICE_DIR = _ROOT / "data" / "price"

sys.path.insert(0, str(_ROOT))
from ml.db_writer import _resolve_db_path

try:
    from logger import get_collector_logger

    _log = get_collector_logger()
except Exception:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _log = logging.getLogger("ml.forward_updater")

_price_cache: dict[str, Optional[pd.DataFrame]] = {}


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _get_conn() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10, factory=_ClosingConnection)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _load_price(market: str, ticker: str) -> Optional[pd.DataFrame]:
    key = f"{market}:{ticker}"
    if key in _price_cache:
        return _price_cache[key]

    mkt = market.lower()
    path = _PRICE_DIR / mkt / f"{mkt}_{ticker}.csv"
    if not path.exists():
        _log.warning(f"[forward] missing CSV: {path}")
        _price_cache[key] = None
        return None

    try:
        df = pd.read_csv(path, dtype={"date": str}).sort_values("date").reset_index(drop=True)
        df = df.set_index("date")
        _price_cache[key] = df
        return df
    except Exception as e:
        _log.warning(f"[forward] failed to load CSV {path}: {e}")
        _price_cache[key] = None
        return None


def _calc_forward_return(df: pd.DataFrame, session_date: str, n_days: int) -> tuple[Optional[float], str]:
    if "close" not in df.columns:
        return None, "price_column_missing"
    dates = df.index.tolist()
    if session_date not in dates:
        return None, "session_date_missing"
    base_idx = dates.index(session_date)
    future_idx = base_idx + n_days
    if future_idx >= len(dates):
        return None, "future_price_not_available"

    try:
        base_close = float(df.loc[session_date, "close"])
        future_close = float(df.iloc[future_idx]["close"])
    except Exception:
        return None, "price_column_missing"
    if not math.isfinite(base_close) or base_close <= 0:
        return None, "base_close_invalid"
    if not math.isfinite(future_close):
        return None, "future_price_not_available"
    return round((future_close - base_close) / base_close * 100, 4), ""


def _fetch_pending(market: Optional[str]) -> list[dict]:
    conditions = ["(forward_1d IS NULL OR forward_3d IS NULL OR forward_5d IS NULL)"]
    params: list[object] = []
    if market:
        conditions.append("market = ?")
        params.append(market)
    where = " AND ".join(conditions)
    sql = f"""
    SELECT id, market, ticker, session_date, forward_1d, forward_3d, forward_5d
    FROM decisions
    WHERE {where}
    ORDER BY id
    """
    with _get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def run(
    market: Optional[str] = None,
    dry_run: bool = False,
    forward_days: tuple[int, ...] = (1, 3, 5),
) -> dict:
    pending = _fetch_pending(market)
    _log.info(f"[forward] pending rows={len(pending)} market={market or 'ALL'} dry_run={dry_run}")

    summary = {
        "pending": len(pending),
        "updated": 0,
        "partial_updated": 0,
        "skipped": 0,
        "missing_csv": 0,
        "would_update": 0,
        "skip_by": {
            "session_date_missing": 0,
            "future_price_not_available": 0,
            "base_close_invalid": 0,
            "price_column_missing": 0,
        },
        "dry_run": dry_run,
    }

    if not pending:
        _log.info("[forward] nothing to update")
        return summary

    for row in pending:
        decision_id = row["id"]
        market_code = row["market"]
        ticker = row["ticker"]
        session_date = row["session_date"]

        df = _load_price(market_code, ticker)
        if df is None:
            summary["missing_csv"] += 1
            continue

        calc_map = {n: _calc_forward_return(df, session_date, n) for n in forward_days}
        values = {n: result[0] for n, result in calc_map.items()}
        reasons = {n: result[1] for n, result in calc_map.items()}

        f1d = row["forward_1d"] if row["forward_1d"] is not None else values.get(1)
        f3d = row["forward_3d"] if row["forward_3d"] is not None else values.get(3)
        f5d = row["forward_5d"] if row["forward_5d"] is not None else values.get(5)

        if f1d is None and f3d is None and f5d is None:
            reason = _row_skip_reason(reasons)
            summary["skipped"] += 1
            if reason in summary["skip_by"]:
                summary["skip_by"][reason] += 1
            continue

        if any(v is None for v in (f1d, f3d, f5d)):
            summary["partial_updated"] += 1

        if dry_run:
            summary["would_update"] += 1
            print(
                f"[DRY] id={decision_id:6d} {market_code} {ticker:8s} {session_date} "
                f"f1d={_fmt(f1d)} f3d={_fmt(f3d)} f5d={_fmt(f5d)}"
            )
            continue

        try:
            with _get_conn() as conn:
                conn.execute(
                    "UPDATE decisions SET forward_1d=?, forward_3d=?, forward_5d=? WHERE id=?",
                    (f1d, f3d, f5d, decision_id),
                )
            summary["updated"] += 1
        except Exception as e:
            _log.warning(f"[forward] update failed id={decision_id}: {e}")

    skip_parts = ", ".join(
        f"{key}={value}" for key, value in summary["skip_by"].items() if value
    ) or "none"
    _log.info(
        f"[forward] done updated={summary['updated']} "
        f"partial_updated={summary['partial_updated']} skipped={summary['skipped']} "
        f"skip_by={{{skip_parts}}} missing_csv={summary['missing_csv']}"
    )
    return summary


def _row_skip_reason(reasons: dict[int, str]) -> str:
    priority = (
        "session_date_missing",
        "base_close_invalid",
        "price_column_missing",
        "future_price_not_available",
    )
    present = {reason for reason in reasons.values() if reason}
    for reason in priority:
        if reason in present:
            return reason
    return "future_price_not_available"


def _fmt(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "N/A"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update forward returns in decisions.db")
    parser.add_argument("--market", choices=["KR", "US"], default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--days", default="1,3,5")
    args = parser.parse_args()

    fdays = tuple(int(x.strip()) for x in args.days.split(",") if x.strip())
    run(market=args.market, dry_run=args.dry_run, forward_days=fdays)
