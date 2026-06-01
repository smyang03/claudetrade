from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


def _connect_readonly(path: str | Path) -> sqlite3.Connection | None:
    db_path = Path(path)
    if not db_path.exists():
        return None
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _round(value: Any, digits: int = 4) -> float | None:
    try:
        if value is None:
            return None
        return round(float(value), digits)
    except Exception:
        return None


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _pct_input(value: str) -> float:
    parsed = float(value)
    return parsed * 100.0 if abs(parsed) <= 1.0 else parsed


def _safe_text(value: Any, default: str = "unknown") -> str:
    text = str(value or "").strip()
    return text if text else default


def _fetch_learning_rows(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    session_date: str = "",
) -> list[dict[str, Any]]:
    if conn is None:
        return []
    where = ["market = ?", "runtime_mode = ?", "filled = 1", "closed = 1"]
    params: list[Any] = [market, runtime_mode]
    if session_date:
        where.append("session_date = ?")
        params.append(session_date)
    sql = f"""
        SELECT
            session_date, ticker, route, path_type, strategy, origin_action,
            pnl_pct, mfe_pct, mae_pct, close_reason, path_run_id
        FROM v2_learning_performance
        WHERE {" AND ".join(where)}
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _metric(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [_num(row.get("pnl_pct")) for row in rows]
    pnl_values = [value for value in pnl_values if value is not None]
    mfe_values = [_num(row.get("mfe_pct")) for row in rows]
    mfe_values = [value for value in mfe_values if value is not None]
    mae_values = [_num(row.get("mae_pct")) for row in rows]
    mae_values = [value for value in mae_values if value is not None]
    wins = sum(1 for value in pnl_values if value > 0)
    losses = sum(1 for value in pnl_values if value <= 0)
    loss_cap = sum(1 for row in rows if "LOSS_CAP" in str(row.get("close_reason") or "").upper())
    return {
        "n": len(rows),
        "with_pnl": len(pnl_values),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": _round((wins / len(pnl_values)) * 100.0 if pnl_values else 0.0, 2),
        "total_pnl_pct": _round(sum(pnl_values), 4),
        "avg_pnl_pct": _round(mean(pnl_values) if pnl_values else 0.0, 4),
        "avg_mfe_pct": _round(mean(mfe_values), 4) if mfe_values else None,
        "avg_mae_pct": _round(mean(mae_values), 4) if mae_values else None,
        "loss_cap_count": loss_cap,
    }


def _group_metrics(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[_safe_text(row.get(key))].append(row)
    return {
        name: _metric(group_rows)
        for name, group_rows in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))
    }


def _performance_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    loss_cap_rows = [
        row for row in rows if "LOSS_CAP" in str(row.get("close_reason") or "").upper()
    ]
    return {
        "overall": _metric(rows),
        "by_route": _group_metrics(rows, "route"),
        "by_path_type": _group_metrics(rows, "path_type"),
        "by_strategy": _group_metrics(rows, "strategy"),
        "loss_cap_breakdown": {
            "overall": _metric(loss_cap_rows),
            "by_route": _group_metrics(loss_cap_rows, "route"),
            "by_path_type": _group_metrics(loss_cap_rows, "path_type"),
            "by_strategy": _group_metrics(loss_cap_rows, "strategy"),
            "rows": [
                {
                    "session_date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "route": row.get("route"),
                    "path_type": row.get("path_type"),
                    "strategy": row.get("strategy"),
                    "pnl_pct": _round(row.get("pnl_pct")),
                    "mfe_pct": _round(row.get("mfe_pct")),
                    "close_reason": row.get("close_reason"),
                }
                for row in loss_cap_rows
            ],
        },
    }


def _path_run_metadata_summary(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    session_date: str = "",
) -> dict[str, Any]:
    if conn is None:
        return {"available": False, "row_count": 0}
    where = ["market = ?", "runtime_mode = ?"]
    params: list[Any] = [market, runtime_mode]
    if session_date:
        where.append("session_date = ?")
        params.append(session_date)
    rows = conn.execute(
        f"""
        SELECT path_run_id, session_date, ticker, path_type, status, plan_json
        FROM v2_path_runs
        WHERE {" AND ".join(where)}
        """,
        params,
    ).fetchall()
    strategy_counts: Counter[str] = Counter()
    path_type_counts: Counter[str] = Counter()
    missing_strategy: list[dict[str, Any]] = []
    invalid_json = 0
    for row in rows:
        path_type_counts[_safe_text(row["path_type"])] += 1
        try:
            plan = json.loads(row["plan_json"] or "{}")
        except Exception:
            plan = {}
            invalid_json += 1
        strategy = _safe_text(plan.get("strategy") or plan.get("source_strategy") or plan.get("recommended_strategy"), "")
        if not strategy:
            strategy_counts["missing"] += 1
            missing_strategy.append(
                {
                    "path_run_id": row["path_run_id"],
                    "session_date": row["session_date"],
                    "ticker": row["ticker"],
                    "path_type": row["path_type"],
                    "status": row["status"],
                }
            )
        else:
            strategy_counts[strategy] += 1
    return {
        "available": True,
        "row_count": len(rows),
        "path_type_counts": dict(sorted(path_type_counts.items())),
        "plan_json_strategy_counts": dict(sorted(strategy_counts.items())),
        "missing_strategy_count": len(missing_strategy),
        "invalid_plan_json_count": invalid_json,
        "missing_strategy_sample": missing_strategy[:20],
        "live_filter_blocker": bool(missing_strategy),
    }


def _trailing_replay_approx(rows: list[dict[str, Any]], trail_pcts: list[float]) -> dict[str, Any]:
    mfe_rows = [
        row for row in rows
        if _num(row.get("mfe_pct")) is not None and _num(row.get("pnl_pct")) is not None
    ]
    reversal_rows = [
        row for row in mfe_rows
        if (_num(row.get("mfe_pct")) or 0.0) >= 3.0 and (_num(row.get("pnl_pct")) or 0.0) < 0.0
    ]
    by_trail: dict[str, Any] = {}
    for trail in trail_pcts:
        candidates = []
        for row in mfe_rows:
            mfe = _num(row.get("mfe_pct")) or 0.0
            pnl = _num(row.get("pnl_pct")) or 0.0
            theoretical_floor = mfe - trail
            candidates.append(
                {
                    "session_date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "strategy": row.get("strategy"),
                    "close_reason": row.get("close_reason"),
                    "mfe_pct": _round(mfe),
                    "actual_pnl_pct": _round(pnl),
                    "mfe_minus_trail_pct": _round(theoretical_floor),
                    "actual_vs_mfe_gap_pct": _round(mfe - pnl),
                    "gap_fill_risk": bool(mfe >= 3.0 and pnl < 0.0),
                }
            )
        positive_floor = [
            item["mfe_minus_trail_pct"]
            for item in candidates
            if item.get("mfe_minus_trail_pct") is not None and item["mfe_minus_trail_pct"] > 0
        ]
        by_trail[f"{trail:.2f}"] = {
            "trail_pct_points": trail,
            "rows_with_mfe": len(candidates),
            "positive_mfe_minus_trail_count": len(positive_floor),
            "avg_mfe_minus_trail_pct": _round(mean(positive_floor), 4) if positive_floor else None,
            "sample": candidates[:20],
        }
    return {
        "method": "approx_from_closed_trade_mfe_only",
        "not_a_fill_replay": True,
        "limitations": [
            "minute path, halt/VI, gap fill, broker fill delay, and slippage are not modeled",
            "use this only to choose replay candidates before changing live execution risk",
        ],
        "rows_with_mfe": len(mfe_rows),
        "mfe_to_loss_reversal_count": len(reversal_rows),
        "mfe_to_loss_reversal_sample": [
            {
                "session_date": row.get("session_date"),
                "ticker": row.get("ticker"),
                "strategy": row.get("strategy"),
                "pnl_pct": _round(row.get("pnl_pct")),
                "mfe_pct": _round(row.get("mfe_pct")),
                "close_reason": row.get("close_reason"),
            }
            for row in reversal_rows[:20]
        ],
        "by_trail_pct": by_trail,
    }


def analyze_kr_strategy_redesign(
    *,
    market: str = "KR",
    runtime_mode: str = "live",
    ml_db: str | Path | None = None,
    event_db: str | Path | None = None,
    session_date: str = "",
    trail_replay: list[float] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "KR").upper()
    runtime = str(runtime_mode or "live")
    ml_path = Path(ml_db or get_runtime_path("data", "ml", "decisions.db"))
    event_path = Path(event_db or get_runtime_path("data", "v2_event_store.db"))
    ml_conn = _connect_readonly(ml_path)
    event_conn = _connect_readonly(event_path)
    try:
        rows = _fetch_learning_rows(
            ml_conn,
            market=market_key,
            runtime_mode=runtime,
            session_date=session_date,
        )
        return {
            "filters": {
                "market": market_key,
                "runtime_mode": runtime,
                "session_date": session_date or "ALL",
            },
            "read_only": True,
            "truth_contract": {
                "outcome_truth": "data/ml/decisions.db::v2_learning_performance",
                "path_run_metadata_truth": "data/v2_event_store.db::v2_path_runs.plan_json",
                "state_brain_json_is_policy_memory": True,
            },
            "db_paths": {"ml_db": str(ml_path), "event_db": str(event_path)},
            "performance": _performance_summary(rows),
            "path_run_metadata": _path_run_metadata_summary(
                event_conn,
                market=market_key,
                runtime_mode=runtime,
                session_date=session_date,
            ),
            "trailing_replay_approx": _trailing_replay_approx(rows, trail_replay or [2.0, 4.0, 6.0, 8.0]),
        }
    finally:
        if ml_conn is not None:
            ml_conn.close()
        if event_conn is not None:
            event_conn.close()


def to_markdown(payload: dict[str, Any]) -> str:
    perf = payload.get("performance") or {}
    overall = perf.get("overall") or {}
    metadata = payload.get("path_run_metadata") or {}
    lines = [
        "# KR Strategy Redesign Audit",
        "",
        f"Filters: {payload.get('filters')}",
        "",
        "## Performance",
        "",
        (
            f"- overall n={overall.get('n')} wins={overall.get('wins')} "
            f"avg={overall.get('avg_pnl_pct')}% total={overall.get('total_pnl_pct')}%"
        ),
        "",
        "### By Strategy",
        "",
    ]
    for name, item in (perf.get("by_strategy") or {}).items():
        lines.append(
            f"- `{name}`: n={item.get('n')} wins={item.get('wins')} "
            f"avg={item.get('avg_pnl_pct')}% loss_cap={item.get('loss_cap_count')}"
        )
    lines.extend(["", "## PathB Metadata", ""])
    lines.append(
        f"- rows={metadata.get('row_count')} missing_strategy={metadata.get('missing_strategy_count')} "
        f"live_filter_blocker={metadata.get('live_filter_blocker')}"
    )
    replay = payload.get("trailing_replay_approx") or {}
    lines.extend(["", "## Trailing Approximation", ""])
    lines.append(f"- method={replay.get('method')} not_a_fill_replay={replay.get('not_a_fill_replay')}")
    lines.append(f"- mfe_to_loss_reversal_count={replay.get('mfe_to_loss_reversal_count')}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KR strategy redesign audit.")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--ml-db", default=str(get_runtime_path("data", "ml", "decisions.db")))
    parser.add_argument("--event-db", default=str(get_runtime_path("data", "v2_event_store.db")))
    parser.add_argument("--session-date", default="")
    parser.add_argument("--trail-replay", default="0.02,0.04,0.06,0.08")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    trails = [_pct_input(item.strip()) for item in str(args.trail_replay or "").split(",") if item.strip()]
    payload = analyze_kr_strategy_redesign(
        market=args.market,
        runtime_mode=args.runtime_mode,
        ml_db=args.ml_db,
        event_db=args.event_db,
        session_date=args.session_date,
        trail_replay=trails,
    )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(to_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
