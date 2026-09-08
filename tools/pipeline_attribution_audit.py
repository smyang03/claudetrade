from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"
V2_DB = ROOT / "data" / "v2_event_store.db"

EXECUTABLE_ROUTES = {"PlanA.buy", "PlanA.probe", "PlanA.add", "PathB.wait"}
EXTERNAL_STRATEGIES = {"us_schg_bil_trend_v1", "kr_factor_trend_v1"}


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def rowdict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def load_canonical_fills(since: str) -> list[dict[str, Any]]:
    con = ro(ML_DB)
    try:
        rows = con.execute(
            """
            SELECT c.v2_decision_id, c.canonical_key, c.market, c.runtime_mode, c.session_date,
                   c.ticker, c.status, c.route, c.path_type, c.path_run_id, c.strategy,
                   c.origin_action, c.filled, c.closed, c.first_fill_event_id,
                   c.earliest_fill_at, c.first_closed_at, c.entry_price,
                   c.first_exit_price, c.last_exit_price, c.qty, c.pnl_pct,
                   c.pnl_pct_net, c.pnl_krw_net, c.raw_fill_event_count,
                   c.source_event_count, l.close_reason
            FROM v2_canonical_performance c
            LEFT JOIN v2_learning_performance l
              ON l.v2_decision_id = c.v2_decision_id
            WHERE c.session_date >= ? AND c.filled = 1
            ORDER BY c.session_date, c.earliest_fill_at, c.market, c.ticker
            """,
            (since,),
        ).fetchall()
        return [rowdict(row) for row in rows]
    finally:
        con.close()


def load_candidate_exec_rows(since: str) -> list[dict[str, Any]]:
    con = ro(AUDIT_DB)
    try:
        rows = con.execute(
            """
            SELECT candidate_key, market, session_date, known_at, ticker, claude_action,
                   route_final_action, route_route, route_reason, route_runtime_gate_reason,
                   execution_link_source, execution_decision_id, execution_event_id,
                   filled_count, first_signal_at, first_fill_at, entry_price, exit_price,
                   pnl_pct, no_submit_reason_code, actual_prompt_included
            FROM audit_candidate_rows
            WHERE session_date >= ?
              AND COALESCE(execution_decision_id, '') != ''
            ORDER BY session_date, market, ticker, known_at, candidate_key
            """,
            (since,),
        ).fetchall()
        return [rowdict(row) for row in rows]
    finally:
        con.close()


def same_ticker_prompt_count(fill: dict[str, Any]) -> int:
    con = ro(AUDIT_DB)
    try:
        return int(
            con.execute(
                """
                SELECT COUNT(*)
                FROM audit_candidate_rows
                WHERE session_date=? AND market=? AND ticker=? AND actual_prompt_included=1
                """,
                (fill["session_date"], fill["market"], fill["ticker"]),
            ).fetchone()[0]
            or 0
        )
    finally:
        con.close()


def lifecycle_summary(since: str) -> dict[str, Any]:
    con = ro(V2_DB)
    try:
        event_counts = {
            row["event_type"]: int(row["c"] or 0)
            for row in con.execute(
                """
                SELECT event_type, COUNT(*) AS c
                FROM lifecycle_events
                WHERE session_date >= ?
                GROUP BY event_type
                """,
                (since,),
            )
        }
        fill_rows = [
            rowdict(row)
            for row in con.execute(
                """
                SELECT event_id, event_type, market, runtime_mode, session_date, ticker,
                       decision_id, execution_id, occurred_at, payload_json
                FROM lifecycle_events
                WHERE session_date >= ? AND event_type='FILLED'
                ORDER BY session_date, occurred_at, event_id
                """,
                (since,),
            )
        ]
        unique_decisions = sorted({str(row.get("decision_id") or "") for row in fill_rows if row.get("decision_id")})
        dup_groups: list[dict[str, Any]] = []
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in fill_rows:
            grouped[
                (
                    str(row.get("decision_id") or ""),
                    str(row.get("execution_id") or ""),
                    str(row.get("occurred_at") or ""),
                )
            ].append(row)
        for key, rows in grouped.items():
            if len(rows) > 1:
                dup_groups.append(
                    {
                        "decision_id": key[0],
                        "execution_id": key[1],
                        "occurred_at": key[2],
                        "event_ids": [row["event_id"] for row in rows],
                        "count": len(rows),
                    }
                )
        return {
            "event_counts": event_counts,
            "filled_event_count": len(fill_rows),
            "filled_unique_decision_count": len(unique_decisions),
            "filled_unique_decision_ids": unique_decisions,
            "duplicate_fill_groups": dup_groups,
        }
    finally:
        con.close()


def is_filled_candidate(row: dict[str, Any]) -> bool:
    return int(row.get("filled_count") or 0) > 0 or bool(str(row.get("first_fill_at") or "").strip())


