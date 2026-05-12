from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def _json_load(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(str(value or "{}"))
    except Exception:
        return {}


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        parsed = float(value)
    except Exception:
        return None
    return parsed if parsed == parsed else None


def _adaptive_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = _json_load(row["payload_json"])
    for key in ("adaptive_live_condition", "_adaptive_live_condition"):
        value = payload.get(key)
        if isinstance(value, dict):
            return value
    runtime_gate = payload.get("runtime_gate")
    if isinstance(runtime_gate, dict):
        value = runtime_gate.get("adaptive_live_condition") or runtime_gate.get("_adaptive_live_condition")
        if isinstance(value, dict):
            return value
    return {}


def _decision_for(row: sqlite3.Row) -> dict[str, Any]:
    ticker = str(row["ticker"] or "")
    adaptive = _adaptive_payload(row)
    decisions = adaptive.get("decisions") if isinstance(adaptive.get("decisions"), dict) else {}
    if ticker in decisions and isinstance(decisions[ticker], dict):
        return decisions[ticker]
    if ticker.upper() in decisions and isinstance(decisions[ticker.upper()], dict):
        return decisions[ticker.upper()]
    return {}


def build_report(*, db_path: Path, session_date: str, market: str = "ALL") -> dict[str, Any]:
    market_key = str(market or "ALL").upper()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        where = ["r.session_date=?"]
        params: list[Any] = [session_date]
        if market_key != "ALL":
            where.append("r.market=?")
            params.append(market_key)
        rows = conn.execute(
            f"""
            SELECT
                r.candidate_key,
                r.market,
                r.session_date,
                r.ticker,
                r.claude_action,
                r.payload_json,
                o30.return_pct AS ret30,
                o60.return_pct AS ret60
            FROM audit_candidate_rows r
            LEFT JOIN audit_candidate_outcomes o30
              ON o30.candidate_key=r.candidate_key AND o30.horizon_min=30
            LEFT JOIN audit_candidate_outcomes o60
              ON o60.candidate_key=r.candidate_key AND o60.horizon_min=60
            WHERE {' AND '.join(where)}
            """,
            params,
        ).fetchall()
    finally:
        conn.close()

    matched: list[dict[str, Any]] = []
    for row in rows:
        decision = _decision_for(row)
        suggested = str(decision.get("suggested_claude_action") or "").upper()
        if not suggested:
            continue
        ret30 = _num(row["ret30"])
        ret60 = _num(row["ret60"])
        matched.append(
            {
                "market": row["market"],
                "ticker": row["ticker"],
                "claude_action": str(row["claude_action"] or "").upper(),
                "suggested_claude_action": suggested,
                "suggested_size_intent": decision.get("suggested_size_intent") or "",
                "ret30": ret30,
                "ret60": ret60,
            }
        )

    by_suggestion: dict[str, dict[str, Any]] = {}
    for item in matched:
        key = item["suggested_claude_action"]
        bucket = by_suggestion.setdefault(key, {"count": 0, "ret30": [], "ret60": [], "agree": 0})
        bucket["count"] += 1
        if item["ret30"] is not None:
            bucket["ret30"].append(item["ret30"])
        if item["ret60"] is not None:
            bucket["ret60"].append(item["ret60"])
        if item["claude_action"] == key:
            bucket["agree"] += 1

    summary = {}
    for key, bucket in by_suggestion.items():
        ret30 = list(bucket["ret30"])
        ret60 = list(bucket["ret60"])
        summary[key] = {
            "count": bucket["count"],
            "ret30_n": len(ret30),
            "ret30_avg": round(mean(ret30), 4) if ret30 else None,
            "ret30_positive_ratio": round(sum(1 for value in ret30 if value > 0) / len(ret30), 4) if ret30 else None,
            "ret60_n": len(ret60),
            "ret60_avg": round(mean(ret60), 4) if ret60 else None,
            "claude_agreement_ratio": round(bucket["agree"] / bucket["count"], 4) if bucket["count"] else None,
        }

    return {
        "session_date": session_date,
        "market": market_key,
        "db_path": str(db_path),
        "matched_suggestions": len(matched),
        "by_suggestion": summary,
        "rows": matched,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Report adaptive live condition shadow suggestion accuracy.")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--market", default="ALL", choices=["KR", "US", "ALL"])
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output-json", action="store_true")
    args = parser.parse_args()
    report = build_report(db_path=Path(args.db), session_date=args.session_date, market=args.market)
    if args.output_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(json.dumps({k: v for k, v in report.items() if k != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
