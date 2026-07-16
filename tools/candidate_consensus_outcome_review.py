from __future__ import annotations

"""Mature the candidate consensus shadow with observed 30/60 minute outcomes.

This is a read-only review of the candidate audit DB.  It never writes live
candidate state and has no order authority.  One deterministic summary row is
written per market/session/horizon so the shadow clock advances without fills.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import statistics
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path
from tools.candidate_consensus_status import write_candidate_consensus_status


AUTHORITY = "SHADOW_ONLY_NO_ORDER_AUTHORITY"
HORIZONS = (30, 60)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _append_unique(path: Path, records: list[dict[str, Any]]) -> int:
    existing: set[str] = set()
    for row in _read_jsonl(path):
        event_id = str(row.get("event_id") or "")
        if event_id:
            existing.add(event_id)
    new_rows = [row for row in records if str(row.get("event_id") or "") not in existing]
    if not new_rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in new_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(new_rows)


def _metrics(values: list[float]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "n": len(clean),
        "mean_return_pct": statistics.fmean(clean) if clean else None,
        "median_return_pct": statistics.median(clean) if clean else None,
        "positive_rate": (
            sum(value > 0 for value in clean) / len(clean)
            if clean
            else None
        ),
    }


def review_consensus_outcomes(
    *,
    session_date: str,
    market: str,
    audit_db: Path,
    decision_ledger: Path,
    outcome_ledger: Path,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    decisions = [
        row
        for row in _read_jsonl(decision_ledger)
        if str(row.get("session_date") or "") == str(session_date)
        and str(row.get("market") or "").upper() == market_key
    ]
    candidate_keys = sorted(
        {
            str(row.get("candidate_key") or "")
            for row in decisions
            if str(row.get("candidate_key") or "")
        }
    )
    outcomes: dict[tuple[str, int], dict[str, Any]] = {}
    if candidate_keys and audit_db.exists():
        uri = f"file:{audit_db.resolve().as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in candidate_keys)
            rows = con.execute(
                f"""
                SELECT candidate_key,horizon_min,return_pct,max_runup_pct,
                       max_drawdown_pct,status,source,label_generated_at
                FROM audit_candidate_outcomes
                WHERE candidate_key IN ({placeholders})
                  AND horizon_min IN (30,60)
                """,
                candidate_keys,
            ).fetchall()
            outcomes = {
                (str(row["candidate_key"]), int(row["horizon_min"])): dict(row)
                for row in rows
            }
        finally:
            con.close()

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    decision_metrics: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        grouped: dict[str, list[float]] = defaultdict(list)
        quality: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        matched = 0
        for decision in decisions:
            result = outcomes.get((str(decision.get("candidate_key") or ""), horizon))
            decision_name = str(decision.get("decision") or "ABSTAIN").upper()
            if result is None:
                quality[decision_name]["missing"] += 1
                continue
            quality[decision_name][str(result.get("status") or "unknown")] += 1
            value = result.get("return_pct")
            if value is None:
                continue
            matched += 1
            grouped[decision_name].append(float(value))
        per_decision = {
            decision: {
                **_metrics(grouped.get(decision, [])),
                "quality_counts": dict(quality.get(decision, {})),
            }
            for decision in sorted(
                set(grouped) | set(quality) | {"SELECT_SHADOW", "ABSTAIN"}
            )
        }
        decision_metrics[str(horizon)] = per_decision
        event_seed = f"{session_date}|{market_key}|{horizon}|candidate_consensus_outcome_v1"
        records.append(
            {
                "event_id": "csout_" + hashlib.sha256(event_seed.encode("utf-8")).hexdigest()[:28],
                "schema_version": "candidate_consensus_outcome_v1",
                "authority": AUTHORITY,
                "session_date": session_date,
                "market": market_key,
                "horizon_min": horizon,
                "generated_at": generated_at,
                "decision_records": len(decisions),
                "matched_outcomes": matched,
                "metrics": per_decision,
                "promotion_eligible": False,
                "promotion_block_reason": "prospective_gate_not_met",
            }
        )
    written = _append_unique(outcome_ledger, records)
    summary = {
        "schema_version": "candidate_consensus_outcome_status_v1",
        "authority": AUTHORITY,
        "status": "OK" if decisions else "NO_DECISIONS",
        "session_date": session_date,
        "market": market_key,
        "generated_at": generated_at,
        "updated_at": generated_at,
        "decision_records": len(decisions),
        "selected_records": sum(
            str(row.get("decision") or "").upper() == "SELECT_SHADOW"
            for row in decisions
        ),
        "unique_tickers": len({str(row.get("ticker") or "") for row in decisions}),
        "written": written,
        "outcome_ledger": str(outcome_ledger),
        "horizons": decision_metrics,
        "promotion_eligible": False,
        "promotion_block_reason": "prospective_gate_not_met",
    }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Mature consensus shadow outcomes")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--market", choices=["US", "KR"], required=True)
    parser.add_argument(
        "--audit-db",
        default=str(ROOT / "data" / "audit" / "candidate_audit.db"),
    )
    parser.add_argument("--decision-ledger", default="")
    parser.add_argument("--outcome-ledger", default="")
    parser.add_argument("--status-output", default="")
    args = parser.parse_args()
    compact = args.session_date.replace("-", "")
    decision_ledger = (
        Path(args.decision_ledger)
        if args.decision_ledger
        else get_runtime_path(
            "logs",
            "shadow",
            f"candidate_consensus_shadow_{compact}.jsonl",
        )
    )
    outcome_ledger = (
        Path(args.outcome_ledger)
        if args.outcome_ledger
        else get_runtime_path(
            "logs",
            "shadow",
            f"candidate_consensus_outcomes_{compact}.jsonl",
        )
    )
    status_path = (
        Path(args.status_output)
        if args.status_output
        else get_runtime_path("state", "candidate_consensus_outcome_status.json")
    )
    summary = review_consensus_outcomes(
        session_date=args.session_date,
        market=args.market,
        audit_db=Path(args.audit_db),
        decision_ledger=decision_ledger,
        outcome_ledger=outcome_ledger,
    )
    write_candidate_consensus_status(
        summary,
        kind="outcome",
        markets=[args.market],
        primary_path=status_path,
        write_market_copy=not bool(args.status_output),
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
