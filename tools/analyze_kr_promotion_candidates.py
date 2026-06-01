from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


@dataclass(frozen=True)
class Thresholds:
    min_n: int = 30
    min_days: int = 5
    max_top_day_share: float = 0.40
    live_min_avg_pct: float = 0.30
    live_min_profit_factor: float = 1.20
    probe_min_avg_pct: float = 0.10
    probe_min_profit_factor: float = 1.05


@dataclass(frozen=True)
class PreopenRule:
    name: str
    entry_offset_min: int
    min_entry_return_pct: float
    top_n: int
    target_offset_min: int = 120


PREOPEN_RULES = [
    PreopenRule("d5_ret5_ge_1_top10", 5, 1.0, 10),
    PreopenRule("d30_ret30_ge_1_top10", 30, 1.0, 10),
    PreopenRule("d30_ret30_ge_3_top10", 30, 3.0, 10),
    PreopenRule("d60_ret60_ge_3_top10", 60, 3.0, 10),
    PreopenRule("d60_ret60_ge_5_top10", 60, 5.0, 10),
    PreopenRule("d60_ret60_ge_8_top10", 60, 8.0, 10),
    PreopenRule("d60_ret60_ge_8_top5", 60, 8.0, 5),
]


AUDIT_COHORTS: dict[str, Callable[[dict[str, Any]], bool]] = {
    "audit:evidence=BUY_READY": lambda row: _upper(row.get("evidence_action_ceiling")) == "BUY_READY",
    "audit:evidence=BUY_READY|strategy=opening_range_pullback": lambda row: (
        _upper(row.get("evidence_action_ceiling")) == "BUY_READY"
        and _lower(row.get("recommended_strategy")) == "opening_range_pullback"
    ),
    "audit:evidence=BUY_READY|strategy=momentum": lambda row: (
        _upper(row.get("evidence_action_ceiling")) == "BUY_READY"
        and _lower(row.get("recommended_strategy")) == "momentum"
    ),
    "audit:claude_action=BUY_READY": lambda row: _upper(row.get("claude_action")) == "BUY_READY",
    "audit:route_final=BUY_READY": lambda row: _upper(row.get("route_final_action")) == "BUY_READY",
    "audit:route_final_executable": lambda row: _upper(row.get("route_final_action"))
    in {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT"},
    "audit:trainer_state=PLAN_A": lambda row: _upper(row.get("trainer_candidate_state")) == "PLAN_A",
}


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        out = float(str(value).replace(",", ""))
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _round(value: Any, digits: int = 4) -> float | None:
    parsed = _num(value)
    return round(parsed, digits) if parsed is not None else None


def _profit_factor(values: list[float]) -> float | str | None:
    gains = [value for value in values if value > 0]
    losses = [value for value in values if value < 0]
    if losses:
        return round(sum(gains) / abs(sum(losses)), 6) if gains else 0.0
    if gains:
        return "INF"
    return None


def _pf_as_float(value: Any) -> float | None:
    if value == "INF":
        return 999.0
    return _num(value)


def _connect_readonly(path: str | Path) -> sqlite3.Connection | None:
    db_path = Path(path)
    if not db_path.exists():
        return None
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection | None, table_name: str) -> bool:
    if conn is None:
        return False
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection | None, table_name: str) -> set[str]:
    if conn is None or not _table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _date_clause(alias: str, start_date: str, end_date: str, params: list[Any]) -> list[str]:
    where: list[str] = []
    if start_date:
        where.append(f"{alias}.session_date >= ?")
        params.append(start_date)
    if end_date:
        where.append(f"{alias}.session_date <= ?")
        params.append(end_date)
    return where


