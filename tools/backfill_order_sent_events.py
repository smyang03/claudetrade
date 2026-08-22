"""ORDER_SENT lifecycle 이벤트 소급 주입 (라이브 코드 무접촉, 2026-08-22).

배경 — `lifecycle/quality.py:20`이 **FILLED은 있는데 ORDER_SENT가 없으면 DIRTY**로
판정한다. 그런데 이 이벤트를 발행하는 코드가 한 곳도 없었다(08-01 이후 실측:
FILLED 11 · CLOSED 5 · **ORDER_SENT 0**). 결과로 코호트 전건이
`FILLED_WITHOUT_ORDER_SENT` → `learning_allowed=0`이 되어, **30건이 다 차도
학습·판정에 쓸 수 있는 건이 0건**이었다.

라이브 발행은 `trading_bot._emit_order_sent_event`(`_add_pending_order` 훅)로 고쳤다.
이 도구는 **그 수리 이전의 과거 FILLED**에 짝이 되는 ORDER_SENT를 소급 주입한다.

규약:
  - **FILLED이 있는 decision_id만** 대상. 주문이 실제로 나갔다는 증거가 FILLED이므로
    사실을 만들어내지 않는다(없던 주문을 지어내지 않는다).
  - 이미 ORDER_SENT가 있으면 건너뛴다(멱등).
  - occurred_at은 해당 FILLED보다 1초 앞선 시각. 순서가 뒤집히면 품질 판정이 아니라
    타임라인이 거짓말을 한다.
  - payload에 `backfilled_by`를 박아 라이브 발행분과 구분한다.

사용:
  python tools/backfill_order_sent_events.py --dry-run   # 대상만 표시
  python tools/backfill_order_sent_events.py             # 실제 주입
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "v2_event_store.db"
MARKER = "tools/backfill_order_sent_events.py"


def _shift_1s(ts: str) -> str:
    """FILLED보다 1초 앞선 시각. 파싱 실패 시 원본 유지(순서만 잃고 사실은 안 바뀐다)."""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt - timedelta(seconds=1)).isoformat()
    except ValueError:
        return str(ts)


def main() -> int:
    parser = argparse.ArgumentParser(description="ORDER_SENT 소급 주입")
    parser.add_argument("--dry-run", action="store_true", help="주입 대상만 표시")
    parser.add_argument("--since", default="2026-08-01", help="이 날짜 이후 FILLED만 (기본 2026-08-01)")
    args = parser.parse_args()

    if not DB.exists():
        print(f"이벤트 스토어 없음: {DB}")
        return 1

    with closing(sqlite3.connect(DB, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        fills = [dict(r) for r in con.execute(
            """SELECT event_id, event_uuid, event_type, market, runtime_mode, session_date,
                      ticker, decision_id, execution_id, position_id, prompt_version,
                      brain_snapshot_id, occurred_at, payload_json
               FROM lifecycle_events
               WHERE event_type='FILLED' AND occurred_at >= ?
               ORDER BY event_id""",
            (args.since,),
        )]
        have = {str(r[0]) for r in con.execute(
            "SELECT DISTINCT decision_id FROM lifecycle_events WHERE event_type='ORDER_SENT'"
        ) if r[0]}

        targets = [f for f in fills if str(f.get("decision_id") or "") and str(f["decision_id"]) not in have]
        print(f"FILLED {len(fills)}건 / ORDER_SENT 기보유 {len(have)}건 → 주입 대상 {len(targets)}건")
        for f in targets:
            print(f"  {str(f['occurred_at'])[:19]} {f['market']} {f['ticker']:6s} {f['decision_id']}")

        if args.dry_run:
            print("\n[dry-run] 주입하지 않았다.")
            return 0
        if not targets:
            print("주입할 것이 없다.")
            return 0

        inserted = 0
        for f in targets:
            src = {}
            try:
                src = json.loads(f.get("payload_json") or "{}")
            except ValueError:
                src = {}
            payload = {
                "order_no": src.get("order_no", ""),
                "qty": src.get("qty"),
                "backfilled_by": MARKER,
                "backfill_note": (
                    "ORDER_SENT를 발행하는 코드가 없어 FILLED만 남았다(08-22 수리 이전분). "
                    "FILLED이 있는 건에 한해 짝을 소급 주입한다."
                ),
                "emitted_by": "backfill",
            }
            con.execute(
                """INSERT INTO lifecycle_events
                   (event_uuid, event_type, market, runtime_mode, session_date, ticker,
                    decision_id, execution_id, position_id, prompt_version, brain_snapshot_id,
                    occurred_at, reason_code, data_quality, payload_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"backfill-order-sent-{f['event_id']}",
                    "ORDER_SENT", f["market"], f["runtime_mode"], f["session_date"], f["ticker"],
                    f["decision_id"], f["execution_id"], f["position_id"],
                    f["prompt_version"], f["brain_snapshot_id"],
                    _shift_1s(f["occurred_at"]), "", "",
                    json.dumps(payload, ensure_ascii=False),
                    datetime.now().astimezone().isoformat(timespec="seconds"),
                ),
            )
            inserted += 1
        con.commit()
        print(f"\n주입 완료 {inserted}건. sync_v2_learning_performance를 다시 돌리면 등급이 갱신된다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
