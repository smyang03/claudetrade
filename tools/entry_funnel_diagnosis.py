"""진입 퍼널 시장별 진단 — 어느 단계에서 0이 되는지 오진 없이 본다. read-only.

2026-07-13 오진 교정 2건:
1. CLAUDE_TRADE_READY를 통째로 세면 안 된다. Tier2 섹터플레이 종목은 스크리너 후보가
   아니라서 가격 플랜 경로를 타지 않는다(PROFIT_EVIDENCE_SHADOW만 남기고 끝난다).
   이걸 섞으면 "trade_ready는 나오는데 플랜이 안 난다"는 잘못된 결론이 나온다.
2. KR/US를 합치면 안 된다. RR 거부 81건 중 US 75 / KR 6 — 두 시장의 병이 다르다.
   US는 RR 게이트에서 죽고, KR은 그 앞 단계에서 이미 죽는다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# 가격 플랜 경로를 탈 수 없는 코호트(스크리너 후보가 아님). 퍼널에서 분리한다.
NON_PLAN_STRATEGIES = {"kr_sector_play", "sector_play"}

PLAN_STAGES = (
    "CLAUDE_TRADE_READY",
    "CLAUDE_PRICE_PLAN_CREATED",
    "CLAUDE_PRICE_WAITING",
    "PATHB_ZONE_UPDATED",
    "ORDER_SENT",
    "FILLED",
)


def _payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def trade_ready_split(
    con: sqlite3.Connection,
    audit: sqlite3.Connection,
    session: str,
    market: str,
) -> dict[str, int]:
    """CLAUDE_TRADE_READY를 '플랜 경로를 탈 수 있는 것'과 '아닌 것'으로 나눈다.

    판정 기준은 payload의 strategy가 아니라 **그 세션의 스크리너 후보로 등재됐는지**다.
    Tier2 섹터플레이 종목은 audit_candidate_rows에 행이 없다(실측: 2026-07-13 KR 4종목 전부 0행).
    payload에는 strategy가 실리지 않는 경우가 있어 문자열 판정은 조용히 실패한다.
    """
    plan_eligible = 0
    non_plan = Counter()
    rows = con.execute(
        """
        SELECT ticker, payload_json FROM lifecycle_events
        WHERE event_type='CLAUDE_TRADE_READY' AND session_date=? AND market=?
        """,
        (session, market),
    ).fetchall()
    for ticker, raw in rows:
        payload = _payload(raw)
        strategy = str(payload.get("strategy") or payload.get("strategy_used") or "").strip().lower()
        screened = audit.execute(
            "SELECT 1 FROM audit_candidate_rows WHERE session_date=? AND market=? AND ticker=? LIMIT 1",
            (session, market, str(ticker)),
        ).fetchone()
        if screened and strategy not in NON_PLAN_STRATEGIES:
            plan_eligible += 1
        else:
            non_plan[strategy or "not_screened"] += 1
    return {
        "trade_ready_total": len(rows),
        "trade_ready_plan_eligible": plan_eligible,
        "trade_ready_non_plan": int(sum(non_plan.values())),
        "non_plan_by_strategy": dict(non_plan),
    }


def block_reasons(con: sqlite3.Connection, session: str, market: str) -> dict[str, int]:
    counter: Counter = Counter()
    for (raw,) in con.execute(
        "SELECT payload_json FROM lifecycle_events WHERE event_type='SAFETY_BLOCKED' AND session_date=? AND market=?",
        (session, market),
    ):
        payload = _payload(raw)
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            for item in errors:
                counter[str(item)] += 1
            continue
        reason = str(payload.get("reason") or payload.get("block_reason") or "").strip()
        counter[reason or "unspecified"] += 1
    return dict(counter.most_common())


def session_funnel(con: sqlite3.Connection, audit: sqlite3.Connection, session: str, market: str) -> dict[str, Any]:
    stages = {
        stage: con.execute(
            "SELECT COUNT(*) FROM lifecycle_events WHERE event_type=? AND session_date=? AND market=?",
            (stage, session, market),
        ).fetchone()[0]
        for stage in PLAN_STAGES
    }
    output: dict[str, Any] = {"session_date": session, "market": market}
    output.update(trade_ready_split(con, audit, session, market))
    output.update(stages)
    output["blocked_by"] = block_reasons(con, session, market)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="진입 퍼널 시장별 진단 (read-only)")
    parser.add_argument("--event-db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--audit-db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--market", default="KR,US")
    parser.add_argument("--since", default="2026-06-25")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.event_db}?mode=ro", uri=True, timeout=30)
    audit = sqlite3.connect(f"file:{args.audit_db}?mode=ro", uri=True, timeout=30)
    try:
        sessions = [
            row[0]
            for row in con.execute(
                "SELECT DISTINCT session_date FROM lifecycle_events WHERE session_date>=? ORDER BY 1",
                (args.since,),
            )
        ]
        report: list[dict[str, Any]] = []
        for market in [value.strip().upper() for value in args.market.split(",") if value.strip()]:
            for session in sessions:
                report.append(session_funnel(con, audit, session, market))
    finally:
        con.close()
        audit.close()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    for market in sorted({row["market"] for row in report}):
        print(f"\n=== {market} 진입 퍼널 (trade_ready는 플랜 가능/불가로 분리) ===")
        print(
            "%-11s %6s %6s %6s %6s %6s %6s  %s"
            % ("session", "TR(계)", "TR(플랜)", "TR(제외)", "PLAN", "SENT", "FILL", "차단사유")
        )
        for row in [item for item in report if item["market"] == market]:
            blocked = ", ".join(f"{key}:{value}" for key, value in list(row["blocked_by"].items())[:2])
            print(
                "%-11s %6d %6d %6d %6d %6d %6d  %s"
                % (
                    row["session_date"],
                    row["trade_ready_total"],
                    row["trade_ready_plan_eligible"],
                    row["trade_ready_non_plan"],
                    row["CLAUDE_PRICE_PLAN_CREATED"],
                    row["ORDER_SENT"],
                    row["FILLED"],
                    blocked[:60],
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
