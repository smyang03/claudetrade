"""재귀속으로 생긴 stale canonical 행 정리 (2026-08-22).

`reattach_sleeve_closed_to_entry.py`가 합성 CLOSED를 진입 decision_id로 옮기면,
sync가 진입 키로 **새 행**을 만든다. 그런데 구 합성 키 행은 upsert 대상이 아니라
그대로 남아 **같은 거래가 두 행**이 된다.

    live|US|2026-08-03|FRMI|dec_20260803_...   CLEAN  closed=1 filled=1  net 12.32   <- 정본
    live|US|2026-08-05|FRMI|sleeve_US_FRMI_...  DIRTY  closed=1 filled=0  net 12.32   <- stale

판정에서 중복 계산될 수 있으므로 지운다.

삭제 조건(전부 만족할 때만 — 안전하게 좁힌다):
  1. `v2_decision_id`가 `sleeve_`로 시작
  2. `filled=0` (진입 이벤트가 없는 고아 행)
  3. **같은 (market, ticker)에 filled=1인 CLEAN/SUSPECT 행이 존재** — 즉 정본이 이미 있다

3번이 핵심이다. 정본이 없으면 지우지 않는다 — 유일한 기록을 없애는 것이 최악이다.

사용:
  python tools/prune_stale_sleeve_canonical_rows.py --dry-run
  python tools/prune_stale_sleeve_canonical_rows.py
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ml" / "decisions.db"


def main() -> int:
    parser = argparse.ArgumentParser(description="stale sleeve canonical 행 정리")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not DB.exists():
        print(f"DB 없음: {DB}")
        return 1

    with closing(sqlite3.connect(DB, timeout=10)) as con:
        con.row_factory = sqlite3.Row
        stale = [dict(r) for r in con.execute(
            """SELECT canonical_key, market, ticker, session_date, v2_decision_id,
                      quality_grade, pnl_pct_net
               FROM v2_canonical_performance
               WHERE v2_decision_id LIKE 'sleeve_%' AND COALESCE(filled,0)=0
               ORDER BY session_date"""
        )]
        targets = []
        for row in stale:
            twin = con.execute(
                """SELECT canonical_key, session_date, quality_grade FROM v2_canonical_performance
                   WHERE market=? AND ticker=? AND COALESCE(filled,0)=1 AND COALESCE(closed,0)=1
                     AND v2_decision_id NOT LIKE 'sleeve_%'
                   ORDER BY session_date DESC LIMIT 1""",
                (row["market"], row["ticker"]),
            ).fetchone()
            if not twin:
                print(f"  [보존] {row['ticker']} {row['session_date']} — 정본 행이 없다")
                continue
            targets.append((row, dict(twin)))

        print(f"stale 후보 {len(stale)}건 / 삭제 대상 {len(targets)}건")
        for row, twin in targets:
            print(f"  {row['ticker']:6s} {row['session_date']} {row['quality_grade']:8s} "
                  f"net={row['pnl_pct_net']}  ->  정본 {twin['session_date']} {twin['quality_grade']}")

        if args.dry_run:
            print("\n[dry-run] 삭제하지 않았다.")
            return 0
        if not targets:
            print("삭제할 것이 없다.")
            return 0

        for row, _ in targets:
            con.execute("DELETE FROM v2_canonical_performance WHERE canonical_key=?",
                        (row["canonical_key"],))
        con.commit()
        print(f"\n삭제 완료 {len(targets)}건.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
