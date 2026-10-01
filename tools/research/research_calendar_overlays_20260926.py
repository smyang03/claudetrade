#!/usr/bin/env python3
"""달력·이벤트 오버레이 3종 — 사전등록 검정 (read-only, 2026-09-26).

운영자: "기존 틀 부숴도 된다, 수익성 봇으로 처음부터 설계·검증." 개별주 선택은 닫혔고(09-12~24),
남은 후보는 **지수 상품 위의 구조적 달력 효과**다. 파라미터는 **문헌값 그대로** — 창 폭을 스윕하지 않는다.

## 후보(문헌값 고정)
T1 KR 턴오브먼스(McConnell & Xu 2008; 07-19 "KR만 근거" 표시만 됨): 069500을 월 마지막 거래일 −2의 종가에 사서
   다음 달 3번째 거래일 종가에 판다(= −1,0(월말),+1,+2,+3 중 −1~+3 네 날의 수익). 보조 229200(2015~) 병기.
T2 휴장 전날(Ariel 1990): 다음 거래일까지 평일 공백이 1일 이상인 날 = 휴장 전날. 그 전날 종가 매수 → 휴장 전날 종가 매도.
   KR 069500 · US SPY 각각.
T3 FOMC 발표일(Lucca & Moench 2015의 일봉 근사): 발표일 전날 종가 매수 → 발표일 종가 매도. US SPY.
   날짜: federalreserve.gov 원문 파싱(`data/analysis/voltarget_20260924/fomc_dates.csv`, 연 8회 검산).

## 판정(실행 전 고정)
- 이벤트 단위 표본. 초과 = 이벤트 보유 수익 − (같은 해 비이벤트 날 일평균 × 보유일수). 국면 효과를 해 단위로 뺀다.
- 통과 = ① 초과 평균 t ≥ 2 ② 전·후반 부호 모두 양수 ③ **안전여유 = 이벤트 초과 평균(비용 전) ÷ 왕복비용 ≥ 3**.
  왕복비용: KR ETF 0.10% · US(KIS) 0.44%(tools/measure_actual_fees.py 실측).
- t ≥ 3 열 병기(탐색 분모 96~301 + 이번 3건).
- 검정 전 **최소검출효과(MDE, t=2)** = 2 × 표준편차 ÷ √n 을 먼저 적는다. 문헌 효과가 그 아래면 '검출 불가'.
⚠️ 069500 수정주가는 배당 재투자 포함 → 12월 말 배당락 이벤트가 T1에 섞인다. 12월 이벤트 제외 열을 병기.
"""
from __future__ import annotations

import csv
import math
import statistics as st
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "analysis" / "voltarget_20260924"
OUT = ROOT / "docs" / "reports" / "calendar_overlays_20260926.md"
COST = {"KR": 0.10, "US": 0.44}


def load(name):
    rows = []
    with (D / f"{name}.csv").open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                c = float(r["Close"])
            except (TypeError, ValueError):
                continue
            if c > 0:
                rows.append((r[next(iter(r))][:10], c))
    return sorted(rows)


def dd(s):
    y, m, d = map(int, s.split("-"))
    return date(y, m, d)


def evaluate(bars, events, mk, label, split, L, extra_filter=None):
    """events = [(i_buy, i_sell)] 인덱스(종가 매수·종가 매도)."""
    daily = [(bars[i][0], bars[i][1] / bars[i - 1][1] - 1) for i in range(1, len(bars))]
    in_event = set()
    for a, b in events:
        for k in range(a + 1, b + 1):
            in_event.add(bars[k][0])
    yr_mean = defaultdict(list)
    for d, r in daily:
        if d not in in_event:
            yr_mean[d[:4]].append(r)
    yr_mean = {y: st.mean(v) for y, v in yr_mean.items() if v}
    ex = []
    for a, b in events:
        d = bars[b][0]
        if extra_filter and not extra_filter(bars[a][0], d):
            continue
        hold = b - a
        raw = bars[b][1] / bars[a][1] - 1
        ex.append((d, (raw - hold * yr_mean.get(d[:4], 0.0)) * 100, raw * 100))
    if len(ex) < 10:
        L.append(f"| {label} | 표본 부족 ({len(ex)}) |")
        return None
    xs = [x for _, x, _ in ex]
    m, sd = st.mean(xs), st.stdev(xs)
    t = m / (sd / math.sqrt(len(xs)))
    mde = 2 * sd / math.sqrt(len(xs))
    h1 = [x for d, x, _ in ex if d < split]
    h2 = [x for d, x, _ in ex if d >= split]
    margin = m / COST[mk]
    rawm = st.mean(r for _, _, r in ex)
    ok = t >= 2 and h1 and h2 and st.mean(h1) > 0 and st.mean(h2) > 0 and margin >= 3
    L.append(f"| {label} | {len(xs)} | {rawm:+.3f}% | **{m:+.3f}%p** | {t:+.2f} | {mde:.3f}%p | "
             f"{st.mean(h1):+.3f} / {st.mean(h2):+.3f} | {margin:.1f}배 | "
             f"{'**통과**' if ok else '기각'}{' · t≥3' if ok and t >= 3 else ''} |")
    return ok


