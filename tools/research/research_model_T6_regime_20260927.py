#!/usr/bin/env python3
"""T6 국면 모델(HMM) — 얼마나 넣을지를 국면으로 정하면 고정 크기보다 나은가 (read-only, 2026-09-27).

사전등록 (실행 전 고정 — 결과를 본 뒤 바꾸지 않는다)
=====================================================
질문
- (A) 패닉 오버레이: 패닉 신호 밤(233740, 원장 net_pct)마다 1단위/2단위를 국면으로 정하면
      고정 1단위·현행 강신호 규칙·단순 rv 규칙보다 나은가?
- (B) 지수 본체 노출: 069500 / SPY를 평온 100% · 스트레스 50%로 바꾸면 보유·rv 규칙보다 나은가?

데이터
- KR 피처: ^KS11, ^KQ11 (direction_models_20260927/raw), VIX는 KR 날짜 D에 대해 날짜 < D인 마지막 값(미국 전날 종가).
- US 피처: SPY, VIX 같은 날짜(종가 시점에 알려짐).
- 가격: 069500.KS (voltarget_20260924, 수정주가), SPY (direction_models raw, 수정주가).
- 패닉 원장: data/shadow/kr_index_etf_panic.jsonl, kind=trade, ticker=233740, net_pct 있는 것 전부
  (backfill + live 혼합 — 보고서에 건수 명시). net_pct는 비용 반영, 1단위 기준 %.

피처 (종가 시점에 알려진 값만)
- KR 모델(한 번 적합해 A·B-KR 공용): [KS11 로그수익, KQ11 로그수익, log rv20(KS11), log rv20(KQ11),
  log VIX(lag), Δlog VIX(lag)]. rv20 = 당일 포함 최근 20일 로그수익 표준편차 × √252.
- US 모델: [SPY 로그수익, log rv20(SPY), log VIX, Δlog VIX].
- 표준화: 각 적합 연도의 학습 구간 평균·표준편차.

모델
- hmmlearn GaussianHMM, n_components ∈ {2, 3}, covariance_type='full', n_iter=300, seed 0..4 중
  학습 로그우도 최대. 연 단위 확장창 워크포워드: 테스트 연도 Y의 파라미터 = Y-01-01 이전 데이터로 적합.
- 상태 확률 = **필터(forward 알고리즘) 확률만**. hmmlearn predict_proba(=smoothed)·predict/decode(=Viterbi)
  사용 금지. 방출 로그밀도는 scipy multivariate_normal로 직접 계산, 이력 시작부터 Y 말까지
  Y 파라미터로 forward 진행(모두 t 이전 데이터 → 합법), Y 날짜의 확률만 사용.
- 상태 라벨: 학습 구간 상태 평균의 log rv20(주 지수: A=KQ11, B-KR=KS11... 단, KR 모델 공용이므로
  라벨은 KS11·KQ11 log rv20 평균의 합) 기준. 스트레스 = 가장 높은 변동성 상태 1개(3상태면 나머지 2개는 평온).
  매년 재라벨(EM 상태 순서 임의).
- 판정 상태 = argmax 필터 확률.

기준선
- 고정: (A) 매 밤 1단위 / (B) 100% 보유(B&H).
- 단순 rv 규칙: 주 지수 rv20 > 그 날까지의 확장 중앙값(이력 시작부터)이면 스트레스.
  주 지수 = A: KQ11, B-KR: KS11, B-US: SPY.
- (A) 참고 비교: 현행 원장 units(강신호 2 / 보통 1).

(A) 패닉 오버레이 크기
- 단위 = 스트레스 2, 평온 1. 판독 2종(둘 다 사전등록, 둘 다 판정):
  주 판독 = 신호일 t 종가의 필터 상태, 보조 판독 = t−1 종가의 필터 상태(패닉 당일 수익이
  피처에 들어가 거의 모든 신호 밤이 '스트레스'로 몰리는 퇴화 대비 — 실행 전 등록).
- 슬리브 자본 = 2단위(2단위 밤 = 슬리브 100%, 1단위 = 50%). 슬리브 일수익은 exit_date에 기록,
  신호 없는 날 0(현금 이자 0). 첫 테스트 2016(원장 시작).
- 샤프 = 슬리브 일수익 기준(KQ11 거래일, √252) — 주 지표. 밤 단위 평균·최악 밤은 보고용.
- 노출 매칭 t: 밤 n의 슬리브 수익 s_n = (u_n/2)·r_n. 매칭 초과 d_n = (u_n/ū − 1)·r_n/2
  (= 크기와 다음 수익의 공분산 검정, ū는 테스트 창 평균 단위 — 검정 통계용이지 매매 규칙 아님).
  vs rv: d_n = (u_h/ū_h − u_rv/ū_rv)·r_n/2. 밤은 겹치지 않으므로 보통 t.
- 회전 비용: 해당 없음(밤 수·진입은 동일, 크기만 다름. 크기 비례 비용은 net_pct에 이미 포함).

(B) 본체 노출
- 069500(2010~, KR 모델), SPY(2010~, US 모델). 첫 테스트 2010, 학습 KR 2001~/US 1995~.
- 노출: 평온 100%, 스트레스 50%, 나머지 현금(이자 0).
- 전환 규칙: (argmax 상태 ≠ 보유 상태) AND (직전 전환 후 결정일 ≥ 20일)이면 전환. 매일 재평가.
  rv 규칙에도 동일한 20일 체류 적용. 시작 = 100%·평온.
- 체결: t 종가 결정 → t+1 종가 체결 → t+1→t+2 수익부터 반영. 비용: 편도 전환마다
  |Δ노출| × 왕복비용/2 (KR 0.10%, SPY 0.44% 왕복) = 자본의 왕복/4. 2× 비용 민감도 병기.
- 지표: CAGR, 변동성, 샤프(rf 0), MaxDD, 연 전환 수, 연 비용 %.
- 노출 매칭 t (Newey-West lag 20): d_t = s_t − ē·r_t (s = 비용 후 전략 수익). vs rv: s_h/ē_h − s_rv/ē_rv.
- 반기: B = 2010-01~2018-05-14 / 2018-05-15~, A = 2016-01~2021-05-14 / 2021-05-15~.

합격 (칸별, 검정 8개 = A 2모델×2판독 + B 2시장×2모델, t 문턱 2.5 — 프로그램 §3 기본 3보다 완화, 명시)
- 샤프가 고정·rv 규칙 둘 다보다 높음 — 전반·후반 각각.
- 전체 노출매칭 t ≥ 2.5 — 고정 대비, rv 규칙 대비 둘 다.
- (B만) 연 회전비용 ≤ 0.5%.
- 보고: 신호 밤 vs 전체 날의 스트레스 비율(판별력 확인), 연도별 매칭 초과와 최대 연도 비중
  (>60%면 '한 번의 국면 회피'로 표기), 연도별 수렴 로그.
"""
from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp
from scipy.stats import multivariate_normal

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "analysis" / "direction_models_20260927" / "raw"
VT = ROOT / "data" / "analysis" / "voltarget_20260924"
LEDGER = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
OUT = ROOT / "data" / "analysis" / "model_T6_regime_20260927"
REPORT_DATA = OUT / "results.json"

