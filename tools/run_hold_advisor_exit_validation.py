"""hold advisor profit_guard 청산 교훈 forward-validation 실행 (#4a).

수집(decisions+v2) + yfinance forward 백필 + score_cell 채점 + store upsert를 한 번에.
수동/cron 주기 실행용. read-only(decisions.db) + 격리 store(validated_lesson). 봇 무관.

config로 동작:
- 봇 세션마감 hook은 토글 `HOLD_ADVISOR_EXIT_LESSON_ENABLED=true`일 때 rescore(라벨 읽기)만 자동.
- 이 도구는 forward 라벨(yfinance)을 공급한다(무거워서 봇 루프에 안 넣음). 주기 실행 권장.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minority_report import hold_advisor_exit_lessons as h


def main() -> int:
    ap = argparse.ArgumentParser(description="hold advisor profit_guard 청산 forward-validation")
    ap.add_argument("--forward-days", type=int, default=h.DEFAULT_FORWARD_DAYS)
    ap.add_argument("--store", default=None, help="validated_lesson store db (기본 표준 경로)")
    args = ap.parse_args()

    res = h.run_all(store_db=args.store, forward_days=args.forward_days)
    print(f"[hold_advisor_exit_validation] collected={res['collected']} "
          f"forward_filled={res['forward_filled']} cells={res['cells']}")

    # verdict 요약 (성숙 셀)
    cells = h.rescore(store_db=args.store)
    if cells:
        print("# (market, regime) verdict — gain>0=익절(SELL)>HOLD=profit_guard valid / gain<0=조기절단")
        for c in sorted(cells, key=lambda x: (x.get("market", ""), x.get("regime", ""))):
            print(f"  {c.get('market'):3} {str(c.get('regime')):10} "
                  f"verdict={c.get('verdict'):14} gain={c.get('counterfactual_gain')} "
                  f"n_sell={c.get('n_would_be', c.get('n_wo'))} n_hold={c.get('n_actual', c.get('n_tr'))}")
    else:
        print("# 성숙 셀 없음 — forward(SELL 후 3거래일) 미성숙 또는 표본 부족. 며칠 후 재실행.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