def metrics_from_values(values: list[float], dates: list[str] | None = None) -> dict[str, Any]:
    clean = [value for value in values if math.isfinite(value)]
    gains = [value for value in clean if value > 0]
    losses = [value for value in clean if value < 0]
    dates = dates or [""] * len(clean)
    positive_by_date: Counter[str] = Counter()
    for value, session_date in zip(clean, dates):
        if value > 0:
            positive_by_date[str(session_date or "unknown")] += value
    total_positive = sum(positive_by_date.values())
    top_day_share = (
        max(positive_by_date.values()) / total_positive
        if positive_by_date and total_positive > 0
        else None
    )
    return {
        "n": len(clean),
        "days": len({date for date in dates if date}),
        "wins": len(gains),
        "losses": len(losses),
        "win_rate_pct": round((len(gains) / len(clean)) * 100.0, 4) if clean else None,
        "avg_pct": round(sum(clean) / len(clean), 6) if clean else None,
        "median_pct": round(median(clean), 6) if clean else None,
        "sum_pct": round(sum(clean), 6) if clean else None,
        "profit_factor": _profit_factor(clean),
        "worst_pct": round(min(clean), 6) if clean else None,
        "best_pct": round(max(clean), 6) if clean else None,
        "top_day_positive_share": round(top_day_share, 6) if top_day_share is not None else None,
    }


def metrics_from_rows(rows: list[dict[str, Any]], value_key: str) -> dict[str, Any]:
    values: list[float] = []
    dates: list[str] = []
    for row in rows:
        value = _num(row.get(value_key))
        if value is None:
            continue
        values.append(value)
        dates.append(str(row.get("session_date") or ""))
    return metrics_from_values(values, dates)


def classify_metrics(metrics: dict[str, Any], thresholds: Thresholds | None = None) -> dict[str, Any]:
    thresholds = thresholds or Thresholds()
    n = int(metrics.get("n") or 0)
    days = int(metrics.get("days") or 0)
    avg = _num(metrics.get("avg_pct"))
    pf = _pf_as_float(metrics.get("profit_factor"))
    top_share = _num(metrics.get("top_day_positive_share"))
    reasons: list[str] = []

    if n == 0 or avg is None or pf is None:
        return {"verdict": "NO_DATA", "reasons": ["no outcome labels"]}
    if avg < 0 or pf < 1.0:
        reasons.append(f"negative edge avg={round(avg, 4)} pf={round(pf, 4)}")
        return {"verdict": "BLOCK", "reasons": reasons}
    if n < thresholds.min_n:
        reasons.append(f"sample too small n={n} min={thresholds.min_n}")
        return {"verdict": "SHADOW_ONLY", "reasons": reasons}
    if days < thresholds.min_days:
        reasons.append(f"date coverage too small days={days} min={thresholds.min_days}")
        return {"verdict": "SHADOW_ONLY", "reasons": reasons}
    if top_share is not None and top_share > thresholds.max_top_day_share:
        reasons.append(
            f"positive contribution concentrated top_day_share={round(top_share, 4)} "
            f"max={thresholds.max_top_day_share}"
        )
        return {"verdict": "SHADOW_ONLY", "reasons": reasons}
    if avg >= thresholds.live_min_avg_pct and pf >= thresholds.live_min_profit_factor:
        reasons.append("passes live promotion thresholds")
        return {"verdict": "LIVE_READY", "reasons": reasons}
    if avg >= thresholds.probe_min_avg_pct and pf >= thresholds.probe_min_profit_factor:
        reasons.append("passes probe thresholds but not live thresholds")
        return {"verdict": "PROBE_READY", "reasons": reasons}
    reasons.append(
        f"edge below probe/live threshold avg={round(avg, 4)} pf={round(pf, 4)}"
    )
    return {"verdict": "SHADOW_ONLY", "reasons": reasons}


