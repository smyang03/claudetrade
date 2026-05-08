from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.update_candidate_audit_outcomes import MIN_SAMPLES_BY_HORIZON


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _percentile(values: list[float], pct: float) -> float | None:
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    rank = (len(clean) - 1) * pct
    lower = int(rank)
    upper = min(lower + 1, len(clean) - 1)
    weight = rank - lower
    return clean[lower] + (clean[upper] - clean[lower]) * weight


def _mean(values: list[float]) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None else None


def _rate(values: list[float], predicate) -> float | None:
    clean = [v for v in values if v is not None]
    if not clean:
        return None
    return sum(1 for value in clean if predicate(value)) / len(clean)


def _metric_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    returns = [_to_float(row.get("return_pct")) for row in rows]
    runups = [_to_float(row.get("max_runup_pct")) for row in rows]
    drawdowns = [_to_float(row.get("max_drawdown_pct")) for row in rows]
    returns_clean = [v for v in returns if v is not None]
    runups_clean = [v for v in runups if v is not None]
    drawdowns_clean = [v for v in drawdowns if v is not None]
    return {
        "rows": len(rows),
        "unique_tickers": len({str(row.get("ticker") or "").upper() for row in rows if row.get("ticker")}),
        "labeled_rows": len(returns_clean),
        "mean_return_pct": _round(_mean(returns_clean)),
        "median_return_pct": _round(_percentile(returns_clean, 0.50)),
        "p75_return_pct": _round(_percentile(returns_clean, 0.75)),
        "p90_return_pct": _round(_percentile(returns_clean, 0.90)),
        "p95_return_pct": _round(_percentile(returns_clean, 0.95)),
        "mean_mfe_pct": _round(_mean(runups_clean)),
        "median_mfe_pct": _round(_percentile(runups_clean, 0.50)),
        "p90_mfe_pct": _round(_percentile(runups_clean, 0.90)),
        "positive_return_rate": _round(_rate(returns_clean, lambda value: value > 0)),
        "mfe_2pct_rate": _round(_rate(runups_clean, lambda value: value >= 2.0)),
        "mfe_3pct_rate": _round(_rate(runups_clean, lambda value: value >= 3.0)),
        "mfe_5pct_rate": _round(_rate(runups_clean, lambda value: value >= 5.0)),
        "mae_minus2pct_rate": _round(_rate(drawdowns_clean, lambda value: value <= -2.0)),
        "small_bucket_sample": len(returns_clean) < 5,
    }


