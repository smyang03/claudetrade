from __future__ import annotations

"""레거시 미귀속 selection 행 확정 마킹 (운영자 승인 2026-08-13).

대상: bot_mode='live'·traded=1인데 execution_decision_id가 빈 행.
실측(2026-08-13): 전량 2026-04-16~05-12(23건)의 재구성 이전 레거시 매수 경로
(momentum/gap_pullback/RECOVERY_MICRO × rescreen/preopen_watch/partial 등).
교정판 backfill(tools/backfill_candidate_fill_attribution.py)도 route 불일치로
귀속하지 못한 건이라, 억지 귀속 대신 execution_reason='legacy_unattributed_final'로
확정 마킹해 결손 경보(preflight)·감사(issues)에서 제외한다. id 컬럼은 비워 둔다 —
가짜 id로 linked를 부풀리지 않는다.

안전장치: 날짜 상한(--max-date, 기본 2026-05-31) 밖의 결손은 건드리지 않는다.
그 이후 결손은 레거시가 아니라 현행 경로의 회귀이므로 경보로 남아야 한다.

  python tools/mark_legacy_unattributed_selection.py            (dry-run)
  python tools/mark_legacy_unattributed_selection.py --apply
"""

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ticker_selection_log.db"
MARKER = "legacy_unattributed_final"
DEFAULT_MAX_DATE = "2026-05-31"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="실제 UPDATE 실행 (기본: dry-run)")
    parser.add_argument("--max-date", default=DEFAULT_MAX_DATE, help="이 날짜 이후 결손은 마킹하지 않음")
    args = parser.parse_args()

    if not DB.exists():
        print(f"DB 없음: {DB}")
        return 1

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    try:
        where = (
            "bot_mode='live' AND traded=1 "
            "AND (execution_decision_id IS NULL OR TRIM(execution_decision_id)='') "
            "AND COALESCE(execution_reason,'')!=? AND date<=?"
        )
        rows = conn.execute(
            f"SELECT id, date, market, ticker, strategy_name FROM ticker_selection_log WHERE {where} ORDER BY date",
            (MARKER, args.max_date),
        ).fetchall()
        print(f"마킹 대상 {len(rows)}건 (date<={args.max_date}):")
        for r in rows:
            print(f"  id={r['id']} {r['date']} {r['market']} {r['ticker']} strat={r['strategy_name']}")

        leftover = conn.execute(
            "SELECT COUNT(*) FROM ticker_selection_log "
            "WHERE bot_mode='live' AND traded=1 "
            "AND (execution_decision_id IS NULL OR TRIM(execution_decision_id)='') "
            "AND COALESCE(execution_reason,'')!=? AND date>?",
            (MARKER, args.max_date),
        ).fetchone()[0]
        if leftover:
            print(f"⚠ 상한 이후 결손 {leftover}건은 마킹하지 않음(현행 경로 회귀 후보 — 별도 조사)")

        if not args.apply:
            print("dry-run — 변경 없음. 실행하려면 --apply")
            return 0
        if not rows:
            print("마킹할 행 없음")
            return 0
        cur = conn.execute(
            f"UPDATE ticker_selection_log SET execution_reason=? WHERE {where}",
            (MARKER, MARKER, args.max_date),
        )
        conn.commit()
        print(f"UPDATE 완료: {cur.rowcount}건 → execution_reason='{MARKER}'")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    main()