def _candidate_item(
    *,
    name: str,
    category: str,
    metric: dict[str, Any],
    thresholds: Thresholds,
    basis: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = classify_metrics(metric, thresholds)
    return {
        "name": name,
        "category": category,
        "basis": basis,
        "verdict": decision["verdict"],
        "reasons": decision["reasons"],
        "metrics": metric,
        **(extra or {}),
    }


def _fetch_closed_learning(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "v2_learning_performance"):
        return []
    params: list[Any] = [market, runtime_mode]
    where = ["market = ?", "runtime_mode = ?", "filled = 1", "closed = 1"]
    where.extend(_date_clause("", start_date, end_date, params))
    sql = f"""
        SELECT
            session_date, ticker, route, path_type, strategy, origin_action,
            pnl_pct, mfe_pct, mae_pct, close_reason
        FROM v2_learning_performance
        WHERE {" AND ".join(item.replace(".session_date", "session_date") for item in where)}
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()] if conn is not None else []


def closed_learning_candidates(
    rows: list[dict[str, Any]],
    *,
    thresholds: Thresholds,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    overall = metrics_from_rows(rows, "pnl_pct")
    items.append(
        _candidate_item(
            name="closed:KR_live_overall",
            category="closed_trade",
            metric=overall,
            thresholds=thresholds,
            basis="v2_learning_performance.pnl_pct",
        )
    )

    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_route: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_strategy[str(row.get("strategy") or "unknown")].append(row)
        by_route[str(row.get("route") or "unknown")].append(row)
    for strategy, group in sorted(by_strategy.items(), key=lambda item: (-len(item[1]), item[0])):
        items.append(
            _candidate_item(
                name=f"closed:strategy={strategy}",
                category="closed_trade_strategy",
                metric=metrics_from_rows(group, "pnl_pct"),
                thresholds=thresholds,
                basis="v2_learning_performance.pnl_pct",
            )
        )
    for route, group in sorted(by_route.items(), key=lambda item: (-len(item[1]), item[0])):
        items.append(
            _candidate_item(
                name=f"closed:route={route}",
                category="closed_trade_route",
                metric=metrics_from_rows(group, "pnl_pct"),
                thresholds=thresholds,
                basis="v2_learning_performance.pnl_pct",
            )
        )
    loss_cap_rows = [
        row for row in rows if "LOSS_CAP" in str(row.get("close_reason") or "").upper()
    ]
    return {
        "available": True,
        "row_count": len(rows),
        "loss_cap_count": len(loss_cap_rows),
        "items": items,
    }


def _fetch_counterfactual_rows(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "candidate_counterfactual_paths"):
        return []
    params: list[Any] = [market, runtime_mode]
    where = ["market = ?", "runtime_mode = ?"]
    where.extend(_date_clause("", start_date, end_date, params))
    sql = f"""
        SELECT
            session_date, ticker, candidate_key, path_name, status,
            outcome_30m_pct, outcome_60m_pct, outcome_close_pct
        FROM candidate_counterfactual_paths
        WHERE {" AND ".join(item.replace(".session_date", "session_date") for item in where)}
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()] if conn is not None else []


def counterfactual_candidates(
    rows: list[dict[str, Any]],
    *,
    thresholds: Thresholds,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("path_name") or "unknown")].append(row)
    horizon_keys = {
        "30m": "outcome_30m_pct",
        "60m": "outcome_60m_pct",
        "close": "outcome_close_pct",
    }
    for path_name, group in sorted(grouped.items(), key=lambda item: item[0]):
        for horizon, value_key in horizon_keys.items():
            metric = metrics_from_rows(group, value_key)
            items.append(
                _candidate_item(
                    name=f"counterfactual:path={path_name}|horizon={horizon}",
                    category="counterfactual_path",
                    metric=metric,
                    thresholds=thresholds,
                    basis=value_key,
                    extra={
                        "path_name": path_name,
                        "horizon": horizon,
                        "status_counts": dict(
                            sorted(Counter(str(row.get("status") or "") for row in group).items())
                        ),
                    },
                )
            )
    return {"available": True, "row_count": len(rows), "items": items}


