"""
Populate forward_1d / forward_3d / forward_5d in decisions.db from price CSVs.

This updater is idempotent:
- it revisits rows where any forward column is still NULL
- it preserves already-populated values
- it only fills values that can be computed from currently available CSV history
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "ml" / "decisions.db"
_PRICE_DIR = _ROOT / "data" / "price"

sys.path.insert(0, str(_ROOT))
try:
    from logger import get_collector_logger

    _log = get_collector_logger()
except Exception:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _log = logging.getLogger("ml.forward_updater")

_price_cache: dict[str, Optional[pd.DataFrame]] = {}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
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


def _calc_forward_return(df: pd.DataFrame, session_date: str, n_days: int) -> Optional[float]:
    dates = df.index.tolist()
    if session_date not in dates:
        return None
    base_idx = dates.index(session_date)
    future_idx = base_idx + n_days
    if future_idx >= len(dates):
        return None

    base_close = float(df.loc[session_date, "close"])
    future_close = float(df.iloc[future_idx]["close"])
    if base_close <= 0:
        return None
    return round((future_close - base_close) / base_close * 100, 4)


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
) -> None:
    pending = _fetch_pending(market)
    _log.info(f"[forward] pending rows={len(pending)} market={market or 'ALL'} dry_run={dry_run}")

    if not pending:
        _log.info("[forward] nothing to update")
        return

    updated = 0
    skipped = 0
    missing_csv = 0

    for row in pending:
        decision_id = row["id"]
        market_code = row["market"]
        ticker = row["ticker"]
        session_date = row["session_date"]

        df = _load_price(market_code, ticker)
        if df is None:
            missing_csv += 1
            continue

        calc_map = {n: _calc_forward_return(df, session_date, n) for n in forward_days}

        f1d = row["forward_1d"] if row["forward_1d"] is not None else calc_map.get(1)
        f3d = row["forward_3d"] if row["forward_3d"] is not None else calc_map.get(3)
        f5d = row["forward_5d"] if row["forward_5d"] is not None else calc_map.get(5)

        if f1d is None and f3d is None and f5d is None:
            skipped += 1
            continue

        if dry_run:
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
            updated += 1
        except Exception as e:
            _log.warning(f"[forward] update failed id={decision_id}: {e}")

    _log.info(
        f"[forward] done updated={updated} skipped={skipped} missing_csv={missing_csv}"
    )


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
