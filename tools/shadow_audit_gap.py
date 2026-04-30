from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


DB_TARGETS = {
    "decisions": ROOT / "data" / "ml" / "decisions.db",
    "v2_event_store": ROOT / "data" / "v2_event_store.db",
    "ticker_selection_log": ROOT / "data" / "ticker_selection_log.db",
    "intraday_strategy_log": ROOT / "data" / "intraday_strategy_log.db",
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]
    except Exception:
        return []


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
    except Exception:
        return 0


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    try:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None
        return row[0]
    except Exception:
        return None


def _strip_alias(col: str) -> str:
    import re
    return re.sub(r"\s+AS\s+\w+\s*$", "", col, flags=re.IGNORECASE).strip()


def _group_counts(
    conn: sqlite3.Connection,
    table: str,
    columns: list[str],
    *,
    where: str = "",
    params: tuple[Any, ...] = (),
    limit: int = 20,
) -> list[dict[str, Any]]:
    cols = ", ".join(columns)
    group_cols = ", ".join(_strip_alias(c) for c in columns)
    where_clause = f"WHERE {where}" if where else ""
    sql = (
        f"SELECT {cols}, COUNT(*) AS rows FROM {table} "
        f"{where_clause} GROUP BY {group_cols} ORDER BY rows DESC LIMIT {int(limit)}"
    )
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    except Exception:
        return []


def _jsonl_stats(path: Path) -> dict[str, Any]:
    stats = {
        "path": str(path.relative_to(ROOT)),
        "exists": path.exists(),
        "rows": 0,
        "events": {},
        "markets": {},
        "has_state": 0,
        "has_decision_id": 0,
        "has_signal_price": 0,
    }
    if not path.exists():
        return stats
    events: Counter[str] = Counter()
    markets: Counter[str] = Counter()
    rows = 0
    has_state = 0
    has_decision_id = 0
    has_signal_price = 0
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            rows += 1
            events[str(item.get("event") or "")] += 1
            markets[str(item.get("market") or "")] += 1
            state = item.get("state") or {}
            if state:
                has_state += 1
            if item.get("decision_id") or state.get("decision_id"):
                has_decision_id += 1
            if state.get("signal_fired_price") or state.get("order_sent_price") or state.get("filled_price"):
                has_signal_price += 1
    stats.update(
        {
            "rows": rows,
            "events": dict(events),
            "markets": dict(markets),
            "has_state": has_state,
            "has_decision_id": has_decision_id,
            "has_signal_price": has_signal_price,
        }
    )
    return stats


