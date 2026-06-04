from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


READY_ACTIONS = {"BUY_READY", "PROBE_READY", "ADD_READY", "PULLBACK_WAIT"}
DEFAULT_DB = Path("data/audit/candidate_audit.db")
OUTCOME_HORIZONS = (30, 60, 120)


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed == parsed else None


def _mean(values: list[float]) -> float | None:
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def _int_value(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except Exception:
        return None


def _text_blob(row: sqlite3.Row | dict[str, Any]) -> str:
    parts = []
    for key in (
        "candidate_source",
        "source_file",
        "source_tags_json",
        "payload_json",
        "prompt_pool_version",
        "candidate_pool_role",
        "discovery_signal_family",
        "discovery_reason",
    ):
        try:
            parts.append(str(row[key] or ""))
        except Exception:
            pass
    return " ".join(parts).lower()


def source_bucket(row: sqlite3.Row | dict[str, Any]) -> str:
    blob = _text_blob(row)
    if "sub_screener" in blob:
        return "sub_screener"
    try:
        discovery_signal = str(row["discovery_signal_family"] or "").strip()
        discovery_reason = str(row["discovery_reason"] or "").strip()
    except Exception:
        discovery_signal = ""
        discovery_reason = ""
    if discovery_signal or discovery_reason or "discovery" in blob:
        return "discovery"
    try:
        if int(row["final_prompt_included"] or 0) or int(row["actual_prompt_included"] or 0):
            return "base_prompt"
    except Exception:
        pass
    return "other"


def _candidate_key(row: sqlite3.Row | dict[str, Any]) -> str:
    try:
        return str(row["candidate_key"] or "").strip()
    except Exception:
        return ""


def _fetch_outcomes(conn: sqlite3.Connection, candidate_keys: list[str]) -> dict[str, dict[int, list[sqlite3.Row]]]:
    outcomes: dict[str, dict[int, list[sqlite3.Row]]] = {}
    keys = sorted({key for key in candidate_keys if key})
    if not keys:
        return outcomes
    for start in range(0, len(keys), 500):
        chunk = keys[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        try:
            rows = conn.execute(
                f"""
                SELECT candidate_key, horizon_min, return_pct, max_runup_pct, max_drawdown_pct, status
                FROM audit_candidate_outcomes
                WHERE candidate_key IN ({placeholders})
                  AND horizon_min IN ({','.join(str(value) for value in OUTCOME_HORIZONS)})
                """,
                chunk,
            )
        except sqlite3.Error:
            return {}
        for row in rows:
            key = _candidate_key(row)
            horizon = _int_value(row["horizon_min"])
            if not key or horizon not in OUTCOME_HORIZONS:
                continue
            outcomes.setdefault(key, {}).setdefault(horizon, []).append(row)
    return outcomes


def _outcome_summary(
    rows: list[sqlite3.Row],
    outcomes_by_key: dict[str, dict[int, list[sqlite3.Row]]],
) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for horizon in OUTCOME_HORIZONS:
        outcome_rows: list[sqlite3.Row] = []
        for row in rows:
            outcome_rows.extend(outcomes_by_key.get(_candidate_key(row), {}).get(horizon, []))
        returns = [_num(row["return_pct"]) for row in outcome_rows]
        runups = [_num(row["max_runup_pct"]) for row in outcome_rows]
        drawdowns = [_num(row["max_drawdown_pct"]) for row in outcome_rows]
        summary[f"horizon_{horizon}_rows"] = len(outcome_rows)
        summary[f"horizon_{horizon}_avg_return_pct"] = _mean([value for value in returns if value is not None])
        summary[f"horizon_{horizon}_avg_max_runup_pct"] = _mean([value for value in runups if value is not None])
        summary[f"horizon_{horizon}_avg_max_drawdown_pct"] = _mean([value for value in drawdowns if value is not None])
    return summary


def _summarize(
    rows: list[sqlite3.Row],
    outcomes_by_key: dict[str, dict[int, list[sqlite3.Row]]] | None = None,
) -> dict[str, Any]:
    tickers = {str(row["ticker"] or "").strip() for row in rows if str(row["ticker"] or "").strip()}
    actionable = [
        row for row in rows
        if str(row["route_final_action"] or "").strip().upper() in READY_ACTIONS
        or int(row["claude_trade_ready"] or 0) > 0
    ]
    signals = sum(int(row["buy_signal_count"] or 0) for row in rows)
    filled = sum(int(row["filled_count"] or 0) for row in rows)
    quality = [_num(row["candidate_quality_score"]) for row in rows]
    pnl = [_num(row["pnl_pct"]) for row in rows]
    mfe = [_num(row["position_mfe_pct"]) for row in rows]
    no_signal = [row for row in rows if int(row["buy_signal_count"] or 0) <= 0 and str(row["first_ready_at"] or "").strip() == ""]
    summary = {
        "rows": len(rows),
        "tickers": len(tickers),
        "actionable_rows": len(actionable),
        "actionable_rate": round(len(actionable) / len(rows), 4) if rows else None,
        "signal_count": signals,
        "filled_count": filled,
        "fill_rate": round(filled / len(rows), 4) if rows else None,
        "no_signal_rows": len(no_signal),
        "no_signal_rate": round(len(no_signal) / len(rows), 4) if rows else None,
        "avg_quality": _mean([value for value in quality if value is not None]),
        "avg_pnl_pct": _mean([value for value in pnl if value is not None]),
        "avg_position_mfe_pct": _mean([value for value in mfe if value is not None]),
    }
    if outcomes_by_key is not None:
        summary.update(_outcome_summary(rows, outcomes_by_key))
    return summary


def build_report(
    db_path: str | Path = DEFAULT_DB,
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> dict[str, Any]:
    path = Path(db_path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    where = ["runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market or "").upper())
    try:
        rows = list(conn.execute(
            f"""
            SELECT candidate_key, session_date, market, runtime_mode, ticker,
                   candidate_source, source_file, source_tags_json, payload_json,
                   prompt_pool_version, candidate_pool_role,
                   discovery_signal_family, discovery_reason,
                   final_prompt_included, actual_prompt_included,
                   claude_trade_ready, route_final_action,
                   buy_signal_count, filled_count, first_ready_at,
                   pnl_pct, position_mfe_pct, candidate_quality_score
            FROM audit_candidate_latest_rows
            WHERE {' AND '.join(where)}
            """,
            params,
        ))
        outcomes_by_key = _fetch_outcomes(conn, [_candidate_key(row) for row in rows])
    finally:
        conn.close()

    buckets: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        buckets.setdefault(source_bucket(row), []).append(row)
    bucket_summary = {
        name: _summarize(bucket_rows, outcomes_by_key)
        for name, bucket_rows in sorted(buckets.items())
    }
    base = bucket_summary.get("base_prompt") or {}
    report = {
        "schema": "sub_screener_uplift_report.v1",
        "db_path": str(path),
        "session_date": session_date or "ALL",
        "market": str(market or "ALL").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "total_rows": len(rows),
        "buckets": bucket_summary,
        "delta_vs_base_prompt": {},
    }
    for name, summary in bucket_summary.items():
        if name == "base_prompt" or not base:
            continue
        delta: dict[str, Any] = {}
        for metric in (
            "actionable_rate",
            "fill_rate",
            "no_signal_rate",
            "avg_quality",
            "avg_pnl_pct",
            "avg_position_mfe_pct",
            "horizon_30_avg_return_pct",
            "horizon_30_avg_max_runup_pct",
            "horizon_30_avg_max_drawdown_pct",
            "horizon_60_avg_return_pct",
            "horizon_60_avg_max_runup_pct",
            "horizon_60_avg_max_drawdown_pct",
            "horizon_120_avg_return_pct",
            "horizon_120_avg_max_runup_pct",
            "horizon_120_avg_max_drawdown_pct",
        ):
            if summary.get(metric) is not None and base.get(metric) is not None:
                delta[metric] = round(float(summary[metric]) - float(base[metric]), 4)
        report["delta_vs_base_prompt"][name] = delta
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report sub-screener/discovery candidate uplift from candidate audit DB.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--session-date", default="")
    parser.add_argument("--market", default="")
    parser.add_argument("--runtime-mode", default="live")
    args = parser.parse_args(argv)
    report = build_report(args.db, session_date=args.session_date, market=args.market, runtime_mode=args.runtime_mode)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