def main():
    L = ["# 달력·이벤트 오버레이 3종 — 사전등록 검정 (2026-09-26)", "",
         "read-only. `tools/research/research_calendar_overlays_20260926.py`(문헌값·판정 기준은 docstring에 실행 전 고정).",
         "초과 = 이벤트 수익 − 같은 해 비이벤트 날 일평균 × 보유일. 비용: KR 0.10% · US 0.44% 왕복.", "",
         "| 검정 | 이벤트 수 | 원 수익 평균 | 초과 평균 | t | 최소검출효과(t=2) | 전반 / 후반 초과 | 안전여유 | 판정 |",
         "|---|---:|---:|---:|---:|---:|---|---:|---|"]
    kr, kq, spy = load("069500.KS"), load("229200.KS") if (D / "229200.KS.csv").exists() else None, load("SPY")
    fomc = {r["date"] for r in csv.DictReader((D / "fomc_dates.csv").open(encoding="utf-8"))}

    def tom_events(b):
        ev = []
        for i in range(len(b) - 1):
            if b[i][0][:7] != b[i + 1][0][:7]:      # i = 월 마지막 거래일
                a, s = i - 2, i + 3
                if a >= 0 and s < len(b):
                    ev.append((a, s))
        return ev

    def preholiday_events(b):
        ev, ex = [], []
        for i in range(1, len(b) - 1):
            gap = (dd(b[i + 1][0]) - dd(b[i][0])).days
            wd = dd(b[i][0]).weekday()
            # 평일 공백: 금→월(3일)은 정상. 그 외 gap>1 이거나 금요일 gap>3이면 휴장 전날
            normal = 3 if wd == 4 else 1
            if gap > normal:
                ev.append((i - 1, i))
                ex.append(b[i][0])
        return ev, ex

    ev = tom_events(kr)
    evaluate(kr, ev, "KR", "T1 턴오브먼스 · 069500", "2017-01-01", L)
    evaluate(kr, ev, "KR", "T1 턴오브먼스 · 069500 (12월 말 제외)", "2017-01-01", L,
             extra_filter=lambda a, s: not (a[5:7] == "12"))
    if kq:
        evaluate(kq, tom_events(kq), "KR", "T1 보조 · 229200 (판정 제외, 참고)", "2021-01-01", L)
    pk, pk_dates = preholiday_events(kr)
    evaluate(kr, pk, "KR", "T2 휴장 전날 · 069500", "2017-01-01", L)
    pu, pu_dates = preholiday_events(spy)
    evaluate(spy, pu, "US", "T2 휴장 전날 · SPY", "2013-01-01", L)
    si = {d: i for i, (d, _) in enumerate(spy)}
    fe = [(si[d] - 1, si[d]) for d in sorted(fomc) if d in si and si[d] >= 1]
    evaluate(spy, fe, "US", "T3 FOMC 발표일 · SPY", "2013-01-01", L)

    L += ["", "## 검산 — 유도된 휴장 전날 예시(눈으로 확인용)", "",
          f"- KR 최근 8건: {', '.join(pk_dates[-8:])} (연 평균 {len(pk_dates) / max(1, len({d[:4] for d in pk_dates})):.1f}건)",
          f"- US 최근 8건: {', '.join(pu_dates[-8:])} (연 평균 {len(pu_dates) / max(1, len({d[:4] for d in pu_dates})):.1f}건)",
          f"- FOMC 이벤트 {len(fe)}건 (연 8회 × 약 {len(fe) / 8:.0f}년)", "",
          "## 읽는 법", "",
          "- 통과는 세 조건(t ≥ 2 · 전후반 양수 · 안전여유 ≥ 3배) 동시. 하나라도 빠지면 기각.",
          "- 최소검출효과보다 문헌 효과가 작으면, 기각은 '효과 없음'이 아니라 '이 표본으로 못 잡음'이다.",
          "- 통과해도 분모 보정(t ≥ 3) 전에는 후보다. 창 폭을 바꿔 재검정하지 않는다."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
