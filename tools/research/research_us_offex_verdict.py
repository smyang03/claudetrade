# -*- coding: utf-8 -*-
"""US 장외 거래비중 축 — 사전등록 규칙 판정 (2026-09-11).

사전등록: `docs/reports/preregistration_us_offexchange_share_20260911.md` (데이터 보기 전 고정)
  모집단  : Alpaca SIP 2023-01~2025-04, 전일 대비 ≤−5% & 전일 거래대금 ≥$50M
  지표    : off_ex_share = FINRA TotalVolume(신호일) / Alpaca volume(신호일) × 100
  진입/출구: 다음 세션 시가, TP12 / SL25 / D7, **US BE락 4%**, 비용 0.50
  선별    : 세션당 off_ex_share 최상위 3종목(K=3)

판정선(단측, 넷 다 통과해야 후보)
  ① 상위 3분위 세션평균 > 하위 3분위 세션평균
  ② 상위 3분위 초과(무조건 급락 매수 대비) 세션 t ≥ 2.0
  ③ K=3 세션평균 > 0 및 전량 대조 대비 증분 > 0
  ④ 2023 / 2024 / 2025 세 구간 부호 동일

사용: python tools/research/research_us_offex_verdict.py <finra_jsonl>
"""
from __future__ import annotations

import csv
import json
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from virtual_books import contract_exit_v2, FEE_US, HOLD_SESSIONS  # noqa: E402

ALPACA = ROOT / "data" / "price_backfill_alpaca" / "us"
K = 3


def sess(rows):
    if not rows:
        return None
    byd = defaultdict(list)
    for d, _, v in rows:
        byd[d].append(v)
    sm = [st.mean(v) for v in byd.values()]
    t = st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5) if len(sm) > 2 and st.pstdev(sm) else float("nan")
    return dict(n=len(rows), sessions=len(sm), mean=st.mean(sm), t=t,
                trade=st.mean(v for _, _, v in rows),
                win=100 * sum(v > 0 for _, _, v in rows) / len(rows))