def _where_clause(*, session_date: str, market: str, runtime_mode: str) -> tuple[str, list[Any]]:
    where = ["r.runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("r.session_date=?")
        params.append(session_date)
    if market:
        where.append("r.market=?")
        params.append(str(market).upper())
    return " AND ".join(where), params


def _load_outcome_rows(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
    horizon_min: int,
) -> list[dict[str, Any]]:
    where, params = _where_clause(session_date=session_date, market=market, runtime_mode=runtime_mode)
    params.append(int(horizon_min))
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT r.candidate_key, r.session_date, r.market, r.call_id, r.known_at,
                   r.ticker, COALESCE(r.classification, 'unknown') AS classification,
                   r.recommended_strategy, r.strategy_used, r.filled_count,
                   r.pnl_pct, r.close_reason, r.route_reason, r.route_cancel_pathb,
                   r.route_suspend_pathb, r.route_warnings_json,
                   o.horizon_min, o.status,
                   o.return_pct, o.max_runup_pct, o.max_drawdown_pct,
                   o.observed_at, o.observed_price, o.payload_json
            FROM audit_candidate_rows r
            LEFT JOIN audit_candidate_outcomes o
              ON o.candidate_key = r.candidate_key
             AND o.horizon_min = ?
            WHERE {where}
            """,
            [params[-1], *params[:-1]],
        )
    ]


def _top_by_mfe(
    rows: list[dict[str, Any]],
    classification: str,
    *,
    limit: int,
    horizon_min: int,
) -> list[dict[str, Any]]:
    subset = [
        row
        for row in rows
        if row.get("classification") == classification and _to_float(row.get("max_runup_pct")) is not None
    ]
    subset.sort(key=lambda row: _to_float(row.get("max_runup_pct")) or -9999.0, reverse=True)
    out: list[dict[str, Any]] = []
    for row in subset[:limit]:
        sample_count = None
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
            sample_count = payload.get("sample_count")
        except Exception:
            sample_count = None
        min_samples = int(MIN_SAMPLES_BY_HORIZON.get(int(horizon_min), 1))
        out.append(
            {
                "candidate_key": row.get("candidate_key"),
                "session_date": row.get("session_date"),
                "market": row.get("market"),
                "call_id": row.get("call_id"),
                "known_at": row.get("known_at"),
                "ticker": row.get("ticker"),
                "classification": row.get("classification"),
                "return_pct": _round(_to_float(row.get("return_pct"))),
                "max_runup_pct": _round(_to_float(row.get("max_runup_pct"))),
                "max_drawdown_pct": _round(_to_float(row.get("max_drawdown_pct"))),
                "sample_count": sample_count,
                "thin_price_sample": sample_count is None or int(sample_count or 0) < min_samples,
            }
        )
    return out


def _strategy_tokens(value: Any) -> set[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return set()
    return {part for part in re.split(r"[^a-z0-9_]+", raw) if part}


def classify_strategy_match(recommended_strategy: Any, strategy_used: Any) -> str:
    used = str(strategy_used or "").strip().lower()
    if not used:
        return "not_applicable"
    recommended = _strategy_tokens(recommended_strategy)
    if not recommended:
        return "unclassified"
    return "match" if used in recommended else "mismatch"


def _strategy_mismatch(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [
        row
        for row in rows
        if int(row.get("filled_count") or 0) > 0 and str(row.get("strategy_used") or "").strip()
    ]
    buckets: dict[str, list[dict[str, Any]]] = {"match": [], "mismatch": [], "unclassified": []}
    pairs: dict[str, dict[str, Any]] = {}
    for row in filled:
        status = classify_strategy_match(row.get("recommended_strategy"), row.get("strategy_used"))
        if status == "not_applicable":
            continue
        buckets.setdefault(status, []).append(row)
        pair_key = f"{row.get('recommended_strategy') or ''} -> {row.get('strategy_used') or ''}"
        item = pairs.setdefault(
            pair_key,
            {
                "recommended_strategy": row.get("recommended_strategy") or "",
                "strategy_used": row.get("strategy_used") or "",
                "status": status,
                "count": 0,
                "pnl_values": [],
                "close_reasons": {},
            },
        )
        item["count"] += 1
        pnl = _to_float(row.get("pnl_pct"))
        if pnl is not None:
            item["pnl_values"].append(pnl)
        close_reason = str(row.get("close_reason") or "").strip() or "unknown"
        item["close_reasons"][close_reason] = item["close_reasons"].get(close_reason, 0) + 1

    summary: dict[str, Any] = {
        "filled_strategy_rows": len(filled),
        "match_count": len(buckets.get("match", [])),
        "mismatch_count": len(buckets.get("mismatch", [])),
        "unclassified_count": len(buckets.get("unclassified", [])),
    }
    summary["mismatch_rate"] = _round(
        len(buckets.get("mismatch", [])) / len(filled) if filled else None
    )
    pair_rows = []
    for item in pairs.values():
        pnl_values = item.pop("pnl_values")
        item["avg_pnl_pct"] = _round(_mean(pnl_values))
        item["median_pnl_pct"] = _round(_percentile(pnl_values, 0.50))
        pair_rows.append(item)
    pair_rows.sort(key=lambda item: (item["status"] != "mismatch", -int(item["count"])))
    summary["pairs"] = pair_rows[:30]
    return summary


def _outcome_coverage(
    conn: sqlite3.Connection,
    *,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, dict[str, Any]]:
    where, params = _where_clause(session_date=session_date, market=market, runtime_mode=runtime_mode)
    rows = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT o.horizon_min, COALESCE(o.status, 'unknown') AS status, COUNT(*) AS rows
            FROM audit_candidate_rows r
            JOIN audit_candidate_outcomes o ON o.candidate_key = r.candidate_key
            WHERE {where}
            GROUP BY o.horizon_min, COALESCE(o.status, 'unknown')
            ORDER BY o.horizon_min ASC, rows DESC
            """,
            params,
        )
    ]
    by_horizon: dict[str, dict[str, Any]] = {}
    for row in rows:
        horizon = str(int(row.get("horizon_min") or 0))
        item = by_horizon.setdefault(
            horizon,
            {"total": 0, "audit_sparse": 0, "insufficient_samples": 0, "status_counts": {}},
        )
        status = str(row.get("status") or "unknown")
        count = int(row.get("rows") or 0)
        item["total"] += count
        item["status_counts"][status] = count
        if status == "audit_sparse":
            item["audit_sparse"] = count
        elif status == "insufficient_samples":
            item["insufficient_samples"] = count
    for item in by_horizon.values():
        total = int(item.get("total") or 0)
        audit_sparse = int(item.get("audit_sparse") or 0)
        item["coverage_rate"] = _round(audit_sparse / total if total else None)
    return by_horizon


