from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path

KST = timezone(timedelta(hours=9))


def _to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


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
    return sum(clean) / len(clean) if clean else None


def _parse_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value in (None, ""):
        return None
    try:
        return json.loads(str(value))
    except Exception:
        return None


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _date_text(row: dict[str, Any]) -> str:
    explicit = str(row.get("date") or row.get("call_date") or "").strip()
    if explicit:
        return explicit[:10]
    parsed = _parse_dt(row.get("timestamp") or row.get("known_at") or row.get("ts"))
    return parsed.date().isoformat() if parsed else ""


def _in_scope(row: dict[str, Any], *, start_date: str, end_date: str, market: str) -> bool:
    day = _date_text(row)
    if (start_date or end_date) and not day:
        return False
    if start_date and day and day < start_date:
        return False
    if end_date and day and day > end_date:
        return False
    market_key = str(market or "").upper()
    if market_key and market_key != "ALL" and str(row.get("market") or "").upper() != market_key:
        return False
    return True


def _analyst_from_label(label: Any) -> str:
    text = str(label or "").strip().lower()
    prefix = "hold_advisor_"
    if text.startswith(prefix):
        analyst = text[len(prefix):]
        return analyst or "unknown"
    return "unknown"


def _review_reason(*sources: Any) -> str:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in (
            "review_reason",
            "auto_sell_review_reason",
            "soft_exit_review_reason",
            "reason_family",
            "reason",
            "default_policy",
        ):
            value = str(source.get(key) or "").strip()
            if value:
                return value[:80]
        ctx = source.get("advisor_context_v2")
        if isinstance(ctx, dict):
            value = _review_reason(ctx)
            if value and value != "unknown":
                return value
    return "unknown"


def _completeness_score(value: Any) -> float | None:
    payload = value if isinstance(value, dict) else {}
    return _to_float(payload.get("score"))


def _completeness_low(value: Any) -> bool:
    score = _completeness_score(value)
    return bool(score is not None and score < 0.8)