def _fetch_audit_rows(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if not (
        _table_exists(conn, "audit_candidate_rows")
        and _table_exists(conn, "audit_candidate_outcomes")
    ):
        return []
    columns = _columns(conn, "audit_candidate_rows")

    def col(name: str, default_sql: str = "NULL") -> str:
        return f"r.{name}" if name in columns else f"{default_sql} AS {name}"

    params: list[Any] = [market, runtime_mode]
    where = ["r.market = ?", "r.runtime_mode = ?"]
    where.extend(_date_clause("r", start_date, end_date, params))
    sql = f"""
        SELECT
            r.candidate_key, r.session_date, r.known_at, r.ticker,
            {col('evidence_action_ceiling', "''")},
            {col('recommended_strategy', "''")},
            {col('claude_action', "''")},
            {col('claude_trade_ready', "0")},
            {col('route_original_action', "''")},
            {col('route_final_action', "''")},
            {col('route_reason', "''")},
            {col('route_runtime_gate_reason', "''")},
            {col('trainer_candidate_state', "''")},
            o30.return_pct AS ret30,
            o60.return_pct AS ret60,
            o60.max_runup_pct AS mfe60,
            o60.max_drawdown_pct AS mae60
        FROM audit_candidate_rows r
        LEFT JOIN audit_candidate_outcomes o30
          ON o30.candidate_key = r.candidate_key AND o30.horizon_min = 30
        LEFT JOIN audit_candidate_outcomes o60
          ON o60.candidate_key = r.candidate_key AND o60.horizon_min = 60
        WHERE {" AND ".join(where)}
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()] if conn is not None else []


def audit_cohort_candidates(
    rows: list[dict[str, Any]],
    *,
    thresholds: Thresholds,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for name, predicate in AUDIT_COHORTS.items():
        group = [row for row in rows if predicate(row)]
        if not group:
            continue
        metric = metrics_from_rows(group, "ret60")
        items.append(
            _candidate_item(
                name=name,
                category="audit_candidate_cohort",
                metric=metric,
                thresholds=thresholds,
                basis="audit_candidate_outcomes.return_pct horizon=60",
                extra={
                    "ret30_metrics": metrics_from_rows(group, "ret30"),
                    "mfe60_metrics": metrics_from_rows(group, "mfe60"),
                    "mae60_metrics": metrics_from_rows(group, "mae60"),
                    "rows": len(group),
                },
            )
        )
    return {"available": True, "row_count": len(rows), "items": items}


def _return_by_offset(candidate: dict[str, Any]) -> dict[int, float]:
    out: dict[int, float] = {}
    for sample in candidate.get("outcome_samples") or []:
        if not isinstance(sample, dict):
            continue
        offset = _num(sample.get("offset_min"))
        ret = _num(sample.get("return_pct"))
        if offset is None or ret is None:
            continue
        out[int(offset)] = ret
    return out


def forward_return(entry_return_pct: float, target_return_pct: float) -> float:
    return ((1.0 + target_return_pct / 100.0) / (1.0 + entry_return_pct / 100.0) - 1.0) * 100.0


def load_preopen_candidates(
    state_dir: str | Path,
    *,
    market: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    root = Path(state_dir)
    if not root.exists():
        return rows
    for path in sorted(root.glob(f"preopen_{market}_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        session_date = str(payload.get("session_date") or "")
        if start_date and session_date < start_date:
            continue
        if end_date and session_date > end_date:
            continue
        for raw in payload.get("candidates") or []:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            item["session_date"] = str(item.get("session_date") or session_date)
            item["ticker"] = str(item.get("ticker") or "")
            item["_returns"] = _return_by_offset(item)
            rows.append(item)
    return rows


def preopen_rule_candidates(
    rows: list[dict[str, Any]],
    *,
    thresholds: Thresholds,
    rules: list[PreopenRule] | None = None,
) -> dict[str, Any]:
    rules = rules or PREOPEN_RULES
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_date[str(row.get("session_date") or "")].append(row)

    items: list[dict[str, Any]] = []
    for rule in rules:
        selected_rows_120: list[dict[str, Any]] = []
        selected_rows_latest: list[dict[str, Any]] = []
        examples: list[dict[str, Any]] = []
        for session_date, day_rows in sorted(by_date.items()):
            eligible = []
            for row in day_rows:
                returns = row.get("_returns") or {}
                entry_ret = returns.get(rule.entry_offset_min)
                target_ret = returns.get(rule.target_offset_min)
                if entry_ret is None or target_ret is None or entry_ret < rule.min_entry_return_pct:
                    continue
                eligible.append((entry_ret, row))
            for rank, (entry_ret, row) in enumerate(
                sorted(eligible, key=lambda item: (-item[0], str(item[1].get("ticker") or "")))[: rule.top_n],
                start=1,
            ):
                returns = row.get("_returns") or {}
                target_ret = returns.get(rule.target_offset_min)
                latest_offset = max(returns) if returns else None
                latest_ret = returns.get(latest_offset) if latest_offset is not None else None
                if target_ret is not None:
                    fwd_120 = forward_return(entry_ret, target_ret)
                    selected_rows_120.append(
                        {
                            "session_date": session_date,
                            "ticker": row.get("ticker"),
                            "entry_return_pct": entry_ret,
                            "return_pct": fwd_120,
                        }
                    )
                    if len(examples) < 12:
                        examples.append(
                            {
                                "session_date": session_date,
                                "ticker": row.get("ticker"),
                                "rank": rank,
                                "entry_return_pct": _round(entry_ret),
                                "fwd_to_120_pct": _round(fwd_120),
                            }
                        )
                if latest_ret is not None:
                    selected_rows_latest.append(
                        {
                            "session_date": session_date,
                            "ticker": row.get("ticker"),
                            "entry_return_pct": entry_ret,
                            "return_pct": forward_return(entry_ret, latest_ret),
                        }
                    )

        metric_120 = metrics_from_rows(selected_rows_120, "return_pct")
        metric_latest = metrics_from_rows(selected_rows_latest, "return_pct")
        item = _candidate_item(
            name=f"preopen:{rule.name}|fwd_to_{rule.target_offset_min}",
            category="preopen_future_blind_rule",
            metric=metric_120,
            thresholds=thresholds,
            basis=(
                f"entry_offset={rule.entry_offset_min}m min_entry_return={rule.min_entry_return_pct}% "
                f"top_n={rule.top_n}; return is entry-to-{rule.target_offset_min}m"
            ),
            extra={
                "entry_offset_min": rule.entry_offset_min,
                "min_entry_return_pct": rule.min_entry_return_pct,
                "top_n": rule.top_n,
                "target_offset_min": rule.target_offset_min,
                "latest_metrics": metric_latest,
                "examples": examples,
            },
        )
        if (
            item["verdict"] in {"LIVE_READY", "PROBE_READY"}
            and _num(metric_latest.get("avg_pct")) is not None
            and (_num(metric_latest.get("avg_pct")) or 0.0) < 0.0
        ):
            item["reasons"].append("latest-hold return is negative; any probe must be short-hold only")
        items.append(item)
    return {"available": True, "row_count": len(rows), "items": items}


def _verdict_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(Counter(str(item.get("verdict") or "") for item in items).items()))


def _rank_items(items: list[dict[str, Any]], verdicts: set[str], limit: int = 20) -> list[dict[str, Any]]:
    selected = [item for item in items if str(item.get("verdict")) in verdicts]
    return sorted(
        selected,
        key=lambda item: (
            -(_num((item.get("metrics") or {}).get("avg_pct")) or -999.0),
            -(_pf_as_float((item.get("metrics") or {}).get("profit_factor")) or -999.0),
            item.get("name") or "",
        ),
    )[:limit]


def _fetch_micro_probe_rows(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    if conn is None or not _table_exists(conn, "micro_probe_log"):
        return []
    columns = _columns(conn, "micro_probe_log")
    bucket_expr = "experiment_bucket" if "experiment_bucket" in columns else "''"
    entry_source_expr = "entry_source" if "entry_source" in columns else "''"
    exit_horizon_expr = "exit_horizon_min" if "exit_horizon_min" in columns else "0"
    where = ["market = ?", "pnl_pct IS NOT NULL"]
    params: list[Any] = [market]
    if "bot_mode" in columns:
        where.append("bot_mode = ?")
        params.append(runtime_mode)
    if start_date:
        where.append("session_date >= ?")
        params.append(start_date)
    if end_date:
        where.append("session_date <= ?")
        params.append(end_date)
    rows = conn.execute(
        f"""
        SELECT
            session_date, market, ticker, source_strategy, reason,
            {bucket_expr} AS experiment_bucket,
            {entry_source_expr} AS entry_source,
            {exit_horizon_expr} AS exit_horizon_min,
            pnl_pct, pnl_krw, exit_reason
        FROM micro_probe_log
        WHERE {" AND ".join(where)}
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def micro_probe_candidates(rows: list[dict[str, Any]], thresholds: Thresholds) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if not rows:
        return {"available": True, "row_count": 0, "items": items}
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = str(row.get("experiment_bucket") or "standard")
        groups[f"micro_probe:bucket={bucket}"].append(row)
        source = str(row.get("entry_source") or "").strip()
        if source:
            groups[f"micro_probe:entry_source={source}"].append(row)
    for name, grouped in sorted(groups.items()):
        metric = metrics_from_rows(grouped, "pnl_pct")
        item = _candidate_item(
            name=name,
            category="micro_probe_live_filled",
            metric=metric,
            thresholds=thresholds,
            basis="ticker_selection_db.micro_probe_log.pnl_pct",
            extra={
                "filled_count": metric.get("n"),
                "sample_rows": grouped[:5],
            },
        )
        if str(name).endswith("bucket=preopen_ret60_probe") and int(metric.get("n") or 0) < 30:
            item["reasons"].append("preopen ret60 probe requires at least 30 filled probes before regular promotion")
        items.append(item)
    return {"available": True, "row_count": len(rows), "items": items}


def analyze_kr_promotion_candidates(
    *,
    market: str = "KR",
    runtime_mode: str = "live",
    ml_db: str | Path | None = None,
    audit_db: str | Path | None = None,
    ticker_db: str | Path | None = None,
    state_dir: str | Path | None = None,
    start_date: str = "",
    end_date: str = "",
    thresholds: Thresholds | None = None,
) -> dict[str, Any]:
    thresholds = thresholds or Thresholds()
    market_key = str(market or "KR").upper()
    runtime = str(runtime_mode or "live")
    ml_path = Path(ml_db or get_runtime_path("data", "ml", "decisions.db"))
    audit_path = Path(audit_db or get_runtime_path("data", "audit", "candidate_audit.db"))
    ticker_path = Path(ticker_db or get_runtime_path("data", "ticker_selection_log.db"))
    state_path = Path(state_dir or get_runtime_path("state"))

    ml_conn = _connect_readonly(ml_path)
    audit_conn = _connect_readonly(audit_path)
    ticker_conn = _connect_readonly(ticker_path)
    try:
        closed_rows = _fetch_closed_learning(
            ml_conn,
            market=market_key,
            runtime_mode=runtime,
            start_date=start_date,
            end_date=end_date,
        )
        counterfactual_rows = _fetch_counterfactual_rows(
            audit_conn,
            market=market_key,
            runtime_mode=runtime,
            start_date=start_date,
            end_date=end_date,
        )
        audit_rows = _fetch_audit_rows(
            audit_conn,
            market=market_key,
            runtime_mode=runtime,
            start_date=start_date,
            end_date=end_date,
        )
        preopen_rows = load_preopen_candidates(
            state_path,
            market=market_key,
            start_date=start_date,
            end_date=end_date,
        )
        micro_probe_rows = _fetch_micro_probe_rows(
            ticker_conn,
            market=market_key,
            runtime_mode=runtime,
            start_date=start_date,
            end_date=end_date,
        )
    finally:
        if ml_conn is not None:
            ml_conn.close()
        if audit_conn is not None:
            audit_conn.close()
        if ticker_conn is not None:
            ticker_conn.close()

    sections = {
        "closed_learning": closed_learning_candidates(closed_rows, thresholds=thresholds),
        "counterfactual_paths": counterfactual_candidates(counterfactual_rows, thresholds=thresholds),
        "audit_cohorts": audit_cohort_candidates(audit_rows, thresholds=thresholds),
        "preopen_rules": preopen_rule_candidates(preopen_rows, thresholds=thresholds),
        "micro_probe_live_filled": micro_probe_candidates(micro_probe_rows, thresholds=thresholds),
    }
    all_items = [
        item
        for section in sections.values()
        for item in section.get("items", [])
        if isinstance(item, dict)
    ]
    live_ready = _rank_items(all_items, {"LIVE_READY"})
    probe_ready = _rank_items(all_items, {"PROBE_READY"})
    blocked = _rank_items(all_items, {"BLOCK"})
    should_enable_live = bool(live_ready)
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "read_only": True,
        "filters": {
            "market": market_key,
            "runtime_mode": runtime,
            "start_date": start_date or "ALL",
            "end_date": end_date or "ALL",
        },
        "truth_contract": {
            "no_broker_api": True,
            "no_claude_call": True,
            "no_config_or_state_mutation": True,
            "state_brain_json_is_not_used": True,
        },
        "paths": {
            "ml_db": str(ml_path),
            "audit_db": str(audit_path),
            "ticker_db": str(ticker_path),
            "state_dir": str(state_path),
        },
        "thresholds": thresholds.__dict__,
        "summary": {
            "total_items": len(all_items),
            "verdict_counts": _verdict_counts(all_items),
            "should_enable_live": should_enable_live,
            "operator_action": "do_not_enable_live" if not should_enable_live else "review_live_ready_before_config_change",
            "probe_candidate_count": len(probe_ready),
            "blocked_candidate_count": len(blocked),
        },
        "top_live_ready": live_ready,
        "top_probe_ready": probe_ready,
        "top_blocked": blocked[:20],
        "sections": sections,
    }