def _route_shadow_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    watched_reasons = {
        "probe_blocked_above_pathb_zone",
        "probe_ready_cancels_pathb_above_zone",
        "watch_suspends_stale_pathb",
        "claude_avoid",
    }
    summary: dict[str, Any] = {
        "route_suspend_pathb_rows": 0,
        "route_cancel_pathb_rows": 0,
        "reason_counts": {},
        "tickers_by_reason": {},
    }
    for row in rows:
        reason = str(row.get("route_reason") or "").strip()
        suspend = int(row.get("route_suspend_pathb") or 0) > 0
        cancel = int(row.get("route_cancel_pathb") or 0) > 0
        if suspend:
            summary["route_suspend_pathb_rows"] += 1
        if cancel:
            summary["route_cancel_pathb_rows"] += 1
        if reason not in watched_reasons and not suspend and not cancel:
            continue
        key = reason or ("route_suspend_pathb" if suspend else "route_cancel_pathb")
        summary["reason_counts"][key] = int(summary["reason_counts"].get(key, 0) or 0) + 1
        tickers = summary["tickers_by_reason"].setdefault(key, set())
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            tickers.add(ticker)
    summary["tickers_by_reason"] = {
        reason: sorted(tickers)[:30] for reason, tickers in summary["tickers_by_reason"].items()
    }
    return summary


def _iter_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            text = line.strip()
            if not text:
                continue
            try:
                item = json.loads(text)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _watch_trigger_funnel_paths(event_type: str, *, session_date: str, market: str) -> list[Path]:
    log_dir = get_runtime_path("logs", "funnel")
    day = str(session_date or "").replace("-", "") or "*"
    market_part = str(market or "").upper() or "*"
    return sorted(log_dir.glob(f"{event_type}_{day}_{market_part}.jsonl"))


def watch_trigger_funnel_summary(*, session_date: str = "", market: str = "") -> dict[str, Any]:
    not_evaluated_rows: list[dict[str, Any]] = []
    shadow_rows: list[dict[str, Any]] = []
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_not_evaluated",
        session_date=session_date,
        market=market,
    ):
        not_evaluated_rows.extend(_iter_jsonl_rows(path))
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_shadow",
        session_date=session_date,
        market=market,
    ):
        shadow_rows.extend(_iter_jsonl_rows(path))

    result_counts: Counter[str] = Counter()
    blocked_reason_counts: Counter[str] = Counter()
    not_eval_reason_counts: Counter[str] = Counter()
    strategy_counts: Counter[str] = Counter()
    tickers_by_result: dict[str, set[str]] = defaultdict(set)
    for row in not_evaluated_rows:
        reason = str(row.get("reason") or "unknown")
        not_eval_reason_counts[reason] += 1
    for row in shadow_rows:
        result = str(row.get("result") or "unknown")
        result_counts[result] += 1
        strategy = str(row.get("strategy") or "unassigned")
        strategy_counts[strategy] += 1
        blocked = str(row.get("blocked_reason") or "").strip()
        if blocked:
            blocked_reason_counts[blocked] += 1
        ticker = str(row.get("ticker") or "").strip().upper()
        if ticker:
            tickers_by_result[result].add(ticker)

    return {
        "watch_trigger_not_evaluated_count": len(not_evaluated_rows),
        "watch_trigger_shadow_count": len(shadow_rows),
        "watch_trigger_would_promote_count": int(result_counts.get("would_promote", 0)),
        "watch_trigger_no_signal_count": int(result_counts.get("no_signal", 0)),
        "watch_trigger_blocked_count": int(result_counts.get("blocked", 0)),
        "not_evaluated_reason_counts": dict(not_eval_reason_counts.most_common()),
        "shadow_result_counts": dict(result_counts.most_common()),
        "blocked_reason_counts": dict(blocked_reason_counts.most_common()),
        "strategy_counts": dict(strategy_counts.most_common()),
        "tickers_by_result": {
            result: sorted(tickers)[:30]
            for result, tickers in sorted(tickers_by_result.items())
        },
    }


