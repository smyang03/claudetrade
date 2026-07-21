from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_LOG_DIR = Path("logs/funnel")
DEFAULT_AUDIT_DB = Path("data/audit/candidate_audit.db")

_SNAPSHOT_RE = re.compile(r"candidate_funnel_snapshot_(\d{8})_([A-Za-z]+)\.jsonl$")


def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _ticker_list(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        ticker = ""
        if isinstance(item, dict):
            ticker = str(item.get("ticker") or "").strip().upper()
        else:
            ticker = str(item or "").strip().upper()
        if ticker and ticker not in out:
            out.append(ticker)
    return out


def _event_payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("payload")
    if isinstance(payload, dict):
        return payload
    return raw


def _snapshot_file_info(path: Path) -> tuple[str, str] | None:
    match = _SNAPSHOT_RE.search(path.name)
    if not match:
        return None
    yyyymmdd, market = match.groups()
    return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}", market.upper()


def _in_date_range(session_date: str, date_from: str | None, date_to: str | None) -> bool:
    if date_from and session_date < date_from:
        return False
    if date_to and session_date > date_to:
        return False
    return True


def load_shadow_events(
    *,
    log_dir: Path,
    market: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    market_filter = str(market or "").upper()
    for path in sorted(log_dir.glob("candidate_funnel_snapshot_*_*.jsonl")):
        info = _snapshot_file_info(path)
        if not info:
            continue
        file_date, file_market = info
        if market_filter and file_market != market_filter:
            continue
        if not _in_date_range(file_date, date_from, date_to):
            continue
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw = _json_obj(line)
                payload = _event_payload(raw)
                shadow = payload.get("prompt_pool_shadow_reorder")
                if not isinstance(shadow, dict):
                    metrics = payload.get("prompt_pool_metrics")
                    if isinstance(metrics, dict):
                        shadow = metrics.get("shadow_reorder")
                if not isinstance(shadow, dict):
                    continue
                active_top = _ticker_list(shadow.get("active_top"))
                trainer_top = _ticker_list(shadow.get("trainer_top"))
                if not active_top and not trainer_top:
                    continue
                events.append(
                    {
                        "session_date": str(payload.get("session_date") or file_date),
                        "market": str(payload.get("market") or file_market).upper(),
                        "selection_trace_id": str(
                            payload.get("selection_trace_id")
                            or payload.get("trace_id")
                            or raw.get("selection_trace_id")
                            or ""
                        ),
                        "source_file": str(path),
                        "source_line": line_no,
                        "active_top": active_top,
                        "trainer_top": trainer_top,
                        "trainer_top_new_tickers": _ticker_list(
                            shadow.get("trainer_top_new_tickers")
                        )
                        or [ticker for ticker in trainer_top if ticker not in set(active_top)],
                        "active_top_displaced_tickers": _ticker_list(
                            shadow.get("active_top_displaced_tickers")
                        )
                        or [ticker for ticker in active_top if ticker not in set(trainer_top)],
                        "top_overlap_ratio": shadow.get("top_overlap_ratio"),
                        "largest_rank_deltas": shadow.get("largest_rank_deltas")
                        if isinstance(shadow.get("largest_rank_deltas"), list)
                        else [],
                    }
                )
    return events


def build_observations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for event in events:
        for bucket, key in (
            ("trainer_top_new", "trainer_top_new_tickers"),
            ("active_top_displaced", "active_top_displaced_tickers"),
        ):
            for ticker in event.get(key) or []:
                observations.append(
                    {
                        "bucket": bucket,
                        "ticker": str(ticker).upper(),
                        "market": event["market"],
                        "session_date": event["session_date"],
                        "selection_trace_id": event.get("selection_trace_id", ""),
                        "source_file": event["source_file"],
                        "source_line": event["source_line"],
                    }
                )
    return observations


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def attach_outcomes(
    observations: list[dict[str, Any]],
    *,
    audit_db: Path,
    horizon_min: int,
) -> list[dict[str, Any]]:
    if not observations:
        return observations
    if not audit_db.exists():
        for row in observations:
            row["outcome_status"] = "audit_db_missing"
        return observations

    conn = sqlite3.connect(str(audit_db))
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "audit_candidate_rows") or not _table_exists(
            conn, "audit_candidate_outcomes"
        ):
            for row in observations:
                row["outcome_status"] = "audit_tables_missing"
            return observations

        row_columns = _columns(conn, "audit_candidate_rows")
        order_expr = "r.candidate_key"
        if "actual_prompt_rank" in row_columns:
            order_expr = "COALESCE(r.actual_prompt_rank, 999999), r.candidate_key"
        elif "prompt_rank" in row_columns:
            order_expr = "COALESCE(r.prompt_rank, 999999), r.candidate_key"

        for row in observations:
            outcome = conn.execute(
                f"""
                SELECT
                  r.candidate_key,
                  o.status,
                  o.return_pct,
                  o.max_runup_pct,
                  o.max_drawdown_pct,
                  o.observed_at,
                  o.source
                FROM audit_candidate_rows r
                LEFT JOIN audit_candidate_outcomes o
                  ON o.candidate_key = r.candidate_key
                 AND o.horizon_min = ?
                WHERE r.market = ?
                  AND r.session_date = ?
                  AND UPPER(r.ticker) = ?
                ORDER BY {order_expr}
                LIMIT 1
                """,
                (
                    horizon_min,
                    row["market"],
                    row["session_date"],
                    row["ticker"],
                ),
            ).fetchone()
            if outcome is None:
                row["outcome_status"] = "candidate_row_missing"
                continue
            row["candidate_key"] = outcome["candidate_key"]
            row["outcome_status"] = str(outcome["status"] or "outcome_missing")
            row["return_pct"] = outcome["return_pct"]
            row["max_runup_pct"] = outcome["max_runup_pct"]
            row["max_drawdown_pct"] = outcome["max_drawdown_pct"]
            row["observed_at"] = outcome["observed_at"]
            row["outcome_source"] = outcome["source"]
    finally:
        conn.close()
    return observations


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def summarize(observations: list[dict[str, Any]]) -> dict[str, Any]:
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        by_bucket[str(row.get("bucket") or "unknown")].append(row)

    summary: dict[str, Any] = {}
    for bucket, rows in sorted(by_bucket.items()):
        labeled = [row for row in rows if row.get("return_pct") is not None]
        returns = [float(row["return_pct"]) for row in labeled]
        runups = [
            float(row["max_runup_pct"])
            for row in labeled
            if row.get("max_runup_pct") is not None
        ]
        drawdowns = [
            float(row["max_drawdown_pct"])
            for row in labeled
            if row.get("max_drawdown_pct") is not None
        ]
        summary[bucket] = {
            "count": len(rows),
            "labeled_count": len(labeled),
            "missing_outcome_count": len(rows) - len(labeled),
            "mean_return_pct": _avg(returns),
            "positive_rate_pct": round(100.0 * sum(1 for value in returns if value > 0) / len(returns), 2)
            if returns
            else None,
            "mean_max_runup_pct": _avg(runups),
            "mean_max_drawdown_pct": _avg(drawdowns),
        }
    return summary