def expected_route(fill: dict[str, Any]) -> str:
    if str(fill.get("route") or "").lower() == "path_b" or str(fill.get("path_type") or "") == "claude_price":
        return "PathB.wait"
    if str(fill.get("route") or "").lower() == "path_a":
        return "PlanA.buy"
    return ""


def route_matches_fill(fill: dict[str, Any], row: dict[str, Any]) -> bool:
    route = expected_route(fill)
    if route and str(row.get("route_route") or "") != route:
        return False
    if route == "PathB.wait" and str(row.get("claude_action") or "") != "PULLBACK_WAIT":
        return False
    if route == "PlanA.buy" and str(row.get("claude_action") or "") not in {"BUY_READY", "PROBE_READY"}:
        return False
    return bool(route)


def classify_fill(fill: dict[str, Any], linked_rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled_rows = [row for row in linked_rows if is_filled_candidate(row)]
    executable_rows = [row for row in linked_rows if str(row.get("route_route") or "") in EXECUTABLE_ROUTES]
    correct_rows = [row for row in linked_rows if route_matches_fill(fill, row)]
    correct_filled_rows = [row for row in correct_rows if is_filled_candidate(row)]
    wrong_filled_rows = [row for row in filled_rows if not route_matches_fill(fill, row)]
    no_submit_filled_rows = [row for row in filled_rows if str(row.get("no_submit_reason_code") or "").strip()]
    strategy = str(fill.get("strategy") or "").lower()
    prompt_count = same_ticker_prompt_count(fill) if not linked_rows else None

    if not linked_rows:
        classification = "external_strategy_no_candidate_expected" if strategy in EXTERNAL_STRATEGIES else "no_candidate_link"
    elif correct_filled_rows:
        classification = "correct_candidate_fill"
    elif wrong_filled_rows and correct_rows:
        classification = "wrong_row_filled_correct_row_unfilled"
    elif wrong_filled_rows:
        classification = "wrong_row_filled_no_correct_row"
    elif correct_rows:
        classification = "correct_row_unfilled"
    elif executable_rows:
        classification = "executable_row_exists_but_route_mismatch"
    else:
        classification = "linked_no_executable_candidate"

    return {
        "v2_decision_id": fill["v2_decision_id"],
        "market": fill["market"],
        "session_date": fill["session_date"],
        "ticker": fill["ticker"],
        "canonical": {
            "route": fill.get("route"),
            "path_type": fill.get("path_type"),
            "path_run_id": fill.get("path_run_id"),
            "strategy": fill.get("strategy"),
            "origin_action": fill.get("origin_action"),
            "first_fill_event_id": fill.get("first_fill_event_id"),
            "earliest_fill_at": fill.get("earliest_fill_at"),
            "entry_price": fill.get("entry_price"),
            "pnl_pct": fill.get("pnl_pct"),
            "pnl_pct_net": fill.get("pnl_pct_net"),
            "close_reason": fill.get("close_reason"),
            "raw_fill_event_count": fill.get("raw_fill_event_count"),
            "source_event_count": fill.get("source_event_count"),
        },
        "expected_candidate_route": expected_route(fill),
        "classification": classification,
        "linked_candidate_count": len(linked_rows),
        "same_ticker_prompt_count": prompt_count,
        "filled_candidate_count": len(filled_rows),
        "correct_candidate_count": len(correct_rows),
        "wrong_filled_count": len(wrong_filled_rows),
        "no_submit_and_filled_count": len(no_submit_filled_rows),
        "linked_rows": [
            {
                "candidate_key": row.get("candidate_key"),
                "known_at": row.get("known_at"),
                "claude_action": row.get("claude_action"),
                "route_final_action": row.get("route_final_action"),
                "route_route": row.get("route_route"),
                "route_reason": row.get("route_reason"),
                "route_runtime_gate_reason": row.get("route_runtime_gate_reason"),
                "execution_link_source": row.get("execution_link_source"),
                "execution_event_id": row.get("execution_event_id"),
                "filled_count": row.get("filled_count"),
                "first_fill_at": row.get("first_fill_at"),
                "entry_price": row.get("entry_price"),
                "pnl_pct": row.get("pnl_pct"),
                "no_submit_reason_code": row.get("no_submit_reason_code"),
            }
            for row in linked_rows[:20]
        ],
    }


def analyze(since: str) -> dict[str, Any]:
    canonical = load_canonical_fills(since)
    candidate_rows = load_candidate_exec_rows(since)
    by_decision: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidate_rows:
        by_decision[str(row.get("execution_decision_id") or "")].append(row)
    decisions = [classify_fill(fill, by_decision.get(fill["v2_decision_id"], [])) for fill in canonical]
    class_counts = Counter(item["classification"] for item in decisions)

    life = lifecycle_summary(since)
    canonical_ids = {str(row.get("v2_decision_id") or "") for row in canonical}
    lifecycle_ids = set(life["filled_unique_decision_ids"])
    return {
        "since": since,
        "dbs": {
            "candidate_audit": str(AUDIT_DB),
            "ml_decisions": str(ML_DB),
            "v2_event_store": str(V2_DB),
        },
        "summary": {
            "canonical_filled_count": len(canonical),
            "candidate_exec_link_rows": len(candidate_rows),
            "candidate_exec_link_unique_decisions": len({row["execution_decision_id"] for row in candidate_rows}),
            "candidate_filled_rows": sum(1 for row in candidate_rows if is_filled_candidate(row)),
            "lifecycle_filled_event_count": life["filled_event_count"],
            "lifecycle_unique_filled_decisions": life["filled_unique_decision_count"],
            "lifecycle_filled_not_yet_canonical": sorted(lifecycle_ids - canonical_ids),
            "canonical_filled_missing_lifecycle": sorted(canonical_ids - lifecycle_ids),
            "classification_counts": dict(class_counts),
        },
        "lifecycle": life,
        "decisions": decisions,
    }


def write_md(result: dict[str, Any], out: Path) -> None:
    summary = result["summary"]
    lines: list[str] = [
        "# Candidate → Decision → Fill Attribution Audit 2026-07-23",
        "",
        "## Summary",
        "",
        f"- since: `{result['since']}`",
        f"- canonical filled: {summary['canonical_filled_count']}",
        f"- lifecycle FILLED events: {summary['lifecycle_filled_event_count']}",
        f"- lifecycle unique filled decisions: {summary['lifecycle_unique_filled_decisions']}",
        f"- candidate rows with execution_decision_id: {summary['candidate_exec_link_rows']}",
        f"- candidate unique execution decisions: {summary['candidate_exec_link_unique_decisions']}",
        f"- candidate filled rows among linked rows: {summary['candidate_filled_rows']}",
        "",
        "## Classification Counts",
        "",
    ]
    for key, value in sorted(summary["classification_counts"].items()):
        lines.append(f"- {key}: {value}")
    if summary["lifecycle_filled_not_yet_canonical"]:
        lines.extend(["", "## Lifecycle filled but not yet canonical", ""])
        for item in summary["lifecycle_filled_not_yet_canonical"]:
            lines.append(f"- `{item}`")
    if result["lifecycle"]["duplicate_fill_groups"]:
        lines.extend(["", "## Duplicate FILLED event groups", ""])
        for group in result["lifecycle"]["duplicate_fill_groups"]:
            lines.append(
                f"- `{group['decision_id']}` execution=`{group['execution_id']}` "
                f"occurred_at=`{group['occurred_at']}` events={group['event_ids']}"
            )
    lines.extend(["", "## Filled Decision Details", ""])
    for item in result["decisions"]:
        c = item["canonical"]
        lines.append(f"### {item['market']} {item['ticker']} / {item['v2_decision_id']}")
        lines.append("")
        lines.append(
            f"- canonical: route=`{c['route']}` path_type=`{c['path_type']}` "
            f"strategy=`{c['strategy']}` origin_action=`{c['origin_action']}`"
        )
        lines.append(
            f"- fill: event_id=`{c['first_fill_event_id']}` at=`{c['earliest_fill_at']}` "
            f"entry={c['entry_price']} pnl_net={c['pnl_pct_net']} close_reason=`{c['close_reason']}`"
        )
        lines.append(f"- expected candidate route: `{item['expected_candidate_route']}`")
        lines.append(f"- classification: `{item['classification']}`")
        lines.append(
            f"- linked_candidate_count={item['linked_candidate_count']} "
            f"filled_candidate_count={item['filled_candidate_count']} "
            f"correct_candidate_count={item['correct_candidate_count']} "
            f"wrong_filled_count={item['wrong_filled_count']} "
            f"no_submit_and_filled_count={item['no_submit_and_filled_count']}"
        )
        if item["same_ticker_prompt_count"] is not None:
            lines.append(f"- same_ticker_prompt_count={item['same_ticker_prompt_count']}")
        if item["linked_rows"]:
            lines.append("")
            lines.append("| candidate | known_at | action | route | filled | event | no_submit | source |")
            lines.append("|---|---|---|---|---:|---:|---|---|")
            for row in item["linked_rows"]:
                lines.append(
                    f"| `{row['candidate_key']}` | {row['known_at']} | "
                    f"{row['claude_action']} | {row['route_final_action']}/{row['route_route']} | "
                    f"{row['filled_count']} | {row['execution_event_id']} | "
                    f"{row['no_submit_reason_code'] or ''} | {row['execution_link_source'] or ''} |"
                )
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit candidate to v2 fill attribution.")
    parser.add_argument("--since", default="2026-07-01")
    parser.add_argument("--json-out", default=str(ROOT / "docs" / "reports" / "pipeline_attribution_audit_20260723.json"))
    parser.add_argument("--md-out", default=str(ROOT / "docs" / "reports" / "pipeline_attribution_audit_20260723.md"))
    args = parser.parse_args()
    result = analyze(args.since)
    json_path = Path(args.json_out)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(result, Path(args.md_out))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