def _db_summary(session_date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, path in DB_TARGETS.items():
        info: dict[str, Any] = {"path": str(path.relative_to(ROOT)), "exists": path.exists(), "tables": {}}
        if not path.exists():
            out[name] = info
            continue
        with _connect(path) as conn:
            tables = [
                str(row["name"])
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            ]
            for table in tables:
                cols = _table_columns(conn, table)
                table_info: dict[str, Any] = {
                    "columns": cols,
                    "row_count": _table_count(conn, table),
                    "session_rows": 0,
                }
                if "session_date" in cols:
                    table_info["session_rows"] = int(
                        _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE session_date=?", (session_date,)) or 0
                    )
                elif "date" in cols:
                    table_info["session_rows"] = int(
                        _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE date=?", (session_date,)) or 0
                    )
                info["tables"][table] = table_info

            if name == "decisions" and "decisions" in tables:
                info["decision_counts"] = _group_counts(
                    conn,
                    "decisions",
                    ["market", "decision", "COALESCE(block_reason, '') AS block_reason"],
                    where="session_date=?",
                    params=(session_date,),
                )
                info["coverage"] = {
                    "price_rows": _scalar(
                        conn,
                        "SELECT COUNT(*) FROM decisions WHERE session_date=? AND price IS NOT NULL",
                        (session_date,),
                    ),
                    "score_rows": _scalar(
                        conn,
                        "SELECT COUNT(*) FROM decisions WHERE session_date=? AND entry_priority_score IS NOT NULL",
                        (session_date,),
                    ),
                    "forward_1d_rows": _scalar(
                        conn,
                        "SELECT COUNT(*) FROM decisions WHERE session_date=? AND forward_1d IS NOT NULL",
                        (session_date,),
                    ),
                }
            elif name == "v2_event_store" and "lifecycle_events" in tables:
                info["event_counts"] = _group_counts(
                    conn,
                    "lifecycle_events",
                    ["market", "event_type", "COALESCE(reason_code, '') AS reason_code"],
                    where="session_date=?",
                    params=(session_date,),
                )
            elif name == "intraday_strategy_log" and "intraday_strategy_log" in tables:
                info["stage_counts"] = _group_counts(
                    conn,
                    "intraday_strategy_log",
                    ["bot_mode", "market", "stage"],
                    where="session_date=?",
                    params=(session_date,),
                )
            elif name == "ticker_selection_log" and "ticker_selection_log" in tables:
                info["selection_counts"] = _group_counts(
                    conn,
                    "ticker_selection_log",
                    ["bot_mode", "market", "source_type", "trade_ready"],
                    where="date=?",
                    params=(session_date,),
                )
        out[name] = info
    return out


def _entry_timing_summary(session_date: str) -> list[dict[str, Any]]:
    day = session_date.replace("-", "")
    root = ROOT / "logs" / "entry_timing"
    if not root.exists():
        return []
    return [_jsonl_stats(path) for path in sorted(root.glob(f"*_{day}_*.jsonl"))]


def _candidate_health_summary(session_date: str) -> list[dict[str, Any]]:
    day = session_date.replace("-", "")
    rows: list[dict[str, Any]] = []
    for path in sorted((ROOT / "state").glob(f"candidate_health_*_{day}.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        tickers = data.get("tickers") if isinstance(data, dict) else {}
        rows.append(
            {
                "path": str(path.relative_to(ROOT)),
                "exists": True,
                "ticker_rows": len(tickers or {}),
                "top_level_keys": sorted(list(data.keys()))[:20] if isinstance(data, dict) else [],
            }
        )
    return rows


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if not rows:
        return "_No rows._\n"
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            value = row.get(col, "")
            vals.append(str(value).replace("\n", " ")[:220])
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def build_report(session_date: str) -> str:
    dbs = _db_summary(session_date)
    entry_timing = _entry_timing_summary(session_date)
    candidate_health = _candidate_health_summary(session_date)

    lines: list[str] = []
    lines.append(f"# Shadow Audit Gap Report - {session_date}")
    lines.append("")
    lines.append(f"Generated at: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("- Operating DBs already contain useful signal, block, lifecycle, and price samples.")
    lines.append("- No existing live table provides uniform +5m/+15m/+30m/+60m outcome rows for every signal.")
    lines.append("- `decision_id` exists in V2 lifecycle data, but a deterministic per-signal `signal_id` is still needed.")
    lines.append("- `entry_timing` has candidate/signal/order/fill timing but not enough cross-system keys for full joins.")
    lines.append("")
    lines.append("## Database Inventory")
    lines.append("")
    inv_rows = []
    for name, info in dbs.items():
        inv_rows.append(
            {
                "db": name,
                "path": info["path"],
                "exists": info["exists"],
                "tables": ", ".join(info.get("tables", {}).keys()),
            }
        )
    lines.append(_md_table(inv_rows, ["db", "path", "exists", "tables"]))

    for name, info in dbs.items():
        lines.append(f"## {name}")
        lines.append("")
        table_rows = []
        for table, table_info in (info.get("tables") or {}).items():
            table_rows.append(
                {
                    "table": table,
                    "rows": table_info.get("row_count", 0),
                    "session_rows": table_info.get("session_rows", 0),
                    "key_columns": ", ".join(
                        c
                        for c in table_info.get("columns", [])
                        if c
                        in {
                            "decision_id",
                            "path_run_id",
                            "event_type",
                            "ticker",
                            "market",
                            "session_date",
                            "date",
                            "price",
                            "block_reason",
                            "entry_priority_score",
                            "pnl_pct",
                            "forward_1d",
                        }
                    ),
                }
            )
        lines.append(_md_table(table_rows, ["table", "rows", "session_rows", "key_columns"]))
        for key in ("coverage", "decision_counts", "event_counts", "stage_counts", "selection_counts"):
            if key not in info:
                continue
            lines.append(f"### {key}")
            lines.append("")
            value = info[key]
            if isinstance(value, dict):
                rows = [{"metric": k, "value": v} for k, v in value.items()]
                lines.append(_md_table(rows, ["metric", "value"]))
            elif isinstance(value, list):
                columns = list(value[0].keys()) if value else []
                lines.append(_md_table(value, columns))

    lines.append("## entry_timing JSONL")
    lines.append("")
    lines.append(
        _md_table(
            entry_timing,
            ["path", "exists", "rows", "events", "has_state", "has_decision_id", "has_signal_price"],
        )
    )

    lines.append("## candidate_health")
    lines.append("")
    lines.append(_md_table(candidate_health, ["path", "exists", "ticker_rows", "top_level_keys"]))

    lines.append("## Gap Decisions")
    lines.append("")
    lines.append("| Requirement | Current Coverage | Action |")
    lines.append("| --- | --- | --- |")
    lines.append("| Per-signal deterministic key | Missing | Add `signal_id` |")
    lines.append("| Episode key for ORDER_UNKNOWN pause | Missing | Add `episode_id` |")
    lines.append("| Uniform intraday outcomes | Missing | Add passive price samples + updater |")
    lines.append("| No-trade/block joins | Partial | Link `signal_id`, `decision_id`, `episode_id` |")
    lines.append("| PathB market-block missed plans | Partial | Add blocked waiting-plan audit |")
    lines.append("| Existing operating DB isolation | Good | Keep shadow DB separate |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect current data gaps for shadow audit.")
    parser.add_argument("--date", default=datetime.now().date().isoformat(), help="session date YYYY-MM-DD")
    parser.add_argument(
        "--output",
        default="",
        help="output markdown path; defaults to docs/reports/shadow_audit_gap_YYYYMMDD.md",
    )
    args = parser.parse_args()

    session_date = str(args.date)
    report = build_report(session_date)
    output = Path(args.output) if args.output else ROOT / "docs" / "reports" / f"shadow_audit_gap_{session_date.replace('-', '')}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(str(output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