def _pathb_revenue_exit_reason(value: Any) -> str:
    payload = value if isinstance(value, dict) else {}
    if not bool(payload.get("is_pathb")):
        return "not_pathb"
    return str(payload.get("exit_reason") or "other")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_raw_call_rows(raw_dir: Path, *, start_date: str, end_date: str, market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not raw_dir.exists():
        return rows
    for path in sorted(raw_dir.glob("*_hold_advisor_*.json")):
        data = _read_json_file(path)
        if not data:
            continue
        label = str(data.get("label") or "")
        if not label.startswith("hold_advisor_"):
            continue
        extra = data.get("extra") if isinstance(data.get("extra"), dict) else {}
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        parsed = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
        completeness = extra.get("input_completeness") if isinstance(extra.get("input_completeness"), dict) else {}
        revenue_context = extra.get("pathb_revenue_path_context") if isinstance(extra.get("pathb_revenue_path_context"), dict) else {}
        row = {
            "source": "raw_call",
            "call_id": str(data.get("call_id") or path.stem),
            "path": str(path),
            "timestamp": data.get("timestamp"),
            "date": data.get("date"),
            "market": str(data.get("market") or "").upper(),
            "label": label,
            "analyst_type": _analyst_from_label(label),
            "model": str(data.get("model") or ""),
            "decision_stage": str(extra.get("decision_stage") or "unknown"),
            "decision": str(parsed.get("action") or parsed.get("decision") or "unknown").upper(),
            "review_reason": _review_reason(extra, parsed),
            "duration_ms": _to_int(data.get("duration_ms")),
            "input_tokens": int(tokens.get("input") or 0),
            "output_tokens": int(tokens.get("output") or 0),
            "fallback": bool(parsed.get("fallback") or extra.get("fallback")),
            "input_completeness": completeness,
            "completeness_score": _completeness_score(completeness),
            "completeness_low": _completeness_low(completeness),
            "pathb_revenue_path_context": revenue_context,
            "pathb_revenue_exit_reason": _pathb_revenue_exit_reason(revenue_context),
        }
        if _in_scope(row, start_date=start_date, end_date=end_date, market=market):
            rows.append(row)
    return rows


def _load_db_call_rows(db_path: Path, *, start_date: str, end_date: str, market: str) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_call_events'"
        ).fetchone()
        if row is None:
            return []
        where = ["label LIKE 'hold_advisor_%'"]
        params: list[Any] = []
        if start_date:
            where.append("call_date>=?")
            params.append(start_date)
        if end_date:
            where.append("call_date<=?")
            params.append(end_date)
        market_key = str(market or "").upper()
        if market_key and market_key != "ALL":
            where.append("market=?")
            params.append(market_key)
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT call_id, label, market, call_date, known_at, model,
                       duration_ms, input_tokens, output_tokens, payload_json
                FROM agent_call_events
                WHERE {' AND '.join(where)}
                ORDER BY call_date, known_at, call_id
                """,
                params,
            ).fetchall()
        ]
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for item in rows:
        payload = _parse_json(item.get("payload_json")) or {}
        extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
        completeness = extra.get("input_completeness") if isinstance(extra.get("input_completeness"), dict) else {}
        revenue_context = extra.get("pathb_revenue_path_context") if isinstance(extra.get("pathb_revenue_path_context"), dict) else {}
        row = {
            "source": "agent_call_events",
            "call_id": str(item.get("call_id") or ""),
            "timestamp": item.get("known_at"),
            "date": item.get("call_date"),
            "market": str(item.get("market") or "").upper(),
            "label": str(item.get("label") or ""),
            "analyst_type": _analyst_from_label(item.get("label")),
            "model": str(item.get("model") or ""),
            "decision_stage": str(extra.get("decision_stage") or "unknown"),
            "decision": "unknown",
            "review_reason": _review_reason(extra),
            "duration_ms": _to_int(item.get("duration_ms")),
            "input_tokens": int(item.get("input_tokens") or 0),
            "output_tokens": int(item.get("output_tokens") or 0),
            "fallback": bool(extra.get("fallback")),
            "input_completeness": completeness,
            "completeness_score": _completeness_score(completeness),
            "completeness_low": _completeness_low(completeness),
            "pathb_revenue_path_context": revenue_context,
            "pathb_revenue_exit_reason": _pathb_revenue_exit_reason(revenue_context),
        }
        if _in_scope(row, start_date=start_date, end_date=end_date, market=market):
            out.append(row)
    return out


def _load_decision_rows(decision_dir: Path, *, start_date: str, end_date: str, market: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not decision_dir.exists():
        return rows
    for path in sorted(decision_dir.glob("decisions_*.jsonl")):
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    data = json.loads(text)
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                row = {
                    "source": "hold_advisor_decision",
                    "timestamp": data.get("ts") or data.get("timestamp"),
                    "date": _date_text({"timestamp": data.get("ts") or data.get("timestamp")}) or path.stem[-10:],
                    "market": str(data.get("market") or "").upper(),
                    "ticker": str(data.get("ticker") or "").upper(),
                    "decision_stage": str(data.get("decision_stage") or "unknown"),
                    "decision": str(data.get("decision") or "unknown").upper(),
                    "review_reason": _review_reason(data, data.get("advisor_context_v2")),
                    "duration_ms": _to_int(data.get("duration_ms")),
                    "votes": data.get("votes") if isinstance(data.get("votes"), dict) else {},
                    "fallback": bool(data.get("fallback")),
                    "input_completeness": data.get("input_completeness") if isinstance(data.get("input_completeness"), dict) else {},
                    "completeness_score": _completeness_score(data.get("input_completeness")),
                    "completeness_low": _completeness_low(data.get("input_completeness")),
                    "pathb_revenue_path_context": data.get("pathb_revenue_path_context") if isinstance(data.get("pathb_revenue_path_context"), dict) else {},
                    "pathb_revenue_exit_reason": _pathb_revenue_exit_reason(data.get("pathb_revenue_path_context")),
                }
                if _in_scope(row, start_date=start_date, end_date=end_date, market=market):
                    rows.append(row)
    return rows


def _dedupe_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        call_id = str(row.get("call_id") or "")
        key = call_id or "|".join(
            [
                str(row.get("timestamp") or ""),
                str(row.get("market") or ""),
                str(row.get("label") or ""),
                str(row.get("duration_ms") or ""),
            ]
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    durations = [_to_float(row.get("duration_ms")) for row in rows]
    clean = [value for value in durations if value is not None]
    return {
        "calls": len(rows),
        "duration_count": len(clean),
        "missing_duration_count": len(rows) - len(clean),
        "avg_ms": _round(_mean(clean)),
        "p50_ms": _round(_percentile(clean, 0.50)),
        "p95_ms": _round(_percentile(clean, 0.95)),
        "max_ms": _round(max(clean) if clean else None),
        "input_tokens": sum(int(row.get("input_tokens") or 0) for row in rows),
        "output_tokens": sum(int(row.get("output_tokens") or 0) for row in rows),
        "fallback_count": sum(1 for row in rows if bool(row.get("fallback"))),
        "completeness_low_count": sum(1 for row in rows if bool(row.get("completeness_low"))),
    }


def _group_summary(rows: list[dict[str, Any]], fields: tuple[str, ...], *, limit: int = 50) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row.get(field) or "unknown") for field in fields)].append(row)
    out: list[dict[str, Any]] = []
    for key, items in groups.items():
        item = {field: key[idx] for idx, field in enumerate(fields)}
        item.update(_summary(items))
        out.append(item)
    out.sort(key=lambda item: (int(item.get("calls") or 0), int(item.get("duration_count") or 0)), reverse=True)
    return out[: max(int(limit or 1), 1)]


def _slow_rows(rows: list[dict[str, Any]], *, limit: int = 20) -> list[dict[str, Any]]:
    ranked = [row for row in rows if _to_float(row.get("duration_ms")) is not None]
    ranked.sort(key=lambda row: _to_float(row.get("duration_ms")) or -1.0, reverse=True)
    keep = []
    for row in ranked[: max(int(limit or 1), 1)]:
        keep.append(
            {
                "date": _date_text(row),
                "market": row.get("market") or "",
                "ticker": row.get("ticker") or "",
                "analyst_type": row.get("analyst_type") or "",
                "decision_stage": row.get("decision_stage") or "",
                "decision": row.get("decision") or "",
                "review_reason": row.get("review_reason") or "",
                "duration_ms": row.get("duration_ms"),
                "source": row.get("source") or "",
            }
        )
    return keep


def _vote_rows_from_decisions(decision_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for decision in decision_rows:
        votes = decision.get("votes") if isinstance(decision.get("votes"), dict) else {}
        for analyst_type, vote in votes.items():
            if not isinstance(vote, dict):
                continue
            rows.append(
                {
                    "source": "hold_advisor_decision_vote",
                    "timestamp": decision.get("timestamp"),
                    "date": _date_text(decision),
                    "market": decision.get("market") or "",
                    "ticker": decision.get("ticker") or "",
                    "analyst_type": str(analyst_type or "unknown"),
                    "decision_stage": decision.get("decision_stage") or "unknown",
                    "decision": str(vote.get("action") or "unknown").upper(),
                    "review_reason": decision.get("review_reason") or "unknown",
                    "duration_ms": _to_int(vote.get("duration_ms")),
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "fallback": bool(vote.get("fallback")),
                    "input_completeness": decision.get("input_completeness") if isinstance(decision.get("input_completeness"), dict) else {},
                    "completeness_score": decision.get("completeness_score"),
                    "completeness_low": bool(decision.get("completeness_low")),
                    "pathb_revenue_path_context": decision.get("pathb_revenue_path_context") if isinstance(decision.get("pathb_revenue_path_context"), dict) else {},
                    "pathb_revenue_exit_reason": decision.get("pathb_revenue_exit_reason") or "not_pathb",
                }
            )
    return rows


def analyze_hold_advisor_latency(
    *,
    db_path: str | Path | None = None,
    raw_dir: str | Path | None = None,
    decision_dir: str | Path | None = None,
    start_date: str = "",
    end_date: str = "",
    market: str = "ALL",
    source: str = "auto",
    limit: int = 20,
) -> dict[str, Any]:
    db = Path(db_path) if db_path else get_runtime_path("data", "audit", "agent_call_events.db")
    raw = Path(raw_dir) if raw_dir else get_runtime_path("logs", "raw_calls")
    decisions = Path(decision_dir) if decision_dir else get_runtime_path("logs", "hold_advisor")
    source_key = str(source or "auto").lower()
    if source_key not in {"auto", "db", "raw", "all"}:
        source_key = "auto"

    db_rows: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    if source_key in {"auto", "db", "all"}:
        db_rows = _load_db_call_rows(db, start_date=start_date, end_date=end_date, market=market)
    if source_key in {"auto", "raw", "all"}:
        raw_rows = _load_raw_call_rows(raw, start_date=start_date, end_date=end_date, market=market)

    if source_key == "auto":
        single_call_rows = db_rows if db_rows else raw_rows
        single_call_source = "agent_call_events" if db_rows else "raw_calls"
    elif source_key == "db":
        single_call_rows = db_rows
        single_call_source = "agent_call_events"
    elif source_key == "raw":
        single_call_rows = raw_rows
        single_call_source = "raw_calls"
    else:
        single_call_rows = _dedupe_calls([*db_rows, *raw_rows])
        single_call_source = "combined"

    decision_rows = _load_decision_rows(
        decisions,
        start_date=start_date,
        end_date=end_date,
        market=market,
    )
    decision_vote_rows = _vote_rows_from_decisions(decision_rows)

    return {
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "scope": {
            "start_date": start_date,
            "end_date": end_date,
            "market": str(market or "ALL").upper(),
            "source": source_key,
            "single_call_source": single_call_source,
            "db_path": str(db),
            "raw_dir": str(raw),
            "decision_dir": str(decisions),
        },
        "single_calls": {
            "summary": _summary(single_call_rows),
            "by_analyst": _group_summary(single_call_rows, ("analyst_type",), limit=limit),
            "by_stage": _group_summary(single_call_rows, ("decision_stage",), limit=limit),
            "by_market": _group_summary(single_call_rows, ("market",), limit=limit),
            "by_market_stage_analyst": _group_summary(
                single_call_rows,
                ("market", "decision_stage", "analyst_type"),
                limit=limit,
            ),
            "slowest": _slow_rows(single_call_rows, limit=limit),
        },
        "decision_requests": {
            "summary": _summary(decision_rows),
            "by_stage": _group_summary(decision_rows, ("decision_stage",), limit=limit),
            "by_market": _group_summary(decision_rows, ("market",), limit=limit),
            "by_market_stage_decision": _group_summary(
                decision_rows,
                ("market", "decision_stage", "decision"),
                limit=limit,
            ),
            "by_pathb_revenue_path_decision": _group_summary(
                decision_rows,
                ("market", "pathb_revenue_exit_reason", "decision"),
                limit=limit,
            ),
            "by_symbol": _group_summary(decision_rows, ("market", "ticker"), limit=limit),
            "slowest": _slow_rows(decision_rows, limit=limit),
        },
        "decision_votes": {
            "summary": _summary(decision_vote_rows),
            "by_analyst": _group_summary(decision_vote_rows, ("analyst_type",), limit=limit),
            "by_market_stage_analyst": _group_summary(
                decision_vote_rows,
                ("market", "decision_stage", "analyst_type"),
                limit=limit,
            ),
        },
    }


def _table(lines: list[str], rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> None:
    if not rows:
        lines.append("None")
        lines.append("")
        return
    lines.append("| " + " | ".join(label for label, _ in columns) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in rows:
        values = [str(row.get(key, "")) for _, key in columns]
        lines.append("| " + " | ".join(value.replace("|", "/") for value in values) + " |")
    lines.append("")


def to_markdown(payload: dict[str, Any]) -> str:
    scope = payload.get("scope") or {}
    lines = [
        "# Hold Advisor Latency Report",
        "",
        f"- generated_at: {payload.get('generated_at', '')}",
        f"- scope: {scope.get('start_date', '') or '*'} to {scope.get('end_date', '') or '*'} / {scope.get('market', 'ALL')}",
        f"- single_call_source: {scope.get('single_call_source', '')}",
        "",
        "## Single Claude Calls",
        "",
    ]
    summary = (payload.get("single_calls") or {}).get("summary") or {}
    lines.append(
        f"calls={summary.get('calls', 0)}, duration_count={summary.get('duration_count', 0)}, "
        f"missing_duration={summary.get('missing_duration_count', 0)}, "
        f"p50={summary.get('p50_ms')}ms, p95={summary.get('p95_ms')}ms, "
        f"fallback={summary.get('fallback_count', 0)}, completeness_low={summary.get('completeness_low_count', 0)}"
    )
    lines.append("")
    lines.append("### By Analyst")
    lines.append("")
    _table(
        lines,
        (payload.get("single_calls") or {}).get("by_analyst") or [],
        [("analyst", "analyst_type"), ("calls", "calls"), ("p50_ms", "p50_ms"), ("p95_ms", "p95_ms"), ("missing", "missing_duration_count")],
    )
    lines.append("### By Market / Stage / Analyst")
    lines.append("")
    _table(
        lines,
        (payload.get("single_calls") or {}).get("by_market_stage_analyst") or [],
        [
            ("market", "market"),
            ("stage", "decision_stage"),
            ("analyst", "analyst_type"),
            ("calls", "calls"),
            ("p50_ms", "p50_ms"),
            ("p95_ms", "p95_ms"),
        ],
    )
    lines.append("## 3-Vote Decision Requests")
    lines.append("")
    req_summary = (payload.get("decision_requests") or {}).get("summary") or {}
    lines.append(
        f"requests={req_summary.get('calls', 0)}, duration_count={req_summary.get('duration_count', 0)}, "
        f"missing_duration={req_summary.get('missing_duration_count', 0)}, "
        f"p50={req_summary.get('p50_ms')}ms, p95={req_summary.get('p95_ms')}ms, "
        f"fallback={req_summary.get('fallback_count', 0)}, completeness_low={req_summary.get('completeness_low_count', 0)}"
    )
    lines.append("")
    lines.append("### By Market / Stage / Decision")
    lines.append("")
    _table(
        lines,
        (payload.get("decision_requests") or {}).get("by_market_stage_decision") or [],
        [
            ("market", "market"),
            ("stage", "decision_stage"),
            ("decision", "decision"),
            ("requests", "calls"),
            ("p50_ms", "p50_ms"),
            ("p95_ms", "p95_ms"),
        ],
    )
    lines.append("### By PathB Revenue Path / Decision")
    lines.append("")
    _table(
        lines,
        (payload.get("decision_requests") or {}).get("by_pathb_revenue_path_decision") or [],
        [
            ("market", "market"),
            ("path", "pathb_revenue_exit_reason"),
            ("decision", "decision"),
            ("requests", "calls"),
            ("fallback", "fallback_count"),
            ("low_input", "completeness_low_count"),
            ("p50_ms", "p50_ms"),
        ],
    )
    lines.append("### Slowest Requests")
    lines.append("")
    _table(
        lines,
        (payload.get("decision_requests") or {}).get("slowest") or [],
        [
            ("date", "date"),
            ("market", "market"),
            ("ticker", "ticker"),
            ("stage", "decision_stage"),
            ("decision", "decision"),
            ("duration_ms", "duration_ms"),
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
    parser = argparse.ArgumentParser(description="Analyze hold advisor Claude call latency.")
    parser.add_argument("--db", default="", help="agent_call_events.db path")
    parser.add_argument("--raw-dir", default="", help="logs/raw_calls directory")
    parser.add_argument("--decision-dir", default="", help="logs/hold_advisor directory")
    parser.add_argument("--date", default="", help="single date YYYY-MM-DD")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--market", default="ALL")
    parser.add_argument("--source", choices=("auto", "db", "raw", "all"), default="auto")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)
    start_date = args.date or args.start_date
    end_date = args.date or args.end_date
    payload = analyze_hold_advisor_latency(
        db_path=args.db or None,
        raw_dir=args.raw_dir or None,
        decision_dir=args.decision_dir or None,
        start_date=start_date,
        end_date=end_date,
        market=args.market,
        source=args.source,
        limit=args.limit,
    )
    if args.output:
        path = write_report(payload, output=args.output, fmt=args.format)
        print(str(path))
    elif args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(to_markdown(payload), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
