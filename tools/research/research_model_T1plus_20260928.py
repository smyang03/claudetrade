"""T1-plus — 현행 T1(M2 전체 피처, H20, I1 세션 임계, 평탄 0.44%)보다 나은 소수 변형이 있는가 (2026-09-28, 사전등록)

=== 사전등록 (계산 전 고정. 결과를 본 뒤 변경 금지. 이탈은 보고서에 '이탈'로 기록) ===

[기준선 B0] 현행 T1 = M2 LGBMRegressor 기본값(seed 0), FEATS_A 14개, 라벨 gA20(1/99 winsorize), 연 1회 확장
  워크포워드(테스트 2017..2026, 학습 = 진입연도 < Y & 청산 인덱스 < Y 첫 세션). 점수는 T1 강건성 캐시
  (`model_T1_robust_20260927/scores_A_open_H20.csv.gz`의 M2_lgbm_reg). 재현 게이트: (a) FEATS_A 재학습 점수와 캐시의
  최대 |차| < 1e-6, (b) I1 평탄 0.44% 건별 n=4,580 / 평균 +1.50% / 세션 NW20 t 2.79 재현. 실패 시 중단.
[규칙 I1] T1-impl과 동일: 연도 Y(≥2018) 임계 = 진입연도 < Y인 OOS 점수 90분위(연 고정), 2017은 그 세션 이전 2017
  점수의 확장 90분위(200건 burn-in). 매 세션 score ≥ 임계인 시가 진입 이벤트 전부. 각 변형은 **자기 점수**로 임계를 만든다.
[비용] 건별 왕복 평탄 0.44%(시가 동시호가 LOO/MOO 체결 전제 — slippage 보고서). net = gA − 0.0044. gA = IWM 초과(같은 open 창).

[변형 4종 — 각각 B0와 비교, 4검정]
- V1 이벤트 피처만: sue1, sue2, sgn, beats4, nprev4, dsue, days_since, month_sin, month_cos (9개).
  X1의 이벤트 전용(7개, 월 없음)의 상위집합이다. X1 t 3.08은 주간 top-10% − 코호트 지표였고 I1 판정 t가 아니다.
- V2 보유 H40: 같은 모델·피처, 라벨 gA40(open[e]→open[e+40] − 같은 창 IWM), 퍼지 x40 < Y 첫 세션.
  비교 단위 = 자본·시간당: net40/2(20세션 환산) vs B0 net20. 원 net40도 병기. 2026 후반은 청산 가격 부족으로 빠진다(컷오프 보고).
- V3 유동성: B0 임계(전체 풀 기준)를 그대로 쓰고, 통과분 중 dvol20 ≥ $5M만 남긴다(임계 재계산 없음).
- V4 앙상블: M2·M4(캐시 점수)를 각각 인과 백분위로 변환(연도 Y≥2018: 진입연도 < Y OOS 점수의 ECDF,
  2017: 그 세션 이전 2017 점수의 ECDF, 200건 burn-in) → 평균 = 앙상블 점수 → 위 I1 임계 방식 그대로.

[판정]
- 쌍대: 주(W-FRI `week`) 단위, 각 쪽 = 그 주 진입 거래의 건별 net 평균, 양쪽 모두 ≥1건인 주만(버린 주 수 보고).
  차이 = 변형 − B0, NW t lag 4주(V2는 8주). 반기 = 주 연도 ≤2021 / ≥2022.
- 'better' = 쌍대 t ≥ 2.5 AND 반기 차이 평균 둘 다 > 0. 4검정, 다중검정 보정 없음(문턱 2.5로 갈음, 보고서에 명시).
- 자기 I1 t(판정 아님, 병기) = 진입 세션 평균 net 시계열 NW t lag 20(V2는 40).
- 병기: n, 평균/중앙 net, 승률, 반기 건별 평균, 양수 연도 수.
[쉐도우 스펙 해석] I1은 동시 보유 평균 37·최대 244라 포트폴리오 실행이 안 된다. 쉐도우는 I2 기계다. 여기서 정하는 것은
  "I2 쉐도우에 넣을 점수 스펙"이고, 변형×I2 조합은 검정하지 않는다.
[한계] 생존편향(현재 상장 종목), eps_est 비PIT 가능, 시가 동시호가 체결 실측 0건(open = 체결가 가정).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T1_earnings_20260927 as t1  # noqa: E402

CACHE = ROOT / "data" / "analysis" / "model_T1_robust_20260927" / "scores_A_open_H20.csv.gz"
OUT = ROOT / "data" / "analysis" / "model_T1plus_20260928"
COST = 0.0044
BURN = 200
V1_FEATS = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since", "month_sin", "month_cos"]


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(OUT / "run.log", "a", encoding="utf-8") as fh:
        fh.write(s + "\n")


def nw_t(x, lags):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return float("nan")
    u = x - x.mean(); s = (u @ u) / n
    for L in range(1, min(lags, n - 1) + 1):
        s += 2 * (1 - L / (lags + 1)) * (u[L:] @ u[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def m2(tr, te, feats, y):
    import lightgbm as lgb
    ytr = tr[y].values
    lo, hi = np.nanpercentile(ytr, [1, 99])
    m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1).fit(
        tr[feats].values.astype(np.float32), np.clip(ytr, lo, hi))
    return m.predict(te[feats].values.astype(np.float32))


def walk(tab, feats, gcol, xcol, cal):
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2017, 2027)}
    d = tab[tab[gcol].notna()]
    out = []
    for Y in range(2017, 2027):
        tr = d[(d.year < Y) & (d[xcol] < first_idx[Y])]; te = d[d.year == Y]
        if len(te) == 0:
            continue
        p = te[["ticker", "date", "e", "week", "year", "dvol20", gcol]].copy()
        p["date"] = pd.to_datetime(p.date).dt.strftime("%Y-%m-%d")
        p["score"] = m2(tr, te, feats, gcol)
        out.append(p)
        log("fit", gcol, len(feats), Y, len(tr), len(te))
    return pd.concat(out, ignore_index=True)


def thresholds(s, e, yr):
    thr = np.full(len(s), np.nan)
    for Y in range(2018, 2027):
        prior = s[yr < Y]
        if len(prior):
            thr[yr == Y] = np.percentile(prior, 90)
    m17 = np.where(yr == 2017)[0]
    order = m17[np.argsort(e[m17], kind="stable")]; ss = s[order]; ee = e[order]
    for sess in np.unique(ee):
        prior = ss[ee < sess]
        if len(prior) >= BURN:
            thr[order[ee == sess]] = np.percentile(prior, 90)
    return thr


def causal_pct(s, e, yr):
    pc = np.full(len(s), np.nan)
    for Y in range(2018, 2027):
        ref = np.sort(s[yr < Y]); m = yr == Y
        pc[m] = np.searchsorted(ref, s[m], side="right") / len(ref)
    m17 = np.where(yr == 2017)[0]
    order = m17[np.argsort(e[m17], kind="stable")]; ss = s[order]; ee = e[order]
    for sess in np.unique(ee):
        prior = np.sort(ss[ee < sess])
        if len(prior) >= BURN:
            idx = order[ee == sess]
            pc[idx] = np.searchsorted(prior, s[idx], side="right") / len(prior)
    return pc


def select_I1(P, col):
    thr = thresholds(P[col].values, P.e.values, P.year.values)
    return P[~np.isnan(thr) & (P[col].values >= thr)].copy()


def own(T, net, lag):
    half = np.where(T.year.values <= 2021, "h1", "h2")
    sess = pd.Series(net).groupby(T.e.values).mean()
    yr = pd.Series(net).groupby(T.year.values).mean()
    return {"n": int(len(T)), "mean": float(net.mean()), "median": float(np.median(net)),
            "win": float((net > 0).mean()), "t_session": nw_t(sess.values, lag),
            "h1": float(net[half == "h1"].mean()), "h2": float(net[half == "h2"].mean()),
            "years_pos": f"{int((yr > 0).sum())}/{len(yr)}",
            "by_year": {int(k): float(v) for k, v in yr.items()}}


def weekly(T, net):
    return pd.Series(net).groupby(pd.to_datetime(T.week.values)).mean()


def paired(wv, wb, lag):
    j = pd.concat([wv.rename("v"), wb.rename("b")], axis=1)
    both = j.dropna().sort_index()
    d = both.v - both.b
    h = both.index.year <= 2021
    r = {"weeks_both": int(len(both)), "weeks_dropped": int(len(j) - len(both)),
         "diff": float(d.mean()), "t": nw_t(d.values, lag),
         "h1": float(d[h].mean()), "h2": float(d[~h].mean())}
    r["better"] = bool(r["t"] >= 2.5 and r["h1"] > 0 and r["h2"] > 0)
    return r


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "run.log").write_text("", encoding="utf-8")
    C = pd.read_csv(CACHE, dtype={"ticker": str})
    C = C[C.gA20.notna()].reset_index(drop=True)
    res = {}

    # ---- 재학습: B0 재현 게이트 + V1 + V2
    t1.HS = (5, 20, 40)  # 런타임 확장(파일 수정 없음) — build가 gA40/x40도 만든다
    ev, _ = t1.load_events()
    cal, ti, W = t1.load_prices()
    tab, _ = t1.build(ev, cal, ti, W)
    del W
    R0 = walk(tab, t1.FEATS_A, "gA20", "x20", cal)
    k = ["ticker", "date"]
    mg = C[k + ["M2_lgbm_reg"]].merge(R0[k + ["score"]], on=k, how="inner")
    res["repro_retrain"] = {"n_cache": len(C), "n_retrain": len(R0), "n_merge": len(mg),
                            "max_abs_diff": float((mg.M2_lgbm_reg - mg.score).abs().max())}
    log("재학습 재현", res["repro_retrain"])
    assert len(mg) == len(C) and res["repro_retrain"]["max_abs_diff"] < 1e-6
    R1 = walk(tab, V1_FEATS, "gA20", "x20", cal)
    R2 = walk(tab, t1.FEATS_A, "gA40", "x40", cal)
    res["v2_last_entry"] = str(R2.date.max())[:10]

    # ---- 선택
    B0 = select_I1(C, "M2_lgbm_reg")
    nb = B0.gA20.values - COST
    ob = own(B0, nb, 20)
    res["B0"] = ob
    log("B0", {k2: ob[k2] for k2 in ("n", "mean", "t_session", "h1", "h2")})
    assert ob["n"] == 4580 and abs(ob["mean"] - 0.0150) < 0.0006 and abs(ob["t_session"] - 2.79) < 0.02
    wb = weekly(B0, nb)

    T1v = select_I1(R1, "score"); n1 = T1v.gA20.values - COST
    T2v = select_I1(R2, "score"); n2 = T2v.gA40.values - COST
    T3v = B0[B0.dvol20 >= 5e6]; n3 = T3v.gA20.values - COST
    C["ens"] = (causal_pct(C.M2_lgbm_reg.values, C.e.values, C.year.values)
                + causal_pct(C.M4_mlp.values, C.e.values, C.year.values)) / 2
    T4v = select_I1(C[C.ens.notna()], "ens"); n4 = T4v.gA20.values - COST

    specs = {"V1_event_only": (T1v, n1, 20, 4), "V2_H40": (T2v, n2, 40, 8),
             "V3_dvol5M": (T3v, n3, 20, 4), "V4_ens_M2M4": (T4v, n4, 20, 4)}
    for name, (T, net, lag_s, lag_w) in specs.items():
        o = own(T, net, lag_s)
        wv = weekly(T, net / 2 if name == "V2_H40" else net)
        o["paired"] = paired(wv, wb, lag_w)
        if name == "V2_H40":
            o["mean_per20"] = o["mean"] / 2
            # 같은 기간 한정 B0(V2 마지막 진입일까지) 참고
            bb = B0[B0.date <= res["v2_last_entry"]]
            o["B0_same_window_mean"] = float((bb.gA20.values - COST).mean())
        o["sel_share_by_year"] = (T.groupby("year").size()
                                  / specs_pool(name, C, R1, R2).groupby("year").size()).round(4).to_dict()
        res[name] = o
        log(name, {k2: o[k2] for k2 in ("n", "mean", "t_session", "h1", "h2", "years_pos")}, o["paired"])
        T.to_csv(OUT / f"trades_{name}.csv.gz", index=False, compression="gzip")
    B0.to_csv(OUT / "trades_B0.csv.gz", index=False, compression="gzip")
    (OUT / "results.json").write_text(json.dumps(res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")


def specs_pool(name, C, R1, R2):
    return {"V1_event_only": R1, "V2_H40": R2}.get(name, C)


if __name__ == "__main__":
    main()
