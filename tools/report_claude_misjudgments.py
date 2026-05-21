from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.label_claude_judgments import DEFAULT_FACT_DB


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(data: Any) -> str:
    return json.dumps(data if data is not None else {}, ensure_ascii=False, sort_keys=True, default=str)


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_pct(value: Any) -> str:
    number = _as_float(value)
    return "NA" if number is None else f"{number:.2f}%"


def _json_value(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return text


def _market(value: str) -> str:
    key = str(value or "").strip().upper()
    return key if key in {"KR", "US", "ALL"} else key


def _resolve_dates(date: str = "", start_date: str = "", end_date: str = "") -> tuple[str, str]:
    if date:
        return date, date
    return start_date, end_date


def _scope_where(alias: str, start_date: str, end_date: str, market: str, runtime_mode: str) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    where = [f"{prefix}runtime_mode=?"]
    params: list[Any] = [runtime_mode]
    if start_date:
        where.append(f"{prefix}session_date>=?")
        params.append(start_date)
    if end_date:
        where.append(f"{prefix}session_date<=?")
        params.append(end_date)
    if market and market != "ALL":
        where.append(f"{prefix}market=?")
        params.append(market)
    return " AND ".join(where), params


def _load_rows(
    conn: sqlite3.Connection,
    *,
    start_date: str,
    end_date: str,
    market: str,
    runtime_mode: str,
    latest_only: bool,
) -> list[dict[str, Any]]:
    if not _table_exists(conn, "decision_labels"):
        return []
    where, params = _scope_where("l", start_date, end_date, market, runtime_mode)
    if latest_only:
        where += " AND COALESCE(s.latest_rank, 1)=1"
    sql = f"""
        SELECT
            l.label_key, l.selection_key, l.runtime_mode, l.session_date, l.market, l.ticker,
            l.label, l.owner, l.confidence, l.label_rule, l.improvement_hint, l.evidence_json,

            s.final_action, s.raw_action, s.classification, s.source,
            s.gap_pct, s.from_high_pct, s.volume_ratio, s.change_pct, s.latest_rank,
            o.forward_1d_pct, o.forward_3d_pct, o.forward_5d_pct,
            o.max_runup_3d_pct, o.max_drawdown_3d_pct, o.outcome_source,
            e.pnl_pct, e.mfe_pct, e.mae_pct, e.quality_grade, e.match_quality,
            e.source_quality AS execution_source_quality
        FROM decision_labels l
        LEFT JOIN fact_selection s ON s.selection_key=l.selection_key
        LEFT JOIN fact_forward_outcome o ON o.selection_key=l.selection_key
        LEFT JOIN fact_execution e ON e.selection_key=l.selection_key
        WHERE {where}
        ORDER BY l.session_date, l.market, l.label, l.ticker, l.selection_key
    """
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _counter_key(row: dict[str, Any], *fields: str) -> tuple[str, ...]:
    return tuple(str(row.get(field) or "") for field in fields)


def _top_rows(rows: list[dict[str, Any]], key: str, *, limit: int = 10, reverse: bool = True) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> float:
        value = _as_float(row.get(key))
        if value is None:
            return float("-inf") if reverse else float("inf")
        return value

    return sorted(rows, key=sort_key, reverse=reverse)[:limit]


def _data_quality_reasons(row: dict[str, Any]) -> list[str]:
    evidence = _json_value(row.get("evidence_json")) or {}
    if isinstance(evidence, dict):
        reasons = evidence.get("data_quality_reasons")
        if isinstance(reasons, list):
            return [str(item) for item in reasons]
    return ["unknown"]


def _lesson_eligible(row: dict[str, Any]) -> bool:
    return row.get("owner") == "claude_selection" and row.get("label") in {"false_positive", "false_negative"}


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_market_label: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_market_owner: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        market = str(row.get("market") or "")
        by_market_label[market][str(row.get("label") or "")] += 1
        by_market_owner[market][str(row.get("owner") or "")] += 1

    kr_false_positive = [
        row
        for row in rows
        if row.get("market") == "KR" and row.get("label") == "false_positive" and row.get("owner") == "claude_selection"
    ]
    kr_overextended = [
        row
        for row in kr_false_positive
        if (_as_float(row.get("gap_pct")) or 0.0) >= 5.0
        or (_as_float(row.get("from_high_pct")) is not None and (_as_float(row.get("from_high_pct")) or 0.0) >= -3.0)
    ]
    false_negative = [row for row in rows if row.get("label") == "false_negative"]
    risk_miss = [row for row in rows if row.get("label") == "risk_justified_miss"]
    execution_issue = [row for row in rows if row.get("label") == "execution_issue"]
    data_quality_issue = [row for row in rows if row.get("label") == "data_quality_issue"]
    dq_reasons = Counter(reason for row in data_quality_issue for reason in _data_quality_reasons(row))

    eligible = [row for row in rows if _lesson_eligible(row)]
    blocked = [
        row
        for row in rows
        if row.get("label") in {"risk_justified_miss", "execution_issue", "data_quality_issue"}
    ]

    return {
        "total_rows": len(rows),
        "by_market_label": {market: dict(counts) for market, counts in sorted(by_market_label.items())},
        "by_market_owner": {market: dict(counts) for market, counts in sorted(by_market_owner.items())},
        "us_correct_positive": _top_rows(
            [row for row in rows if row.get("market") == "US" and row.get("label") == "correct_positive"],
            "max_runup_3d_pct",
            limit=20,
        ),
        "kr_false_positive": _top_rows(kr_false_positive, "max_drawdown_3d_pct", limit=20, reverse=False),
        "kr_overextended_false_positive": _top_rows(kr_overextended, "max_drawdown_3d_pct", limit=20, reverse=False),
        "false_negative": _top_rows(false_negative, "max_runup_3d_pct", limit=20),
        "risk_justified_miss": _top_rows(risk_miss, "max_runup_3d_pct", limit=20),
        "execution_issue": _top_rows(execution_issue, "pnl_pct", limit=20, reverse=False),
        "data_quality_issue_count_by_reason": dict(dq_reasons),
        "data_quality_issue_examples": data_quality_issue[:20],
        "lesson_candidate_eligible_count": len(eligible),
        "lesson_candidate_blocked_count": len(blocked),
        "lesson_candidate_eligible_examples": eligible[:20],
        "lesson_candidate_blocked_examples": blocked[:20],
    }


def build_report_payload(
    *,
    db_path: str | Path = DEFAULT_FACT_DB,
    date: str = "",
    start_date: str = "",
    end_date: str = "",
    market: str = "ALL",
    runtime_mode: str = "live",
    latest_only: bool = False,
) -> dict[str, Any]:
    start_date, end_date = _resolve_dates(date=date, start_date=start_date, end_date=end_date)
    market_key = _market(market or "ALL")
    runtime_key = str(runtime_mode or "live").strip().lower()
    db = Path(db_path)
    payload: dict[str, Any] = {
        "generated_at": _utc_now(),
        "db_path": str(db_path),
        "start_date": start_date,
        "end_date": end_date,
        "market": market_key,
        "runtime_mode": runtime_key,
        "latest_only": bool(latest_only),
        "status": "OK",
        "summary": {},
    }
    if not db.exists():
        payload["status"] = "MISSING_FACT_DB"
        return payload
    with closing(_connect(db)) as conn:
        rows = _load_rows(
            conn,
            start_date=start_date,
            end_date=end_date,
            market=market_key,
            runtime_mode=runtime_key,
            latest_only=bool(latest_only),
        )
    payload["summary"] = _summarize(rows)
    return payload


def _table(lines: list[str], rows: list[dict[str, Any]], columns: list[tuple[str, str]], *, empty: str = "None") -> None:
    if not rows:
        lines.append(empty)
        lines.append("")
        return
    lines.append("| " + " | ".join(title for title, _ in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        values = []
        for _, key in columns:
            value = row.get(key)
            if key.endswith("_pct") or key in {"confidence"}:
                value = _fmt_pct(value) if key != "confidence" else f"{float(value or 0.0):.2f}"
            values.append(str(value if value not in (None, "") else ""))
        lines.append("| " + " | ".join(value.replace("|", "\\|") for value in values) + " |")
    lines.append("")


def to_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines: list[str] = [
        "# Claude Misjudgment Report",
        "",
        f"- generated_at: {payload.get('generated_at')}",
        f"- scope: {payload.get('runtime_mode')} {payload.get('market')} {payload.get('start_date')}..{payload.get('end_date')}",
        f"- latest_only: {payload.get('latest_only')}",
        f"- status: {payload.get('status')}",
        "",
        "## Market Label Distribution",
        "",
    ]
    by_market = summary.get("by_market_label") or {}
    if by_market:
        lines.append("| market | label | count |")
        lines.append("| --- | --- | --- |")
        for market, counts in by_market.items():
            for label, count in sorted((counts or {}).items()):
                lines.append(f"| {market} | {label} | {count} |")
        lines.append("")
    else:
        lines.append("None")
        lines.append("")

    lines.append("## US Correct Positive Preservation")
    lines.append("")
    _table(
        lines,
        summary.get("us_correct_positive") or [],
        [
            ("date", "session_date"),
            ("ticker", "ticker"),
            ("action", "final_action"),
            ("f3d", "forward_3d_pct"),
            ("runup3d", "max_runup_3d_pct"),
        ],
    )

    lines.append("## KR False Positive Overextension Bucket")
    lines.append("")
    _table(
        lines,
        summary.get("kr_overextended_false_positive") or [],
        [
            ("date", "session_date"),
            ("ticker", "ticker"),
            ("action", "final_action"),
            ("gap", "gap_pct"),
            ("from_high", "from_high_pct"),
            ("drawdown3d", "max_drawdown_3d_pct"),
        ],
    )

    lines.append("## False Negative vs Risk Justified Miss")
    lines.append("")
    fn = len(summary.get("false_negative") or [])
    risk = len(summary.get("risk_justified_miss") or [])
    lines.append(f"- false_negative examples: {fn}")
    lines.append(f"- risk_justified_miss examples: {risk}")
    lines.append("")
    _table(
        lines,
        summary.get("false_negative") or [],
        [
            ("date", "session_date"),
            ("market", "market"),
            ("ticker", "ticker"),
            ("action", "final_action"),
            ("runup3d", "max_runup_3d_pct"),
        ],
    )

    lines.append("## Execution Issues")
    lines.append("")
    _table(
        lines,
        summary.get("execution_issue") or [],
        [
            ("date", "session_date"),
            ("market", "market"),
            ("ticker", "ticker"),
            ("f3d", "forward_3d_pct"),
            ("pnl", "pnl_pct"),
            ("quality", "quality_grade"),
        ],
    )

    lines.append("## Data Quality Issues")
    lines.append("")
    dq = summary.get("data_quality_issue_count_by_reason") or {}
    if dq:
        lines.append("| reason | count |")
        lines.append("| --- | --- |")
        for reason, count in sorted(dq.items()):
            lines.append(f"| {reason} | {count} |")
        lines.append("")
    else:
        lines.append("None")
        lines.append("")

    lines.append("## Lesson Candidate Eligibility")
    lines.append("")
    lines.append(f"- eligible selection labels: {summary.get('lesson_candidate_eligible_count', 0)}")
    lines.append(f"- blocked non-selection labels: {summary.get('lesson_candidate_blocked_count', 0)}")
    lines.append("")
    _table(
        lines,
        summary.get("lesson_candidate_eligible_examples") or [],
        [
            ("date", "session_date"),
            ("market", "market"),
            ("ticker", "ticker"),
            ("label", "label"),
            ("owner", "owner"),
        ],
    )
    return "\n".join(lines).rstrip() + "\n"


def write_report(payload: dict[str, Any], *, output: str | Path, fmt: str) -> Path:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = to_markdown(payload) if fmt == "md" else json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text + ("" if text.endswith("\n") else "\n"))
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Claude selection misjudgments by owner.")
    parser.add_argument("--db", default=str(DEFAULT_FACT_DB))
    parser.add_argument("--date", default="")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--latest-only", action="store_true")
    parser.add_argument("--format", choices=("md", "json"), default="md")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    payload = build_report_payload(
        db_path=args.db,
        date=args.date,
        start_date=args.start_date,
        end_date=args.end_date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        latest_only=bool(args.latest_only),
    )
    if args.output:
        path = write_report(payload, output=args.output, fmt=args.format)
        print(str(path))
    elif args.format == "json":
        print(_json(payload))
    else:
        print(to_markdown(payload), end="")
    return 0 if payload.get("status") == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