def _md_cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|")


def _item_row(item: dict[str, Any]) -> str:
    metrics = item.get("metrics") or {}
    return (
        f"| {_md_cell(item.get('verdict'))} | {_md_cell(item.get('name'))} | "
        f"{metrics.get('n')} | {metrics.get('days')} | {metrics.get('avg_pct')} | "
        f"{metrics.get('profit_factor')} | {metrics.get('top_day_positive_share')} | "
        f"{_md_cell('; '.join(item.get('reasons') or []))} |"
    )


def to_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# KR Promotion Candidate Audit",
        "",
        f"Generated: {payload.get('generated_at')}",
        f"Filters: {payload.get('filters')}",
        "",
        "## Decision",
        "",
        f"- operator_action: `{summary.get('operator_action')}`",
        f"- should_enable_live: `{summary.get('should_enable_live')}`",
        f"- verdict_counts: `{summary.get('verdict_counts')}`",
        "",
        "## Live Ready",
        "",
        "| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in payload.get("top_live_ready") or []:
        lines.append(_item_row(item))
    if not payload.get("top_live_ready"):
        lines.append("|  | none |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Probe Ready",
            "",
            "| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload.get("top_probe_ready") or []:
        lines.append(_item_row(item))
    if not payload.get("top_probe_ready"):
        lines.append("|  | none |  |  |  |  |  |  |")
    micro_items = [
        item
        for item in ((payload.get("sections") or {}).get("micro_probe_live_filled") or {}).get("items", [])
        if isinstance(item, dict)
    ]
    lines.extend(
        [
            "",
            "## Live Filled Micro-Probes",
            "",
            "| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in micro_items:
        lines.append(_item_row(item))
    if not micro_items:
        lines.append("|  | none |  |  |  |  |  |  |")
    lines.extend(
        [
            "",
            "## Blocked",
            "",
            "| verdict | name | n | days | avg_pct | pf | top_day_share | reasons |",
            "|---|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for item in payload.get("top_blocked") or []:
        lines.append(_item_row(item))
    if not payload.get("top_blocked"):
        lines.append("|  | none |  |  |  |  |  |  |")
    lines.extend(["", "## Notes", ""])
    lines.append("- This is local-data-only analysis. It does not call broker APIs or Claude.")
    lines.append("- Promotion labels are decision aids; live config changes still require operator review.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KR live promotion candidate audit.")
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--ml-db", default=str(get_runtime_path("data", "ml", "decisions.db")))
    parser.add_argument("--audit-db", default=str(get_runtime_path("data", "audit", "candidate_audit.db")))
    parser.add_argument("--ticker-db", default=str(get_runtime_path("data", "ticker_selection_log.db")))
    parser.add_argument("--state-dir", default=str(get_runtime_path("state")))
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--min-n", type=int, default=30)
    parser.add_argument("--min-days", type=int, default=5)
    parser.add_argument("--max-top-day-share", type=float, default=0.40)
    parser.add_argument("--live-min-avg-pct", type=float, default=0.30)
    parser.add_argument("--live-min-profit-factor", type=float, default=1.20)
    parser.add_argument("--probe-min-avg-pct", type=float, default=0.10)
    parser.add_argument("--probe-min-profit-factor", type=float, default=1.05)
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    thresholds = Thresholds(
        min_n=args.min_n,
        min_days=args.min_days,
        max_top_day_share=args.max_top_day_share,
        live_min_avg_pct=args.live_min_avg_pct,
        live_min_profit_factor=args.live_min_profit_factor,
        probe_min_avg_pct=args.probe_min_avg_pct,
        probe_min_profit_factor=args.probe_min_profit_factor,
    )
    payload = analyze_kr_promotion_candidates(
        market=args.market,
        runtime_mode=args.runtime_mode,
        ml_db=args.ml_db,
        audit_db=args.audit_db,
        ticker_db=args.ticker_db,
        state_dir=args.state_dir,
        start_date=args.start_date,
        end_date=args.end_date,
        thresholds=thresholds,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"kr_promotion_candidates_{args.stamp}.json"
    md_path = output_dir / f"kr_promotion_candidates_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(payload), encoding="utf-8")
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"json: {json_path}")
        print(f"markdown: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
