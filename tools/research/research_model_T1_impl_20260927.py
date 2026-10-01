"""T1 실행 가능 규칙 검증 — A_open·H20 M2(LightGBM) 점수의 구현 가능한 선택 규칙 (2026-09-27, 사전등록)

배경: T1 강건성(`model_T1_robust_20260927.md`)에서 M2 "주간 top-10%"는 확정 후보가 됐지만 그대로는 실행할 수 없다.
월요일에 그 주 코호트 N을 모르고, 동시 보유가 최대 128종목이다. 이 스크립트는 **이미 저장된 워크포워드 M2 점수**
(`data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz`)만 쓴다. 재학습과 신규 피처는 없다.

=== 사전등록 (계산 전 고정. 결과를 본 뒤 규칙 변경 금지. 이탈은 보고서에 '이탈'로 기록) ===

[블라인드 아님 — 사전 인지]
- 강건성 보고서의 R-g(세션 코호트 top-10%, t 3.06)와 R-h(세션당 1위, t 2.57, 사후)를 이미 봤다. I1~I4는 그 친척이다.
  이 사전등록이 고정하는 것은 정확한 규칙 기계·비용 모형·판정 t이지, 신호 방향을 새로 검정하는 것이 아니다.

[입력]
- 점수: M2_lgbm_reg(워크포워드 OOS, 테스트 2017..2026, 연 1회 확장 재학습). gA20 = open[e]→open[e+20] 종목 수익
  − 같은 창 IWM 수익(원시, winsorize 전). 이 값이 건별 정확 창 초과다.
- 가격: `data/analysis/model_T1_earnings_20260927/prices/` open만(수정주가), 달력 = IWM 날짜. e는 원 build의 세션 인덱스.

[임계값 — 과제 문구에서의 이탈(사전 인지)]
- 과제: "학습 창 점수의 확장 90분위, 연도별 고정". 학습 데이터에 대한 점수(인샘플 예측)는 저장돼 있지 않고 재학습은
  금지다. 그래서 대체한다: **연도 Y(≥2018)의 임계 T_Y = 진입연도 < Y 인 OOS M2 점수 전체의 90분위**(연 고정, 인과적).
- 2017(이전 OOS 없음): 2017 안에서 **그 세션 이전에 진입한 2017 점수들의 확장 90분위**(세션마다 갱신, 인과적).
  누적 200건 미만이면 그 세션은 선택하지 않는다(burn-in). 반기 정의(2017–2021/2022–2026)는 그대로 둔다.
- 연도별 실현 선택 비율(임계 이상 비율)을 먼저 기록한다. 모델이 해마다 달라 비율이 10%에서 벗어나도
  사후 재보정하지 않고 결과로 보고한다.
- 감도(판정 불포함): S1 = 매 세션 갱신되는 인과 풀(그 세션 이전 진입 OOS 점수 전체 90분위),
  S2 = 2017 제외(2018~2026만).

[규칙 — 각각 별도 검정, 4검정]
- I1 세션 코호트: 매 세션, 그 시가 진입 이벤트 중 score ≥ 임계 전부. 20세션 보유(open[e]→open[e+20]).
- I2 = I1 + 세션당 1종목 상한(최고 점수, 동률은 ticker 오름차순). 포트폴리오 슬롯 K=20(1/세션 × 20세션 보유).
- I3 = I1 + 동시 보유 16 상한. 가득 차면 신규 스킵. 한 세션에 빈 슬롯보다 후보가 많으면 점수 높은 순
  (동률 ticker 오름차순). 슬롯은 청산 시가(e+20)에 풀리고 같은 시가 진입에 재사용 가능. K=16.
- I4 = I3 + 유동성 필터(결정 시점): close[a] ≥ $5 AND 20일 평균 거래대금 dvol20 ≥ $5M. 필터 후 I3 기계 적용.

[비용 — 건별 왕복]
- 기본 0.44%(KIS 실측) + 유동성 슬리피지: dvol20 ≥ $20M → +0.10%, $5~20M → +0.30%, < $5M → +0.60%.
  (판정용 = 이 현실 비용.) 참고: 평탄 0.44%, 평탄 0.10%.

[지표]
- 건별 net 초과 = gA20 − 비용. 평균·중앙값·승률, 건수, 연도별, 반기(진입연도 ≤2021 / ≥2022), 양수 연도 비율.
- 건별 t 두 가지: (a) 진입 세션 평균 시계열(거래 있는 세션만, 시간순)의 Newey-West t, lag 20(20세션 보유 겹침 보정),
  (b) 진입일 클러스터 t(CR1, 겹침 미보정 — 참고).
- 포트폴리오(I2·I3·I4): 2017 첫 세션~마지막 청산 세션의 **모든** 거래일 일간 시계열. 슬롯 비중 1/K, 빈 슬롯 수익 0.
  보유 종목 일수익 = open[t+1]/open[t]−1(t = e..e+19, 결측 open은 직전값 전진 채움 → 창 전체 곱은 정확).
  비용: 왕복의 절반을 진입일, 절반을 마지막 보유일(e+19)에 1/K로 차감.
  절대 수익 시계열 → 샤프(연율 √252), MaxDD(복리 자산), CAGR. IWM open→open 매수보유 동일 지표 병기.
  초과 시계열 = Σ(r_i − r_IWM)/K − 비용/K(IWM 헤지 슬롯 기준) → 연율 평균, NW t lag 20, 반기·연도별.
- 판정 t(에이전트 고정 — 운영자 추인 항목):
  I1 = 건별 (a) 세션 시계열 NW20 t. I2~I4 = 포트폴리오 초과 일간 NW20 t(주), 건별 (a)는 병기.
- 통과(각 규칙): 판정 t ≥ 2.5 AND 반기 건별 net 초과 평균 둘 다 > 0 (현실 비용 기준). 4검정.

[자본 산수(백테스트 상한 — 생존편향·FX 미반영 표기)]
- 슬롯 24만원 × 16 = 384만원. I3/I4 연 기대 초과(원) = 연평균 건수 × 24만 × 건별 평균 net 초과, 그리고
  포트폴리오 초과 연율 × 384만. 비용 0.44%(평탄)·0.10%(평탄)·현실 비용 3종. 가능 최대 회전 ≈ 16×252/20 ≈ 202건/년.

[이탈 기록 (1차 실행 후)]
- 1차 실행은 포트폴리오 일손익을 Σ(open[t+1]/open[t]−1)/K 로 합산했다. 이는 매일 슬롯을 1/K로 재조정하는 것과 같아,
  변동성이 큰 종목에서 창 수익(곱)보다 합이 커지는 상향 편향이 있다. 사전등록 의도(슬롯 = 창 매수보유, 건별 정확 창)와
  어긋나므로 **슬롯 내 매수보유**(일손익 = Δopen/진입 open, 창 합 = 정확 창 수익)로 고쳐 재실행했다.
  1차 결과는 results_run1_daily_rebalanced.json / run1_daily_rebalanced.log 에 보존하고 보고서에 병기한다. 판정은 수정본.
- 자본 산수에 I2(K=20, 북 480만)를 추가했다(과제는 I3/I4만 지정 — I2가 통과 후보라서 참고로 추가).

[한계(사전 인지)]
- 생존편향(현재 상장 종목만) → IWM 대비 절대 수준은 위로 치우칠 수 있다.
- $5 필터는 수정주가 close 기준(역분할 생존 종목은 과거 가격이 부풀어 잘못 포함, 정분할은 잘못 제외). dvol20은 분할 중립.
- eps_est 비PIT 가능(강건성 R-c 참고). 시가 체결 슬리피지는 위 사다리로만 근사.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SCORES = ROOT / "data" / "analysis" / "model_T1_robust_20260927" / "scores_A_open_H20.csv.gz"
PX = ROOT / "data" / "analysis" / "model_T1_earnings_20260927" / "prices"
OUT = ROOT / "data" / "analysis" / "model_T1_impl_20260927"
H = 20
SCORE = "M2_lgbm_reg"
LAG = 20
BURN = 200
SLOT_KRW = 240_000
K_SLOTS = 16


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(OUT / "run.log", "a", encoding="utf-8") as fh:
        fh.write(s + "\n")


def nw_t(x, lags=LAG):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return float("nan")
    u = x - x.mean()
    s = (u @ u) / n
    for L in range(1, min(lags, n - 1) + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def cluster_t(v, g):
    v = np.asarray(v, float); df = pd.DataFrame({"v": v - v.mean(), "g": g})
    sums = df.groupby("g").v.sum().values
    G = len(sums); n = len(v)
    var = (sums @ sums) / n ** 2 * G / (G - 1)
    return float(v.mean() / math.sqrt(var)) if var > 0 else float("nan")


def slip(dv):
    return np.where(dv >= 20e6, 0.001, np.where(dv >= 5e6, 0.003, 0.006))


# ---------------------------------------------------------------- data
def load_open(tickers):
    need = set(tickers) | {"IWM"}
    frames = []
    for f in sorted(PX.glob("batch_*.csv.gz")):
        p = pd.read_csv(f, usecols=["date", "ticker", "open"], dtype={"ticker": str})
        frames.append(p[p.ticker.isin(need)])
    p = pd.concat(frames, ignore_index=True); del frames
    cal = pd.DatetimeIndex(sorted(p.loc[p.ticker == "IWM", "date"].unique()))
    ds = cal.strftime("%Y-%m-%d")
    p = p[p.date.isin(set(ds))]
    tick = sorted(p.ticker.unique()); ti = {t: i for i, t in enumerate(tick)}
    di = {d: i for i, d in enumerate(ds)}
    O = np.full((len(tick), len(cal)), np.nan, np.float32)
    O[p.ticker.map(ti).values, p.date.map(di).values] = p.open.values.astype(np.float32)
    O = pd.DataFrame(O.T).ffill().values.T.astype(np.float32)  # 전진 채움(창 곱 보존)
    return cal, ti, O


# ---------------------------------------------------------------- thresholds
def thresholds(P, mode="primary"):
    """세션별 임계 배열(이벤트 행 단위)을 반환."""
    s = P[SCORE].values; e = P.e.values; yr = P.year.values
    thr = np.full(len(P), np.nan)
    if mode in ("primary", "excl2017"):
        for Y in range(2018, 2027):
            prior = s[yr < Y]
            thr[yr == Y] = np.percentile(prior, 90)
        # 2017: 세션 이전 2017 점수의 확장 90분위
        m17 = np.where(yr == 2017)[0]
        if mode == "primary":
            order = m17[np.argsort(e[m17], kind="stable")]
            ss = s[order]; ee = e[order]
            for sess in np.unique(ee):
                prior = ss[ee < sess]
                if len(prior) >= BURN:
                    thr[order[ee == sess]] = np.percentile(prior, 90)
    elif mode == "rolling":
        order = np.argsort(e, kind="stable"); ss = s[order]; ee = e[order]
        for sess in np.unique(ee):
            k = np.searchsorted(ee, sess, side="left")
            if k >= BURN:
                thr[order[ee == sess]] = np.percentile(ss[:k], 90)
    return thr


# ---------------------------------------------------------------- rules
def select(P, rule):
    c = P[P.qual].copy()
    if rule == "I4":
        c = c[(c.close_a >= 5) & (c.dvol20 >= 5e6)]
    c = c.sort_values(["e", SCORE, "ticker"], ascending=[True, False, True])
    if rule == "I1":
        return c
    if rule == "I2":
        return c.groupby("e", sort=True).head(1)
    # I3 / I4 : 동시 보유 16
    keep = []; exits = []  # exits: 보유 중 청산 세션 인덱스
    for sess, g in c.groupby("e", sort=True):
        exits = [x for x in exits if x > sess]
        free = K_SLOTS - len(exits)
        if free <= 0:
            continue
        take = g.index[:free]
        keep.extend(take); exits.extend([sess + H] * len(take))
    return c.loc[keep]


def per_trade(T, costcol):
    net = T.gA20.values - T[costcol].values
    half = np.where(T.year.values <= 2021, "h1", "h2")
    sess = pd.Series(net).groupby(T.e.values).mean()
    yr = pd.Series(net).groupby(T.year.values).agg(["mean", "count"])
    return {
        "n": int(len(T)), "mean": float(net.mean()), "median": float(np.median(net)),
        "win": float((net > 0).mean()),
        "t_session_nw20": nw_t(sess.values), "t_dayclust": cluster_t(net, T.e.values),
        "h1": float(net[half == "h1"].mean()), "h2": float(net[half == "h2"].mean()),
        "n_h1": int((half == "h1").sum()), "n_h2": int((half == "h2").sum()),
        "by_year": {int(y): {"mean": float(r["mean"]), "n": int(r["count"])} for y, r in yr.iterrows()},
        "years_pos_share": float((yr["mean"] > 0).mean()),
    }


def portfolio(T, O, ti, cal, K, costcol, start, end):
    n = end - start + 1
    iw = O[ti["IWM"]]
    riw = np.zeros(len(cal)); riw[:-1] = iw[1:] / iw[:-1] - 1
    absr = np.zeros(n); exr = np.zeros(n); npos = np.zeros(n, int)
    for tk, e, c in zip(T.ticker.values, T.e.values, T[costcol].values):
        o = O[ti[tk], e:e + H + 1].astype(float)
        b = iw[e:e + H + 1].astype(float)
        # 이탈(1차 실행 후 수정): 슬롯 안은 매수보유 — 일 손익 = Δopen / 진입 open (합 = 창 수익, 정확)
        r = np.diff(o) / o[0]; rb = np.diff(b) / b[0]
        sl = slice(e - start, e - start + H)
        absr[sl] += r / K; exr[sl] += (r - rb) / K; npos[sl] += 1
        absr[e - start] -= c / 2 / K; exr[e - start] -= c / 2 / K
        absr[e - start + H - 1] -= c / 2 / K; exr[e - start + H - 1] -= c / 2 / K
    days = cal[start:end + 1]
    return pd.DataFrame({"date": days, "abs": absr, "ex": exr, "iwm": riw[start:end + 1], "npos": npos})


def pstats(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    yrs = len(r) / 252
    dd = eq / np.maximum.accumulate(eq) - 1
    return {"ann_mean": float(r.mean() * 252), "ann_vol": float(r.std() * math.sqrt(252)),
            "sharpe": float(r.mean() / r.std() * math.sqrt(252)) if r.std() > 0 else float("nan"),
            "maxdd": float(dd.min()), "cagr": float(eq[-1] ** (1 / yrs) - 1)}


def port_block(D):
    yr = D.date.dt.year
    half = np.where(yr <= 2021, "h1", "h2")
    out = {"abs": pstats(D["abs"].values), "iwm_bh": pstats(D.iwm.values), "ex": pstats(D.ex.values),
           "ex_t_nw20": nw_t(D.ex.values),
           "ex_ann_h1": float(D.ex[half == "h1"].mean() * 252), "ex_ann_h2": float(D.ex[half == "h2"].mean() * 252),
           "ex_ann_by_year": {int(y): float(g.mean() * 252) for y, g in D.ex.groupby(yr)},
           "abs_ann_by_year": {int(y): float(g.mean() * 252) for y, g in D["abs"].groupby(yr)},
           "iwm_ann_by_year": {int(y): float(g.mean() * 252) for y, g in D.iwm.groupby(yr)},
           "npos_mean": float(D.npos.mean()), "npos_max": int(D.npos.max()),
           "util": float(D.npos.mean())}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "run.log").write_text("", encoding="utf-8")
    P = pd.read_csv(SCORES, usecols=["ticker", "date", "hour", "entry_date", "e", "year", "close_a", "dvol20",
                                     "gA20", SCORE], dtype={"ticker": str})
    P = P[P.gA20.notna()].reset_index(drop=True)
    cal, ti, O = load_open(P.ticker.unique())
    chk = (cal[P.e.values].strftime("%Y-%m-%d") == P.entry_date.str[:10].values).mean()
    log("events", len(P), "tickers", P.ticker.nunique(), "e↔entry_date 일치율", round(float(chk), 6))
    assert chk > 0.999
    miss = [t for t in P.ticker.unique() if t not in ti]
    if miss:
        log("가격 없는 티커 제외", len(miss)); P = P[~P.ticker.isin(miss)].reset_index(drop=True)
    P["c_real"] = 0.0044 + slip(P.dvol20.values)
    P["c_044"] = 0.0044; P["c_010"] = 0.0010
    res = {"n_events": int(len(P)), "e_date_match": float(chk)}

    start = int(P.e.min()); end = int(P.e.max()) + H
    res["port_window"] = [str(cal[start].date()), str(cal[end].date()), end - start + 1]

    for mode in ("primary", "rolling", "excl2017"):
        P["thr"] = thresholds(P, mode)
        P["qual"] = P.thr.notna() & (P[SCORE] >= P.thr)
        Q = P if mode != "excl2017" else P
        if mode == "excl2017":
            P.loc[P.year == 2017, "qual"] = False
        # 연도별 실현 선택 비율
        rate = P.groupby("year").apply(lambda g: pd.Series({
            "thr": float(g.thr.median()) if g.thr.notna().any() else float("nan"),
            "sel_share": float(g.qual.mean()), "n_qual": int(g.qual.sum()), "n": int(len(g))})).to_dict("index")
        R = {"selection_rate_by_year": {int(k): v for k, v in rate.items()}}
        if mode == "primary":
            log("=== 연도별 실현 선택 비율 (primary)")
            for y, v in rate.items():
                log(y, f"thr={v['thr']:.5f} share={v['sel_share']:.3f} n_qual={v['n_qual']}/{v['n']}")
        for rule in ("I1", "I2", "I3", "I4"):
            T = select(P, rule)
            rr = {"per_trade": {c: per_trade(T, c) for c in ("c_real", "c_044", "c_010")},
                  "gross_mean": float(T.gA20.mean()),
                  "slip_mix": {k: float(v) for k, v in pd.Series(slip(T.dvol20.values)).value_counts(normalize=True).items()},
                  "median_close_a": float(T.close_a.median()), "median_dvol20": float(T.dvol20.median())}
            if rule == "I1":
                # 동시 보유 분포(참고)
                cnt = np.zeros(end - start + 1, int)
                for e in T.e.values:
                    cnt[e - start:e - start + H] += 1
                rr["concurrent"] = {"mean": float(cnt.mean()), "median": float(np.median(cnt)),
                                    "p90": float(np.percentile(cnt, 90)), "max": int(cnt.max())}
            if rule in ("I2", "I3", "I4") and mode == "primary":
                K = 20 if rule == "I2" else K_SLOTS
                rr["portfolio"] = {}
                for c in ("c_real", "c_044", "c_010"):
                    D = portfolio(T, O, ti, cal, K, c, start, end)
                    rr["portfolio"][c] = port_block(D)
                    if c == "c_real":
                        D.to_csv(OUT / f"daily_{rule}.csv.gz", index=False, compression="gzip")
                rr["K"] = K
            if mode == "primary":
                T.drop(columns=["qual"]).to_csv(OUT / f"trades_{rule}.csv.gz", index=False, compression="gzip")
            R[rule] = rr
            pt = rr["per_trade"]["c_real"]
            msg = (f"[{mode}] {rule} n={pt['n']} mean={pt['mean']*100:.3f}% t_sess={pt['t_session_nw20']:.2f} "
                   f"t_dc={pt['t_dayclust']:.2f} h1={pt['h1']*100:.3f} h2={pt['h2']*100:.3f} "
                   f"yrs+={pt['years_pos_share']:.2f}")
            if "portfolio" in rr:
                pp = rr["portfolio"]["c_real"]
                msg += (f" | port ex_ann={pp['ex']['ann_mean']*100:.2f}% t={pp['ex_t_nw20']:.2f} "
                        f"sharpe={pp['abs']['sharpe']:.2f} cagr={pp['abs']['cagr']*100:.2f}% mdd={pp['abs']['maxdd']*100:.1f}%")
            log(msg)
        res[mode] = R

    # 판정
    verdict = {}
    for rule in ("I1", "I2", "I3", "I4"):
        R = res["primary"][rule]; pt = R["per_trade"]["c_real"]
        t_j = pt["t_session_nw20"] if rule == "I1" else R["portfolio"]["c_real"]["ex_t_nw20"]
        verdict[rule] = {"t_judged": t_j, "h1": pt["h1"], "h2": pt["h2"],
                         "pass": bool(t_j >= 2.5 and pt["h1"] > 0 and pt["h2"] > 0)}
    res["verdict"] = verdict
    log("판정", json.dumps(verdict, ensure_ascii=False))

    # 자본 산수
    cap = {}
    nyrs = (cal[end] - cal[start]).days / 365.25
    for rule in ("I2", "I3", "I4"):
        R = res["primary"][rule]
        cap[rule] = {}
        Kr = R["K"]
        for c in ("c_real", "c_044", "c_010"):
            pt = R["per_trade"][c]
            tpy = pt["n"] / nyrs
            cap[rule][c] = {"trades_per_year": tpy,
                            "krw_per_year_trade_basis": tpy * SLOT_KRW * pt["mean"],
                            "krw_per_year_port_basis": R["portfolio"][c]["ex"]["ann_mean"] * SLOT_KRW * Kr,
                            "book_krw": SLOT_KRW * Kr,
                            "krw_2025_2026_trade_basis": float(np.mean([pt["by_year"].get(y, {"mean": np.nan})["mean"]
                                                                        for y in (2025, 2026)])) * tpy * SLOT_KRW}
    # 실전 임계(2027용 = OOS 2017~2026 90분위, 2026용 = OOS 2017~2025)
    cap["live_threshold_2026"] = float(np.percentile(P.loc[P.year <= 2025, SCORE], 90))
    cap["live_threshold_2027"] = float(np.percentile(P[SCORE], 90))
    cap["years_span"] = nyrs
    res["capital"] = cap
    log("capital", json.dumps(cap, ensure_ascii=False, default=float))
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