T_PASS = 2.5
DWELL = 20
COST_RT = {"KR": 0.0010, "US": 0.0044}
LOG: list[str] = []


def log(msg):
    LOG.append(msg)
    print(msg, flush=True)


def load_close(path):
    d = pd.read_csv(path, parse_dates=["Date"])
    d = d.dropna(subset=["Close"])
    d = d[d["Close"] > 0]
    s = d.set_index("Date")["Close"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def rv20(logret):
    return logret.rolling(20).std() * math.sqrt(252)


# ---------------------------------------------------------------- features
def kr_features():
    ks, kq, vix = load_close(RAW / "KS11.csv"), load_close(RAW / "KQ11.csv"), load_close(RAW / "VIX.csv")
    df = pd.concat({"ks": ks, "kq": kq}, axis=1, join="inner")
    f = pd.DataFrame(index=df.index)
    f["r_ks"] = np.log(df.ks).diff()
    f["r_kq"] = np.log(df.kq).diff()
    f["lrv_ks"] = np.log(rv20(f.r_ks))
    f["lrv_kq"] = np.log(rv20(f.r_kq))
    v = pd.DataFrame({"lvix": np.log(vix)})
    v["dlvix"] = v.lvix.diff()
    v = v.reset_index().rename(columns={"Date": "vdate"})
    left = f.reset_index().rename(columns={"Date": "date"})
    m = pd.merge_asof(left.sort_values("date"), v.sort_values("vdate"), left_on="date", right_on="vdate",
                      allow_exact_matches=False, direction="backward")
    assert (m.vdate < m.date).all() or m.vdate.isna().any()
    assert (m.dropna(subset=["vdate"]).vdate < m.dropna(subset=["vdate"]).date).all()
    m = m.set_index("date").drop(columns=["vdate"])
    m = m.replace([np.inf, -np.inf], np.nan).dropna()
    m = m[m.index >= "2001-01-01"]
    return m, ["r_ks", "r_kq", "lrv_ks", "lrv_kq", "lvix", "dlvix"], ["lrv_ks", "lrv_kq"]


def us_features():
    spy, vix = load_close(RAW / "SPY.csv"), load_close(RAW / "VIX.csv")
    df = pd.concat({"spy": spy, "vix": vix}, axis=1, join="inner")
    f = pd.DataFrame(index=df.index)
    f["r_spy"] = np.log(df.spy).diff()
    f["lrv_spy"] = np.log(rv20(f.r_spy))
    f["lvix"] = np.log(df.vix)
    f["dlvix"] = f.lvix.diff()
    f = f.replace([np.inf, -np.inf], np.nan).dropna()
    return f, ["r_spy", "lrv_spy", "lvix", "dlvix"], ["lrv_spy"]


# ---------------------------------------------------------------- HMM walk-forward (filtered only)
def forward_filter(model, X):
    K = model.n_components
    logb = np.column_stack([multivariate_normal(model.means_[k], model.covars_[k], allow_singular=True).logpdf(X)
                            for k in range(K)])
    logA = np.log(model.transmat_ + 1e-300)
    la = np.log(model.startprob_ + 1e-300) + logb[0]
    la -= logsumexp(la)
    out = np.empty_like(logb)
    out[0] = la
    for t in range(1, len(X)):
        la = logsumexp(la[:, None] + logA, axis=0) + logb[t]
        la -= logsumexp(la)
        out[t] = la
    return np.exp(out)


def walk_forward(feat, cols, vol_cols, K, years, tag):
    """returns DataFrame(date -> stress flag, p_stress) for dates in test years."""
    rows = []
    for Y in years:
        tr = feat[feat.index < f"{Y}-01-01"]
        mu, sd = tr[cols].mean(), tr[cols].std()
        Xtr = ((tr[cols] - mu) / sd).values
        best, bestll = None, -np.inf
        conv = []
        for seed in range(5):
            m = GaussianHMM(n_components=K, covariance_type="full", n_iter=300, random_state=seed, tol=1e-4)
            m.fit(Xtr)
            ll = m.score(Xtr)
            conv.append(bool(m.monitor_.converged))
            if ll > bestll:
                best, bestll = m, ll
        vi = [cols.index(c) for c in vol_cols]
        volscore = best.means_[:, vi].sum(axis=1)
        stress = int(np.argmax(volscore))
        hist = feat[feat.index <= f"{Y}-12-31"]
        P = forward_filter(best, ((hist[cols] - mu) / sd).values)
        sel = hist.index >= f"{Y}-01-01"
        am = P[sel].argmax(axis=1)
        part = pd.DataFrame({"stress": (am == stress).astype(int), "p_stress": P[sel][:, stress]},
                            index=hist.index[sel])
        rows.append(part)
        log(f"[{tag} K={K} {Y}] train_n={len(tr)} ll={bestll:.0f} converged={conv} "
            f"stress_state={stress} volscore={np.round(volscore,2).tolist()} "
            f"test_n={sel.sum()} stress_share={part.stress.mean():.2f}")
    return pd.concat(rows)


def rv_rule(feat, col):
    lrv = feat[col]
    med = lrv.expanding().median()
    return (lrv > med).astype(int)


# ---------------------------------------------------------------- stats
def nw_t(x, lag):
    x = np.asarray(x, float)
    n = len(x)
    if n < 5:
        return float("nan")
    e = x - x.mean()
    s = e @ e / n
    for L in range(1, lag + 1):
        s += 2 * (1 - L / (lag + 1)) * (e[L:] @ e[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def perf(r):
    r = np.asarray(r, float)
    nav = np.cumprod(1 + r)
    yrs = len(r) / 252
    cagr = nav[-1] ** (1 / yrs) - 1 if len(r) else float("nan")
    vol = r.std() * math.sqrt(252)
    sh = r.mean() * 252 / vol if vol > 0 else float("nan")
    mdd = (nav / np.maximum.accumulate(np.maximum(nav, 1.0)) - 1).min()
    return {"cagr": cagr * 100, "vol": vol * 100, "sharpe": sh, "mdd": mdd * 100}


# ---------------------------------------------------------------- (A) panic overlay
def load_panic():
    rs = []
    for line in LEDGER.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") != "trade" or r.get("ticker") != "233740" or r.get("net_pct") is None:
            continue
        rs.append({"sdate": pd.Timestamp(r["session_date"]), "xdate": pd.Timestamp(r["exit_date"]),
                   "r": float(r["net_pct"]) / 100, "units_exist": int(r.get("units") or 1), "mode": r.get("mode")})
    return pd.DataFrame(rs).sort_values("sdate").reset_index(drop=True)


def study_A(kr_feat, states, rvflag, split="2021-05-15"):
    P = load_panic()
    log(f"[A] trades={len(P)} modes={P['mode'].value_counts().to_dict()} {P.sdate.min().date()}~{P.sdate.max().date()}")
    idx = kr_feat.index
    days = idx[(idx >= "2016-01-01")]
    # 필터 상태 lookup
    def st_at(stdf, d, lagged):
        pos = stdf.index.searchsorted(d, side="right") - 1  # 날짜 <= d
        if lagged:
            pos -= 1 if (pos >= 0 and stdf.index[pos] == d) else 0
        return int(stdf.stress.iloc[pos]) if pos >= 0 else 0

    rules = {"fixed": np.ones(len(P), int), "exist": P.units_exist.values,
             "rv": np.array([1 + int(rvflag.loc[:d].iloc[-1]) for d in P.sdate])}
    stress_info = {}
    for K, sdf in states.items():
        for lagged, lab in ((False, "t"), (True, "t-1")):
            u = np.array([1 + st_at(sdf, d, lagged) for d in P.sdate])
            rules[f"hmm{K}_{lab}"] = u
        allshare = sdf.loc[sdf.index >= "2016-01-01", "stress"].mean()
        stress_info[f"hmm{K}"] = {"all_days": allshare,
                                  "signal_t": float((rules[f"hmm{K}_t"] == 2).mean()),
                                  "signal_t-1": float((rules[f"hmm{K}_t-1"] == 2).mean())}
    stress_info["rv"] = {"all_days": float(rvflag[rvflag.index >= "2016-01-01"].mean()),
                         "signal_t": float((rules["rv"] == 2).mean())}
    stress_info["exist"] = {"signal_t": float((rules["exist"] == 2).mean())}

    r = P.r.values
    res = {}
    halves = {"all": np.ones(len(P), bool), "H1": (P.sdate < split).values, "H2": (P.sdate >= split).values}
    for name, u in rules.items():
        sleeve_night = u / 2 * r
        daily = pd.Series(0.0, index=days)
        for d, v in zip(P.xdate, sleeve_night):
            pos = days.searchsorted(d)
            if pos < len(days):
                daily.iloc[pos] += v
        out = {}
        for h, m in halves.items():
            dmask = (days < split) if h == "H1" else (days >= split) if h == "H2" else np.ones(len(days), bool)
            pf = perf(daily.values[dmask])
            sn = sleeve_night[m]
            ubar = u[m].mean()
            out[h] = {**pf, "n": int(m.sum()), "mean_units": ubar, "night_mean": sn.mean() * 100,
                      "night_sharpe": sn.mean() / sn.std(), "worst_night": sn.min() * 100,
                      "unit_net_2u": r[m][u[m] == 2].mean() * 100 if (u[m] == 2).any() else float("nan"),
                      "unit_net_1u": r[m][u[m] == 1].mean() * 100 if (u[m] == 1).any() else float("nan"),
                      "n_2u": int((u[m] == 2).sum())}
            for bname in ("fixed", "rv", "exist"):
                ub = rules[bname][m]
                d_ = (u[m] / ubar - ub / ub.mean()) * r[m] / 2
                tt = d_.mean() / (d_.std(ddof=1) / math.sqrt(len(d_))) if d_.std() > 0 else float("nan")
                out[h][f"t_vs_{bname}"] = tt
                out[h][f"d_vs_{bname}_bp"] = d_.mean() * 1e4
        # 연도별 매칭 초과(vs fixed)
        d_all = (u / u.mean() - 1) * r / 2
        yr = pd.Series(d_all, index=P.sdate.dt.year).groupby(level=0).sum() * 100
        out["year_excess_pct"] = yr.round(3).to_dict()
        res[name] = out
    return res, stress_info, P.assign(**{k: v for k, v in rules.items()})


# ---------------------------------------------------------------- (B) base exposure
def sim_exposure(stress_dec, dates, rets, cost_rt):
    """stress_dec: Series(date->0/1 decision signal) aligned to dates. returns net returns, exposure, n switches."""
    sig = stress_dec.reindex(dates).ffill().fillna(0).astype(int).values
    held, last_sw = 0, -10**9
    dec = np.zeros(len(dates), int)
    nsw = 0
    for i in range(len(dates)):
        if sig[i] != held and i - last_sw >= DWELL:
            held, last_sw = sig[i], i
            nsw += 1
        dec[i] = held
    expo_target = np.where(dec == 1, 0.5, 1.0)
    # 결정 t → 체결 t+1 종가 → 수익 t+2(=rets[i] 은 dates[i-1]→dates[i])
    expo = np.ones(len(dates))
    expo[2:] = expo_target[:-2]
    cost = np.zeros(len(dates))
    chg = np.abs(np.diff(np.r_[1.0, expo_target]))  # 목표 변경 at t
    # 체결일 t+1 에 비용
    cost[1:] = chg[:-1] * cost_rt / 2
    net = expo * rets - cost
    return net, expo, nsw


def study_B(market, feat, states, rvflag, price, split="2018-05-15"):
    p = price[price.index >= "2009-12-01"]
    dates = p.index[p.index >= "2010-01-01"]
    rets_full = p.pct_change()
    rets = rets_full.reindex(dates).fillna(0).values
    bh = rets
    rules = {"rv": rvflag}
    for K, sdf in states.items():
        rules[f"hmm{K}"] = sdf.stress
    out = {}
    series = {"bh": bh}
    extra = {}
    for costmult in (1, 2):
        for name, sflag in rules.items():
            net, expo, nsw = sim_exposure(sflag[sflag.index >= "2009-06-01"], dates, rets, COST_RT[market] * costmult)
            key = name if costmult == 1 else f"{name}_cost2x"
            series[key] = net
            extra[key] = {"switches": nsw, "switches_per_yr": nsw / (len(dates) / 252),
                          "cost_pct_per_yr": COST_RT[market] * costmult / 4 * nsw / (len(dates) / 252) * 100 * 1,
                          "mean_expo": expo.mean(), "expo": expo}
    halves = {"all": np.ones(len(dates), bool), "H1": dates < split, "H2": dates >= split}
    for key, s in series.items():
        o = {}
        for h, m in halves.items():
            o[h] = perf(s[m])
            if key != "bh":
                e = extra[key]["expo"][m]
                ebar = e.mean()
                o[h]["mean_expo"] = ebar
                o[h]["t_vs_bh_matched"] = nw_t(s[m] - ebar * bh[m], 20)
                o[h]["t_vs_bh_raw"] = nw_t(s[m] - bh[m], 20)
                base = key.replace("hmm2", "rv").replace("hmm3", "rv")
                if base != key:
                    eb = extra[base]["expo"][m].mean()
                    o[h]["t_vs_rv_matched"] = nw_t(s[m] / ebar - series[base][m] / eb, 20)
        if key != "bh":
            o["switches_per_yr"] = extra[key]["switches_per_yr"]
            # 비용: 편도 |Δ|=0.5 × RT/2 = RT/4 per switch
            o["cost_pct_per_yr"] = extra[key]["cost_pct_per_yr"]
            e = extra[key]["expo"]
            d = s - e.mean() * bh
            yr = pd.Series(d, index=dates.year).groupby(level=0).sum() * 100
            o["year_matched_excess_pct"] = yr.round(3).to_dict()
        out[key] = o
    daily = pd.DataFrame({k: v for k, v in series.items()}, index=dates)
    daily.to_csv(OUT / f"B_{market}_daily_returns.csv")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="KR K=2 2016 한 해만 적합해 상태 점검")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    kr, kr_cols, kr_vol = kr_features()
    us, us_cols, us_vol = us_features()
    log(f"KR feat {kr.index.min().date()}~{kr.index.max().date()} n={len(kr)} / US feat {us.index.min().date()}~{us.index.max().date()} n={len(us)}")
    if a.dry:
        s = walk_forward(kr, kr_cols, kr_vol, 2, [2016], "KR-dry")
        j = s.join(np.exp(kr[["lrv_ks", "lrv_kq"]]))
        print(j.groupby("stress")[["lrv_ks", "lrv_kq"]].mean())
        print(j.stress.diff().abs().sum(), "state changes in 2016")
        return
    years_kr = list(range(2010, 2027))
    years_us = list(range(2010, 2027))
    st_kr = {K: walk_forward(kr, kr_cols, kr_vol, K, years_kr, "KR") for K in (2, 3)}
    st_us = {K: walk_forward(us, us_cols, us_vol, K, years_us, "US") for K in (2, 3)}
    for K in (2, 3):
        st_kr[K].to_csv(OUT / f"states_KR_K{K}.csv")
        st_us[K].to_csv(OUT / f"states_US_K{K}.csv")
    rv_kq, rv_ks, rv_spy = rv_rule(kr, "lrv_kq"), rv_rule(kr, "lrv_ks"), rv_rule(us, "lrv_spy")

    resA, stressA, Pdf = study_A(kr, st_kr, rv_kq)
    Pdf.to_csv(OUT / "A_panic_nights.csv", index=False)
    k200 = load_close(VT / "069500.KS.csv")
    spy = load_close(RAW / "SPY.csv")
    resB_kr = study_B("KR", kr, st_kr, rv_ks, k200)
    resB_us = study_B("US", us, st_us, rv_spy, spy)

    # 합격 판정
    verdict = {}
    for name in ("hmm2_t", "hmm2_t-1", "hmm3_t", "hmm3_t-1"):
        o = resA[name]
        c1 = all(o[h]["sharpe"] > resA["fixed"][h]["sharpe"] and o[h]["sharpe"] > resA["rv"][h]["sharpe"] for h in ("H1", "H2"))
        c2 = o["all"]["t_vs_fixed"] >= T_PASS and o["all"]["t_vs_rv"] >= T_PASS
        verdict[f"A_{name}"] = {"sharpe_both_halves": c1, "t_ok": c2, "pass": c1 and c2}
    for mk, rb in (("KR", resB_kr), ("US", resB_us)):
        for name in ("hmm2", "hmm3"):
            o = rb[name]
            c1 = all(o[h]["sharpe"] > rb["bh"][h]["sharpe"] and o[h]["sharpe"] > rb["rv"][h]["sharpe"] for h in ("H1", "H2"))
            c2 = o["all"]["t_vs_bh_matched"] >= T_PASS and o["all"]["t_vs_rv_matched"] >= T_PASS
            c3 = o["cost_pct_per_yr"] <= 0.5
            verdict[f"B_{mk}_{name}"] = {"sharpe_both_halves": c1, "t_ok": c2, "cost_ok": c3, "pass": c1 and c2 and c3}
    for k, v in verdict.items():
        log(f"VERDICT {k}: {v}")
    REPORT_DATA.write_text(json.dumps({"A": resA, "A_stress_share": stressA, "B_KR": resB_kr, "B_US": resB_us,
                                       "verdict": verdict}, ensure_ascii=False, indent=1, default=float),
                           encoding="utf-8")
    (OUT / "run_log.txt").write_text("\n".join(LOG) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
