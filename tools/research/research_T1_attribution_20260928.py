#!/usr/bin/env python3
"""T1 수익 분해 — 어디서 벌고 무엇이 깎아먹나 (read-only, 2026-09-28, 사후 기술통계).

대상: T1-impl I1 규칙 거래 4,580건(`data/analysis/model_T1_impl_20260927/trades_I1.csv.gz`),
net = gA20(IWM 대비 20세션 초과, 시가→시가) − 0.44%(동시호가 체결 전제 수수료).
피처는 연구 모듈 build()로 재계산해 (ticker, date)로 붙인다.
⚠️ 사후 분해다 — 여기서 나온 칸을 규칙으로 쓰려면 별도 사전등록·표본 밖 검증이 필요하다.
t = 진입일 클러스터(일평균 → t). 20세션 보유 겹침은 미보정(|t| 상한으로 읽기).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T1_earnings_20260927 as rm  # noqa: E402

OUT = ROOT / "docs" / "reports" / "T1_attribution_20260928.md"
COST = 0.0044


def dt(s: pd.DataFrame, col="net"):
    g = s.groupby("entry_date")[col].mean()
    if len(g) < 5 or g.std() == 0:
        return float("nan")
    return g.mean() / (g.std() / math.sqrt(len(g)))


def table(df, key, label, L):
    L += [f"### {label}", "", "| 구간 | 건수 | 건당 net | 중앙값 | 승률 | 합계 기여 | 날짜 t |", "|---|---:|---:|---:|---:|---:|---:|"]
    tot = df.net.sum()
    for k, s in df.groupby(key, observed=True):
        L.append(f"| {k} | {len(s)} | {s.net.mean()*100:+.2f}% | {s.net.median()*100:+.2f}% | "
                 f"{(s.net > 0).mean()*100:.0f}% | {s.net.sum()/tot*100:+.0f}% | {dt(s):+.2f} |")
    L.append("")


def main():
    t = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz")
    t["date"] = pd.to_datetime(t.date)
    ev, _ = rm.load_events()
    cal, ti, W = rm.load_prices()
    tab, _ = rm.build(ev, cal, ti, W)
    tab["date"] = pd.to_datetime(tab.date)
    f = tab[["ticker", "date"] + rm.FEATS_A + ["gap", "react"]]
    d = t.merge(f, on=["ticker", "date"], how="left")
    d["net"] = d.gA20 - COST
    d["entry_date"] = pd.to_datetime(d.entry_date)
    n_miss = int(d.sue1.isna().sum())

    L = ["# T1 수익 분해 — 어디서 벌고 무엇이 깎아먹나 (2026-09-28)", "",
         "read-only · 사후 기술통계. `tools/research/research_T1_attribution_20260928.py`.",
         f"I1 거래 {len(d):,}건(2017~2026), net = IWM 대비 20세션 초과 − 수수료 0.44%(동시호가 전제). 피처 결합 실패 {n_miss}건.",
         "⚠️ 여기서 좋은 칸을 골라 규칙으로 쓰면 과최적화다 — 개선 가설 목록일 뿐이다.", ""]

    # 0. 분포 — 소수가 번다
    s = d.net.sort_values(ascending=False).values
    tot = s.sum()
    L += ["## 0. 수익 분포 — 누가 버나", "",
          f"- 건당 평균 **{d.net.mean()*100:+.2f}%** · 중앙값 **{d.net.median()*100:+.2f}%** · 승률 {(d.net>0).mean()*100:.0f}%",
          f"- 총 net 합 {tot*100:+.0f}%p 중 상위 5% 거래가 **{s[:len(s)//20].sum()/tot*100:.0f}%**, 상위 10%가 **{s[:len(s)//10].sum()/tot*100:.0f}%**",
          f"- 하위 10% 거래 합 {s[-len(s)//10:].sum()*100:+.0f}%p (평균 {s[-len(s)//10:].mean()*100:+.1f}%)",
          f"- −20% 이하 손실 {int((d.net<=-0.20).sum())}건 · +30% 이상 이익 {int((d.net>=0.30).sum())}건",
          f"- 수수료 총합 {len(d)*COST*100:.0f}%p = gross 합의 {len(d)*COST/d.gA20.sum()*100:.0f}%", ""]

    d["score_x"] = pd.qcut(d.M2_lgbm_reg / d.thr, 5, labels=["1(기준 바로 위)", "2", "3", "4", "5(최고점)"])
    table(d, "score_x", "1. 모델 점수 강도(기준 대비 배수 5분위)", L)
    d["sue_b"] = pd.cut(d.sue1, [-9, 0, 0.05, 0.15, 0.5, 9], labels=["예상 미달·동일(≤0)", "+0~5%", "+5~15%", "+15~50%", "+50% 이상"])
    table(d, "sue_b", "2. 서프라이즈 크기(sue1)", L)
    table(d, "hour", "3. 발표 시각", L)
    d["liq"] = pd.cut(d.dvol20, [0, 5e6, 20e6, 100e6, 1e13], labels=["<$5M", "$5~20M", "$20~100M", "≥$100M"])
    table(d, "liq", "4. 유동성(20일 평균 거래대금)", L)
    d["px"] = pd.cut(d.close_a, [0, 10, 30, 100, 1e6], labels=["<$10", "$10~30", "$30~100", "≥$100"])
    table(d, "px", "5. 주가 수준", L)
    d["vol_b"] = pd.qcut(d.vol20, 3, labels=["저변동", "중", "고변동"])
    table(d, "vol_b", "6. 발표 전 20일 변동성", L)
    d["mom_b"] = pd.qcut(d.ret60, 3, labels=["60일 하락", "중립", "60일 상승"])
    table(d, "mom_b", "7. 발표 전 60일 추세", L)
    d["beats_b"] = d.beats4.fillna(-1).map(lambda x: "이력 없음" if x < 0 else f"{int(x)}회")
    table(d, "beats_b", "8. 최근 4분기 예상 상회 횟수", L)
    d["gap_b"] = pd.cut(d.gap, [-9, -0.05, -0.01, 0.01, 0.05, 9], labels=["갭 ≤−5%", "−5~−1%", "±1%", "+1~5%", "≥+5%"])
    table(d, "gap_b", "9. 진입일 시가 갭(진입 시점에 앎 — 규칙 후보)", L)
    table(d, "year", "10. 연도", L)
    d["mon"] = d.entry_date.dt.month
    table(d, "mon", "11. 진입 월", L)
    L += ["## 12. 깎아먹는 것 — 최악 20건", "", "| 종목 | 진입일 | net | sue1 | 갭 | 거래대금 | 60일 추세 |", "|---|---|---:|---:|---:|---:|---:|"]
    for _, r in d.nsmallest(20, "net").iterrows():
        L.append(f"| {r.ticker} | {r.entry_date.date()} | {r.net*100:+.1f}% | {r.sue1:+.2f} | {r.gap*100:+.1f}% | "
                 f"${r.dvol20/1e6:.1f}M | {r.ret60*100:+.0f}% |")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
