"""Korean-market Yahoo Finance secondary quote monitor (shadow-only)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.session_date import KST, resolve_session_date_str
from preopen.storage import append_jsonl, load_preopen_state, log_path
from preopen.yfinance_shadow import (
    compare_kr_primary_to_yfinance,
    fetch_kr_yfinance_quote,
    select_fresh_kis_primary_samples,
)
from runtime_paths import get_runtime_path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    rows.append(value)
    except OSError:
        return []
    return rows


def _rank_by_ticker(state: dict[str, Any]) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for index, candidate in enumerate(list((state or {}).get("candidates") or []), start=1):
        if not isinstance(candidate, dict):
            continue
        ticker = str(candidate.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        try:
            rank = int(candidate.get("shadow_preopen_rank") or index)
        except (TypeError, ValueError):
            rank = index
        ranks[ticker] = min(ranks.get(ticker, rank), rank)
    return ranks


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _status_path(mode: str) -> Path:
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    name = "kr_yfinance_shadow_status.json" if runtime_mode == "live" else f"kr_yfinance_shadow_status_{runtime_mode}.json"
    return get_runtime_path("state", name)


def _write_status(mode: str, payload: dict[str, Any]) -> Path:
    path = _status_path(mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
    return path


def run_once(
    *,
    mode: str = "live",
    session_date: str = "",
    max_tickers: int | None = None,
    primary_outcome_path: str | Path | None = None,
    quote_fetcher: Callable[[str], dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare a bounded set of fresh KIS outcome samples to Yahoo quotes."""
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    session = session_date or resolve_session_date_str("KR")
    captured_at = (now or datetime.now(KST)).astimezone(KST)
    cap = max(0, int(max_tickers if max_tickers is not None else _env_int("KR_YFINANCE_SHADOW_MAX_TICKERS", 12)))
    primary_path = Path(primary_outcome_path) if primary_outcome_path else log_path("outcome", "KR", session, mode=runtime_mode)
    state = load_preopen_state("KR", session_date=session, max_age_min=24 * 60, mode=runtime_mode)
    primary_rows = _read_jsonl(primary_path)
    selected = select_fresh_kis_primary_samples(
        primary_rows,
        rank_by_ticker=_rank_by_ticker(state),
        max_tickers=cap,
    )
    fetch = quote_fetcher or fetch_kr_yfinance_quote
    divergence_warn_pct = _env_float("KR_YFINANCE_SHADOW_DIVERGENCE_WARN_PCT", 1.0)
    max_stale_min = _env_float("KR_YFINANCE_SHADOW_MAX_STALE_MIN", 20.0)
    output_path = log_path("yfinance_shadow", "KR", session, mode=runtime_mode)
    status_counts: dict[str, int] = {}
    records: list[dict[str, Any]] = []
    for primary in selected:
        secondary = fetch(str(primary.get("ticker") or ""))
        comparison = compare_kr_primary_to_yfinance(
            primary,
            secondary,
            captured_at=captured_at,
            divergence_warn_pct=divergence_warn_pct,
            max_stale_min=max_stale_min,
        )
        comparison.update({
            "market": "KR",
            "mode": runtime_mode,
            "session_date": session,
            "captured_at": captured_at.isoformat(timespec="seconds"),
            "monitor": "kr_yfinance_shadow",
            "authoritative_provider": "kis",
        })
        append_jsonl(output_path, comparison)
        records.append(comparison)
        key = str(comparison.get("comparison_status") or "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1
    summary = {
        "market": "KR",
        "mode": runtime_mode,
        "session_date": session,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "policy": "shadow_only_kis_authoritative",
        "primary_outcome_path": str(primary_path),
        "output_path": str(output_path),
        "primary_rows": len(primary_rows),
        "eligible_fresh_kis_rows": len(selected),
        "max_tickers": cap,
        "divergence_warn_pct": divergence_warn_pct,
        "max_stale_min": max_stale_min,
        "status_counts": status_counts,
        "execution_eligible": False,
        "selection_input": False,
    }
    summary["status_path"] = str(_write_status(runtime_mode, summary))
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare fresh KIS KR quotes to Yahoo Finance in shadow mode.")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--session-date", default="")
    parser.add_argument("--max-tickers", type=int, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    summary = run_once(mode=args.mode, session_date=args.session_date, max_tickers=args.max_tickers)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
