"""T1 — US 실적 서프라이즈 모델 연구 (2026-09-27, 사전등록)

질문: US EPS 서프라이즈(가격 밖의 새 정보)가 발표 후 IWM 대비 초과수익(우리 비용 0.5% 왕복 후)을
예측하는가, 어떤 아키텍처가 단순 SUE 정렬 기준선보다 나은가.

=== 사전등록 (실행 전 고정, 결과를 본 뒤 규칙 변경 금지. 이탈은 보고서에 '이탈'로 기록) ===

[데이터]
- 이벤트: data/analysis/us_earnings_dates.jsonl, confirmed=True & eps_act·eps_est 존재.
- 중복 정리: (ticker,date) 중복은 fetched_at 최신 1건. 이후 같은 티커에서 |Δdate|≤7일이고 eps_act가
  같은 연속 이벤트는 날짜 정정 중복으로 보고 fetched_at 최신 1건만 유지(합친 건수 보고).
- hour: AMC → 발표일 이후 첫 세션 시가 진입. BMO → 발표일 당일(휴장이면 다음) 세션 시가 진입.
  DMH(장중 발표)는 앵커 종가에 반응이 섞일 수 있어 **제외**. hour 결측은 AMC 취급.
- 가격: yfinance 일봉 auto_adjust=True, 2013-06-01~2026-09-26, 배치 100, 실패는 건너뛰고 기록.
  벤치 IWM(판정), SPY(참고). 거래일 달력 = IWM 인덱스.
- 앵커 세션 a = 진입 세션 e의 직전 세션. 모든 가격 피처는 close[a]까지(포함)만 사용.
- 트레이더빌리티 필터: close[a] ≥ $3, 20일 평균 거래대금(a 포함) ≥ $1M, open[e]>0, 청산 가격 존재.
- 학습/평가 이벤트: 진입연도 2014~2026. 2013-06~12 이벤트는 이력 피처(streak·직전 서프라이즈) 계산용만.

[진입 변형 2종]
- A(open): open[e] 진입 → open[e+H] 청산. IWM도 open[e]→open[e+H].
- B(post-reaction): close[e] 진입 → close[e+H] 청산. IWM도 close[e]→close[e+H].
  B만 추가 피처: 반응일 수익 react=close[e]/close[a]−1, 갭 gap=open[e]/close[a]−1, react−IWM 같은날.
- 보유 H ∈ {5, 20} 세션. gross 초과 = r_stock − r_IWM, net 초과 = gross − 0.005.

[피처 (A 기준, 전부 결정 시점 이전)]
- sue1 = clip((act−est)/max(|est|,0.05), −5, 5); sue2 = clip((act−est)/close[a], −0.2, 0.2); sign(act−est)
- beats4 = 직전 최대 4개 이벤트 중 beat(act>est) 수, nprev4 = 직전 이벤트 수(최대 4)
- dsue = sue1 − 직전 이벤트 sue1; days_since = 직전 이벤트와의 일수
- ret20 = close[a]/close[a−20]−1, ret60, vol20 = 일간 로그수익 표준편차(a−19..a),
  ldvol20 = log(평균 close×volume, a−19..a), lsize = log(close[a]×평균 volume a−59..a)
- month_sin, month_cos(진입월)

[아키텍처 5종 — 기본 파라미터, 튜닝 없음(내부 CV 튜닝도 하지 않음), 시드 고정]
- M0 baseline: 점수 = sue1 (동률은 sue2).
- M1 logistic: LogisticRegression(max_iter=2000), 라벨 net 초과>0, StandardScaler+학습 중앙값 대치.
- M2 LightGBM 회귀: LGBMRegressor 기본값(n_jobs=4, seed 0), 타깃 = gross 초과를 학습 1/99 분위 winsorize.
- M3 LightGBM LambdaRank: LGBMRanker(objective=lambdarank) 기본값, query=진입 주(W-FRI),
  relevance = 주 내부 gross 초과 5분위(0~4).
- M4 MLP: MLPRegressor((32,16), early_stopping=True, random_state 0), 표준화 피처, 타깃 = winsorize 후 표준화.

[워크포워드]
- 테스트 연도 Y=2017..2026, 학습 = 진입연도 2014..Y−1 중 청산 세션 인덱스 < Y 첫 세션 인덱스(퍼지).
  연 1회 확장 재학습.

[평가 포트폴리오]
- 주 = 진입 세션의 W-FRI 주. 테스트 이벤트 N<10인 주는 모든 전략에서 공통 스킵.
- 매주 점수 상위 n = max(3, floor(0.1·N)) 동일가중, H 보유. 비교: (a) 그 주 전체 동일가중, (b) M0 기준선.
- 지표: 선택 이벤트 net 초과 평균/중앙값(이벤트 단위), 주간 시계열 평균과 Newey-West t
  (lag = ceil(H/5)주: H5→1, H20→4), 주간 차이(모델−기준선) NW t, 반기(2017–2021 / 2022–2026) 부호,
  주간 Spearman IC(점수 vs gross 초과, 전체 이벤트) 평균과 NW t, 손익분기 비용 = 선택 이벤트 gross 초과 평균
  (보유 후 전량 교체라 회전율 = 1왕복/건).
- 합격(모델 M1~M4): 주간 차이(모델−M0) NW t ≥ 3.0 AND 반기 모두 [모델 net>0 AND 차이>0]
  AND 손익분기 비용 ≥ 1.0%(= 0.5%×2).
- 합격(M0 셀): 주간 net 초과 NW t ≥ 3.0 AND 반기 모두 net>0 AND 손익분기 ≥ 1.0%.
- 다중검정: 2 변형 × 2 H × 5 모델 = 20셀. t≥3 문턱이 보정 기준.
- 등급: 합격 셀 있음 = 확정 후보(확정은 쉐도우 forward 후) / 차이 t≥2 & 반기 부호 유지 = 후보 / 그 외 기각.

[한계(사전 인지)]
- 생존편향: 티커는 현재 이름(상폐 종목 가격 없음) → 절대 수준이 아니라 같은 모집단 내부 비교로만 판정.
- eps_est는 조회 시점 값(비PIT 가능) — 스냅샷 간 불일치 건수 보고.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

import warnings
import numpy as np
warnings.filterwarnings("ignore", message="X does not have valid feature names")
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T1_earnings_20260927"
PX = OUT / "prices"
EVT = ROOT / "data" / "analysis" / "us_earnings_dates.jsonl"
START, END = "2013-06-01", "2026-09-27"
COST = 0.005
HS = (5, 20)
VARIANTS = ("A_open", "B_close")
MODELS = ("M0_sue", "M1_logit", "M2_lgbm_reg", "M3_lgbm_rank", "M4_mlp")


# ---------------------------------------------------------------- events
def load_events():
    d = pd.read_json(EVT, lines=True)
    raw_n = len(d)
    c = d[d.confirmed & d.eps_act.notna() & d.eps_est.notna()].copy()
    g = c.groupby(["ticker", "date"]).agg(nest=("eps_est", "nunique"), nact=("eps_act", "nunique"))
    info = {"raw_rows": raw_n, "confirmed_with_eps": len(c),
            "snapshot_est_mismatch": int((g.nest > 1).sum()),
            "snapshot_act_mismatch": int((g.nact > 1).sum())}
    c = c.sort_values("fetched_at").drop_duplicates(["ticker", "date"], keep="last")
    info["dedup_ticker_date"] = len(c)
    c = c.sort_values(["ticker", "date"]).reset_index(drop=True)
    # merge date-correction duplicates
    keep = np.ones(len(c), bool)
    tk = c.ticker.values; dt = c.date.values; act = c.eps_act.values; fa = c.fetched_at.values
    i = 0
    merged = 0
    for j in range(1, len(c)):
        if tk[j] == tk[j - 1] and (dt[j] - dt[j - 1]) <= np.timedelta64(7, "D") and act[j] == act[j - 1] \
                and keep[j - 1]:
            # keep latest fetched
            if fa[j] >= fa[j - 1]:
                keep[j - 1] = False
            else:
                keep[j] = False
            merged += 1
    c = c[keep].reset_index(drop=True)
    info["merged_date_corrections"] = merged
    c = c[c.date >= "2013-06-01"].reset_index(drop=True)
    info["events_since_2013_06"] = len(c)
    info["hour_counts"] = c.hour.fillna("NA").value_counts().to_dict()
    # history features (on full dedup table, before DMH exclusion — DMH surprise is still past info)
    c["surp"] = c.eps_act - c.eps_est
    c["sue1"] = np.clip(c.surp / np.maximum(c.eps_est.abs(), 0.05), -5, 5)
    c["beat"] = (c.surp > 0).astype(float)
    g = c.groupby("ticker")
    c["beats4"] = g.beat.transform(lambda s: s.shift(1).rolling(4, min_periods=1).sum())
    c["nprev4"] = g.beat.transform(lambda s: s.shift(1).rolling(4, min_periods=1).count())
    c["prev_sue1"] = g.sue1.shift(1)
    c["dsue"] = c.sue1 - c.prev_sue1
    c["days_since"] = g.date.diff().dt.days.astype(float)
    c["nprev4"] = c.nprev4.fillna(0)
    return c, info


# ---------------------------------------------------------------- prices
def download(tickers):
    import yfinance as yf
    PX.mkdir(parents=True, exist_ok=True)
    tickers = sorted(set(tickers))
    syms = {t: t.replace(".", "-").replace("/", "-") for t in tickers}
    batches = [tickers[i:i + 100] for i in range(0, len(tickers), 100)]
    batches = [["IWM", "SPY"]] + batches
    fails = []
    for bi, b in enumerate(batches):
        fp = PX / f"batch_{bi:03d}.csv.gz"
        if fp.exists():
            continue
        ys = [syms.get(t, t) for t in b]
        for attempt in range(3):
            try:
                df = yf.download(ys, start=START, end=END, auto_adjust=True, group_by="ticker",
                                 threads=4, progress=False)
                break
            except Exception as e:  # noqa
                print("retry", bi, e); time.sleep(10)
        else:
            fails.extend(b); continue
        rows = []
        for t, y in zip(b, ys):
            try:
                sub = df[y] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                fails.append(t); continue
            sub = sub.dropna(subset=["Close"])
            if len(sub) == 0:
                fails.append(t); continue
            s = pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "ticker": t,
                              "open": sub.Open.astype("float32"), "high": sub.High.astype("float32"),
                              "low": sub.Low.astype("float32"), "close": sub.Close.astype("float32"),
                              "volume": sub.Volume.astype("float64")})
            rows.append(s)
        del df
        if rows:
            pd.concat(rows).to_csv(fp, index=False, compression="gzip")
        print(f"batch {bi}/{len(batches)} ok={len(rows)} fails_total={len(fails)}", flush=True)
        time.sleep(2)
    (OUT / "download_fails.json").write_text(json.dumps(fails))


def load_prices():
    frames = [pd.read_csv(f, dtype={"ticker": str}) for f in sorted(PX.glob("batch_*.csv.gz"))]
    p = pd.concat(frames, ignore_index=True)
    del frames
    cal = pd.DatetimeIndex(sorted(p.loc[p.ticker == "IWM", "date"].unique()))
    p = p[p.date.isin(set(cal.strftime("%Y-%m-%d")))]
    tick = sorted(p.ticker.unique())
    ti = {t: i for i, t in enumerate(tick)}
    di = {d: i for i, d in enumerate(cal.strftime("%Y-%m-%d"))}
    r = p.ticker.map(ti).values; cidx = p.date.map(di).values
    W = {}
    for f in ("open", "close", "volume"):
        a = np.full((len(tick), len(cal)), np.nan, np.float32)
        a[r, cidx] = p[f].values.astype(np.float32)
        W[f] = a
    return cal, ti, W


# ---------------------------------------------------------------- build table
def build(ev, cal, ti, W):
    O, C, V = W["open"], W["close"], W["volume"]
    iw = ti["IWM"]
    ev = ev[ev.hour != "DMH"].copy()
    ev = ev[ev.ticker.isin(ti)].copy()
    dnum = cal.values
    evd = ev.date.values.astype("datetime64[ns]")
    amc = (ev.hour != "BMO").values
    e = np.where(amc, np.searchsorted(dnum, evd, side="right"), np.searchsorted(dnum, evd, side="left"))
    ev["e"] = e
    ev = ev[(ev.e >= 61) & (ev.e < len(cal))].copy()
    e = ev.e.values; a = e - 1
    r = ev.ticker.map(ti).values
    ca = C[r, a]
    ev["close_a"] = ca
    ev["ret20"] = ca / C[r, a - 20] - 1
    ev["ret60"] = ca / C[r, a - 60] - 1
    idx20 = a[:, None] + np.arange(-19, 1)[None, :]
    c20 = C[r[:, None], idx20]
    lr = np.diff(np.log(C[r[:, None], np.concatenate([idx20[:, :1] - 1, idx20], 1)]), axis=1)
    ev["vol20"] = np.nanstd(lr, axis=1)
    dv = np.nanmean(c20 * V[r[:, None], idx20], axis=1)
    ev["dvol20"] = dv
    ev["ldvol20"] = np.log(np.maximum(dv, 1))
    idx60 = a[:, None] + np.arange(-59, 1)[None, :]
    ev["lsize"] = np.log(np.maximum(ca * np.nanmean(V[r[:, None], idx60], axis=1), 1))
    ev["sue2"] = np.clip((ev.eps_act - ev.eps_est) / ca, -0.2, 0.2)
    ev["sgn"] = np.sign(ev.eps_act - ev.eps_est)
    edate = cal[e]
    ev["entry_date"] = edate
    ev["month_sin"] = np.sin(2 * np.pi * edate.month / 12)
    ev["month_cos"] = np.cos(2 * np.pi * edate.month / 12)
    ev["week"] = edate.to_period("W-FRI").start_time.values
    ev["year"] = edate.year
    ev["gap"] = O[r, e] / ca - 1
    ev["react"] = C[r, e] / ca - 1
    ev["react_x"] = ev.react - (C[iw, e] / C[iw, a] - 1)
    ev["open_e"] = O[r, e]
    n0 = len(ev)
    ev = ev[(ev.close_a >= 3) & (ev.dvol20 >= 1e6) & (ev.open_e > 0) & (ev.year >= 2014)].copy()
    filt = {"after_price_join": n0, "after_tradability_and_2014": len(ev)}
    e = ev.e.values; r = ev.ticker.map(ti).values
    T = len(cal)
    for H in HS:
        x = e + H
        ok = x < T
        xs = np.minimum(x, T - 1)
        rA = O[r, xs] / O[r, e] - 1; bA = O[iw, xs] / O[iw, e] - 1
        rB = C[r, xs] / C[r, e] - 1; bB = C[iw, xs] / C[iw, e] - 1
        rA[~ok] = np.nan; rB[~ok] = np.nan
        ev[f"gA{H}"] = rA - bA
        ev[f"gB{H}"] = rB - bB
        ev[f"x{H}"] = x
    return ev.reset_index(drop=True), filt


FEATS_A = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since", "ret20", "ret60", "vol20",
           "ldvol20", "lsize", "month_sin", "month_cos"]
FEATS_B = FEATS_A + ["gap", "react", "react_x"]


# ---------------------------------------------------------------- models
def fit_predict(model, tr, te, feats, y):
    from sklearn.preprocessing import StandardScaler
    Xtr = tr[feats].values.astype(np.float32); Xte = te[feats].values.astype(np.float32)
    ytr = tr[y].values
    lo, hi = np.nanpercentile(ytr, [1, 99]); yw = np.clip(ytr, lo, hi)
    imp = {}
    if model in ("M1_logit", "M4_mlp"):
        med = np.nanmedian(Xtr, axis=0)
        Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
        sc = StandardScaler().fit(Xtr); Xtr = sc.transform(Xtr); Xte = sc.transform(Xte)
    if model == "M1_logit":
        from sklearn.linear_model import LogisticRegression
        m = LogisticRegression(max_iter=2000).fit(Xtr, (ytr - COST > 0).astype(int))
        imp = dict(zip(feats, m.coef_[0].tolist()))
        return m.predict_proba(Xte)[:, 1], imp
    if model == "M4_mlp":
        from sklearn.neural_network import MLPRegressor
        mu, sd = yw.mean(), yw.std()
        m = MLPRegressor(hidden_layer_sizes=(32, 16), early_stopping=True, random_state=0, max_iter=200)
        m.fit(Xtr, (yw - mu) / sd)
        return m.predict(Xte), imp
    import lightgbm as lgb
    if model == "M2_lgbm_reg":
        m = lgb.LGBMRegressor(n_jobs=4, random_state=0, verbose=-1).fit(Xtr, yw)
    else:
        trs = tr.assign(_y=ytr).sort_values("week", kind="stable")
        pct = trs.groupby("week")._y.rank(pct=True, method="first").values
        rel = np.clip(np.floor(pct * 5 - 1e-9), 0, 4).astype(int)
        grp = trs.groupby("week", sort=False).size().values
        m = lgb.LGBMRanker(objective="lambdarank", n_jobs=4, random_state=0, verbose=-1)
        m.fit(trs[feats].values.astype(np.float32), rel, group=grp)
    imp = dict(zip(feats, m.booster_.feature_importance("gain").tolist()))
    return m.predict(Xte), imp


def nw_t(x, lags):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return float("nan")
    u = x - x.mean()
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def evaluate(pred, H, gcol, lags):
    """pred: DataFrame with week, year, score cols per model, gcol gross excess."""
    from scipy.stats import spearmanr
    rows = []
    for wk, g in pred.groupby("week"):
        N = len(g)
        if N < 10:
            continue
        n = max(3, int(math.floor(0.1 * N)))
        rec = {"week": wk, "N": N, "all": g[gcol].mean() - COST}
        for m in MODELS:
            sc = g[m].values
            if m == "M0_sue":
                order = np.lexsort((-g.sue2.values, -sc))
            else:
                order = np.argsort(-sc, kind="stable")
            top = g[gcol].values[order[:n]]
            rec[m] = top.mean() - COST
            rec[m + "_gsum"] = top.sum(); rec[m + "_n"] = n
            rec[m + "_ic"] = spearmanr(sc, g[gcol].values).correlation if np.std(sc) > 0 else np.nan
            rec[m + "_top"] = top.tolist()
        rows.append(rec)
    Wk = pd.DataFrame(rows)
    Wk["half"] = np.where(pd.to_datetime(Wk.week).dt.year <= 2021, "h1", "h2")
    out = {}
    for m in MODELS:
        tops = np.concatenate([np.array(t) for t in Wk[m + "_top"]])
        diff = Wk[m] - Wk["M0_sue"]
        dall = Wk[m] - Wk["all"]
        res = {
            "weeks": len(Wk), "events_selected": int(len(tops)),
            "mean_net_event": float(tops.mean() - COST), "median_net_event": float(np.median(tops) - COST),
            "weekly_mean_net": float(Wk[m].mean()), "t_net": nw_t(Wk[m], lags),
            "diff_vs_base": float(diff.mean()), "t_diff_base": nw_t(diff, lags),
            "diff_vs_all": float(dall.mean()), "t_diff_all": nw_t(dall, lags),
            "h1_net": float(Wk.loc[Wk.half == "h1", m].mean()), "h2_net": float(Wk.loc[Wk.half == "h2", m].mean()),
            "h1_diff": float(diff[Wk.half == "h1"].mean()), "h2_diff": float(diff[Wk.half == "h2"].mean()),
            "ic_mean": float(Wk[m + "_ic"].mean()), "ic_t": nw_t(Wk[m + "_ic"], lags),
            "breakeven_cost": float(tops.mean()),
        }
        if m == "M0_sue":
            res["pass"] = bool(res["t_net"] >= 3 and res["h1_net"] > 0 and res["h2_net"] > 0
                               and res["breakeven_cost"] >= 2 * COST)
        else:
            res["pass"] = bool(res["t_diff_base"] >= 3 and res["h1_net"] > 0 and res["h2_net"] > 0
                               and res["h1_diff"] > 0 and res["h2_diff"] > 0
                               and res["breakeven_cost"] >= 2 * COST)
        out[m] = res
    out["_all"] = {"weekly_mean_net": float(Wk["all"].mean()), "t_net": nw_t(Wk["all"], lags),
                   "h1": float(Wk.loc[Wk.half == "h1", "all"].mean()),
                   "h2": float(Wk.loc[Wk.half == "h2", "all"].mean()),
                   "events": int(Wk.N.sum())}
    return out, Wk


def run(max_tickers=None, years=None):
    ev, info = load_events()
    cal, ti, W = load_prices()
    tick_in = set(ev.ticker)
    info["price_coverage_tickers"] = len(tick_in & set(ti))
    info["tickers_in_events"] = len(tick_in)
    if max_tickers:
        keep = sorted(tick_in & set(ti))[:max_tickers]
        ev = ev[ev.ticker.isin(keep)]
    tab, filt = build(ev, cal, ti, W)
    del W
    info.update(filt)
    info["events_used"] = len(tab); info["tickers_used"] = int(tab.ticker.nunique())
    info["year_counts"] = tab.year.value_counts().sort_index().to_dict()
    info["last_price_date"] = str(cal[-1].date())
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2017, 2027)}
    results = {}; imps = {}
    test_years = years or list(range(2017, 2027))
    for var in VARIANTS:
        feats = FEATS_A if var == "A_open" else FEATS_B
        for H in HS:
            gcol = f"g{var[0]}{H}"; xcol = f"x{H}"
            d = tab[tab[gcol].notna()]
            preds = []
            for Y in test_years:
                tr = d[(d.year < Y) & (d[xcol] < first_idx[Y])]
                te = d[d.year == Y]
                if len(te) == 0:
                    continue
                p = te[["week", "year", "sue2", gcol]].copy()
                p["M0_sue"] = te.sue1.values
                for m in MODELS[1:]:
                    s, imp = fit_predict(m, tr, te, feats, gcol)
                    p[m] = s
                    if imp:
                        imps.setdefault(f"{var}_H{H}_{m}", []).append(imp)
                preds.append(p)
                print(var, H, Y, len(tr), len(te), flush=True)
            P = pd.concat(preds)
            res, Wk = evaluate(P, H, gcol, int(math.ceil(H / 5)))
            results[f"{var}_H{H}"] = res
            Wk.drop(columns=[c for c in Wk.columns if c.endswith("_top")]).to_csv(
                OUT / f"weekly_{var}_H{H}.csv", index=False)
    imp_avg = {k: pd.DataFrame(v).mean().sort_values(ascending=False).to_dict() for k, v in imps.items()}
    (OUT / "results.json").write_text(json.dumps({"info": info, "results": results, "importance": imp_avg},
                                                 default=str, indent=1, ensure_ascii=False))
    for k, v in results.items():
        print(k)
        print(pd.DataFrame(v).T.round(4).to_string())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--max-tickers", type=int)
    ap.add_argument("--years", type=int, nargs="*")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.download:
        ev, _ = load_events()
        download(ev.ticker.unique().tolist())
    if a.run:
        run(a.max_tickers, a.years)