def show(rows, label, indent="  "):
    s = sess(rows)
    if not s:
        print(f"{indent}{label:34s} n 0")
        return None
    print(f"{indent}{label:34s} n {s['n']:5d} 세션 {s['sessions']:3d} 세션평균 {s['mean']:+6.2f} t {s['t']:+5.2f} "
          f"| 거래평균 {s['trade']:+6.2f} | 승 {s['win']:4.1f}%")
    return s


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    FIN: dict[str, dict[str, list[float]]] = {}
    for line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("rows"):
            FIN[r["date"]] = r["rows"]
    print(f"FINRA 장외 {len(FIN)}일 ({min(FIN)}~{max(FIN)})")

    T = []
    for p in ALPACA.glob("us_*.csv"):
        sym = p.stem[3:]
        b = []
        for r in csv.DictReader(p.open(encoding="utf-8-sig")):
            try:
                b.append((r["date"][:10], float(r["open"]), float(r["high"]), float(r["low"]),
                          float(r["close"]), float(r["volume"] or 0)))
            except (ValueError, KeyError):
                continue
        b.sort()
        for i in range(1, len(b) - HOLD_SESSIONS - 1):
            d, o, h, l, c, v = b[i]
            pc, pv = b[i - 1][4], b[i - 1][5]
            if pc <= 0 or c <= 0 or v <= 0 or d not in FIN:
                continue
            if (c / pc - 1) * 100 > -5 or pc * pv < 5e7:
                continue
            fin = FIN[d].get(sym)
            if not fin or not fin[1]:
                continue
            share = fin[1] / v * 100.0
            if not (0 < share <= 100):      # 분모 불일치(수정주가·거래소 차이)로 100% 넘는 건 버린다
                continue
            res = contract_exit_v2(b[i + 1][1], b[i + 1:i + 1 + HOLD_SESSIONS], fee=FEE_US, be_lock=True)
            if res:
                T.append(dict(d=d, tk=sym, v=res[0], share=share, yr=d[:4]))
    if not T:
        print("표본 없음")
        return 1
    R = lambda L: [(x["d"], x["tk"], x["v"]) for x in L]
    print(f"급락 표본 {len(T)}건 / {len({x['d'] for x in T})}세션 · 장외비중 중앙 {st.median(x['share'] for x in T):.1f}%\n")
    base = show(R(T), "전량(무조건 급락 매수)")

    shares = sorted(x["share"] for x in T)
    q1, q2 = shares[len(shares) // 3], shares[2 * len(shares) // 3]
    print(f"\n[1] 장외비중 3분위 (경계 {q1:.1f}% / {q2:.1f}%)")
    lo = show(R([x for x in T if x["share"] <= q1]), "하위 3분위(장외 낮음)")
    mid = show(R([x for x in T if q1 < x["share"] <= q2]), "중간 3분위")
    hi = show(R([x for x in T if x["share"] > q2]), "상위 3분위(장외 높음)")

    print("\n[2] 더 촘촘한 사다리")
    for a, b_ in ((0, 20), (20, 30), (30, 40), (40, 50), (50, 60), (60, 101)):
        show(R([x for x in T if a <= x["share"] < b_]), f"장외 {a}~{b_}%")

    print("\n[3] 실행 형태 — 세션당 장외비중 최상위")
    bd = defaultdict(list)
    for x in T:
        bd[x["d"]].append(x)
    picks = [y for v in bd.values() for y in sorted(v, key=lambda z: -z["share"])[:K]]
    k3 = show(R(picks), f"★ 규칙 K={K} (장외 최상위 {K})")
    ctrl = [y for v in bd.values() for y in v[:K]]
    c3 = show(R(ctrl), f"K={K} 전량 대조")
    show(R([sorted(v, key=lambda z: -z["share"])[0] for v in bd.values()]), "K=1 (부수)")

    print("\n[4] 연도별 상위 3분위 부호")
    yr_ok = []
    for y in ("2023", "2024", "2025"):
        s = show(R([x for x in T if x["yr"] == y and x["share"] > q2]), f"{y} 상위 3분위")
        if s:
            yr_ok.append(s["mean"] > 0)

    print("\n" + "=" * 78)
    print("판정 (사전등록 4항목, 단측)")
    c1 = bool(hi and lo and hi["mean"] > lo["mean"])
    ex = [v - base["mean"] for _, _, v in R([x for x in T if x["share"] > q2])] if (hi and base) else []
    t_ex = (st.mean(ex) / (st.pstdev(ex) / len(ex) ** 0.5)) if len(ex) > 2 and st.pstdev(ex) else float("nan")
    c2 = bool(t_ex >= 2.0)
    c3_ok = bool(k3 and c3 and k3["mean"] > 0 and k3["mean"] > c3["mean"])
    c4 = bool(yr_ok) and (all(yr_ok) or not any(yr_ok))
    print(f"  ① 상위>하위          : {'통과' if c1 else '불통과'}" + (f" ({hi['mean']:+.2f} vs {lo['mean']:+.2f})" if hi and lo else ""))
    print(f"  ② 초과 t ≥ 2.0       : {'통과' if c2 else '불통과'} (t {t_ex:+.2f})")
    print(f"  ③ K={K} > 0 & 증분>0  : {'통과' if c3_ok else '불통과'}" + (f" ({k3['mean']:+.2f} vs 대조 {c3['mean']:+.2f})" if k3 and c3 else ""))
    print(f"  ④ 연도 부호 동일      : {'통과' if c4 else '불통과'} ({yr_ok})")
    print(f"\n  최종: {'후보 — 쉐도우 등록 검토' if all((c1, c2, c3_ok, c4)) else '기각 — 사전등록 판정선 미달'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
