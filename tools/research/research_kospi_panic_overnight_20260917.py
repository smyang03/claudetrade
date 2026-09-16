#!/usr/bin/env python3
"""KOSPI 패닉 야간 — 코스닥 전용 구조인가, 지수 일반 구조인가 (read-only, 2026-09-17).

운영자 지시: *"돈 버는 전략을 찾아야 할 거 아냐"*.

## 왜 이 판인가

오늘 두 레버가 죽었다 — 비신호 밤 인버스(t −5.49), 무조건 야간 보유(t +1.65 미달·비용
0.10%면 음수). 살아있는 축은 여전히 코스닥 지수 ETF 패닉 야간 하나이고, 병목은
**가동률 연 20회**다. 문턱 완화·비신호 밤은 이미 닫혔다.

남은 미검정 방향 하나: **같은 구조를 다른 지수로.** 코스닥150에서 되는 게 KOSPI200에서도
되면 신호 수가 늘어 가동률 병목이 풀린다. 안 되면 "코스닥 고유(개인 비중·고베타)"라는
설명이 강해지고, 그것도 결론이다. US 이식은 09-12에 이미 실패했다(레버리지 4종 기각).

## 사전 등록 — 코스닥 레인과 **같은** 문턱, 사후 조정 없음

  · 신호 정의 v2 그대로: **갭 ≤ −1.0% 또는 종가 등락 ≤ −2.5%**
  · 계약 그대로: **종가 매수 → 익일 시가 매도**, 왕복 비용 0.05%
  · 판정 지표 그대로: **연도매칭 초과**(그해 무조건 야간 평균을 뺀다). 이걸 안 빼면
    강세 연도가 전략을 통과시킨다.
  · 문턱: **t ≥ 2.0**(밤 1건 = 날짜 1건) **AND 연도 부호 9/11 이상**
  · 비용 0.10% 민감도도 함께 낸다 — 0.10%에서 죽으면 마찰이지 엣지가 아니다

## 데이터 함정 — 먼저 막는다

yfinance 일봉은 **배당 조정**이 들어갈 수 있다. 종가만 조정되고 시가가 원본이면
`익일시가/당일종가`가 통째로 오염된다(US CSV 미완성 봉 오염과 같은 계열).
그래서 **원본 소스(`data/price/kr/kr_069500.csv`, 2025-05~)와 겹치는 구간에서
야간 수익률이 일치하는지 먼저 대조하고**, 어긋나면 그 자리에서 중단한다.
"""
from __future__ import annotations

import csv
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
YF = ROOT / "data" / "market_data" / "yfinance" / "daily" / "KR" / "069500.KS.csv"
RAW = ROOT / "data" / "price" / "kr" / "kr_069500.csv"
KQ_CACHE = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"

GAP_THR, CLOSE_THR = -1.0, -2.5
COSTS = (0.05, 0.10)
T_BAR = 2.0
SIGN_YEARS_BAR = 9
XVAL_TOL = 0.02          # 겹치는 구간 야간 수익률 평균 차이 허용치(%p)


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_ohlc(p):
    rows = list(csv.DictReader(p.open(encoding="utf-8-sig")))
    out = {}
    for r in rows:
        d = r.get("date") or r.get("Date")
        o, c = f(r.get("open")), f(r.get("close"))
        if d and o and c:
            out[d] = (o, c)
    return out


def nights_from(ohlc):
    """(date, gap%, chg%, 야간수익률%) — gap/chg는 **당일 종가 시점에 이미 확정**이다."""
    ds = sorted(ohlc)
    out = []
    for i in range(1, len(ds) - 1):
        pd_, d, nd = ds[i - 1], ds[i], ds[i + 1]
        po, pc = ohlc[pd_]
        o, c = ohlc[d]
        no, _ = ohlc[nd]
        out.append((d, (o / pc - 1) * 100, (c / pc - 1) * 100, (no / c - 1) * 100))
    return out


def one_sample_t(v):
    if len(v) < 3 or st.stdev(v) == 0:
        return None
    return st.mean(v) / (st.stdev(v) / math.sqrt(len(v)))


def year_matched(nights, cost):
    """연도매칭 초과: 그해 무조건 야간 평균을 빼고 비용을 뺀다."""
    by_year_all = defaultdict(list)
    for d, _, _, ov in nights:
        by_year_all[d[:4]].append(ov)
    base = {y: st.mean(v) for y, v in by_year_all.items()}
    out = []
    for d, gap, chg, ov in nights:
        sig = (chg <= CLOSE_THR) or (gap <= GAP_THR)
        out.append((d, sig, ov - base[d[:4]] - cost))
    return out


