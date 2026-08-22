"""합성 ID CLOSED를 진입 decision_id로 재귀속 (2026-08-22).

배경 — 08-21 `3800170` 이전의 sleeve 청산은 합성 ID(`sleeve_{market}_{TICKER}_{YYYYMMDD}`)로
발행됐다. 그 결과 CLOSED만 홀로 있는 고아 행이 되어 `CLOSED_WITHOUT_FILL` → DIRTY다.

    sleeve_US_FRMI_20260805  ['CLOSED']  -> CLOSED_WITHOUT_FILL
    sleeve_US_CVI_20260810   ['CLOSED']  -> CLOSED_WITHOUT_FILL
    ...

진입행은 멀쩡히 있다(`dec_20260803_US_FRMI_3df34b3d` 등). 둘을 이어 붙이면
ORDER_SENT + FILLED + FORWARD_MEASURED + CLOSED가 한 묶음이 되어 등급이 정상화된다.

매칭 규약 — **같은 (market, ticker)의 CLOSED 직전 FILLED**. 재매수 종목(MXL)도
event_id 순서로 라운드가 갈린다. 매칭 실패 시 건너뛴다(추측하지 않는다).

멱등: 이미 `sleeve_`가 아닌 decision_id면 대상이 아니다.

사용:
  python tools/reattach_sleeve_closed_to_entry.py --dry-run
  python tools/reattach_sleeve_closed_to_entry.py
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "v2_event_store.db"
MARKER = "tools/reattach_sleeve_closed_to_entry.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="합성 CLOSED를 진입 decision_id로 재귀속")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DB.exists():
        print(f"이벤트 스토어 없음: {DB}")
        return 1

    with closing(sqlite3.connect(DB, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            """SELECT event_id, decision_id, market, ticker, occurred_at, payload_json
               FROM lifecycle_events
               WHERE event_type='CLOSED' AND decision_id LIKE 'sleeve_%'
               ORDER BY event_id"""
        )]
        plan = []
        for r in rows:
            entry = con.execute(
                """SELECT decision_id FROM lifecycle_events
                   WHERE event_type='FILLED' AND market=? AND ticker=? AND event_id<?
                     AND decision_id IS NOT NULL AND decision_id<>''
                   ORDER BY event_id DESC LIMIT 1""",
                (r["market"], r["ticker"], r["event_id"]),
            ).fetchone()
            if not entry or str(entry["decision_id"]).startswith("sleeve_"):
                print(f"  [건너뜀] {r['ticker']} {str(r['occurred_at'])[:10]} — 진입행 없음")
                continue
            plan.append((r, str(entry["decision_id"])))

        print(f"재귀속 대상 {len(plan)}건 / 합성 CLOSED {len(rows)}건")
        for r, entry_id in plan:
            print(f"  {r['ticker']:6s} {str(r['occurred_at'])[:10]}  {r['decision_id']} -> {entry_id}")

        if args.dry_run:
            print("\n[dry-run] 변경하지 않았다.")
            return 0
        if not plan:
            print("변경할 것이 없다.")
            return 0

        for r, entry_id in plan:
            try:
                payload = json.loads(r.get("payload_json") or "{}")
            except ValueError:
                payload = {}
            # 원래 합성 ID를 남겨 추적 가능하게 한다 — 되돌릴 때와 감사 때 필요하다.
            payload["reattached_from_synthetic_id"] = r["decision_id"]
            payload["reattached_by"] = MARKER
            con.execute(
                "UPDATE lifecycle_events SET decision_id=?, payload_json=? WHERE event_id=?",
                (entry_id, json.dumps(payload, ensure_ascii=False), r["event_id"]),
            )
        con.commit()
        print(f"\n재귀속 완료 {len(plan)}건. sync_v2_learning_performance를 다시 돌린다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
