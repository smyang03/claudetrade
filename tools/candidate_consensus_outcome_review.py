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


def _consensus_grade(decision: dict[str, Any]) -> str:
    """top3 교집합(SELECT)보다 넓은 등급 코호트 — 원장에 이미 있는 arm rank로 소급 계산.

    top3 교집합이 사실상 0건(5세션 1건)이라 SELECT 코호트 forward가 굶는다(2026-07-21).
    스코어러 계약(top3_intersection_else_abstain)은 그대로 두고, 리뷰만 등급을 나눠
    "완화하면 어떤 코호트가 벌었나"를 관측한다. 관측 전용 — 선정 권한 변화 없음.
    """
    left = decision.get("left") if isinstance(decision.get("left"), dict) else {}
    right = decision.get("right") if isinstance(decision.get("right"), dict) else {}
    lr = left.get("rank")
    rr = right.get("rank")
    try:
        lr = int(lr) if lr is not None else None
        rr = int(rr) if rr is not None else None
    except (TypeError, ValueError):
        lr = rr = None
    if lr is None or rr is None:
        return "missing_rank"
    if lr <= 3 and rr <= 3:
        return "both_top3"
    if lr <= 5 and rr <= 5:
        return "both_top5"
    if lr <= 10 and rr <= 10:
        return "both_top10"
    if lr <= 3 or rr <= 3:
        return "one_top3"
    return "neither"


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
    def _ticker_key(value: Any) -> str:
        text = str(value or "").strip()
        return text.upper() if market_key == "US" else text

    tickers = sorted(
        {_ticker_key(row.get("ticker")) for row in decisions if _ticker_key(row.get("ticker"))}
    )
    outcomes: dict[tuple[str, int], dict[str, Any]] = {}
    if tickers and audit_db.exists():
        uri = f"file:{audit_db.resolve().as_posix()}?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        con.row_factory = sqlite3.Row
        try:
            placeholders = ",".join("?" for _ in tickers)
            # candidate_key 네임스페이스 불일치 우회: 원장은 registry의 creg_ 키를 쓰지만
            # outcome 라벨은 audit_candidate_rows의 cand_ 키에 붙는다. registry가 켜진
            # 시장(US)에서만 키가 갈려 문자열 조인이 0 매칭됐다. (market,session,ticker)
            # identity로 조인해 라벨을 붙인다. 같은 세션·티커에 라벨이 여럿이면
            # label_generated_at 최신이 이긴다.
            ticker_expr = "UPPER(r.ticker)" if market_key == "US" else "r.ticker"
            rows = con.execute(
                f"""
                SELECT r.ticker AS ticker, o.horizon_min, o.return_pct, o.max_runup_pct,
                       o.max_drawdown_pct, o.status, o.source, o.label_generated_at
                FROM audit_candidate_rows r
                JOIN audit_candidate_outcomes o ON o.candidate_key = r.candidate_key
                WHERE r.market = ? AND r.session_date = ?
                  AND {ticker_expr} IN ({placeholders})
                  AND o.horizon_min IN (30,60)
                ORDER BY o.label_generated_at
                """,
                [market_key, str(session_date)] + tickers,
            ).fetchall()
            for row in rows:
                outcomes[(_ticker_key(row["ticker"]), int(row["horizon_min"]))] = dict(row)
        finally:
            con.close()

    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    records: list[dict[str, Any]] = []
    decision_metrics: dict[str, dict[str, Any]] = {}
    for horizon in HORIZONS:
        grouped: dict[str, list[float]] = defaultdict(list)
        quality: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        grade_grouped: dict[str, list[float]] = defaultdict(list)
        grade_counts: dict[str, int] = defaultdict(int)
        matched = 0
        for decision in decisions:
            result = outcomes.get((_ticker_key(decision.get("ticker")), horizon))
            decision_name = str(decision.get("decision") or "ABSTAIN").upper()
            grade = _consensus_grade(decision)
            grade_counts[grade] += 1
            if result is None:
                quality[decision_name]["missing"] += 1
                continue
            quality[decision_name][str(result.get("status") or "unknown")] += 1
            value = result.get("return_pct")
            if value is None:
                continue
            matched += 1
            grouped[decision_name].append(float(value))
            grade_grouped[grade].append(float(value))
        per_decision = {
            decision: {
                **_metrics(grouped.get(decision, [])),
                "quality_counts": dict(quality.get(decision, {})),
            }
            for decision in sorted(
                set(grouped) | set(quality) | {"SELECT_SHADOW", "ABSTAIN"}
            )
        }
        # 등급 코호트(원장 rank 소급): top3 교집합이 굶어도 완화 후보 코호트의 forward를 관측
        per_grade = {
            grade: {
                **_metrics(grade_grouped.get(grade, [])),
                "decision_count": grade_counts.get(grade, 0),
            }
            for grade in sorted(set(grade_grouped) | set(grade_counts))
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
                "grade_metrics": per_grade,
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
