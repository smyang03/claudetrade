"""T5 위험률(생존) 매도 모델 — 현행 고정 출구 계약 대비 검증 (2026-09-27).

왜 다시: research_exit_models_20260927.py 의 매도 모델은 분류기 + 고정 임계 0.5 였고 거래의 49~74%를
일찍 팔아 계약의 TP 꼬리를 깎았다. 매도 시점은 시간-사건 구조다 — 매 결정 시점의 질문은
"다음 결정 전까지 큰 역행(또는 SL)이 올 위험률"이고, 확신할 때만 팔아야 한다.

사전등록 (실행 전 고정 — 결과를 보고 바꾸지 않는다)
===================================================
표본·계약·결정 시점·체결·비용·워크포워드·평가 = research_exit_models_20260927.py 와 동일.
  - KR xkr_fallen3 (비용 0.25 왕복, BE락 없음) / US xus_fallen3 (비용 0.50, BE락 4%).
  - 계약 TP12/SL-25/D7, em.contract_path 재계산 + vb.contract_exit_v2 오라클 assert(불일치 시 중단).
  - 결정 시점 D0..D4 종가(그 종가까지만 사용), SELL = 다음 시가 매도. 주 체결 = +12% 캡(sell_cap), 보조 = 원 시가(sell_raw).
  - 특성 = em.FEATS_BASE + 지수 ix_* (동일 정의). 결정 시점 구성은 em.build 와 동일하며
    --verify N 으로 em.build 와 행 단위 일치(1e-6)를 검사한다. (재구성 이유: 봉 캐시가 무한 증가해
    이전 실행이 OOM — 종목별로 묶어 처리하고 캐시를 비운다. 계산식은 동일.)
  - 월별 확장 워크포워드: 테스트 월 M = D0 가 M 인 거래, 학습 = d_end < M 첫날 (라벨 해소 purge).
    첫 D0 월 + 3개월부터 테스트.
  - 평가: 거래별 (정책 net − 계약 net), 진입일(D0) 클러스터 평균·t, 시간순 전·후반, 조기매도 비율.

사건(위험률 라벨, 고정): 결정 시점 j 에서 다음 구간 (j, j+1] 에
  E = 1 if close_{j+1}/close_j − 1 <= −5% (KR·US 공통 X=5) OR 계약이 j+1 봉에서 SL 청산.
  구간별 사건(재발 허용, Andersen-Gill 계수과정). 계약이 j 이하에서 이미 청산된 행은 없다(em 과 동일).

학습/보정 분할 (모든 아키텍처 공통, 고정):
  학습 창(d_end < M) 거래의 D0 80% 분위 날짜 = cut. 적합셋 = d_end < cut 인 거래, 보정셋 = D0 >= cut 인 거래.
  테스트에 쓰는 모델은 **적합셋(창의 앞 80%)으로 적합한 모델 그대로**(재적합 없음 — 임계/conformal 척도 유지).
  → 이전 스크립트 모델보다 적은 데이터로 학습한다. b2/b3 기준선은 이전과 같이 학습 창 전체에서 선택.
  보정셋 거래 < 300 이면 그 달은 "never"(=계약).
  전처리: 로지스틱·Cox 는 적합셋 median 대치 + 표준화(적합셋 통계만). dow4 제외(원핫 합=1 특이).

아키텍처 (기본 파라미터, random_state=0):
  (1) 이산시간 위험률 — pooled logistic: 사람-일 행, 표준화 특성 + 보유일 더미(k2..k5), sklearn LogisticRegression
      (L2 기본 C=1.0, max_iter 2000).
  (2) 이산시간 위험률 — LightGBM 이진(LGBMClassifier 기본, num_threads=2).
  (3) Cox 비례위험 — lifelines CoxTimeVaryingFitter(penalizer=0.01), 각 결정 행을 독립 id 로 start=j, stop=j+1,
      event=E (Andersen-Gill 과 동일한 부분우도, 시간변동 공변량). 시간축이 보유일이므로 k 는 공변량에서 제외.
      적합셋을 거래 단위 표본추출(seed 0)로 최대 COX_MAX_ROWS=30000 행. 위험률 = Δbaseline_cumhaz(j+1) × partial hazard.
  (4) 남은 수익 직접 회귀 + 기권: LightGBM 회귀(기본)로 rem = 계약 net − 지금 매도 net 예측,
      split-conformal 80% 구간(보정셋 |잔차|의 유한표본 보정 분위 ceil((n+1)·0.8)/n = q),
      pred + q < 0 일 때만 SELL. 체결 규약별(cap/raw) 따로 적합.
정책 (1)(2)(3): 거래별 D0..D4 순서로 처음 위험률 > θ 인 시점에 SELL.
  θ 격자 = 보정셋 위험률의 분위 {0.5,0.6,0.7,0.8,0.85,0.9,0.925,0.95,0.965,0.975,0.985,0.99,0.995,0.999} (3모델 공통).
  보정셋에서 거래 단위 조기매도 비율 <= 20% 를 만족하는 θ 중 보정셋 평균 개선(계약 대비) 최대, 양수가 없으면 never.
  (20% 상한 = TP 꼬리 보호. 선택 시점 제약이며 테스트 비율은 결과로 보고한다.)
기준선: b1 = 계약(개선 0), b2 손절선 / b3 트레일링 (em 과 동일 격자, 학습 창 전체에서 선택).
판정 (2시장 × 4아키텍처 = 8 메인 검정, 주 체결 cap):
  확정 = 일클러스터 t >= 2.7 AND 전·후반 모두 양수 AND 최선 단순 기준선(b2/b3 중 테스트 일평균 높은 쪽) 대비
  쌍대 일평균 차이 > 0. 후보 = 2 <= t < 2.7 (나머지 조건 동일), 그 외 기각.
보고: 계약 대비 거래당 평균 개선, 일 t, 전·후반, 조기매도 비율, 매도 정밀도(매도된 거래 중 그 시점 이후 나머지가
  실제로 음수 = 계약 net < 매도 net 인 비율) + 그 기준율(테스트 결정 행 전체의 같은 비율), 사건 AUC(참고).
메모리: 시장별 별도 프로세스(--markets KR / US), float32, n_jobs<=2, 월별 결과 jsonl 즉시 기록, 결정 표 캐시(pkl).
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import time
import warnings
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
warnings.filterwarnings("ignore", message="X does not have valid feature names")
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "research"))

import virtual_books as vb  # noqa: E402
import research_exit_models_20260927 as em  # noqa: E402

OUT = ROOT / "data" / "analysis" / "model_T5_hazard_exit_20260927"
X_DROP = -5.0
CAP_SHARE = 0.20
MIN_CALIB_TRADES = 300
COX_MAX_ROWS = 30000
QGRID = [0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.925, 0.95, 0.965, 0.975, 0.985, 0.99, 0.995, 0.999]
ARCHS = ("hz_logit", "hz_lgbm", "cox", "conformal")
FILLS = ("sell_cap", "sell_raw")


def _clear_caches() -> None:
    vb._KR_BAR_CACHE.clear()
    m = sys.modules.get("research_early_exit_no_bump")
    if m is not None:
        m._BAR_CACHE.clear()


def build(market: str, limit: int | None = None):
    """em.build 와 같은 결정 시점·특성 + 사건 라벨. 종목별 묶음 처리 후 봉 캐시 비움."""
    cfg = em.MKT[market]
    hold = em.hold_of(cfg["sid"])
    ndec = hold - 2
    with closing(sqlite3.connect(f"file:{vb.BOOK_DB.as_posix()}?mode=ro", uri=True, timeout=10)) as c:
        rows = c.execute("select session_date, ticker, net_pct from trades where strategy_id=? and status='CLOSED' "
                         "order by session_date, ticker", (cfg["sid"],)).fetchall()
    if limit:
        rows = rows[:limit]
    ix = em.idx_frame(market)
    _clear_caches()
    stats = dict(n_ledger=len(rows), no_bars=0, short=0, mismatch=0, hold=hold, ndec=ndec)
    by_tk = defaultdict(list)
    for pos, (sd, tk, lnet) in enumerate(rows):
        by_tk[tk].append((pos, sd, lnet))
    trades, dec = [], []
    for tk, items in by_tk.items():
        b = em.load_bars(market, tk)
        if not b:
            stats["no_bars"] += len(items)
            _clear_caches()
            continue
        closes = np.array([x[4] for x in b], float)
        vols = np.array([x[5] for x in b], float)
        dates = [x[0] for x in b]
        for pos, sd, lnet in items:
            if market == "KR":
                ei = next((i for i, x in enumerate(dates) if x > str(sd)), None)
            else:
                ei = next((i for i, x in enumerate(dates) if x == str(sd)), None)
            if ei is None or len(b) < ei + hold or b[ei][1] <= 0:
                stats["short"] += 1
                continue
            win = b[ei: ei + hold]
            entry = float(win[0][1])
            net, reason, xi = em.contract_path(entry, win, cfg["fee"], cfg["be"], hold)
            oracle = vb.contract_exit_v2(entry, win, fee=cfg["fee"], be_lock=cfg["be"], hold=hold)
            assert oracle is not None and abs(oracle[0] - net) < 1e-9, (tk, sd)
            if lnet is None or abs(float(lnet) - net) > 0.01:
                stats["mismatch"] += 1
            trades.append(dict(pos=pos, ticker=tk, d0=win[0][0], d_end=win[-1][0], month=win[0][0][:7],
                               contract=net, reason=reason, xi=xi))
            d0_lo, d0_hi = win[0][3], win[0][2]
            mfe = (win[0][4] - entry) / entry * 100
            mae = mfe
            for j in range(ndec):
                if j > 0:
                    mfe = max(mfe, (win[j][2] - entry) / entry * 100)
                    mae = min(mae, (win[j][3] - entry) / entry * 100)
                if xi <= j:
                    break
                gi = ei + j
                d, o, h, l, cl, v = win[j]
                nxt_open = win[j + 1][1]
                c1 = win[j + 1][4]
                pc = closes[gi - 1] if gi >= 1 else np.nan
                r20 = np.diff(np.log(closes[max(0, gi - 20): gi + 1])) * 100
                vprev = vols[max(0, gi - 20): gi]
                nret = (c1 / cl - 1) * 100 if cl > 0 else np.nan
                ev = int((nret <= X_DROP) or (xi == j + 1 and reason == "SL"))
                dec.append(dict(
                    pos=pos, j=j, date=d, unreal=(cl - entry) / entry * 100, mfe=mfe, mae=mae, k=j + 1,
                    ret1=(cl / pc - 1) * 100 if pc > 0 else np.nan,
                    gap=(o / pc - 1) * 100 if pc > 0 else np.nan,
                    from_d0lo=(cl / d0_lo - 1) * 100, from_d0hi=(cl / d0_hi - 1) * 100,
                    mom5=(cl / closes[gi - 5] - 1) * 100 if gi >= 5 and closes[gi - 5] > 0 else np.nan,
                    mom20=(cl / closes[gi - 20] - 1) * 100 if gi >= 20 and closes[gi - 20] > 0 else np.nan,
                    rv20=float(np.std(r20)) if len(r20) >= 10 else np.nan,
                    volr=v / vprev.mean() if len(vprev) >= 10 and vprev.mean() > 0 else np.nan,
                    dow=date.fromisoformat(d).weekday(),
                    sell_raw=(nxt_open - entry) / entry * 100 - cfg["fee"],
                    sell_cap=min((nxt_open - entry) / entry * 100, em.TP) - cfg["fee"],
                    nret=nret, ev=ev,
                ))
        _clear_caches()
    T = pd.DataFrame(trades).sort_values("pos").reset_index(drop=True)
    T["tid"] = np.arange(len(T))
    D = pd.DataFrame(dec).merge(T[["pos", "tid", "contract", "d_end", "month", "d0", "ticker"]], on="pos")
    D = D.merge(ix, left_on="date", right_index=True, how="left")
    D = D.sort_values(["tid", "j"]).reset_index(drop=True)
    for dow in range(5):
        D[f"dow{dow}"] = (D["dow"] == dow).astype(float)
    stats["n_trades"] = len(T)
    stats["n_dec"] = len(D)
    return T, D, stats


def verify(market: str, n: int) -> None:
    T1, D1, s1 = em.build(market, n)
    _clear_caches()
    T2, D2, s2 = build(market, n)
    D1 = D1.merge(T1[["tid", "ticker"]], on="tid")
    cols = em.feats(D1) + ["sell_cap", "sell_raw", "contract"]
    a = D1.set_index(["ticker", "d0", "j"]).sort_index()
    b = D2.set_index(["ticker", "d0", "j"]).sort_index()
    assert len(a) == len(b), (len(a), len(b))
    assert a.index.equals(b.index)
    for ccol in cols:
        x, y = a[ccol].to_numpy(float), b[ccol].to_numpy(float)
        ok = np.allclose(x, y, atol=1e-6, equal_nan=True)
        assert ok, (ccol, np.nanmax(np.abs(x - y)))
    ta = T1.set_index(["ticker", "d0"]).sort_index()
    tb = T2.set_index(["ticker", "d0"]).sort_index()
    assert (ta["reason"] == tb["reason"]).all() and (ta["xi"] == tb["xi"]).all()
    print(f"[verify {market}] em.build 일치: trades={len(T1)} dec={len(D1)} cols={len(cols)}", flush=True)


def feat_cols(D: pd.DataFrame) -> list[str]:
    return em.feats(D)


class Prep:
    """적합셋 통계로 median 대치 + 표준화 (dow4 제외)."""

    def __init__(self, X: pd.DataFrame):
        self.cols = [c for c in X.columns if c != "dow4"]
        Z = X[self.cols]
        self.med = Z.median()
        Zf = Z.fillna(self.med)
        self.mu = Zf.mean()
        self.sd = Zf.std(ddof=0).replace(0, 1.0)

    def __call__(self, X: pd.DataFrame) -> pd.DataFrame:
        return ((X[self.cols].fillna(self.med) - self.mu) / self.sd).astype("float32")


def kdum(D: pd.DataFrame) -> np.ndarray:
    return np.stack([(D["k"].to_numpy() == k).astype("float32") for k in (2, 3, 4, 5)], axis=1)


def fit_predict_hazards(fit: pd.DataFrame, others: list[pd.DataFrame], Xc: list[str]) -> dict:
    """반환: arch -> [pred for each df in others]."""
    from lightgbm import LGBMClassifier
    from sklearn.linear_model import LogisticRegression
    out = {}
    y = fit["ev"].to_numpy()
    pp = Prep(fit[Xc])
    Zf = pp(fit)
    # (1) pooled logistic (보유일 더미 포함, 연속 k 는 제외 — 더미로 대체)
    lc = [c for c in pp.cols if c != "k"]
    lr = LogisticRegression(max_iter=2000, random_state=0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        lr.fit(np.hstack([Zf[lc].to_numpy(), kdum(fit)]), y)
    out["hz_logit"] = [lr.predict_proba(np.hstack([pp(o)[lc].to_numpy(), kdum(o)]))[:, 1] for o in others]
    # (2) LightGBM 이진
    gb = LGBMClassifier(random_state=0, n_jobs=2, verbose=-1, force_col_wise=True)
    gb.fit(fit[Xc].to_numpy(np.float32), y)
    out["hz_lgbm"] = [gb.predict_proba(o[Xc].to_numpy(np.float32))[:, 1] for o in others]
    # (3) Cox time-varying (행별 id, start=j, stop=j+1)
    from lifelines import CoxTimeVaryingFitter
    cc = [c for c in pp.cols if c != "k"]
    rng = np.random.default_rng(0)
    tids = fit["tid"].unique()
    rows_per = len(fit) / max(1, len(tids))
    ntake = min(len(tids), int(COX_MAX_ROWS / rows_per))
    samp = fit[fit["tid"].isin(rng.choice(tids, ntake, replace=False))] if ntake < len(tids) else fit
    cdf = pp(samp)[cc].astype("float64")
    cdf["id"] = np.arange(len(cdf))
    cdf["start"] = samp["j"].to_numpy(float)
    cdf["stop"] = samp["j"].to_numpy(float) + 1
    cdf["event"] = samp["ev"].to_numpy().astype(bool)
    ctv = CoxTimeVaryingFitter(penalizer=0.01)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        ctv.fit(cdf, id_col="id", event_col="event", start_col="start", stop_col="stop", show_progress=False)
    bch = ctv.baseline_cumulative_hazard_.iloc[:, 0]
    bch = bch.reindex([1.0, 2.0, 3.0, 4.0, 5.0]).ffill().fillna(0.0)
    dh = bch.diff().fillna(bch.iloc[0])
    cox_pred = []
    for o in others:
        ph = ctv.predict_partial_hazard(pp(o)[cc].astype("float64")).to_numpy()
        cox_pred.append(ph * dh.reindex(o["j"].to_numpy(float) + 1).to_numpy())
    out["cox"] = cox_pred
    out["_cox_rows"] = len(samp)
    return out


def fit_predict_conformal(fit: pd.DataFrame, cal: pd.DataFrame, te: pd.DataFrame, Xc: list[str], fill: str):
    from lightgbm import LGBMRegressor
    y = (fit["contract"] - fit[fill]).to_numpy()
    rg = LGBMRegressor(random_state=0, n_jobs=2, verbose=-1, force_col_wise=True)
    rg.fit(fit[Xc].to_numpy(np.float32), y)
    pc = rg.predict(cal[Xc].to_numpy(np.float32))
    res = np.abs((cal["contract"] - cal[fill]).to_numpy() - pc)
    n = len(res)
    lev = min(1.0, math.ceil((n + 1) * 0.8) / n)
    q = float(np.quantile(res, lev, method="higher"))
    pt = rg.predict(te[Xc].to_numpy(np.float32))
    return pt, q


def choose_threshold(cal: pd.DataFrame, Tcal: pd.DataFrame, h: np.ndarray, fill: str):
    ncal = len(Tcal)
    best, thr, info = 0.0, None, []
    for qv in QGRID:
        t = float(np.quantile(h, qv))
        pol = em.policy_first(cal, h > t, fill)
        share = len(pol) / ncal
        imp = em.improvement(Tcal, pol).mean()
        info.append((qv, t, round(share, 4), round(float(imp), 4)))
        if share <= CAP_SHARE and imp > best + 1e-12:
            best, thr = imp, t
    return thr, best, info


def summarize(Tm: pd.DataFrame, Dt: pd.DataFrame, pol: pd.Series, fill: str) -> tuple[dict, pd.Series]:
    imp = em.improvement(Tm, pol)
    st = em.day_stats(Tm, imp)
    daily = st.pop("daily")
    st["sold_share"] = len(pol) / len(Tm)
    if len(pol):
        c = Tm.set_index("tid").loc[pol.index, "contract"].to_numpy()
        st["precision"] = float((c < pol.to_numpy()).mean())
        st["n_sold"] = int(len(pol))
    else:
        st["precision"] = float("nan")
        st["n_sold"] = 0
    return st, daily


def run_market(market: str, limit: int | None, max_months: int | None, tag: str, from_cache: bool) -> dict:
    from sklearn.metrics import roc_auc_score
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"dec_{tag}_{market}.pkl"
    t0 = time.time()
    if from_cache and cache.exists():
        T, D, stats = pd.read_pickle(cache)
    else:
        T, D, stats = build(market, limit)
        Xc0 = feat_cols(D)
        D[Xc0] = D[Xc0].astype("float32")
        pd.to_pickle((T, D, stats), cache)
    print(f"[{market}] build {time.time() - t0:.0f}s stats={stats}", flush=True)
    Xc = feat_cols(D)
    for fill in FILLS:
        D[f"y_{fill}"] = (D["contract"] < D[fill]).astype(int)
    months = sorted(T["month"].unique())
    first = pd.Period(months[0])
    test_months = [m for m in months if pd.Period(m) >= first + 3]
    if max_months:
        test_months = test_months[:max_months]
    Tm_all = T[T["month"].isin(test_months)]
    Dt = D[D["month"].isin(test_months)]
    pols = {fill: defaultdict(list) for fill in FILLS}
    hz_test = {a: [] for a in ("hz_logit", "hz_lgbm", "cox")}
    jl = OUT / f"months_{tag}_{market}.jsonl"
    jl.write_text("", encoding="utf-8")
    for m in test_months:
        tm = time.time()
        mstart = f"{m}-01"
        Ttr = T[T["d_end"] < mstart]
        tr = D[D["d_end"] < mstart]
        te = D[D["month"] == m]
        if len(te) == 0 or len(tr) < 1000:
            continue
        cut = str(np.quantile(pd.to_datetime(Ttr["d0"]).astype("int64"), 0.8).astype("datetime64[ns]"))[:10]
        Tfit, Tcal = Ttr[Ttr["d_end"] < cut], Ttr[Ttr["d0"] >= cut]
        fit, cal = tr[tr["tid"].isin(Tfit["tid"])], tr[tr["tid"].isin(Tcal["tid"])]
        rec = dict(month=m, cut=cut, n_fit_trades=len(Tfit), n_cal_trades=len(Tcal), n_fit_rows=len(fit),
                   ev_rate_fit=float(fit["ev"].mean()), n_test_trades=int((T["month"] == m).sum()))
        # 기준선 (학습 창 전체)
        for fill in FILLS:
            for kind, grid in (("b2", [None, 3, 5, 7, 10, 15, 20]), ("b3", [None, 3, 5, 7, 10, 15])):
                best, bp = -1e9, None
                for p in grid:
                    v = em.improvement(Ttr, em.policy_first(tr, em.rule_mask(tr, kind, p), fill)).mean()
                    if v > best + 1e-12:
                        best, bp = v, p
                pols[fill][kind].append(em.policy_first(te, em.rule_mask(te, kind, bp), fill))
                rec[f"{fill}_{kind}"] = bp
        ok = len(Tcal) >= MIN_CALIB_TRADES and len(fit) >= 1000
        if ok:
            hp = fit_predict_hazards(fit, [cal, te], Xc)
            rec["cox_rows"] = hp.pop("_cox_rows")
            for a, (hc, ht) in hp.items():
                hz_test[a].append(pd.Series(ht, index=te.index))
                for fill in FILLS:
                    thr, bimp, info = choose_threshold(cal, Tcal, hc, fill)
                    rec[f"{fill}_{a}_thr"] = thr
                    rec[f"{fill}_{a}_calimp"] = bimp
                    if fill == "sell_cap":
                        rec[f"{a}_grid"] = info
                    mask = (ht > thr) if thr is not None else np.zeros(len(te), bool)
                    pols[fill][a].append(em.policy_first(te, mask, fill))
            for fill in FILLS:
                pt, q = fit_predict_conformal(fit, cal, te, Xc, fill)
                rec[f"{fill}_conformal_q"] = q
                rec[f"{fill}_conformal_minpred"] = float(pt.min())
                pols[fill]["conformal"].append(em.policy_first(te, (pt + q) < 0, fill))
        else:
            rec["skipped"] = "calib<300 or fit<1000 -> never"
        rec["sec"] = round(time.time() - tm, 1)
        with jl.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"[{market}] {m} fit={len(fit)} cal_tr={len(Tcal)} ev={rec['ev_rate_fit']:.3f} "
              + " ".join(f"{a}={rec.get(f'sell_cap_{a}_thr')}" for a in ("hz_logit", "hz_lgbm", "cox"))
              + f" confq={rec.get('sell_cap_conformal_q')} {rec['sec']}s", flush=True)
    out = dict(stats=stats, test_months=[test_months[0], test_months[-1]])
    out["event"] = dict(row_rate=float(Dt["ev"].mean()),
                        trade_rate=float(Dt.groupby("tid")["ev"].max().mean()))
    for a, lst in hz_test.items():
        if lst:
            p = pd.concat(lst)
            out["event"][f"auc_{a}"] = float(roc_auc_score(Dt.loc[p.index, "ev"], p))
    for fill in FILLS:
        table, daily = {}, {}
        for name in ("b2", "b3") + ARCHS:
            lst = pols[fill].get(name, [])
            pol = pd.concat(lst) if lst else pd.Series(dtype=float)
            st, dly = summarize(Tm_all, Dt, pol, fill)
            table[name], daily[name] = st, dly
        best_b = max(("b2", "b3"), key=lambda k: table[k]["mean_day"])
        for name in ARCHS:
            dm, dt = em.paired_t(daily[name], daily[best_b])
            st = table[name]
            st["vs_best_baseline"] = dict(baseline=best_b, diff_day=dm, t=dt)
            passes = st["h1"] > 0 and st["h2"] > 0 and dm > 0
            st["verdict"] = ("확정" if passes and st["t"] >= 2.7 else
                             "후보" if passes and st["t"] >= 2.0 else "기각")
        table["_base"] = dict(label_rate_rows=float(Dt[f"y_{fill}"].mean()), n_test_trades=len(Tm_all),
                              n_test_dec=len(Dt), contract_mean=float(Tm_all["contract"].mean()))
        out[fill] = table
    (OUT / f"results_{tag}_{market}.json").write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str),
                                                      encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="KR")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-months", type=int, default=None)
    ap.add_argument("--tag", default="full")
    ap.add_argument("--verify", type=int, default=0)
    ap.add_argument("--from-cache", action="store_true")
    a = ap.parse_args()
    for mk in a.markets.split(","):
        if a.verify:
            verify(mk, a.verify)
            _clear_caches()
        r = run_market(mk, a.limit, a.max_months, a.tag, a.from_cache)
        print(json.dumps({"market": mk, "stats": r["stats"], "event": r["event"]}, ensure_ascii=False))
        for fill in FILLS:
            tb = r[fill]
            print(f"\n== {mk} {fill} base={tb['_base']}")
            for name, st in tb.items():
                if name.startswith("_"):
                    continue
                print(f"{name:10s} mean/trade={st['mean_trade']:+.3f} day={st['mean_day']:+.3f} t={st['t']:+.2f} "
                      f"h1={st['h1']:+.3f} h2={st['h2']:+.3f} sold={st['sold_share']:.1%} "
                      f"prec={st['precision']:.3f} vsB={st.get('vs_best_baseline')} {st.get('verdict', '')}",
                      flush=True)


if __name__ == "__main__":
    main()
