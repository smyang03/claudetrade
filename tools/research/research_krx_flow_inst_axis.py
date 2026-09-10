# -*- coding: utf-8 -*-
"""수급 축 — 기관 순매도가 개인보다 강했다 (2026-09-10, 부분 표본).

1차 검정(92세션)에서 강제매도 가설의 주역으로 본 **개인**은 유의하지 않았고(순매도 +0.23 t 0.53),
대조로 넣은 **기관 순매도 ≤−8%**가 +0.79(t 1.80, 종목 t 4.85)로 더 강했다.
여기서는 그 축을 특성화한다. 판정이 아니라 **다음 수집(나머지 213세션·OOS)에서 무엇을 볼지 정하기 위한 사전등록용**이다.

묻는 것
  1) 기관 축 사다리가 단조인가, 아니면 개인처럼 U자(=거래 쏠림 프록시)인가
  2) 이미 등록한 회피 규칙 R1(KOSPI MA20 위 급락 매수 금지)과 **겹치는가, 증분이 있는가**
  3) 갭 조건(v2 패닉 레인의 근거)과 독립인가
  4) 실행 형태(K=1/K=3)에서 살아남는가

⚠️ 표본: 305세션 중 92개(2025-06~2025-10). **2026(H2) 전무** — 인샘플 급락 수익이 몰린 구간이 통째로 빠져 있다. 판정 불가.
사용: python tools/research/research_krx_flow_inst_axis.py <flow_jsonl>
"""
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = f"file:{ROOT / 'data' / 'shadow' / 'virtual_books.db'}?mode=ro"


def cell(rows, label, indent="  "):
    if not rows:
        print(f"{indent}{label:34s} n 0")
        return None
    byd, byt = defaultdict(list), defaultdict(list)
    for d, tk, v in rows:
        byd[d].append(v)
        byt[tk].append(v)
    sm = [st.mean(v) for v in byd.values()]
    tm = [st.mean(v) for v in byt.values()]
    f = lambda xs: st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float("nan")
    print(f"{indent}{label:34s} n {len(rows):5d} 세션 {len(sm):3d} 세션평균 {st.mean(sm):+6.2f} t {f(sm):+5.2f} | "
          f"거래평균 {st.mean(v for _, _, v in rows):+6.2f} | 종목 t {f(tm):+5.2f} | 승 {100 * sum(v > 0 for _, _, v in rows) / len(rows):4.1f}%")
    return st.mean(sm)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    FLOW = {}
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("errors"):
            FLOW[r["date"]] = r["by"]

    with closing(sqlite3.connect(DB, uri=True, timeout=5)) as con:
        raw = con.execute(
            "select session_date, ticker, net_pct, meta from trades where strategy_id='xkr_fallen3' "
            "and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
    T = []
    for d, tk, net, meta in raw:
        if d not in FLOW:
            continue
        m = json.loads(meta) if meta else {}
        f = m.get("feat") or {}
        rg = m.get("regime") or {}
        dvol = (f.get("dvol") or 0) * 1e8
        if dvol <= 0:
            continue
        by = FLOW[d]
        T.append(dict(d=d, tk=tk, v=net, gap=f.get("gap"), above=rg.get("idx_above_ma20"),
                      indiv=by.get("개인", {}).get(tk, 0) / dvol * 100.0,
                      inst=by.get("기관합계", {}).get(tk, 0) / dvol * 100.0,
                      foreign=by.get("외국인", {}).get(tk, 0) / dvol * 100.0))
    R = lambda L: [(t["d"], t["tk"], t["v"]) for t in L]
    print(f"표본 {len(T)}건 / {len({t['d'] for t in T})}세션 ({min(t['d'] for t in T)}~{max(t['d'] for t in T)}) — 2026 없음, 판정 불가\n")
    base = cell(R(T), "전량(기저)")

    print("\n[1] 기관 순매수 비율 사다리 — 단조인가 U자인가")
    for lo, hi in ((-1e9, -20), (-20, -12), (-12, -8), (-8, -4), (-4, -1), (-1, 1), (1, 4), (4, 12), (12, 1e9)):
        cell(R([t for t in T if lo <= t["inst"] < hi]),
             f"기관 {lo if lo > -1e8 else '-inf'}~{hi if hi < 1e8 else '+inf'}%")

    print("\n[2] 기관 ≤−8% × 회피 규칙 R1(KOSPI MA20)")
    for ik, ifn in (("기관≤−8", lambda t: t["inst"] <= -8), ("기관>−8", lambda t: t["inst"] > -8)):
        for ak, afn in (("MA20 아래", lambda t: t["above"] is False), ("MA20 위", lambda t: t["above"] is True)):
            cell(R([t for t in T if ifn(t) and afn(t)]), f"{ik} × {ak}")
    print("   → R1이 이미 잡는 부분인지, 기관 축이 그 위에 증분을 주는지 본다")

    print("\n[3] 기관 ≤−8% × 갭 조건(v2 패닉 레인 근거)")
    for ik, ifn in (("기관≤−8", lambda t: t["inst"] <= -8), ("기관>−8", lambda t: t["inst"] > -8)):
        for gk, gfn in (("갭≤−3", lambda t: (t["gap"] or 0) <= -3), ("갭>−3", lambda t: (t["gap"] or 0) > -3)):
            cell(R([t for t in T if ifn(t) and gfn(t)]), f"{ik} × {gk}")

    print("\n[4] 세 수급 축 조합")
    cell(R([t for t in T if t["inst"] <= -8 and t["foreign"] >= 8]), "기관 던지고 외국인 받음")
    cell(R([t for t in T if t["inst"] <= -8 and t["indiv"] >= 8]), "기관 던지고 개인 받음")
    cell(R([t for t in T if t["inst"] <= -8 and t["indiv"] <= -8]), "기관·개인 동시 순매도")
    cell(R([t for t in T if t["inst"] >= 8 and t["indiv"] <= -8]), "개인 던지고 기관 받음(원 가설)")

    print("\n[5] 상위 2세션 제외 · 실행 형태")
    sel = [t for t in T if t["inst"] <= -8]
    byd = defaultdict(list)
    for t in sel:
        byd[t["d"]].append(t["v"])
    top2 = {d for d, _ in sorted(((d, st.mean(v)) for d, v in byd.items()), key=lambda x: -x[1])[:2]}
    cell(R([t for t in sel if t["d"] not in top2]), "기관≤−8, 상위2세션 제외")
    for k in (1, 3):
        bd = defaultdict(list)
        for t in T:
            bd[t["d"]].append(t)
        picks = [x for v in bd.values() for x in sorted(v, key=lambda y: y["inst"])[:k]]
        cell(R(picks), f"K={k} 기관 최대 순매도")
        picks2 = [x for v in bd.values() for x in v[:k]]
        cell(R(picks2), f"K={k} 전량 무조건(대조)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
