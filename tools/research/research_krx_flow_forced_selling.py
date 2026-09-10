# -*- coding: utf-8 -*-
"""강제매도 가설 — 수급으로 직접 검정 (2026-09-10, KRX 계정 해금 후).

09-10 오전까지는 가격 지문(갭다운·장중회복·연쇄하락·거래량)으로만 강제매도를 찍었고 **매수 신호로는 전부 죽었다**.
이제 그날 실제 수급을 본다.

가설
  H1 급락일에 **개인 순매도**  → 반대매매·신용청산 계열 강제 매도(정보 없는 매도) → 반등한다.
  H2 급락일에 **개인 순매수**  → 정보 있는 쪽(기관·외국인)이 던지고 개인이 받았다 → 계속 빠진다.
반대 방향(기관 순매도 = 펀드 환매·손절)도 같이 본다.

지표(전부 신호일 종가에 확정, no-lookahead)
  indiv_ratio = 개인 순매수거래대금 / 그날 그 종목 거래대금(원장 dvol, 억원 단위라 1e8 곱)
  inst_ratio / foreign_ratio 동일.
원장: virtual_books xkr_fallen3 백필(전일 ≤−3% KR 급락 풀), 계약 TP12/SL25/D7·KR 비용.
수급: research_krx_flow_collect.py가 쌓은 JSONL.

사용: python tools/research/research_krx_flow_forced_selling.py <flow_jsonl>
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
    """rows = [(session_date, ticker, net)]"""
    if not rows:
        print(f"{indent}{label:32s} n 0")
        return None
    byd, byt = defaultdict(list), defaultdict(list)
    for d, tk, v in rows:
        byd[d].append(v)
        byt[tk].append(v)
    sm = [st.mean(v) for v in byd.values()]
    tm = [st.mean(v) for v in byt.values()]
    f = lambda xs: st.mean(xs) / (st.pstdev(xs) / len(xs) ** 0.5) if len(xs) > 2 and st.pstdev(xs) else float("nan")
    print(f"{indent}{label:32s} n {len(rows):6d} 세션 {len(sm):3d} 세션평균 {st.mean(sm):+6.2f} t {f(sm):+5.2f} | "
          f"거래평균 {st.mean(v for _, _, v in rows):+6.2f} | 종목 t {f(tm):+5.2f} | 승 {100 * sum(v > 0 for _, _, v in rows) / len(rows):4.1f}%")
    return st.mean(sm)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    flow_path = Path(sys.argv[1])
    FLOW: dict[str, dict[str, dict[str, int]]] = {}
    for line in flow_path.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not r.get("errors"):
            FLOW[r["date"]] = r["by"]
    print(f"수급 세션 {len(FLOW)}일 ({min(FLOW)}~{max(FLOW)})\n")

    with closing(sqlite3.connect(DB, uri=True, timeout=5)) as con:
        raw = con.execute(
            "select session_date, ticker, net_pct, meta from trades where strategy_id='xkr_fallen3' "
            "and backfill=1 and status='CLOSED' and net_pct is not null").fetchall()
    T = []
    for d, tk, net, meta in raw:
        if d not in FLOW:
            continue
        f = (json.loads(meta) if meta else {}).get("feat") or {}
        dvol = (f.get("dvol") or 0) * 1e8          # 원장 dvol은 억원
        if dvol <= 0:
            continue
        by = FLOW[d]
        row = dict(d=d, tk=tk, v=net, gap=f.get("gap"), chg=f.get("chg"),
                   half="H1" if d < "2026-01-01" else "H2")
        for key, inv in (("indiv", "개인"), ("inst", "기관합계"), ("foreign", "외국인")):
            row[key] = (by.get(inv, {}).get(tk, 0)) / dvol * 100.0
        T.append(row)
    print(f"수급 결합된 급락 거래 {len(T)}건 / {len({t['d'] for t in T})}세션")
    if not T:
        return 1
    R = lambda L: [(t["d"], t["tk"], t["v"]) for t in L]
    cell(R(T), "전량(기저)")

    print("\n[1] 개인 순매수 비율 사다리 — 음수 = 개인이 던진 날(강제매도 가설의 핵심)")
    BINS = [(-1e9, -20), (-20, -8), (-8, -2), (-2, 2), (2, 8), (8, 20), (20, 1e9)]
    for lo, hi in BINS:
        lab = f"개인 {lo if lo > -1e8 else '-inf'}~{hi if hi < 1e8 else '+inf'}%"
        cell(R([t for t in T if lo <= t["indiv"] < hi]), lab)

    print("\n[2] 같은 사다리 — 기관합계 / 외국인 (대조)")
    for key, name in (("inst", "기관"), ("foreign", "외국인")):
        for lo, hi in ((-1e9, -8), (-8, -2), (-2, 2), (2, 8), (8, 1e9)):
            cell(R([t for t in T if lo <= t[key] < hi]), f"{name} {lo if lo > -1e8 else '-inf'}~{hi if hi < 1e8 else '+inf'}%")
        print()

    print("[3] 가설 직접 대비")
    cell(R([t for t in T if t["indiv"] < 0]), "H1 개인 순매도(강제매도 후보)")
    cell(R([t for t in T if t["indiv"] > 0]), "H2 개인 순매수(정보매도 후보)")
    cell(R([t for t in T if t["indiv"] < -5 and (t["inst"] + t["foreign"]) > 5]), "  개인 던지고 기관+외국인 받음")
    cell(R([t for t in T if t["indiv"] > 5 and (t["inst"] + t["foreign"]) < -5]), "  개인 받고 기관+외국인 던짐")

    print("\n[4] 반기 안정성 — 개인 순매도 vs 순매수")
    for h in ("H1", "H2"):
        cell(R([t for t in T if t["half"] == h and t["indiv"] < 0]), f"{h} 개인 순매도")
        cell(R([t for t in T if t["half"] == h and t["indiv"] > 0]), f"{h} 개인 순매수")

    print("\n[5] 상위 2세션 제외 (부호가 살아있는 쪽)")
    for lab, sel in (("개인 순매도", [t for t in T if t["indiv"] < 0]), ("개인 순매수", [t for t in T if t["indiv"] > 0])):
        byd = defaultdict(list)
        for t in sel:
            byd[t["d"]].append(t["v"])
        top2 = {d for d, _ in sorted(((d, st.mean(v)) for d, v in byd.items()), key=lambda x: -x[1])[:2]}
        cell(R([t for t in sel if t["d"] not in top2]), f"{lab}, 상위2세션 제외")

    print("\n[6] 실행 형태 — 세션당 K=1/K=3 (개인 순매도가 큰 순)")
    for k in (1, 3):
        for lab, fn, key in (("개인 최대 순매도", lambda t: t["indiv"] < 0, "indiv"),
                             ("전량 무조건(대조)", lambda t: True, None)):
            bd = defaultdict(list)
            for t in T:
                if fn(t):
                    bd[t["d"]].append(t)
            picks = [x for v in bd.values() for x in (sorted(v, key=lambda y: y["indiv"])[:k] if key else v[:k])]
            cell(R(picks), f"K={k} {lab}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
