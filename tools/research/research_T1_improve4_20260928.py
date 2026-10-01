#!/usr/bin/env python3
"""T1 개선 가설 4종 — 사전등록 검증 (read-only, 2026-09-28).

출처: `T1_attribution_20260928.md`(사후 분해)에서 뽑은 가설. 파라미터는 분해 결과로 최적화하지 않고
**미리 정한 한 값**만 쓴다(격자 탐색 금지).

기준 B0: T1-impl I1 거래 4,580건(M2 점수 ≥ 연도 임계 = 전년까지 OOS 점수 90분위),
         net = IWM 대비 20세션 시가→시가 초과 − 0.44%.
H1 기준 상향: 연도 임계를 전년까지 OOS 점수 **95분위**로(90 → 95). 연도별 전년 OOS 점수만 사용.
H2 비상 손절: 보유 1~19세션 중 종가가 진입가의 −25% 이하면 다음 세션 시가에 청산(IWM도 같은 날 시가까지).
H3 변동성 역비례 비중: 같은 진입일 안에서 가중치 ∝ 1/vol20(합 1). 진입일 평균을 가중평균으로 계산.
H4 생존편향 점검(진단): 2014-06 이전부터 상장·결측 ≤1% 종목만 남겨 '주가 <$10 프리미엄'이 유지되는지.
판정(H1~H3): 진입일 단위 B0 대비 쌍대 차이 t ≥ 2.5 **그리고** 전반(2017–2021)·후반(2022–2026) 모두 차이 > 0.
H4: 생존 종목만으로 <$10 구간 − ≥$10 구간 차이가 진입일 t ≥ 2 면 '프리미엄 유지', 아니면 '편향 의심'.
t: 진입일 클러스터(20세션 보유 겹침 미보정 — 상한으로 읽음).
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

OUT = ROOT / "docs" / "reports" / "T1_improve4_20260928.md"
COST, H, STOP = 0.0044, 20, -0.25


def day_t(x: pd.Series):
    x = x.dropna()
    if len(x) < 5 or x.std() == 0:
        return float("nan")
    return x.mean() / (x.std() / math.sqrt(len(x)))


def paired(base: pd.Series, var: pd.Series):
    j = pd.concat([base, var], axis=1, keys=["b", "v"]).dropna()
    d = j.v - j.b
    h1 = d[d.index < pd.Timestamp("2022-01-01")].mean()
    h2 = d[d.index >= pd.Timestamp("2022-01-01")].mean()
    return d.mean(), day_t(d), h1, h2, len(d)


def main():
    t = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["entry_date"])
    t["net"] = t.gA20 - COST
    sc = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz")
    cal, ti, W = rm.load_prices()
    O, C = W["open"], W["close"]
    iw = ti["IWM"]
    T = len(cal)

    # H1 — 전년까지 OOS 점수 95분위
    thr95 = {}
    for y in sorted(t.year.unique()):
        prior = sc.loc[sc.year < y, "M2_lgbm_reg"]
        thr95[y] = np.nanpercentile(prior, 95) if len(prior) >= 200 else np.nan
    t["thr95"] = t.year.map(thr95)
    h1 = t[t.M2_lgbm_reg >= t.thr95.fillna(t.thr)]            # 2017(전년 없음)은 기존 임계 유지 — 사전 명시

    # H2 — 종가 −25% 비상 손절
    r = t.ticker.map(ti).values
    e = t.e.values.astype(int)
    net2 = np.empty(len(t))
    stopped = np.zeros(len(t), bool)
    for i in range(len(t)):
        ent = O[r[i], e[i]]
        xi = e[i] + H
        for k in range(1, H):
            c = C[r[i], e[i] + k]
            if c == c and c / ent - 1 <= STOP:
                xi = e[i] + k + 1
                stopped[i] = True
                break
        xi = min(xi, T - 1)
        g = O[r[i], xi] / ent - 1
        b = O[iw, xi] / O[iw, e[i]] - 1
        net2[i] = g - b - COST
    t["net_h2"] = net2
    t["stopped"] = stopped

    # H3 — 변동성 역비례 비중
    tab_ev, _ = rm.load_events()
    tab, _ = rm.build(tab_ev, cal, ti, W)
    t = t.merge(tab[["ticker", "e", "vol20"]].drop_duplicates(["ticker", "e"]), on=["ticker", "e"], how="left")
    stopped = t.stopped.values
    t["w"] = 1 / t.vol20.clip(lower=1e-4)
    base_day = t.groupby("entry_date").net.mean()
    h3_day = t.groupby("entry_date").apply(lambda s: np.average(s.net, weights=s.w))
    h1_day = h1.groupby("entry_date").net.mean()
    h2_day = t.groupby("entry_date").net_h2.mean()

    L = ["# T1 개선 가설 4종 — 사전등록 검증 (2026-09-28)", "",
         "read-only. `tools/research/research_T1_improve4_20260928.py`(규칙·판정은 docstring에 실행 전 고정).",
         f"기준 B0 = I1 {len(t):,}건, 건당 {t.net.mean()*100:+.2f}%, 진입일 t {day_t(base_day):+.2f}.", "",
         "| 가설 | 건수 | 건당 net | 자체 진입일 t | B0 대비 차이 | 쌍대 t | 전반 / 후반 차이 | 판정 |",
         "|---|---:|---:|---:|---:|---:|---|---|"]
    for name, day, n, mean in (("H1 기준 상향(95분위)", h1_day, len(h1), h1.net.mean()),
                               ("H2 종가 −25% 비상 손절", h2_day, len(t), t.net_h2.mean()),
                               ("H3 변동성 역비례 비중", h3_day, len(t), float("nan"))):
        dm, dtt, a, b, nd = paired(base_day, day)
        ok = dtt >= 2.5 and a > 0 and b > 0
        mean_s = f"{mean*100:+.2f}%" if mean == mean else "(가중)"
        L.append(f"| {name} | {n:,} | {mean_s} | {day_t(day):+.2f} | {dm*100:+.2f}%p | {dtt:+.2f} | "
                 f"{a*100:+.2f} / {b*100:+.2f} | {'**채택**' if ok else '기각'} |")
    L += ["", f"H2 손절 발동 {int(stopped.sum())}건({stopped.mean()*100:.1f}%) · 발동 건 원래 net 평균 "
              f"{t.loc[stopped, 'net'].mean()*100:+.1f}% → 손절 후 {t.loc[stopped, 'net_h2'].mean()*100:+.1f}%", ""]

    # H4 — 생존 종목만으로 <$10 프리미엄
    hist = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/ticker_history.csv")
    stable = set(hist[(hist.first_date <= "2014-06-30") & (hist.miss_share <= 0.01)].ticker)
    L += ["## H4 — 생존편향 점검: 주가 <$10 프리미엄", "", "| 모집단 | <$10 건수 | <$10 건당 | ≥$10 건당 | 차이 | 진입일 t |", "|---|---:|---:|---:|---:|---:|"]
    for lab, sub in (("전체", t), ("2014 이전 상장 생존 종목만", t[t.ticker.isin(stable)])):
        lo, hi = sub[sub.close_a < 10], sub[sub.close_a >= 10]
        d = (lo.groupby("entry_date").net.mean() - hi.groupby("entry_date").net.mean()).dropna()
        L.append(f"| {lab} | {len(lo)} | {lo.net.mean()*100:+.2f}% | {hi.net.mean()*100:+.2f}% | "
                 f"{(lo.net.mean()-hi.net.mean())*100:+.2f}%p | {day_t(d):+.2f} |")
    L += ["", "판정 기준: 생존 종목만으로 차이 t ≥ 2면 '프리미엄 유지', 아니면 '편향 의심'."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
