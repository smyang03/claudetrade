from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from kis_api import _daily_ohlcv_us_kis, get_access_token


def _date_rows(frame: pd.DataFrame) -> dict[str, pd.Series]:
    if frame is None or frame.empty:
        return {}
    work = frame.copy()
    work["date_key"] = pd.to_datetime(work["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    return {str(row.date_key): row for row in work.dropna(subset=["date_key"]).itertuples(index=False)}


def _p95(values: list[float]) -> float | None:
    return float(np.quantile(np.asarray(values, dtype=float), 0.95)) if values else None


def crosscheck(*, selected: pd.DataFrame, sessions: int, token: str) -> dict:
    dates = sorted(selected["session_date"].astype(str).unique())[-max(1, int(sessions)):]
    sample = selected[selected["session_date"].astype(str).isin(dates)].copy()
    rows: list[dict] = []
    history: dict[str, dict[str, object]] = {}
    for ticker in sorted(sample["ticker"].astype(str).unique()):
        try:
            bars = _daily_ohlcv_us_kis(ticker, token, lookback_days=200)
            history[ticker] = _date_rows(bars)
        except Exception as exc:
            history[ticker] = {"__error__": str(exc)[:240]}
    for item in sample.itertuples(index=False):
        ticker = str(item.ticker)
        lookup = history.get(ticker, {})
        error = str(lookup.get("__error__") or "")
        entry = lookup.get(str(item.entry_date_5d))
        exit_row = lookup.get(str(item.exit_date_5d))
        record = {
            "session_date": str(item.session_date),
            "ticker": ticker,
            "selection_rank": int(item.selection_rank),
            "entry_date": str(item.entry_date_5d),
            "exit_date": str(item.exit_date_5d),
            "status": "PASS",
            "error": error,
        }
        if entry is None or exit_row is None:
            record["status"] = "MISSING_KIS_PATH" if not error else "KIS_ERROR"
            rows.append(record)
            continue
        kis_entry = float(entry.open)
        kis_exit = float(exit_row.close)
        yahoo_entry = float(item.entry_open_5d)
        yahoo_exit = float(item.exit_close_5d)
        entry_dev = (kis_entry / yahoo_entry - 1.0) * 100.0
        exit_dev = (kis_exit / yahoo_exit - 1.0) * 100.0
        kis_return = (kis_exit / kis_entry - 1.0) * 100.0
        yahoo_return = (yahoo_exit / yahoo_entry - 1.0) * 100.0
        record.update(
            {
                "yahoo_entry_open": yahoo_entry,
                "kis_entry_open": kis_entry,
                "entry_deviation_pct": entry_dev,
                "yahoo_exit_close": yahoo_exit,
                "kis_exit_close": kis_exit,
                "exit_deviation_pct": exit_dev,
                "yahoo_gross_usd_pct": yahoo_return,
                "kis_gross_usd_pct": kis_return,
                "return_difference_pct": kis_return - yahoo_return,
            }
        )
        rows.append(record)
    passed = [row for row in rows if row["status"] == "PASS"]
    entry_abs = [abs(float(row["entry_deviation_pct"])) for row in passed]
    exit_abs = [abs(float(row["exit_deviation_pct"])) for row in passed]
    return_abs = [abs(float(row["return_difference_pct"])) for row in passed]
    coverage = len(passed) / len(rows) if rows else 0.0
    metrics = {
        "sample_sessions": len(dates),
        "sample_rows": len(rows),
        "passed_rows": len(passed),
        "coverage": coverage,
        "entry_abs_deviation_median_pct": float(np.median(entry_abs)) if entry_abs else None,
        "entry_abs_deviation_p95_pct": _p95(entry_abs),
        "exit_abs_deviation_median_pct": float(np.median(exit_abs)) if exit_abs else None,
        "exit_abs_deviation_p95_pct": _p95(exit_abs),
        "return_abs_difference_median_pct": float(np.median(return_abs)) if return_abs else None,
        "return_abs_difference_p95_pct": _p95(return_abs),
    }
    agreement_passed = bool(
        coverage >= 0.80
        and metrics["entry_abs_deviation_p95_pct"] is not None
        and metrics["entry_abs_deviation_p95_pct"] <= 1.0
        and metrics["exit_abs_deviation_p95_pct"] <= 1.0
        and metrics["return_abs_difference_p95_pct"] <= 1.5
    )
    return {
        "schema_version": "us_swing_kis_outcome_crosscheck_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "KIS overseas dailyprice HHDFS76240000 MODP=1; no fallback",
        "scope": "recent exact OOS selection sample; not full-history independent replication",
        "agreement_passed": agreement_passed,
        "thresholds": {
            "min_coverage": 0.80,
            "max_entry_exit_p95_deviation_pct": 1.0,
            "max_return_p95_difference_pct": 1.5,
        },
        "metrics": metrics,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-check sealed US swing OOS outcomes against KIS")
    parser.add_argument("--selected", default=str(ROOT / "reports" / "us_swing_oos_selected_20260711.csv"))
    parser.add_argument("--sessions", type=int, default=15)
    parser.add_argument("--output", default=str(ROOT / "reports" / "us_swing_kis_outcome_crosscheck_20260711.json"))
    args = parser.parse_args()
    selected = pd.read_csv(args.selected)
    token = get_access_token(market="US")
    report = crosscheck(selected=selected, sessions=args.sessions, token=token)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0 if report["agreement_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
