#!/usr/bin/env python3
"""변동성 타겟 지수 노출 — "장이 움직일 때 벌되, 폭풍 때는 줄인다" (read-only, 2026-09-24).

## 왜 이 축인가
- 09-24 실거래 정본: 5개월 gross ≈ 0, 손실 −63만 = 비용 64만. 산 종목이 장과 따로 움직였다.
- 07-07 연구: 저회전 패시브 노출이 우리 능동매매를 이겼다(단 그 수익은 베타). TSMOM(추세 필터)은 기각.
- **변동성 타겟은 한 번도 시험하지 않았다.** 방향 예측이 아니라 "위험 크기"로 노출을 조절한다.
  문헌: Moreira & Muir(2017) 시장 포트폴리오 샤프 개선 / Cederburg 외(2020) OOS에서 약하다는 반론.

## 사전 등록 규칙 (실행 전 고정 — 결과 보고 바꾸지 않는다)
- 상품: KR 069500(1배) + 122630(2배, 2010-02~) · US SPY(1배) + SSO(2배, 2006-06~).
  실제 ETF 수정주가(보수·레버리지 감쇠·운용 비용 포함).
- 신호: 1배 ETF 일간수익 20일 실현변동성 σ20(연환산), 목표 σ* = **그 시점까지의 누적(expanding) 일간 변동성**(lookahead 없음, 최소 250일).
- 목표 노출 e = clip(σ*/σ20, 0, 2). e ≤ 1 → 1배에 e, 나머지 현금 · e > 1 → 1배 (2−e) + 2배 (e−1).
- 체결: t일 종가로 신호 → **t+1일 종가**에 체결(하루 지연, 보수적).
- 리밸런스: 주 1회(주 마지막 거래일 신호) · |e목표 − e현재| > 0.25일 때만.
- 비용: 거래 명목 1원당 KR 0.10% · US 0.25%(편도, 슬리피지 포함 보수값). 현금 이자 0(보수) — US는 T-bill 변형 병기.
- 비교: (a) 1배 보유 (b) **평균 노출을 맞춘 고정 비중**(= 타이밍 없이 레버리지만 같게) — 이걸 이겨야 "조절"이 값을 더한 것.

## 통과 기준 (사전 등록)
1. 샤프(rf 0)가 (a)·(b) 둘 다보다 높다 — **KR·US 두 시장 모두**.
2. 전·후반기(기간 절반) 각각에서 (b) 대비 샤프 우위 부호 유지.
3. 최대낙폭이 (a)보다 얕다.
하나라도 실패하면 "기각" 또는 "부분"으로 적는다. 연도별 표는 판정이 아니라 귀속용.
"""
from __future__ import annotations

import csv
import math
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "analysis" / "voltarget_20260924"
OUT = ROOT / "docs" / "reports" / "voltarget_20260924.md"
MARKETS = {"KR": ("069500.KS", "122630.KS", 0.10), "US": ("SPY", "SSO", 0.25)}
LB, MIN_HIST, BAND, EMAX = 20, 250, 0.25, 2.0