def recommendation(summary: dict[str, Any], *, min_labeled: int) -> str:
    trainer = summary.get("trainer_top_new", {})
    displaced = summary.get("active_top_displaced", {})
    labeled = int(trainer.get("labeled_count") or 0) + int(displaced.get("labeled_count") or 0)
    observed = int(trainer.get("count") or 0) + int(displaced.get("count") or 0)
    if observed <= 0:
        return "collect_more_shadow_reorder_events"
    if labeled <= 0:
        return "backfill_outcomes_or_attach_valid_external_history"
    if labeled < min_labeled:
        return "continue_shadow_until_min_labeled_before_reorder_enforce"

    trainer_mean = trainer.get("mean_return_pct")
    displaced_mean = displaced.get("mean_return_pct")
    if trainer_mean is None or displaced_mean is None:
        return "continue_shadow_and_fill_missing_side_outcomes"
    if float(trainer_mean) > float(displaced_mean):
        return "trainer_shadow_outperforms_displaced_consider_tail_reorder_shadow"
    return "keep_legacy_order_and_investigate_trainer_score_features"


def generate_report(
    *,
    log_dir: Path = DEFAULT_LOG_DIR,
    audit_db: Path = DEFAULT_AUDIT_DB,
    market: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    horizon_min: int = 60,
    min_labeled: int = 30,
) -> dict[str, Any]:
    events = load_shadow_events(
        log_dir=log_dir,
        market=market,
        date_from=date_from,
        date_to=date_to,
    )
    observations = attach_outcomes(
        build_observations(events),
        audit_db=audit_db,
        horizon_min=horizon_min,
    )
    summary = summarize(observations)
    return {
        "source": "candidate_funnel_snapshot.prompt_pool_shadow_reorder",
        "log_dir": str(log_dir),
        "audit_db": str(audit_db),
        "market": str(market or "").upper() or "ALL",
        "date_from": date_from,
        "date_to": date_to,
        "horizon_min": horizon_min,
        "event_count": len(events),
        "observation_count": len(observations),
        "summary_by_bucket": summary,
        "recommendation": recommendation(summary, min_labeled=min_labeled),
        "external_data_policy": {
            "status": "required_when_internal_labels_are_missing_or_sparse",
            "rule": "Do not stop at missing DB labels; validate external/preloaded data for availability, timestamp safety, survivorship bias, and no-lookahead before using it as shadow/backfill evidence.",
        },
        "sample_observations": observations[:50],
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze prompt-pool trainer reorder shadow impact using funnel logs and candidate audit outcomes."
    )
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR))
    parser.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
    parser.add_argument("--market", default="")
    parser.add_argument("--date-from", default="")
    parser.add_argument("--date-to", default="")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--min-labeled", type=int, default=30)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    report = generate_report(
        log_dir=Path(args.log_dir),
        audit_db=Path(args.audit_db),
        market=args.market or None,
        date_from=args.date_from or None,
        date_to=args.date_to or None,
        horizon_min=args.horizon_min,
        min_labeled=args.min_labeled,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
