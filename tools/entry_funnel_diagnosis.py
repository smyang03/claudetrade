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


def is_shadow_ready(payload: dict[str, Any]) -> bool:
    """profit_evidence shadow가 만든 가짜 trade_ready인가.

    ★계측 오염(2026-07-13 실측): `trading_bot.py:10534 _record_profit_evidence_shadow_once`가
    `_v2_ensure_execution_decision_id` → `registry.register_trade_ready()`를 호출해서
    **shadow 관측이 CLAUDE_TRADE_READY 이벤트로 발행된다.** 그대로 세면 실제 후보 수가 과대집계된다.
    구분은 payload로 가능하다: shadow는 `shadow_only=True` / `registration_source=profit_evidence_shadow`,
    진짜 ready는 `selection_meta`·`ticker_origin`을 갖는다.
    """
    if bool(payload.get("shadow_only")):
        return True
    return str(payload.get("registration_source") or "").strip().lower() == "profit_evidence_shadow"


def trade_ready_split(
    con: sqlite3.Connection,
    audit: sqlite3.Connection,
    session: str,
    market: str,
) -> dict[str, int]:
    """CLAUDE_TRADE_READY를 실제 ready / shadow ready / 스크리너 미등재로 완전히 분리한다."""
    real_ready = 0
    shadow_ready = 0
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
        if is_shadow_ready(payload):
            shadow_ready += 1
            continue
        strategy = str(payload.get("strategy") or payload.get("strategy_used") or "").strip().lower()
        screened = audit.execute(
            "SELECT 1 FROM audit_candidate_rows WHERE session_date=? AND market=? AND ticker=? LIMIT 1",
            (session, market, str(ticker)),
        ).fetchone()
        if screened and strategy not in NON_PLAN_STRATEGIES:
            real_ready += 1
        else:
            non_plan[strategy or "not_screened"] += 1
    return {
        "trade_ready_total": len(rows),
        "trade_ready_real": real_ready,
        "trade_ready_shadow": shadow_ready,
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


def plan_split(con: sqlite3.Connection, session: str, market: str) -> dict[str, Any]:
    """가격 플랜을 wait-only와 매수가능으로 나누고 종착점을 센다.

    wait-only 플랜(`_registration_scope=candidate_actions_wait_only` / `_not_patha_trade_ready`)은
    애초에 즉시 매수 후보가 아니다. 이걸 매수 퍼널에 섞으면 전환율이 왜곡된다.
    """
    rows = con.execute(
        "SELECT status, plan_json FROM v2_path_runs WHERE session_date=? AND market=?",
        (session, market),
    ).fetchall()
    wait_only = 0
    buy_capable = 0
    status_counter: Counter = Counter()
    cancel_counter: Counter = Counter()
    for status, raw in rows:
        plan = _payload(raw)
        status_counter[str(status or "")] += 1
        if str(status or "") == "CANCELLED":
            cancel_counter[str(plan.get("cancel_reason") or "unrecorded")] += 1
        # raw_plan(SAFETY_BLOCKED payload)은 `_` 접두, v2_path_runs.plan_json은 무접두로 저장된다.
        # 한쪽만 보면 wait-only가 조용히 0으로 집계된다.
        scope = str(plan.get("registration_scope") or plan.get("_registration_scope") or "").strip().lower()
        not_ready = bool(plan.get("not_patha_trade_ready") or plan.get("_not_patha_trade_ready"))
        if scope == "candidate_actions_wait_only" or not_ready:
            wait_only += 1
        else:
            buy_capable += 1
    return {
        "plan_total": len(rows),
        "plan_wait_only": wait_only,
        "plan_buy_capable": buy_capable,
        "plan_status": dict(status_counter),
        "cancel_reasons": dict(cancel_counter.most_common()),
    }


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
    output.update(plan_split(con, session, market))
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
        print(f"\n=== {market} 진입 퍼널 (실제 ready / shadow ready / wait-only 분리) ===")
        print(
            "%-11s %5s %5s %5s | %5s %5s %5s | %4s %4s  %s"
            % ("session", "TR계", "실제", "shadow", "플랜", "매수", "wait", "SENT", "FILL", "취소사유")
        )
        for row in [item for item in report if item["market"] == market]:
            cancels = ", ".join(f"{key}:{value}" for key, value in list(row["cancel_reasons"].items())[:2])
            print(
                "%-11s %5d %5d %5d | %5d %5d %5d | %4d %4d  %s"
                % (
                    row["session_date"],
                    row["trade_ready_total"],
                    row["trade_ready_real"],
                    row["trade_ready_shadow"],
                    row["plan_total"],
                    row["plan_buy_capable"],
                    row["plan_wait_only"],
                    row["ORDER_SENT"],
                    row["FILLED"],
                    cancels[:52],
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