def load(name):
    rows = {}
    with (D / f"{name}.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                c = float(r["Close"])
            except (TypeError, ValueError):
                continue
            if c > 0:
                rows[r[next(iter(r))][:10]] = c
    return rows


def stats(nav, dates):
    rets = [nav[i] / nav[i - 1] - 1 for i in range(1, len(nav))]
    yrs = len(rets) / 252
    cagr = (nav[-1] / nav[0]) ** (1 / yrs) - 1 if yrs > 0 else 0
    vol = st.pstdev(rets) * math.sqrt(252)
    sh = (st.mean(rets) * 252) / vol if vol else 0
    peak, mdd = nav[0], 0.0
    for v in nav:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return {"cagr": cagr * 100, "vol": vol * 100, "sharpe": sh, "mdd": mdd * 100,
            "calmar": (cagr / -mdd) if mdd < 0 else 0}


def run(mk, rf=None):
    t1, t2, cost = MARKETS[mk]
    p1, p2 = load(t1), load(t2)
    dates = sorted(set(p1) & set(p2))
    r1 = {dates[i]: p1[dates[i]] / p1[dates[i - 1]] - 1 for i in range(1, len(dates))}
    r2 = {dates[i]: p2[dates[i]] / p2[dates[i - 1]] - 1 for i in range(1, len(dates))}
    # 1배 전체 이력으로 누적 변동성(2배 상장 전 이력 포함 — 신호용, lookahead 아님)
    all1 = sorted(p1)
    ret1_all = [(all1[i], p1[all1[i]] / p1[all1[i - 1]] - 1) for i in range(1, len(all1))]
    pos = {d: i for i, (d, _) in enumerate(ret1_all)}
    sq_cum = [0.0]
    for _, r in ret1_all:
        sq_cum.append(sq_cum[-1] + r * r)

    def signal(d):
        i = pos.get(d)
        if i is None or i < MIN_HIST:
            return None
        rv = st.pstdev([x for _, x in ret1_all[i - LB + 1: i + 1]])
        lr = math.sqrt(sq_cum[i + 1] / (i + 1))
        return max(0.0, min(EMAX, lr / rv)) if rv > 0 else EMAX

    nav, navA, e_cur, pending, trades, turn = [1.0], [1.0], 0.0, None, 0, 0.0
    exp_hist, used = [], [dates[0]]
    for k in range(1, len(dates)):
        d = dates[k]
        w1 = e_cur if e_cur <= 1 else 2 - e_cur
        w2 = 0.0 if e_cur <= 1 else e_cur - 1
        cash = 1 - w1 - w2
        rc = (rf.get(d, 0.0) / 100 / 252) if rf else 0.0
        nav.append(nav[-1] * (1 + w1 * r1[d] + w2 * r2[d] + cash * rc))
        navA.append(navA[-1] * (1 + r1[d]))
        used.append(d)
        exp_hist.append(e_cur)
        if pending is not None:            # 어제 신호 → 오늘 종가 체결
            n1 = pending if pending <= 1 else 2 - pending
            n2 = 0.0 if pending <= 1 else pending - 1
            traded = abs(n1 - w1) + abs(n2 - w2)
            nav[-1] *= (1 - traded * cost / 100)
            turn += traded
            trades += 1
            e_cur, pending = pending, None
        nxt = dates[k + 1] if k + 1 < len(dates) else None
        week_end = nxt is None or (_iso_week(nxt) != _iso_week(d))
        if week_end:
            s = signal(d)
            if s is not None and abs(s - e_cur) > BAND:
                pending = round(s, 3)
    avg_e = st.mean(exp_hist[MIN_HIST:]) if len(exp_hist) > MIN_HIST else st.mean(exp_hist)
    # (b) 평균 노출 고정 비중(월 1회 복원, 같은 비용)
    navB, wB = [1.0], avg_e
    b1 = wB if wB <= 1 else 2 - wB
    b2 = 0.0 if wB <= 1 else wB - 1
    for k in range(1, len(dates)):
        d = dates[k]
        navB.append(navB[-1] * (1 + b1 * r1[d] + b2 * r2[d]))
    return {"dates": used, "nav": nav, "navA": navA, "navB": navB, "avg_e": avg_e,
            "trades": trades, "turn_yr": turn / (len(dates) / 252)}


def _iso_week(d):
    from datetime import date
    y, m, dd = map(int, d.split("-"))
    return date(y, m, dd).isocalendar()[:2]


def seg(res, lo, hi):
    idx = [i for i, d in enumerate(res["dates"]) if lo <= d <= hi]
    if len(idx) < 60:
        return None
    a, b = idx[0], idx[-1] + 1
    return {k: stats(res[k][a:b], res["dates"][a:b]) for k in ("nav", "navA", "navB")}


def main():
    irx = load("IRX")
    L = ["# 변동성 타겟 지수 노출 — 사전등록 백테스트 (2026-09-24)", "",
         "read-only. `tools/research/research_voltarget_20260924.py`(규칙·통과 기준은 docstring에 실행 전 고정).",
         "데이터: yfinance 수정주가(실제 1배·2배 ETF). 신호 t일 종가 → **t+1일 종가 체결**, 주 1회·밴드 0.25.", ""]
    verdict = {}
    for mk, rf in (("KR", None), ("US", None), ("US", irx)):
        res = run(mk, rf)
        tag = f"{mk}" + (" (현금=T-bill)" if rf else "")
        full = seg(res, res["dates"][0], res["dates"][-1])
        mid = res["dates"][len(res["dates"]) // 2]
        h1, h2 = seg(res, res["dates"][0], mid), seg(res, mid, res["dates"][-1])
        L += [f"## {tag} — {res['dates'][0]} ~ {res['dates'][-1]}", "",
              f"평균 노출 {res['avg_e']:.2f} · 체결 {res['trades']}회 · 연 회전 {res['turn_yr']:.2f}배", "",
              "| 구간 | 방식 | CAGR | 변동성 | 샤프 | 최대낙폭 | 칼마 |", "|---|---|---:|---:|---:|---:|---:|"]
        for nm, sg in (("전체", full), ("전반", h1), ("후반", h2)):
            for key, lab in (("nav", "**변동성 타겟**"), ("navA", "1배 보유"), ("navB", f"고정 {res['avg_e']:.2f}배")):
                s = sg[key]
                L.append(f"| {nm} | {lab} | {s['cagr']:+.1f}% | {s['vol']:.1f}% | {s['sharpe']:.2f} | "
                         f"{s['mdd']:.1f}% | {s['calmar']:.2f} |")
        # 연도별
        L += ["", "| 연도 | 변동성 타겟 | 1배 보유 | 고정 비중 |", "|---|---:|---:|---:|"]
        years = sorted({d[:4] for d in res["dates"]})
        for y in years:
            sg = seg(res, f"{y}-01-01", f"{y}-12-31")
            if sg:
                L.append(f"| {y} | {sg['nav']['cagr']:+.1f}% | {sg['navA']['cagr']:+.1f}% | {sg['navB']['cagr']:+.1f}% |")
        c1 = full["nav"]["sharpe"] > full["navA"]["sharpe"] and full["nav"]["sharpe"] > full["navB"]["sharpe"]
        c2 = all(sg["nav"]["sharpe"] > sg["navB"]["sharpe"] for sg in (h1, h2))
        c3 = full["nav"]["mdd"] > full["navA"]["mdd"]
        verdict[tag] = (c1, c2, c3)
        L += ["", f"사전등록 판정 — ①샤프 우위 {'통과' if c1 else '실패'} · ②전·후반 부호 {'통과' if c2 else '실패'} · "
              f"③낙폭 {'통과' if c3 else '실패'}", ""]
    ok = all(all(v) for k, v in verdict.items() if "T-bill" not in k)
    L += ["## 종합", "", f"KR·US(현금 0 기준) 3조건 전부 통과: **{'예' if ok else '아니오'}**", ""]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