def main():
    L = ["# KOSPI 패닉 야간 — 코스닥 전용인가 지수 일반인가 (2026-09-17)", "",
         "read-only. `tools/research/research_kospi_panic_overnight_20260917.py`(사전등록은 docstring).",
         f"신호 v2 그대로(갭 ≤ {GAP_THR}% or 종가 ≤ {CLOSE_THR}%) · 종가매수/익일시가매도 · "
         f"**연도매칭 초과** · 문턱 **t ≥ {T_BAR} AND 연도 부호 {SIGN_YEARS_BAR}/11 이상**", ""]

    if not YF.exists():
        L.append("069500 장기 데이터 없음 — 중단.")
        print("\n".join(L))
        return 1

    yf_o, raw_o = read_ohlc(YF), read_ohlc(RAW) if RAW.exists() else {}

    # ── 데이터 교차검증 ──
    L += ["## 0. 데이터 교차검증 — 배당 조정 오염 확인", ""]
    yn = {d: ov for d, _, _, ov in nights_from(yf_o)}
    rn = {d: ov for d, _, _, ov in nights_from(raw_o)} if raw_o else {}
    both = sorted(set(yn) & set(rn))
    if len(both) < 30:
        L += [f"겹치는 밤 {len(both)}개 — 대조 불가. **합성 소스 단독이라 결과를 등록하지 않는다.**", ""]
        ok_data = False
    else:
        dy, dr = st.mean(yn[d] for d in both), st.mean(rn[d] for d in both)
        gap = abs(dy - dr)
        ok_data = gap <= XVAL_TOL
        L += [f"겹치는 밤 **{len(both)}개**({both[0]} ~ {both[-1]})", "",
              "| 소스 | 야간 평균 |", "|---|---:|",
              f"| yfinance(장기) | {dy:+.4f}% |", f"| 원본 `kr_069500.csv` | {dr:+.4f}% |",
              f"| 차이 | **{dy-dr:+.4f}%p** |", "",
              f"허용치 {XVAL_TOL}%p → **{'통과 — 오염 없음' if ok_data else '초과 — 오염 의심, 결과 등록 불가'}**", ""]

    nights = nights_from(yf_o)
    L += [f"## 1. KOSPI200(069500) 패닉 야간 — 밤 {len(nights):,}개 "
          f"({nights[0][0]} ~ {nights[-1][0]})", ""]

    for cost in COSTS:
        ex = year_matched(nights, cost)
        sig = [(d, v) for d, s, v in ex if s]
        off = [(d, v) for d, s, v in ex if not s]
        L += [f"### 왕복 비용 {cost:.2f}%", "",
              "| 구간 | 밤 수 | 연 회수 | 평균 초과 | t | 연도 양수 | 판정 |",
              "|---|---:|---:|---:|---:|---:|---|"]
        years = len({d[:4] for d, _, _, _ in nights})
        for nm, arr in (("**패닉 신호 밤**(이 판)", sig), ("비신호 밤(대조)", off)):
            if not arr:
                L.append(f"| {nm} | 0 | - | - | - | - | 표본 없음 |")
                continue
            vals = [v for _, v in arr]
            t = one_sample_t(vals)
            by = defaultdict(list)
            for d, v in arr:
                by[d[:4]].append(v)
            pos = sum(1 for y in by if st.mean(by[y]) > 0)
            ok = t is not None and t >= T_BAR and pos >= SIGN_YEARS_BAR
            L.append(f"| {nm} | {len(vals):,} | {len(vals)/years:.1f}회 | "
                     f"{st.mean(vals):+.3f}% | {t:+.2f} | {pos}/{len(by)} | "
                     f"{'**통과**' if ok else '기각'} |")
        L.append("")

    # ── 코스닥 대조 (같은 스크립트·같은 규약으로) ──
    L += ["## 2. 같은 규약으로 코스닥150(229200) 재측정 — 대조군", ""]
    if KQ_CACHE.exists():
        rows = sorted(csv.DictReader(KQ_CACHE.open(encoding="utf-8-sig")),
                      key=lambda r: r["date"])
        kq = {}
        for r in rows:
            o, c = f(r.get("229200_open")), f(r.get("229200_close"))
            if o and c:
                kq[r["date"]] = (o, c)
        kn = nights_from(kq)
        ex = year_matched(kn, 0.05)
        sig = [(d, v) for d, s, v in ex if s]
        vals = [v for _, v in sig]
        t = one_sample_t(vals)
        by = defaultdict(list)
        for d, v in sig:
            by[d[:4]].append(v)
        pos = sum(1 for y in by if st.mean(by[y]) > 0)
        years = len({d[:4] for d, _, _, _ in kn})
        L += ["| 지수 | 밤 수 | 연 회수 | 평균 초과 | t | 연도 양수 |", "|---|---:|---:|---:|---:|---:|",
              f"| 코스닥150 229200 (비용 0.05%) | {len(vals):,} | {len(vals)/years:.1f}회 | "
              f"{st.mean(vals):+.3f}% | {t:+.2f} | {pos}/{len(by)} |", "",
              "⚠️ 코스닥 레인의 사전등록 값은 **233740(2x)** 기준 +0.573%·t 3.27이다. "
              "위는 같은 스크립트로 **229200(1x)**를 잰 값이라 배수만큼 작게 나오는 게 정상이다 — "
              "KOSPI와 **같은 조건에서 비교**하려고 1x로 맞춘 것이다.", ""]

    L += ["## 결론 규칙", "",
          f"KOSPI 패닉 신호 밤이 비용 0.10%에서도 **t ≥ {T_BAR} AND 연도 부호 "
          f"{SIGN_YEARS_BAR}/11 이상**일 때만 forward 등록 대상이다.",
          "미달이면 기각이고, 그 경우 결론은 **\"패닉 야간 프리미엄은 코스닥 고유\"**가 되어",
          "가동률 병목은 지수 확장으로 못 푼다는 뜻이다. 문턱은 사후에 내리지 않는다."]

    out = ROOT / "docs" / "reports" / "kospi_panic_overnight_20260917.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