def _watch_trigger_shadow_outcomes(
    rows: list[dict[str, Any]],
    *,
    session_date: str = "",
    market: str = "",
) -> dict[str, Any]:
    shadow_rows: list[dict[str, Any]] = []
    for path in _watch_trigger_funnel_paths(
        "watch_trigger_shadow",
        session_date=session_date,
        market=market,
    ):
        shadow_rows.extend(_iter_jsonl_rows(path))

    audit_by_ticker: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        market_key = str(row.get("market") or "").upper()
        ticker = str(row.get("ticker") or "").strip().upper()
        if market_key and ticker:
            audit_by_ticker[(market_key, ticker)].append(row)

    events_by_result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    matched_by_result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in shadow_rows:
        result = str(event.get("result") or "unknown")
        events_by_result[result].append(event)
        market_key = str(event.get("market") or "").upper()
        ticker = str(event.get("ticker") or "").strip().upper()
        matches = audit_by_ticker.get((market_key, ticker), [])
        if matches:
            matched_by_result[result].append(matches[0])

    out: dict[str, Any] = {}
    for result, events in sorted(events_by_result.items()):
        matched = matched_by_result.get(result, [])
        out[result] = {
            "shadow_events": len(events),
            "matched_outcomes": len(matched),
            **_metric_summary(matched),
        }
    return out


def analyze_candidate_audit(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
    horizon_min: int = 60,
    limit: int = 10,
) -> dict[str, Any]:
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    conn = sqlite3.connect(str(target), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        rows = _load_outcome_rows(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            horizon_min=horizon_min,
        )
        coverage = _outcome_coverage(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
    finally:
        conn.close()

    by_class: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_class.setdefault(str(row.get("classification") or "unknown"), []).append(row)
    buckets = [
        {"classification": name, **_metric_summary(items)}
        for name, items in sorted(by_class.items(), key=lambda item: len(item[1]), reverse=True)
    ]
    top_classes = ["not_in_prompt", "in_prompt_not_selected", "ready_no_signal", "avoid_watch"]
    return {
        "db_path": str(target),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "horizon_min": int(horizon_min),
        "candidate_rows": len(rows),
        "outcome_coverage": coverage,
        "buckets": buckets,
        "top_mfe": {
            name: _top_by_mfe(rows, name, limit=limit, horizon_min=int(horizon_min))
            for name in top_classes
        },
        "route_shadow_summary": _route_shadow_summary(rows),
        "strategy_mismatch": _strategy_mismatch(rows),
        "watch_trigger_shadow_summary": watch_trigger_funnel_summary(
            session_date=session_date,
            market=market,
        ),
        "watch_trigger_shadow_outcomes": _watch_trigger_shadow_outcomes(
            rows,
            session_date=session_date,
            market=market,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze candidate audit outcomes.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--market", default="", help="KR or US; empty means all markets")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    result = analyze_candidate_audit(
        db_path=args.db or None,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        horizon_min=args.horizon_min,
        limit=args.limit,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
