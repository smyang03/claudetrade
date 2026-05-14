from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_audit_store import CandidateAuditStore
from runtime_paths import get_runtime_path


BOARD_KEYS = ("KOSPI", "KOSDAQ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        text = str(value).strip().replace(",", "")
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _mean(values: list[float]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(float(value), digits) if value is not None else None


def _normalize_board(value: Any) -> str:
    raw = str(value or "").upper()
    if "KOSDAQ" in raw or raw == "KQ":
        return "KOSDAQ"
    if "KOSPI" in raw or raw == "KP":
        return "KOSPI"
    return ""


def _load_candidate_rows(db_path: Path, *, session_date: str, runtime_mode: str = "live") -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    store = CandidateAuditStore(db_path)
    conn = store.connect()
    try:
        return [
            dict(row)
            for row in conn.execute(
                """
                SELECT ticker, market_type, pnl_pct, filled_count, classification
                FROM audit_candidate_latest_rows
                WHERE runtime_mode=? AND market='KR' AND session_date=?
                """,
                (str(runtime_mode or "live").lower(), session_date),
            )
        ]
    finally:
        conn.close()


def _board_weights(rows: list[dict[str, Any]]) -> tuple[dict[str, float], str]:
    filled = [row for row in rows if int(row.get("filled_count") or 0) > 0 or _num(row.get("pnl_pct")) is not None]
    for source, source_rows in (("filled_rows", filled), ("candidate_rows", rows)):
        counts = {key: 0 for key in BOARD_KEYS}
        for row in source_rows:
            board = _normalize_board(row.get("market_type"))
            if board in counts:
                counts[board] += 1
        total = sum(counts.values())
        if total > 0:
            return {key: counts[key] / total for key in BOARD_KEYS}, source
    return {"KOSPI": 0.5, "KOSDAQ": 0.5}, "default_equal"


def _yf_index_snapshot(symbol: str, label: str) -> dict[str, Any]:
    import yfinance as yf

    hist = yf.Ticker(symbol).history(period="5d", interval="1d")
    if hist is None or hist.empty or "Close" not in hist:
        raise RuntimeError(f"yfinance index history empty: {symbol}")
    closes = [float(v) for v in list(hist["Close"].dropna())]
    if len(closes) < 2:
        raise RuntimeError(f"yfinance index history too short: {symbol}")
    prev_close = closes[-2]
    close = closes[-1]
    change_pct = ((close / prev_close) - 1.0) * 100.0 if prev_close else 0.0
    return {
        "index": label,
        "symbol": symbol,
        "price": close,
        "prev_close": prev_close,
        "change_pct": change_pct,
        "source": "yfinance",
        "observed_at": _utc_now(),
    }


def _index_snapshot(index: str, get_index_snapshot_func: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    getter = get_index_snapshot_func
    if getter is None:
        from kis_api import get_index_snapshot as getter
    try:
        snap = dict(getter("KR", index) or {})
        snap.setdefault("index", index)
        snap.setdefault("source", "kis_index_price")
        snap.setdefault("observed_at", _utc_now())
        snap["change_pct"] = float(snap.get("change_pct") or 0.0)
        return snap
    except Exception as exc:
        symbol = "^KS11" if index == "KOSPI" else "^KQ11"
        try:
            snap = _yf_index_snapshot(symbol, index)
            snap["fallback_reason"] = str(exc)
            return snap
        except Exception as fallback_exc:
            return {
                "index": index,
                "symbol": symbol,
                "change_pct": 0.0,
                "source": "unavailable",
                "observed_at": _utc_now(),
                "error": str(fallback_exc),
                "fallback_reason": str(exc),
            }


def build_kr_benchmark_alpha_report(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    runtime_mode: str = "live",
    get_index_snapshot_func: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    session = str(session_date or date.today().isoformat())[:10]
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    rows = _load_candidate_rows(target, session_date=session, runtime_mode=runtime_mode)
    filled_returns: list[float] = []
    for row in rows:
        value = _num(row.get("pnl_pct"))
        if value is not None and int(row.get("filled_count") or 0) > 0:
            filled_returns.append(float(value))
    strategy_return = _mean(filled_returns)
    weights, weight_source = _board_weights(rows)
    index_snaps = {
        "KOSPI": _index_snapshot("KOSPI", get_index_snapshot_func=get_index_snapshot_func),
        "KOSDAQ": _index_snapshot("KOSDAQ", get_index_snapshot_func=get_index_snapshot_func),
    }
    benchmark_return = sum(
        float(weights.get(board, 0.0)) * float(index_snaps[board].get("change_pct") or 0.0)
        for board in BOARD_KEYS
    )
    alpha = strategy_return - benchmark_return if strategy_return is not None else None
    return {
        "generated_at": _utc_now(),
        "session_date": session,
        "runtime_mode": str(runtime_mode or "live").lower(),
        "db_path": str(target),
        "filled_count": len(filled_returns),
        "candidate_count": len(rows),
        "strategy_return_pct": _round(strategy_return),
        "board_weights": {key: _round(value, 4) for key, value in weights.items()},
        "board_weight_source": weight_source,
        "indexes": {
            key: {
                **snap,
                "change_pct": _round(_num(snap.get("change_pct"))),
            }
            for key, snap in index_snaps.items()
        },
        "benchmark_return_pct": _round(benchmark_return),
        "alpha_pct": _round(alpha),
    }


def write_report(summary: dict[str, Any], *, out_dir: str | Path | None = None) -> dict[str, str]:
    target_dir = Path(out_dir) if out_dir else get_runtime_path("reports")
    target_dir.mkdir(parents=True, exist_ok=True)
    session = str(summary.get("session_date") or date.today().isoformat())
    json_path = target_dir / f"kr_benchmark_alpha_{session}.json"
    md_path = target_dir / f"kr_benchmark_alpha_{session}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    lines = [
        f"# KR Benchmark Alpha Report - {session}",
        "",
        f"- generated_at: {summary.get('generated_at', '')}",
        f"- filled_count: {summary.get('filled_count', 0)}",
        f"- strategy_return_pct: {summary.get('strategy_return_pct')}",
        f"- benchmark_return_pct: {summary.get('benchmark_return_pct')}",
        f"- alpha_pct: {summary.get('alpha_pct')}",
        f"- board_weight_source: {summary.get('board_weight_source')}",
        "",
        "## Index Sources",
    ]
    for board, snap in (summary.get("indexes") or {}).items():
        lines.append(
            f"- {board}: change_pct={snap.get('change_pct')} source={snap.get('source')} observed_at={snap.get('observed_at', '')}"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build KR strategy alpha report versus KOSPI/KOSDAQ.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--out-dir", default="", help="output directory")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args(argv)
    summary = build_kr_benchmark_alpha_report(
        db_path=args.db or None,
        session_date=args.date or date.today().isoformat(),
        runtime_mode=args.runtime_mode,
    )
    if not args.no_write:
        summary["paths"] = write_report(summary, out_dir=args.out_dir or None)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
