"""시뮬레이터가 라이브로 위장해 남긴 행에 `is_simulated=1`을 찍는다 (2026-08-24).

사고: `tools/sim_entry_path_gates.py` / `sim_exit_path_gates.py`가 게이트를 실행해보려고
가짜 티커 `SIMTK`로 봇 경로를 태웠고, 그 행이 `decisions`에 **`data_source='live'`,
`is_simulated=0`** 으로 들어갔다(2026-07-29 하루, US 787 · KR 48 = 835행).
2026-07-30에 원장 오염으로 식별됐지만 데이터는 그대로 남아 있었다.

`is_simulated`는 여러 소비자의 **제외 필터**다 — dashboard digest, `ml/db_health`,
`ml/analyze_features`, `ml/db_writer`. 그래서 이 835행은 지금도 **실거래로 집계된다**
(US 실거래 116,021행 중 787 = 0.68%). 필드를 올바르게 찍는 것이 정정이다.

**삭제하지 않는다.** 오염 사고 자체가 기록으로 남아 있어야 하고, `is_simulated`는
바로 이런 행을 표시하려고 있는 필드다. 지우면 "왜 07-29에 구멍이 있나"를 나중에
설명할 수 없다.

안전 장치:
  · 조건이 전부 만족될 때만 찍는다 — 티커가 SIMTK 계열이고, **체결이 하나도 없다**.
    실제 체결이 섞여 있으면 시뮬 행이 아니므로 건드리지 않고 중단한다.
  · 멱등 — 이미 찍힌 행은 다시 세지 않는다.
  · --dry-run이 기본 검수 경로다.

사용:
  python tools/mark_simulator_rows.py --dry-run
  python tools/mark_simulator_rows.py
"""

from __future__ import annotations

import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "ml" / "decisions.db"
# 시뮬 하네스가 쓰는 가짜 티커. sim_entry_path_gates는 SIMTK / SIMTK1..N을 쓴다.
SIM_TICKER_PATTERN = "SIMTK%"


def main() -> int:
    parser = argparse.ArgumentParser(description="시뮬 위장 행에 is_simulated=1 표시")
    parser.add_argument("--dry-run", action="store_true", help="변경 없이 대상만 출력")
    args = parser.parse_args()

    if not DB.exists():
        print(f"DB 없음: {DB}")
        return 1

    with closing(sqlite3.connect(DB, timeout=10)) as con:
        con.row_factory = sqlite3.Row

        total = con.execute(
            "SELECT COUNT(*) FROM decisions WHERE ticker LIKE ?", (SIM_TICKER_PATTERN,)
        ).fetchone()[0]
        if not total:
            print("SIMTK 행 없음 — 할 일 없음")
            return 0

        # 안전 게이트: 체결이 하나라도 있으면 시뮬이 아니다. 중단한다.
        filled = con.execute(
            "SELECT COUNT(*) FROM decisions WHERE ticker LIKE ? AND COALESCE(filled,0)=1",
            (SIM_TICKER_PATTERN,),
        ).fetchone()[0]
        if filled:
            print(f"중단: SIMTK 행에 체결 {filled}건이 있다 — 시뮬 행이 아닐 수 있으므로 건드리지 않는다")
            return 1

        targets = [dict(r) for r in con.execute(
            """SELECT market, session_date, COUNT(*) n
               FROM decisions
               WHERE ticker LIKE ? AND COALESCE(is_simulated,0)=0
               GROUP BY market, session_date ORDER BY session_date, market""",
            (SIM_TICKER_PATTERN,),
        )]
        pending = sum(t["n"] for t in targets)

        print(f"SIMTK 전체 {total}행 (체결 0건 확인) | 표시 필요 {pending}행")
        for t in targets:
            print(f"  {t['session_date']} {t['market']}: {t['n']}행")
        if not pending:
            print("이미 전부 표시됨 — 변경 없음(멱등)")
            return 0

        if args.dry_run:
            print("[DRY-RUN] 변경하지 않음")
            return 0

        with con:
            cur = con.execute(
                """UPDATE decisions SET is_simulated=1
                   WHERE ticker LIKE ? AND COALESCE(is_simulated,0)=0""",
                (SIM_TICKER_PATTERN,),
            )
        print(f"표시 완료: {cur.rowcount}행 -> is_simulated=1")

        remaining = con.execute(
            "SELECT COUNT(*) FROM decisions WHERE ticker LIKE ? AND COALESCE(is_simulated,0)=0",
            (SIM_TICKER_PATTERN,),
        ).fetchone()[0]
        print(f"검증: 미표시 잔여 {remaining}행")
        return 0 if remaining == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
