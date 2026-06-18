from __future__ import annotations

"""교훈 forward-validation 배치 실행 (수동/검증용).

실제 채점/누적 로직은 `minority_report.lesson_scoring.rescore_lessons`에 있고(세션마감 hook과 공용),
이 tool은 그걸 호출해 결과를 출력한다. read-only(원장) + 격리 store.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minority_report import lesson_scoring


def main() -> int:
    ap = argparse.ArgumentParser(description="교훈 forward-validation 배치")
    ap.add_argument("--store", default=str(ROOT / "data" / "lesson_validation.db"))
    ap.add_argument("--lesson", default="watch_only_missed_runup_ratio")
    args = ap.parse_args()

    cells = lesson_scoring.rescore_lessons(lesson_key=args.lesson, store_db=args.store)
    print(f"validated_lesson 채점 {len(cells)}셀 → {args.store}")
    order = {"valid_apply": 0, "marginal": 1, "invalid_block": 2, "neutral": 3,
             "pending": 4, "insufficient": 5}
    for c in sorted(cells, key=lambda x: (x["market"], order.get(x["verdict"], 9))):
        g_ = c["counterfactual_gain"]
        wb = c.get("would_be_med")
        print(f"  {c['market']}/{c['regime']:9} tr{c['n_tr']:>4}/wo{c['n_wo']:>5} "
              f"gain={'NA' if g_ is None else f'{g_:+.2f}':>6} "
              f"would_be={'NA' if wb is None else f'{wb:+.2f}':>6} "
              f"sess={c['sessions_confirmed']} conf={c['confidence']:.2f} → {c['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
