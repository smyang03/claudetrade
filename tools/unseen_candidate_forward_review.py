#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""안 본 후보 forward 판독 (2026-07-10, 후보 수량 설계) — read-only.

질문: "감시 밖(프롬프트 밖)·judge 캡이 버린 후보를 봤다면 돈이었나?"
= watch/judge 캡을 넓힐 실익이 있나. audit_candidate_rows(in_prompt) × outcomes(forward).

핵심 비교: in_prompt=1(우리가 본 것) vs in_prompt=0(안 본 것)의 forward 분포.
안 본 것이 본 것보다 유의 열위 → "상위만 봐도 됨"(넓혀도 실익 0).
동등 이상 → 넓힐 근거. no-lookahead(forward는 known_at 이후). 우리 net 아님 gross라 상대비교만.
"""
import argparse
import sqlite3
import statistics as st
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "audit" / "candidate_audit.db"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--horizon", type=int, default=1440, choices=[30, 60, 1440, 2880, 4320])
    args = ap.parse_args()

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    q = """SELECT r.market mk, substr(r.session_date,1,7) mon, r.in_prompt inp,
                  r.prompt_rank pr, o.return_pct ret
           FROM audit_candidate_rows r
           JOIN audit_candidate_outcomes o ON r.candidate_key=o.candidate_key
           WHERE o.horizon_min=? AND o.return_pct IS NOT NULL AND r.session_date>=?"""
    rows = list(c.execute(q, (args.horizon, args.since)))
    c.close()
    print(f"표본 {len(rows)} (horizon {args.horizon}m, {args.since}+, gross 상대비교)\n")

    def stat(vals):
        if not vals:
            return "n=0"
        w = sum(1 for x in vals if x > 0)
        return f"n={len(vals):6} 중앙={st.median(vals):+6.3f} 평균={st.mean(vals):+6.3f} 승={100*w/len(vals):4.1f}%"

    for mk in ("KR", "US"):
        seen = [r[4] for r in rows if r[0] == mk and r[2] == 1]         # in_prompt=1 (본 것)
        unseen = [r[4] for r in rows if r[0] == mk and r[2] == 0]       # in_prompt=0 (안 본 것)
        # 감시 경계 근처: prompt_rank 28~50 (넓히면 새로 들어올 구간)
        edge = [r[4] for r in rows if r[0] == mk and r[2] == 0 and r[3] and 28 <= r[3] <= 50]
        print(f"=== {mk} ===")
        print(f"  본 것(in_prompt):   {stat(seen)}")
        print(f"  안 본 것(밖):        {stat(unseen)}")
        print(f"  경계 rank28~50:      {stat(edge)}")
        # 월별 안 본 것 (국면 confound 확인)
        bym = defaultdict(list)
        for r in rows:
            if r[0] == mk and r[2] == 0:
                bym[r[1]].append(r[4])
        md = " / ".join(f"{m}:{st.median(v):+.2f}(n{len(v)})" for m, v in sorted(bym.items()) if len(v) >= 20)
        print(f"  안본것 월별중앙:     {md}\n")

    print("판정: '안 본 것'이 '본 것'보다 유의 열위+월별 일관 → 상위만 봐도 됨(넓혀도 실익0).")
    print("      경계 rank28~50이 본 것과 동등+양수 → watch 확대 근거. gross라 방향만, net은 forward 별도.")


if __name__ == "__main__":
    main()
