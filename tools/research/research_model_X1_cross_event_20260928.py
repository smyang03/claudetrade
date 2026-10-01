"""X1 — 교차 이벤트 모델: 가격 기울기 공통 신호 vs 이벤트 고유 정보 (2026-09-28, 사전등록)

배경: T1(실적)·T3(내부자 매수)·R1(급락 풀)에서 LightGBM이 코호트 내부 t≈2.4~3의 우위를 보였고,
중요도의 상당 부분이 가격 피처(변동성·52주 위치·최근 수익)였다. 가설: "이벤트 직후 소형·고변동 가격 기울기"라는
공통 신호 + 이벤트 고유 정보 일부.

=== 등록 시점에 이미 본 정보 (blind 아님) ===
- data/analysis/model_T1_earnings_20260927/posthoc_ablation_A_open_H20_M2.json 을 등록 전에 읽었다:
  T1 M2 price_only diff_vs_all t 2.13, surprise_only t 3.08. → X1-a T1 칸의 집계값은 확인용이며,
  새로 측정하는 양은 주간 쌍대 차이(all − price-only)의 NW t 다.
- T1/T3 원 보고서의 모든 수치(T1 M2 vs 전체 t 3.08, T3 M2/M3/M4 규칙 대비 t 2.44~2.47, R1 결과).

=== 사전등록 (실행 전 고정. 이탈은 보고서에 '이탈'로 기록) ===

[데이터 — 재사용만, 신규 다운로드 없음]
- T1: research_model_T1_earnings_20260927 의 load_events/load_prices/build 를 import(원 파일 수정 없음).
  변형 A_open, H20, 타깃 gA20(open[e]→open[e+20], IWM 동일 구간 차감). 트레이더빌리티·2014+ 는 원 build 그대로.
  hi52/lo52 는 X1-b 용으로 T1 가격 배열에서 추가 계산(T3 규약: close[a]/max(close[max(0,a−251)..a])−1, min 동일).
  T1 가격은 auto_adjust=True(배당 조정) 종가.
- T3: data/analysis/model_T3_form4_20260927/events_table.csv.gz, tradable==1 & g20 존재. 원 FEATS 그대로.
- R1: data/analysis/model_R1_us_swing_s1_20260927/s1_top3_picks.csv (x_iwm, % 단위).

[모델 — 전 칸 공통] LGBMRegressor 기본값, random_state 0, n_jobs 2, 타깃 = 학습 gross 초과 1/99 winsorize.
- 이탈(사전 인지): T1 원 M2 는 n_jobs 4. T1 강건성 보고서에서 n_jobs 2 재학습이 주간 net 최대차 5.7e-8 로 재현됨.

[X1-a 코호트별 절제 (검정 2회: T1·T3 각 1)]
- 워크포워드는 각 원 스크립트 그대로: T1 테스트 2017..2026 / 학습 진입연도 < Y 이고 x20 < Y 첫 세션 인덱스.
  T3 테스트 2012..2026 / 동일 규칙(T3 달력).
- 피처 분할((i)+(ii) = (iii) = 원 피처 세트):
  T1 (i) price = ret20, ret60, vol20, ldvol20, lsize, month_sin, month_cos (월은 비이벤트 맥락이라 가격 쪽에 둔다)
     (ii) event = sue1, sue2, sgn, beats4, nprev4, dsue, days_since
  T3 (i) price = ret20, ret60, vol20, lsize, hi52, lo52
     (ii) event = n_day, n7, n30, is_ceo, is_cfo, is_dir, is_off, is_10pct, lval, pct_hold, lval_dvol, days_since
     (lval_dvol 은 내부자 금액/거래대금이라 유동성 대리값이 섞인다 — (ii) 기술 칸에만 영향)
- 평가: 주(W-FRI) 코호트 N<10 스킵, 상위 n=max(3, floor(0.1N)) 동일가중. 지표 = top − 같은 주 전체(비용 불변),
  NW t lag 4, 반기(T1 2017–21/22–26, T3 2012–18/19–26), 참고로 IWM 대비 net(−0.5%).
- 1차 질문: 주간 쌍대 차이 (iii) all − (i) price 의 평균과 NW t. t ≥ 2 & 반기 모두 양수 → "이벤트 정보가 가격만보다 더한다(후보)",
  t ≥ 3 → 확정 후보. 음수/0 근처 → 신호는 가격 기울기로 설명된다.
- 재현 확인: (iii) 의 주간 net 을 원 weekly CSV 의 M2_lgbm_reg 와 비교(최대 절대차 보고).

[X1-b 풀링 교차 이벤트 모델 (1차 검정 1회)]
- 풀 = T1 A_open 이벤트(실적, 다음 체결 가능 시가) ∪ T3 트레이더블 이벤트(내부자, filing 다음 시가). 같은 주 같은 종목이
  양쪽에 있어도 둘 다 유지.
- 피처: 공통 가격 ret20, ret60, vol20, lsize, ldvol20(T3 = log(max(dvol20,1))), hi52, lo52 + is_earn 원핫
  + T1 고유(sue1, sue2, sgn, beats4, nprev4, dsue, e_days_since) + T3 고유(n_day, n7, n30, is_ceo, is_cfo, is_dir, is_off,
  is_10pct, lval, pct_hold, lval_dvol, i_days_since). 해당 없으면 NaN.
  한계(사전 인지): lsize/ldvol20 은 T1(배당 조정 종가)·T3(원가격)로 정의가 약간 다르다. 그대로 쓴다.
- 워크포워드: 테스트 Y=2017..2026, 학습 = 풀 이벤트 중 청산일(exit_date = 각 코호트 달력[x20]) < Y-01-01.
  T3 는 2008+, T1 은 2014+ 부터 학습에 들어간다(불균형은 원핫으로 처리, 한계 명시).
- 주간: 풀 N<10 스킵, 상위 n=max(3, floor(0.1N)), 20일 보유(= 라벨).
- 1차: 풀 top − 같은 주 풀 전체, 주간 NW t lag 4. t ≥ 3 & 반기(2017–21/22–26) 모두 양수 → 확정 후보, 2~3 → 후보, 그 외 기각.
- 판별 진단(1차 통과 해석용, 사전 고정):
  (d1) 풀 선택의 코호트 비중(연도별). 풀 전체 비중과 비교.
  (d2) 코호트 내부 쌍대: 코호트 c 의 N_c≥10 주에서, 풀 점수로 c 내부 top n_c 를 고른 것 − c 전용 M2(X1-a (iii) 점수) top n_c.
       NW t. 양 코호트 모두 ≤ 0 이면 풀링은 코호트 선택 외에 더한 것이 없다고 본다.
  (d3) 단일 코호트 합집합 북: 각 코호트 N_c≥10 이면 c 전용 M2 top n_c 를 합쳐 동일가중 − 풀 전체. 풀 top 과의 쌍대 차이 NW t.
  (d4) 풀 가격 전용 모델(공통 가격 7 + is_earn): top − 풀 전체, 풀 전체 모델과의 쌍대 차이.
- 비교 주는 각 칸의 정의대로(교집합 주를 명시).

[X1-c 분산 북 (기술 통계, 합격 판정 없음)]
- T1 M2 주간 북 = weekly_A_open_H20.csv 의 M2_lgbm_reg(IWM 대비 net), T3 1차 = weekly_H20.csv 의 M3_lgbm_rank,
  민감도 = M2_lgbm_reg. 주 = 두 CSV 의 week 교집합(2017+). 결합 = 두 북 동일가중 평균.
- 두 기준으로 보고: (a) IWM 대비 net, (b) 자기 코호트 전체 대비(top − all). 평균·NW t lag 4·반기·상관계수
  (20일 겹침이라 상관의 단순 p값은 무효 — r 만 보고).
- R1 S1(일별 top-3, TP12/SL25/D7, 2025-09~2026-09)은 동격 북이 아니다: x_iwm/100 을 W-FRI 주 평균으로 만들어
  겹치는 주의 상관·3북 결합을 참고로만 보고. 헤드라인에서 제외.

[한계] T1·T3 생존편향(현재 상장 종목 가격만), R1 1년 표본, 모든 판정은 같은 모집단 내부 비교 위주.
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
import research_model_T1_earnings_20260927 as T1  # noqa: E402

OUT = ROOT / "data" / "analysis" / "model_X1_cross_event_20260928"
T3DIR = ROOT / "data" / "analysis" / "model_T3_form4_20260927"
R1DIR = ROOT / "data" / "analysis" / "model_R1_us_swing_s1_20260927"
COST = 0.005
LAG = 4

T1_PRICE = ["ret20", "ret60", "vol20", "ldvol20", "lsize", "month_sin", "month_cos"]
T1_EVENT = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since"]
T3_PRICE = ["ret20", "ret60", "vol20", "lsize", "hi52", "lo52"]
T3_EVENT = ["n_day", "n7", "n30", "is_ceo", "is_cfo", "is_dir", "is_off", "is_10pct", "lval", "pct_hold",
            "lval_dvol", "days_since"]
assert T1_PRICE + T1_EVENT == [f for f in T1.FEATS_A if f in T1_PRICE] + [f for f in T1.FEATS_A if f in T1_EVENT]
assert set(T1_PRICE + T1_EVENT) == set(T1.FEATS_A)

T3FEATS = ["n_day", "n7", "n30", "is_ceo", "is_cfo", "is_dir", "is_off", "is_10pct", "lval", "pct_hold",
           "lval_dvol", "hi52", "lo52", "ret20", "ret60", "vol20", "lsize", "days_since"]  # T3 원 FEATS 사본(순서 동일)
P_COMMON = ["ret20", "ret60", "vol20", "lsize", "ldvol20", "hi52", "lo52"]
E1 = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "e_days_since"]
E3 = ["n_day", "n7", "n30", "is_ceo", "is_cfo", "is_dir", "is_off", "is_10pct", "lval", "pct_hold",
      "lval_dvol", "i_days_since"]
POOL_ALL = P_COMMON + ["is_earn"] + E1 + E3
POOL_PRICE = P_COMMON + ["is_earn"]


def nw_t(x, lags=LAG):
    return T1.nw_t(x, lags)


def m2(tr, te, feats, y):
    import lightgbm as lgb
    ytr = tr[y].values
    lo, hi = np.nanpercentile(ytr, [1, 99])
    m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1)
    m.fit(tr[feats].values.astype(np.float32), np.clip(ytr, lo, hi))
    imp = dict(zip(feats, m.booster_.feature_importance("gain").tolist()))
    return m.predict(te[feats].values.astype(np.float32)), imp


def topn(N):
    return max(3, int(math.floor(0.1 * N)))


def summ(s, halves_split):
    s = pd.Series(s).dropna()
    yr = pd.to_datetime(s.index).year
    return {"mean": float(s.mean()), "t": nw_t(s.values), "weeks": int(len(s)),
            "h1": float(s[yr <= halves_split].mean()), "h2": float(s[yr > halves_split].mean())}


# ---------------------------------------------------------------- data
def load_t1():
    ev, _ = T1.load_events()
    cal, ti, W = T1.load_prices()
    tab, _ = T1.build(ev, cal, ti, W)
    C = W["close"]; T = len(cal)
    r = tab.ticker.map(ti).values; a = tab.e.values - 1
    idx = a[:, None] + np.arange(-251, 1)[None, :]
    valid = idx >= 0
    w = C[r[:, None], np.maximum(idx, 0)]
    w = np.where(valid, w, np.nan)
    ca = C[r, a]
    tab["hi52"] = ca / np.nanmax(w, axis=1) - 1
    tab["lo52"] = ca / np.nanmin(w, axis=1) - 1
    del W, C, w
    tab = tab[tab.gA20.notna()].reset_index(drop=True)
    tab["exit_date"] = cal[np.minimum(tab.x20.values, T - 1)]
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2012, 2027)}
    return tab, first_idx


def load_t3():
    tab = pd.read_csv(T3DIR / "events_table.csv.gz", parse_dates=["entry_date", "week", "fdate"],
                      dtype={"ticker": str})
    iw = pd.read_csv(T3DIR / "prices" / "batch_000.csv.gz")
    cal = pd.DatetimeIndex(pd.to_datetime(sorted(iw[iw.ticker == "IWM"].date.unique())))
    tab = tab[(tab.tradable == 1) & tab.g20.notna()].reset_index(drop=True)
    tab["exit_date"] = cal[np.minimum(tab.x20.values.astype(int), len(cal) - 1)]
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2012, 2027)}
    return tab, first_idx


# ---------------------------------------------------------------- X1-a
def weekly_top(P, score_cols, g):
    rows = []
    for wk, d in P.groupby("week"):
        N = len(d)
        if N < 10:
            continue
        n = topn(N); gv = d[g].values
        rec = {"week": wk, "N": N, "_cohort": gv.mean()}  # 점수 세트 이름 "all" 과 충돌 방지
        for c in score_cols:
            o = np.argsort(-d[c].values, kind="stable")
            rec[c] = gv[o[:n]].mean()
        rows.append(rec)
    return pd.DataFrame(rows).set_index("week")


def ablation(tab, first_idx, years, feats_price, feats_event, feats_all, g, split, name):
    assert set(feats_all) == set(feats_price + feats_event) and len(feats_all) == len(feats_price + feats_event)
    sets = {"price": feats_price, "event": feats_event, "all": feats_all}  # all = 원 피처 순서 그대로
    preds = []; imps = []
    for Y in years:
        tr = tab[(tab.year < Y) & (tab.x20 < first_idx[Y])]
        te = tab[tab.year == Y]
        if len(te) == 0:
            continue
        p = te[["week", "year", g]].copy()
        for k, f in sets.items():
            p[k], imp = m2(tr, te, f, g)
            if k == "all":
                imps.append(imp)
        preds.append(p)
        print(name, Y, len(tr), len(te), flush=True)
    P = pd.concat(preds)
    Wk = weekly_top(P, list(sets), g)
    res = {}
    for k in sets:
        res[k] = {"vs_all": summ(Wk[k] - Wk["_cohort"], split), "vs_iwm_net": summ(Wk[k] - COST, split)}
    res["paired_all_minus_price"] = summ(Wk["all"] - Wk["price"], split)
    res["paired_all_minus_event"] = summ(Wk["all"] - Wk["event"], split)
    res["cohort_all_vs_iwm_net"] = summ(Wk["_cohort"] - COST, split)
    res["importance_all"] = pd.DataFrame(imps).mean().sort_values(ascending=False).to_dict()
    return res, Wk, P


# ---------------------------------------------------------------- X1-b
def make_pool(t1, t3):
    a = pd.DataFrame({c: t1[c] for c in ["ticker", "week", "year", "exit_date", "ret20", "ret60", "vol20",
                                         "lsize", "ldvol20", "hi52", "lo52", "sue1", "sue2", "sgn", "beats4",
                                         "nprev4", "dsue"]})
    a["e_days_since"] = t1.days_since.values
    a["y"] = t1.gA20.values; a["is_earn"] = 1.0; a["score_single"] = t1["score_single"].values
    b = pd.DataFrame({c: t3[c] for c in ["ticker", "week", "year", "exit_date", "ret20", "ret60", "vol20",
                                         "lsize", "hi52", "lo52"] + E3[:-1]})
    b["ldvol20"] = np.log(np.maximum(t3.dvol20.values, 1))
    b["i_days_since"] = t3.days_since.values
    b["y"] = t3.g20.values; b["is_earn"] = 0.0; b["score_single"] = t3["score_single"].values
    pool = pd.concat([a, b], ignore_index=True, sort=False)
    for c in POOL_ALL:
        pool[c] = pool[c].astype(float)
    pool["week"] = pd.to_datetime(pool.week)
    return pool


def pooled(pool):
    preds = []; imps = []
    for Y in range(2017, 2027):
        tr = pool[pool.exit_date < pd.Timestamp(f"{Y}-01-01")]
        te = pool[pool.year == Y]
        p = te[["week", "year", "is_earn", "y", "score_single", "ticker"]].copy()
        p["pool_all"], imp = m2(tr, te, POOL_ALL, "y")
        imps.append(imp)
        p["pool_price"], _ = m2(tr, te, POOL_PRICE, "y")
        preds.append(p)
        print("pool", Y, len(tr), len(te), int(tr.is_earn.sum()), flush=True)
    return pd.concat(preds), pd.DataFrame(imps).mean().sort_values(ascending=False).to_dict()


def pooled_eval(P):
    rows = []; share = []
    for wk, d in P.groupby("week"):
        N = len(d)
        rec = {"week": wk, "N": N}
        gv = d.y.values
        if N >= 10:
            n = topn(N)
            rec["all"] = gv.mean()
            for c in ("pool_all", "pool_price"):
                o = np.argsort(-d[c].values, kind="stable")
                rec[c] = gv[o[:n]].mean()
                if c == "pool_all":
                    share.append({"week": wk, "sel_earn": d.is_earn.values[o[:n]].mean(),
                                  "all_earn": d.is_earn.mean()})
            # d3 single-cohort union
            sel = []
            for ie in (1.0, 0.0):
                dc = d[d.is_earn == ie]
                if len(dc) >= 10:
                    o = np.argsort(-dc.score_single.values, kind="stable")
                    sel.append(dc.y.values[o[:topn(len(dc))]])
            if sel:
                rec["single_union"] = np.concatenate(sel).mean()
        # d2 within-cohort
        for ie, nm in ((1.0, "T1"), (0.0, "T3")):
            dc = d[d.is_earn == ie]
            if len(dc) >= 10:
                n = topn(len(dc)); gc = dc.y.values
                rec[f"{nm}_all"] = gc.mean()
                rec[f"{nm}_pool"] = gc[np.argsort(-dc.pool_all.values, kind="stable")[:n]].mean()
                rec[f"{nm}_single"] = gc[np.argsort(-dc.score_single.values, kind="stable")[:n]].mean()
                rec[f"{nm}_poolprice"] = gc[np.argsort(-dc.pool_price.values, kind="stable")[:n]].mean()
        rows.append(rec)
    Wk = pd.DataFrame(rows).set_index("week")
    S = pd.DataFrame(share)
    return Wk, S


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    R = {}
    # ---- X1-a
    t3, fi3 = load_t3()
    ra3, wk3, P3 = ablation(t3, fi3, list(range(2012, 2027)), T3_PRICE, T3_EVENT, T3FEATS, "g20", 2018, "T3")
    R["X1a_T3"] = ra3
    t1, fi1 = load_t1()
    ra1, wk1, P1 = ablation(t1, fi1, list(range(2017, 2027)), T1_PRICE, T1_EVENT, T1.FEATS_A, "gA20", 2021, "T1")
    R["X1a_T1"] = ra1
    # reproduction check vs original weekly CSV (net vs IWM)
    o1 = pd.read_csv(ROOT / "data/analysis/model_T1_earnings_20260927/weekly_A_open_H20.csv",
                     parse_dates=["week"]).set_index("week")
    o3 = pd.read_csv(T3DIR / "weekly_H20.csv", parse_dates=["week"]).set_index("week")
    R["repro_maxabs_T1"] = float(((wk1["all"] - COST) - o1.M2_lgbm_reg).abs().max())
    R["repro_maxabs_T3"] = float(((wk3["all"] - COST) - o3.M2_lgbm_reg.reindex(wk3.index)).abs().max())
    wk1.to_csv(OUT / "x1a_weekly_T1.csv"); wk3.to_csv(OUT / "x1a_weekly_T3.csv")
    # single-cohort M2 scores (X1-a 'all') attached to tables
    t1["score_single"] = P1["all"].reindex(t1.index).values
    t3["score_single"] = P3["all"].reindex(t3.index).values
    # ---- X1-b
    pool = make_pool(t1[t1.year >= 2014], t3)
    del t1, t3
    Pp, imp_pool = pooled(pool)
    Wk, S = pooled_eval(Pp)
    Wk.to_csv(OUT / "x1b_weekly.csv")
    Pp[["week", "ticker", "is_earn", "y", "pool_all", "pool_price", "score_single"]].to_csv(
        OUT / "x1b_scores.csv.gz", index=False, compression="gzip")
    b = {}
    b["primary_pool_minus_all"] = summ(Wk.pool_all - Wk["all"], 2021)
    b["pool_net_vs_iwm"] = summ(Wk.pool_all - COST, 2021)
    b["pool_all_events_net_vs_iwm"] = summ(Wk["all"] - COST, 2021)
    b["d4_poolprice_minus_all"] = summ(Wk.pool_price - Wk["all"], 2021)
    b["d4_pool_minus_poolprice"] = summ(Wk.pool_all - Wk.pool_price, 2021)
    b["d3_single_union_minus_all"] = summ(Wk.single_union - Wk["all"], 2021)
    b["d3_pool_minus_single_union"] = summ(Wk.pool_all - Wk.single_union, 2021)
    for nm in ("T1", "T3"):
        b[f"d2_{nm}_pool_minus_cohort_all"] = summ(Wk[f"{nm}_pool"] - Wk[f"{nm}_all"], 2021)
        b[f"d2_{nm}_single_minus_cohort_all"] = summ(Wk[f"{nm}_single"] - Wk[f"{nm}_all"], 2021)
        b[f"d2_{nm}_pool_minus_single"] = summ(Wk[f"{nm}_pool"] - Wk[f"{nm}_single"], 2021)
        b[f"d2_{nm}_poolprice_minus_cohort_all"] = summ(Wk[f"{nm}_poolprice"] - Wk[f"{nm}_all"], 2021)
    S["year"] = pd.to_datetime(S.week).dt.year
    b["d1_share_by_year"] = S.groupby("year")[["sel_earn", "all_earn"]].mean().round(3).to_dict("index")
    b["d1_share_total"] = {"sel_earn": float(S.sel_earn.mean()), "all_earn": float(S.all_earn.mean())}
    yr = pd.to_datetime(Wk.index).year
    b["primary_by_year"] = (Wk.pool_all - Wk["all"]).groupby(yr).mean().round(4).to_dict()
    b["events"] = {"T1": int(pool.is_earn.sum()), "T3": int((pool.is_earn == 0).sum()),
                   "test_weeks": int(Wk["all"].notna().sum()), "pool_selected_weeks_n_median": float(
                       Wk.N[Wk["all"].notna()].apply(topn).median())}
    b["importance_pool_all"] = imp_pool
    R["X1b"] = b
    # ---- X1-c
    c = {}
    for t3m in ("M3_lgbm_rank", "M2_lgbm_reg"):
        j = pd.DataFrame({"T1_net": o1.M2_lgbm_reg, "T1_rel": o1.M2_lgbm_reg - o1["all"]}).join(
            pd.DataFrame({"T3_net": o3[t3m], "T3_rel": o3[t3m] - o3["all"]}), how="inner")
        j = j[j.index >= "2017-01-01"]
        cc = {"weeks": int(len(j))}
        for basis in ("net", "rel"):
            comb = (j[f"T1_{basis}"] + j[f"T3_{basis}"]) / 2
            cc[basis] = {"T1": summ(j[f"T1_{basis}"], 2021), "T3": summ(j[f"T3_{basis}"], 2021),
                         "combined": summ(comb, 2021), "corr": float(j[f"T1_{basis}"].corr(j[f"T3_{basis}"]))}
        c[t3m] = cc
        if t3m == "M3_lgbm_rank":
            j.to_csv(OUT / "x1c_weekly_T1M2_T3M3.csv")
            jj = j
    r1 = pd.read_csv(R1DIR / "s1_top3_picks.csv", parse_dates=["session_date"])
    r1["week"] = r1.session_date.dt.to_period("W-FRI").dt.start_time
    r1w = (r1.groupby("week").x_iwm.mean() / 100).rename("R1_net")
    k = jj.join(r1w, how="inner")
    c["R1_supplement"] = {"weeks_overlap": int(len(k)), "r1_weeks_total": int(len(r1w)),
                          "corr_R1_T1": float(k.R1_net.corr(k.T1_net)) if len(k) > 3 else None,
                          "corr_R1_T3": float(k.R1_net.corr(k.T3_net)) if len(k) > 3 else None,
                          "R1": summ(k.R1_net, 2025) if len(k) > 5 else None,
                          "T1T3_same_weeks": summ((k.T1_net + k.T3_net) / 2, 2025) if len(k) > 5 else None,
                          "three_book": summ((k.T1_net + k.T3_net + k.R1_net) / 3, 2025) if len(k) > 5 else None,
                          "note": "R1 x_iwm 은 비용 0.5%p 가 이미 차감된 net 기준; 7세션 보유; 반기 split=2025"}
    R["X1c"] = c
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in R.items()}, indent=1, default=str, ensure_ascii=False)[:20000])


if __name__ == "__main__":
    main()
