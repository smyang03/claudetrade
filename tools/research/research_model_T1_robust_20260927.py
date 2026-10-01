"""T1 강건성 검사 — A_open·H20 M2(LightGBM 회귀)/M4(MLP) 후보 셀 (2026-09-27, 사전등록)

원 연구: tools/research/research_model_T1_earnings_20260927.py (이 파일은 원본을 import만 하고 수정하지 않는다).
후보 셀: A_open(발표 후 첫 체결 가능 세션 시가 진입) · H20(20세션 보유) · M2 LGBM 회귀 / M4 MLP.
원 결과: M2 주간 net vs IWM +0.97%(t 2.22), vs M0(SUE) 차이 t 2.45, vs 같은 주 전체 t_diff_all 3.08(원 실행에
이미 계산돼 있던 값 — 아래 R-a 헤드라인은 blind 검정이 아니다). M4 차이 t 2.30, t_diff_all 2.55.

=== 사전등록 (계산 전 고정. 결과를 본 뒤 규칙 변경 금지. 이탈은 보고서에 '이탈'로 기록) ===

[예측 점수]
- 원 실행은 이벤트 단위 점수를 저장하지 않았다(weekly CSV만 있음) → 원 파이프라인을 그대로 재실행한다.
  원 모듈의 load_events / load_prices / build / nw_t 를 import해 쓰고, 학습 함수만 로컬 복제한다.
  M2: LGBMRegressor 기본값, random_state 0, 타깃 = gA20 학습 1/99 분위 winsorize.
  M4: MLPRegressor((32,16), early_stopping, random_state 0, max_iter 200), 학습 중앙값 대치+StandardScaler,
      타깃 = winsorize 후 표준화. 워크포워드 = 테스트 연도 2017..2026, 학습 = 진입연도 < Y & 청산 인덱스 < Y 첫 세션.
- 유일한 이탈(사전 인지): LightGBM n_jobs 4 → 2 (운영 메모리/CPU 제약). 스레드 수 차이로 트리가 달라질 수 있어
  재실행 주간 M2/M4 net을 원 weekly_A_open_H20.csv와 주 단위 비교하고 최대 |차|를 보고한다.
  차이가 있으면 R-b/c/f/g 는 재실행 점수로 일관되게 계산하고 R-a/R-d/R-e 는 원 CSV와 재실행 둘 다 보고한다
  (판정은 원 CSV 기준 — 원 사전등록 산출물이므로. 재실행과 판정이 갈리면 그 사실을 명시).
- 점수 캐시: data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz (있으면 재사용).

[공통 평가]
- 주 = 진입 세션 W-FRI 주. 주 이벤트 수 N<10이면 스킵. 선택 n = max(3, floor(0.1·N)) 상위 점수, 동일가중.
  M0 동률은 sue2. 비용 기본 0.5% 왕복. NW lag = 4. 반기 h1 = 주 시작연도 ≤2021, h2 = 2022~2026.
- 부분집합 검사(R-b, R-c)는 모집단을 좁힌 뒤 N≥10 스킵·n·"all"을 **다시** 계산한다. 점수는 전체 모집단으로
  학습한 같은 점수(학습 오염은 잔존 — 한계로 기록).

[검사]
R-a 같은 모집단 내부: 주간 (모델 top-10% net) − (그 주 전체 이벤트 동일가중 net). 비용은 양쪽 동일 → 비용 불변.
    지표: 평균, NW t, 반기 평균.
R-b 안정 생존 부분표본: 티커의 첫 유효 close ≤ 2014-06-30 AND 첫 유효일~마지막 달력일 사이 결측 세션 비율 ≤ 1%.
    이 티커만으로 재평가 — vs 같은 주 전체(R-a 지표, 판정용), vs IWM net(참고).
R-c 추정치 수정 위험: 원본 jsonl(confirmed & EPS 존재)에서 (ticker,date) 그룹 eps_est nunique>1 또는
    eps_act nunique>1 인 이벤트 = 플래그. 1차(판정용) = 플래그 이벤트 제외. 2차(감도) = 플래그가 한 번이라도 있는
    티커 전부 제외. 원 날짜정정 병합(2건)으로 키가 어긋날 수 있음. 지표는 R-b와 같다.
R-d 연도별: 연도별 주간 net vs IWM 평균, R-a 차이 평균, 주 수, R-a 차이의 연도 합 / 전체 합(기여 비중).
    2026은 9월까지 부분연도.
R-e 비용 민감도(US): 왕복 비용 c ∈ {0.10, 0.20, 0.44, 0.50, 0.60}% 에서 주간 net vs IWM 평균·NW t
    (M0, M2, M4, 전체). 손익분기 = 주간 gross 평균(및 이벤트 gross 평균). R-a 차이는 비용 불변이라 제외.
R-f 용량·현실성: 주별 선택 수 n 분포, 4주 겹침 동시 보유 수, 선택 이벤트 close[a] 중앙값, 20일 평균 거래대금
    중앙값, close<$5 비중, 거래대금<$5M 비중.
    R-f2(참고, 판정 불포함): close≥$5 & 거래대금≥$5M 모집단에서 재선택한 R-a.
R-g(참고, 판정 불포함, 에이전트 추가): 실행 가능성. 주간 top-10%는 월요일에 그 주 N을 몰라 그대로 구현 불가.
    대체 = 진입 세션별 코호트(같은 날 시가 진입 이벤트)에서 N_day ≥ 5 이면 상위 ceil(0.1·N_day) 선택.
    주 단위로 모아 (선택 평균 − 그 주 전체) 차이, NW t, 반기.
R-h(사후 추가 — 1차 결과를 본 뒤 추가, 판정 불포함): 자본 캡 산수용. 진입 세션 코호트(N_day ≥ 5)에서 점수 1위 1건만
    선택(하루 최대 1종목). 주 단위 차이·IWM net·연도별 차이. 쉐도우 스펙의 캡 근거로만 쓴다.

[판정 규칙 — M2, M4 각각 적용(검정 2회)]
T1 → "확정 후보(shadow 권고)" 승격 조건(모두 충족):
  (1) R-a t ≥ 2.5 AND 반기 R-a 평균 둘 다 > 0
  (2) R-b의 R-a 지표 평균 > 0 AND t ≥ 2
  (3) R-c 1차의 R-a 지표 평균 > 0 AND t ≥ 2
  (4) R-d: R-a 차이 연도 합 중 어느 한 해도 전체 합의 40% 초과 금지 (전체 합 ≤ 0이면 불충족)
  1차 지표는 vs-all 차이(생존편향 중립). vs IWM 기준으로 (2)(3)을 적용하면 결과가 달라지는지 따로 표기한다.
  하나라도 불충족 → "후보" 유지(또는 (1) 불충족 시 강등 검토). 승격돼도 20셀 다중검정·생존편향 절대 수준은 이
  검사로 해소되지 않는다 — 등급 상한은 "확정 후보(shadow 권고)".
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import warnings

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T1_earnings_20260927 as t1  # noqa: E402

SRC = ROOT / "data" / "analysis" / "model_T1_earnings_20260927"
OUT = ROOT / "data" / "analysis" / "model_T1_robust_20260927"
SCORES = OUT / "scores_A_open_H20.csv.gz"
H = 20
GCOL, XCOL = "gA20", "x20"
LAGS = 4
COST = 0.005
MODELS = ("M0_sue", "M2_lgbm_reg", "M4_mlp")


# ---------------------------------------------------------------- scoring
def fit_predict(model, tr, te, feats, y):
    from sklearn.preprocessing import StandardScaler
    Xtr = tr[feats].values.astype(np.float32); Xte = te[feats].values.astype(np.float32)
    ytr = tr[y].values
    lo, hi = np.nanpercentile(ytr, [1, 99]); yw = np.clip(ytr, lo, hi)
    if model == "M4_mlp":
        from sklearn.neural_network import MLPRegressor
        med = np.nanmedian(Xtr, axis=0)
        Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
        sc = StandardScaler().fit(Xtr); Xtr = sc.transform(Xtr); Xte = sc.transform(Xte)
        mu, sd = yw.mean(), yw.std()
        m = MLPRegressor(hidden_layer_sizes=(32, 16), early_stopping=True, random_state=0, max_iter=200)
        m.fit(Xtr, (yw - mu) / sd)
        return m.predict(Xte)
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1).fit(Xtr, yw)
    return m.predict(Xte)


def make_scores():
    ev, info = t1.load_events()
    cal, ti, W = t1.load_prices()
    tab, _ = t1.build(ev, cal, ti, W)
    # R-b: 티커별 첫 유효일, 결측 비율
    C = W["close"]
    valid = ~np.isnan(C)
    first = valid.argmax(axis=1)
    has = valid.any(axis=1)
    span = C.shape[1] - first
    miss = 1 - valid.sum(axis=1) / np.maximum(span, 1)
    inv = {i: t for t, i in ti.items()}
    tinfo = pd.DataFrame({"ticker": [inv[i] for i in range(len(inv))],
                          "first_date": [cal[f].date() if h else None for f, h in zip(first, has)],
                          "miss_share": miss})
    del W, C
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2017, 2027)}
    d = tab[tab[GCOL].notna()]
    preds = []
    for Y in range(2017, 2027):
        tr = d[(d.year < Y) & (d[XCOL] < first_idx[Y])]
        te = d[d.year == Y]
        p = te[["ticker", "date", "hour", "entry_date", "e", "week", "year", "sue1", "sue2", "close_a",
                "dvol20", GCOL]].copy()
        p["M0_sue"] = te.sue1.values
        for m in ("M2_lgbm_reg", "M4_mlp"):
            p[m] = fit_predict(m, tr, te, t1.FEATS_A, GCOL)
        preds.append(p)
        print("fit", Y, len(tr), len(te), flush=True)
    P = pd.concat(preds, ignore_index=True)
    P.to_csv(SCORES, index=False, compression="gzip")
    tinfo.to_csv(OUT / "ticker_history.csv", index=False)
    return P, tinfo


# ---------------------------------------------------------------- weekly
def weekly(P, min_n=10):
    """주별 gross(선택 평균)·all gross·선택 이벤트 기록."""
    rows, sel = [], []
    for wk, g in P.groupby("week"):
        N = len(g)
        if N < min_n:
            continue
        n = max(3, int(math.floor(0.1 * N)))
        rec = {"week": pd.Timestamp(wk), "N": N, "n": n, "all_g": g[GCOL].mean()}
        gv = g[GCOL].values
        for m in MODELS:
            if m == "M0_sue":
                order = np.lexsort((-g.sue2.values, -g[m].values))
            else:
                order = np.argsort(-g[m].values, kind="stable")
            idx = order[:n]
            rec[m + "_g"] = gv[idx].mean()
            if m != "M0_sue":
                s = g.iloc[idx][["ticker", "entry_date", "close_a", "dvol20", GCOL]].copy()
                s["model"] = m; s["week"] = pd.Timestamp(wk)
                sel.append(s)
        rows.append(rec)
    Wk = pd.DataFrame(rows)
    Wk["year"] = Wk.week.dt.year
    Wk["half"] = np.where(Wk.year <= 2021, "h1", "h2")
    return Wk, (pd.concat(sel, ignore_index=True) if sel else pd.DataFrame())


def summ(x, half):
    x = pd.Series(np.asarray(x, float))
    half = np.asarray(half)
    return {"mean": float(x.mean()), "t": t1.nw_t(x.values, LAGS),
            "h1": float(x[half == "h1"].mean()), "h2": float(x[half == "h2"].mean()), "weeks": int(len(x))}


def block(Wk, cost=COST):
    out = {"_all_net": summ(Wk.all_g - cost, Wk.half)}
    for m in MODELS:
        out[m] = {"net_vs_iwm": summ(Wk[m + "_g"] - cost, Wk.half),
                  "diff_vs_all": summ(Wk[m + "_g"] - Wk.all_g, Wk.half)}
    return out


def daily_top1(P):
    """R-h(사후): 진입 세션 코호트(N_day>=5) 점수 1위만."""
    res = {}
    for m in ("M2_lgbm_reg", "M4_mlp"):
        rows = []
        for wk, g in P.groupby("week"):
            if len(g) < 10:
                continue
            picks = [gd[GCOL].values[np.argmax(gd[m].values)] for _, gd in g.groupby("e") if len(gd) >= 5]
            if picks:
                rows.append({"week": pd.Timestamp(wk), "all_g": g[GCOL].mean(), "g": np.mean(picks),
                             "k": len(picks)})
        w = pd.DataFrame(rows); w["year"] = w.week.dt.year
        w["half"] = np.where(w.year <= 2021, "h1", "h2")
        d = w.g - w.all_g
        res[m] = {"diff_vs_all": summ(d, w.half), "net_vs_iwm": summ(w.g - COST, w.half),
                  "picks_per_week_median": float(w.k.median()), "picks_total": int(w.k.sum()),
                  "yearly_diff": {int(y): float((g.g - g.all_g).mean()) for y, g in w.groupby("year")}}
    return res


def daily_cohort(P):
    """R-g: 진입 세션 코호트별 top ceil(10%) (N_day>=5), 주 단위 집계 vs 주 전체."""
    rows = []
    for wk, g in P.groupby("week"):
        if len(g) < 10:
            continue
        rec = {"week": pd.Timestamp(wk), "all_g": g[GCOL].mean()}
        for m in ("M2_lgbm_reg", "M4_mlp"):
            picks = []
            for _, gd in g.groupby("e"):
                if len(gd) < 5:
                    continue
                k = int(math.ceil(0.1 * len(gd)))
                picks.extend(gd.nlargest(k, m)[GCOL].tolist())
            rec[m + "_g"] = np.mean(picks) if picks else np.nan
            rec[m + "_k"] = len(picks)
        rows.append(rec)
    Wk = pd.DataFrame(rows)
    Wk["half"] = np.where(Wk.week.dt.year <= 2021, "h1", "h2")
    res = {}
    for m in ("M2_lgbm_reg", "M4_mlp"):
        w = Wk[Wk[m + "_g"].notna()]
        res[m] = {"diff_vs_all": summ(w[m + "_g"] - w.all_g, w.half),
                  "net_vs_iwm": summ(w[m + "_g"] - COST, w.half),
                  "picks_per_week_median": float(w[m + "_k"].median())}
    return res


def flagged_keys():
    d = pd.read_json(t1.EVT, lines=True)
    c = d[d.confirmed & d.eps_act.notna() & d.eps_est.notna()]
    g = c.groupby(["ticker", "date"]).agg(nest=("eps_est", "nunique"), nact=("eps_act", "nunique")).reset_index()
    f = g[(g.nest > 1) | (g.nact > 1)]
    return set(zip(f.ticker, pd.to_datetime(f.date))), set(f.ticker), len(f)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    if SCORES.exists() and (OUT / "ticker_history.csv").exists():
        P = pd.read_csv(SCORES, parse_dates=["date", "entry_date", "week"])
        tinfo = pd.read_csv(OUT / "ticker_history.csv")
        print("scores reused", len(P))
    else:
        P, tinfo = make_scores()
        P["date"] = pd.to_datetime(P.date); P["week"] = pd.to_datetime(P.week)
    R = {"n_events_test": int(len(P))}

    # --- 재현 확인
    Wk, sel = weekly(P)
    orig = pd.read_csv(SRC / "weekly_A_open_H20.csv", parse_dates=["week"])
    mg = Wk.merge(orig, on="week", how="outer", indicator=True)
    rep = {"weeks_rerun": int(len(Wk)), "weeks_orig": int(len(orig)),
           "weeks_unmatched": int((mg._merge != "both").sum())}
    b = mg[mg._merge == "both"]
    for m in MODELS:
        rep[m + "_max_abs_diff"] = float(np.abs((b[m + "_g"] - COST) - b[m]).max())
    rep["all_max_abs_diff"] = float(np.abs((b.all_g - COST) - b["all"]).max())
    R["reproduction"] = rep
    print("reproduction", rep, flush=True)

    # 원 CSV 기반 주간 프레임(판정용 R-a/R-d/R-e)
    Wo = pd.DataFrame({"week": orig.week, "all_g": orig["all"] + COST})
    for m in MODELS:
        Wo[m + "_g"] = orig[m] + COST
    Wo["year"] = Wo.week.dt.year
    Wo["half"] = np.where(Wo.year <= 2021, "h1", "h2")

    # R-a
    R["R_a_orig"] = block(Wo)
    R["R_a_rerun"] = block(Wk)

    # R-b
    tinfo["first_date"] = pd.to_datetime(tinfo.first_date)
    stable = set(tinfo[(tinfo.first_date <= "2014-06-30") & (tinfo.miss_share <= 0.01)].ticker)
    Pb = P[P.ticker.isin(stable)]
    Wb, _ = weekly(Pb)
    R["R_b"] = {"stable_tickers": len(stable), "tickers_in_test": int(P.ticker.nunique()),
                "tickers_kept": int(Pb.ticker.nunique()), "events_kept": int(len(Pb)), **block(Wb)}

    # R-c
    fk, ftk, nflag = flagged_keys()
    key = list(zip(P.ticker, P.date))
    is_f = np.array([k in fk for k in key])
    Pc1 = P[~is_f]; Pc2 = P[~P.ticker.isin(ftk)]
    Wc1, _ = weekly(Pc1); Wc2, _ = weekly(Pc2)
    R["R_c"] = {"flag_groups": nflag, "flag_tickers": len(ftk), "flag_events_in_test": int(is_f.sum()),
                "primary_drop_events": block(Wc1),
                "sensitivity_drop_tickers": {"events_kept": int(len(Pc2)), **block(Wc2)}}

    # R-d
    def yearly(Wx):
        rows = []
        for m in ("M2_lgbm_reg", "M4_mlp", "M0_sue"):
            d = Wx[m + "_g"] - Wx.all_g
            tot = d.sum()
            for y, g in Wx.groupby("year"):
                dd = g[m + "_g"] - g.all_g
                rows.append({"model": m, "year": int(y), "weeks": int(len(g)),
                             "net_vs_iwm": float((g[m + "_g"] - COST).mean()),
                             "diff_vs_all": float(dd.mean()), "diff_sum": float(dd.sum()),
                             "share_of_total": float(dd.sum() / tot) if tot > 0 else float("nan")})
        return rows
    R["R_d_orig"] = yearly(Wo)
    R["R_d_rerun"] = yearly(Wk)

    # R-e
    R["R_e_orig"] = {}
    for c in (0.001, 0.002, 0.0044, 0.005, 0.006):
        R["R_e_orig"][f"{c:.4f}"] = {m: summ(Wo[m + "_g"] - c, Wo.half) for m in MODELS} | \
                                    {"all": summ(Wo.all_g - c, Wo.half)}
    R["R_e_breakeven"] = {m: {"weekly_gross_mean": float(Wo[m + "_g"].mean())} for m in MODELS}
    R["R_e_breakeven"]["all"] = {"weekly_gross_mean": float(Wo.all_g.mean())}
    for m in MODELS[1:]:
        R["R_e_breakeven"][m]["event_gross_mean_rerun"] = float(sel.loc[sel.model == m, GCOL].mean())

    # R-f
    Wk_sorted = Wk.sort_values("week")
    conc = Wk_sorted.n.rolling(4, min_periods=4).sum()
    rf = {"n_per_week": {"median": float(Wk.n.median()), "min": int(Wk.n.min()), "max": int(Wk.n.max()),
                         "p90": float(Wk.n.quantile(0.9))},
          "concurrent_positions_4wk": {"median": float(conc.median()), "max": float(conc.max())}}
    for m in ("M2_lgbm_reg", "M4_mlp"):
        s = sel[sel.model == m]
        rf[m] = {"price_median": float(s.close_a.median()), "dvol20_median": float(s.dvol20.median()),
                 "share_price_lt5": float((s.close_a < 5).mean()), "share_dvol_lt5m": float((s.dvol20 < 5e6).mean()),
                 "gross_mean_lt5m": float(s.loc[s.dvol20 < 5e6, GCOL].mean()),
                 "gross_mean_ge5m": float(s.loc[s.dvol20 >= 5e6, GCOL].mean())}
    allp = P
    rf["population"] = {"price_median": float(allp.close_a.median()), "dvol20_median": float(allp.dvol20.median()),
                        "share_price_lt5": float((allp.close_a < 5).mean()),
                        "share_dvol_lt5m": float((allp.dvol20 < 5e6).mean())}
    Pf2 = P[(P.close_a >= 5) & (P.dvol20 >= 5e6)]
    Wf2, _ = weekly(Pf2)
    rf["R_f2_liquid_only"] = {"events_kept": int(len(Pf2)), **block(Wf2)}
    R["R_f"] = rf

    # R-g
    R["R_g_daily_cohort"] = daily_cohort(P)
    R["R_h_posthoc_daily_top1"] = daily_top1(P)

    # 판정
    verdict = {}
    for m in ("M2_lgbm_reg", "M4_mlp"):
        a = R["R_a_orig"][m]["diff_vs_all"]
        bb = R["R_b"][m]["diff_vs_all"]
        cc = R["R_c"]["primary_drop_events"][m]["diff_vs_all"]
        shares = [r["share_of_total"] for r in R["R_d_orig"] if r["model"] == m]
        c1 = a["t"] >= 2.5 and a["h1"] > 0 and a["h2"] > 0
        c2 = bb["mean"] > 0 and bb["t"] >= 2
        c3 = cc["mean"] > 0 and cc["t"] >= 2
        c4 = all(np.isfinite(shares)) and max(shares) <= 0.40
        bi = R["R_b"][m]["net_vs_iwm"]; ci = R["R_c"]["primary_drop_events"][m]["net_vs_iwm"]
        verdict[m] = {"c1_Ra": bool(c1), "c2_Rb": bool(c2), "c3_Rc": bool(c3), "c4_Rd": bool(c4),
                      "max_year_share": float(max(shares)) if all(np.isfinite(shares)) else None,
                      "upgrade": bool(c1 and c2 and c3 and c4),
                      "alt_iwm_c2": bool(bi["mean"] > 0 and bi["t"] >= 2),
                      "alt_iwm_c3": bool(ci["mean"] > 0 and ci["t"] >= 2)}
    R["verdict"] = verdict
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str, ensure_ascii=False))
    Wk.to_csv(OUT / "weekly_rerun_A_open_H20.csv", index=False)
    sel.to_csv(OUT / "selected_rerun_A_open_H20.csv.gz", index=False, compression="gzip")
    print(json.dumps(verdict, indent=1))


if __name__ == "__main__":
    main()
