"""audit_candidate_rows.trainer_tier / cohort_reliability 백필 — 저장 payload의 runtime_gate에서.

왜: write 경로가 raw row["payload"]를 읽어 runtime_gate(trainer_tier 등)를 놓쳤다(2026-07-23
forward 수정 완료). 이미 저장된 payload_json에는 runtime_gate.trainer_tier가 60,533행 존재하나
컬럼은 0건이었다. 해석 함수(_candidate_extra_value)를 그대로 재사용해 저장 payload에서 복구한다.

--audit: 복구 가능 건수만 집계(쓰기 없음). --repair: 실제 UPDATE.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_audit_store import _candidate_extra_value

AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
COLUMNS = ("trainer_tier", "cohort_reliability")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repair", action="store_true", help="실제 UPDATE (기본은 audit only)")
    p.add_argument("--db", default=str(AUDIT_DB))
    args = p.parse_args()

    c = sqlite3.connect(args.db)
    c.execute("PRAGMA busy_timeout=10000")
    rows = c.execute(
        "SELECT rowid, candidate_key, payload_json, trainer_tier, cohort_reliability "
        "FROM audit_candidate_rows WHERE payload_json LIKE '%runtime_gate%'"
    ).fetchall()

    stats = {col: {"recoverable": 0, "written": 0} for col in COLUMNS}
    updates = []
    for rowid, key, pj, cur_tt, cur_cr in rows:
        try:
            payload = json.loads(pj)
        except Exception:
            continue
        # 해석 함수와 동일 경로: row에 payload만 실어 재사용
        view = {"payload": payload}
        current = {"trainer_tier": cur_tt, "cohort_reliability": cur_cr}
        row_updates = {}
        for col in COLUMNS:
            val = _candidate_extra_value(col, view)
            if val is None:
                continue
            stats[col]["recoverable"] += 1
            if current[col] is None:  # 비어 있던 것만 채운다(기존 값 보존)
                row_updates[col] = val
                stats[col]["written"] += 1
        if row_updates:
            updates.append((rowid, row_updates))

    print(f"runtime_gate 포함 행: {len(rows)}")
    for col in COLUMNS:
        print(f"  {col}: 복구가능 {stats[col]['recoverable']} · 신규기입 {stats[col]['written']}")

    if args.repair and updates:
        for rowid, ups in updates:
            set_clause = ", ".join(f"{k}=?" for k in ups)
            c.execute(
                f"UPDATE audit_candidate_rows SET {set_clause} WHERE rowid=?",
                [*ups.values(), rowid],
            )
        c.commit()
        print(f"★ UPDATE 커밋: {len(updates)}행")
    elif not args.repair:
        print("(audit only — 쓰기 없음. --repair로 실제 반영)")
    c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
