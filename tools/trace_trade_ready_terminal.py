#!/usr/bin/env python3
"""TRADE_READY 종결 추적 — 진입 후보가 어디서 사라졌는지 전수 계측 (read-only).

왜 필요한가 (실측 2026-07-26, 2026-07-13~24 구간)
  CLAUDE_TRADE_READY 26종목 중
    체결          8
    안전차단      4  (US_MIDDAY_ENTRY_BLOCK / HIGH_PRICE_BUDGET_BLOCK / CLAUDE_PRICE_INVALID)
    미제출        2  (NO_SIGNAL)
    ★기록없이 소멸 12  ← 46%
  소멸 유형은 두 가지였다:
    (1) TRADE_READY 뒤에 FORWARD_* 말고는 **아무 이벤트도 없음** (SMCI/WULF/MBLY/TMUS)
    (2) PLAN_CREATED → WAITING → RECONCILE 뒤 종결 이벤트 없이 끊김 (CRM/GOOGL/BE/215790)
  즉 "왜 안 샀는가"가 원장에 남지 않는 경로가 존재한다. 차단 사유가 없으면
  퍼널 개선 대상도 정할 수 없으므로, 먼저 사멸을 보이게 만든다.

이 도구는 라이브 동작을 바꾸지 않는다. 이벤트 원장을 읽어 종결 상태를 분류하고
미해결 건을 출력할 뿐이다. 봇에 catch-all을 넣는 것은 별건이며 운영자 승인 사항이다.

사용
  python tools/trace_trade_ready_terminal.py --since 2026-07-13
  python tools/trace_trade_ready_terminal.py --since 2026-07-01 --unresolved-only --json
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_DB = ROOT / "data" / "v2_event_store.db"

# 종결로 인정하는 이벤트 (우선순위 순)
TERMINAL = [
    ("FILLED", "체결"),
    ("ORDER_SENT", "주문전송"),
    ("SAFETY_BLOCKED", "안전차단"),
    ("TRADE_READY_NO_SUBMIT", "미제출"),
    ("CLAUDE_PRICE_EXPIRED", "플랜만료"),
    ("CLAUDE_PRICE_CANCELLED", "플랜취소"),
]
# 종결이 아닌 후속 이벤트 (사후 측정·shadow — 이것만 있으면 '사멸'로 본다)
NON_TERMINAL = {
    "FORWARD_PENDING_DATA",
    "FORWARD_MEASURED",
    "PROFIT_EVIDENCE_SHADOW",
    "PATHB_SELECTION_RECONCILE",
    "QUALITY_MARKED",
    "CLAUDE_PRICE_PLAN_GATE_WARNING",
}


def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=10000")
    con.row_factory = sqlite3.Row
    return con


def load_events(con: sqlite3.Connection, since: str, until: str) -> dict[tuple, list[dict]]:
    rows = con.execute(
        """SELECT event_type, market, session_date, ticker, reason_code,
                  created_at, occurred_at, decision_id, payload_json
           FROM lifecycle_events
           WHERE substr(created_at,1,10) >= ? AND substr(created_at,1,10) <= ?
           ORDER BY created_at, event_id""",
        (since, until),
    ).fetchall()
    grouped: dict[tuple, list[dict]] = collections.defaultdict(list)
    for row in rows:
        key = (str(row["session_date"]), str(row["market"]), str(row["ticker"]))
        grouped[key].append(dict(row))
    return grouped


def classify(events: list[dict]) -> dict:
    """TRADE_READY 이후 이벤트로 종결 상태를 판정."""
    types = [e["event_type"] for e in events]
    for etype, label in TERMINAL:
        if etype in types:
            hit = next(e for e in events if e["event_type"] == etype)
            return {
                "resolved": True,
                "outcome": label,
                "event_type": etype,
                "reason_code": hit.get("reason_code"),
                "at": hit.get("created_at"),
            }
    # 종결 이벤트 없음 — 마지막으로 관측된 비종결 이벤트를 기록
    after = [e for e in events if e["event_type"] != "CLAUDE_TRADE_READY"]
    tail = after[-1] if after else None
    plan_made = "CLAUDE_PRICE_PLAN_CREATED" in types
    waiting = "CLAUDE_PRICE_WAITING" in types
    if plan_made or waiting:
        kind = "플랜생성후_종결없음"
    elif not after or all(e["event_type"] in NON_TERMINAL for e in after):
        kind = "TRADE_READY후_완전침묵"
    else:
        kind = "기타_종결없음"
    return {
        "resolved": False,
        "outcome": "★기록없이 소멸",
        "kind": kind,
        "last_event": tail["event_type"] if tail else None,
        "last_reason": tail.get("reason_code") if tail else None,
        "at": tail.get("created_at") if tail else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="TRADE_READY 종결 추적 (read-only)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--since", default="2026-07-13",
                    help="기본값은 elapsed 필드 도입 이후 구간")
    ap.add_argument("--until", default="2026-12-31")
    ap.add_argument("--unresolved-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"DB 없음: {db}")
        return 1
    con = connect(db)
    grouped = load_events(con, args.since, args.until)
    con.close()

    results = []
    for key, events in sorted(grouped.items()):
        if not any(e["event_type"] == "CLAUDE_TRADE_READY" for e in events):
            continue
        info = classify(events)
        info.update({"session_date": key[0], "market": key[1], "ticker": key[2]})
        results.append(info)

    if not results:
        print(f"{args.since}~{args.until} 구간에 CLAUDE_TRADE_READY 없음")
        return 0

    if args.json:
        payload = [r for r in results if not (args.unresolved_only and r["resolved"])]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    unresolved = [r for r in results if not r["resolved"]]
    print(f"TRADE_READY 종결 추적 — {args.since}~{args.until} (read-only)")
    print(f"  대상 {len(results)}종목 / 미해결 {len(unresolved)}건 "
          f"({len(unresolved)/len(results)*100:.1f}%)")

    print("\n" + "=" * 84)
    print("[1] 종결 분포")
    print("=" * 84)
    dist = collections.Counter(r["outcome"] for r in results)
    for outcome, n in dist.most_common():
        print(f"  {outcome:18s}{n:5d}건  ({n/len(results)*100:5.1f}%)")
    reasons = collections.Counter(
        str(r.get("reason_code")) for r in results
        if r["resolved"] and r.get("reason_code")
    )
    if reasons:
        print("\n  차단 사유코드:")
        for reason, n in reasons.most_common():
            print(f"    {reason:36s}{n:4d}건")

    print("\n" + "=" * 84)
    print("[2] ★기록 없이 사멸한 건 — 여기가 계측 공백")
    print("=" * 84)
    if not unresolved:
        print("  없음")
    else:
        kinds = collections.Counter(r["kind"] for r in unresolved)
        for kind, n in kinds.most_common():
            print(f"  유형 {kind:26s}{n:4d}건")
        print(f"\n  {'세션':11s}{'시장':4s}{'종목':9s}{'유형':26s}{'마지막 이벤트':28s}")
        for r in unresolved:
            print(f"  {r['session_date']:11s}{r['market']:4s}{r['ticker'][:9]:9s}"
                  f"{r['kind']:26s}{str(r.get('last_event')):28s}")

    if not args.unresolved_only:
        print("\n" + "=" * 84)
        print("[3] 종결된 건")
        print("=" * 84)
        print(f"  {'세션':11s}{'시장':4s}{'종목':9s}{'결과':10s}{'사유':32s}")
        for r in results:
            if not r["resolved"]:
                continue
            print(f"  {r['session_date']:11s}{r['market']:4s}{r['ticker'][:9]:9s}"
                  f"{r['outcome']:10s}{str(r.get('reason_code') or ''):32s}")

    print("\n※ 이 도구는 원장을 읽기만 한다. 봇에 catch-all 기록을 넣는 것은 별건이며,")
    print("  라이브 동작 변경이므로 운영자 승인 후 진행한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
