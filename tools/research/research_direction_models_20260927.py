"""지수 ETF 방향 예측 모델 패밀리 — 사전등록 검증 (2026-09-27)

목적: "오를지 내릴지" 판단 모델이 비용 후 거래 가능한 열쇠가 되는지 정직하게 판정한다.
이 docstring은 실행 전에 고정한다. 결과를 본 뒤 수정하지 않는다.

=== 사전등록 ===
데이터: yfinance auto_adjust=True, data/analysis/direction_models_20260927/raw/*.csv 캐시.
  실패 시 data/analysis/voltarget_20260924/ 사본 사용.

대상 종목: 069500.KS (KOSPI200), 229200.KS (KOSDAQ150), SPY.
타깃 (종목마다 3개 → 9칸):
  T1 = close[D+1] > close[D]        (다음날 종가-종가 방향, h=1)
  T2 = open[D+1]  > close[D]        (오버나이트 방향, h=1)
  T3 = close[D+5] > close[D]        (5거래일 방향, h=5)
결정 시점 = 대상 시장의 D일 종가. 자기 종목 D일 OHLCV 사용 가능(종가 체결 가정 — 동시호가/MOC 근사).

정렬(다른 시장 시계열은 그 시장 자체 날짜축에서 merge_asof; 조인 후 shift 금지):
  KR 대상(069500, 229200), 결정 = KST D 15:30:
    - 자기 OHLCV: D
    - SPY, QQQ, ^VIX, ^TNX, CL=F, GC=F, DX-Y.NYB: D 미만(strictly before) — US D 세션은 KST D+1 새벽 종료
    - KRW=X: D 미만 (Yahoo 일봉은 UTC 일말 종료 → KR 종가 이후)
    - ^N225: D 미만 (TSE 15:30 JST 종료 = KR 종가와 동시 → 보수적으로 제외)
    - ^HSI: D 미만 (17:00 KST 종료)
  SPY 대상, 결정 = ET D 16:00:
    - 자기 OHLCV: D;  QQQ: D (같은 16:00 종가)
    - ^N225, ^HSI: D 이하 (아시아 D 세션은 US 개장 전 종료)
    - ^VIX(16:15 종가), ^TNX, CL=F, GC=F, DX-Y.NYB: D 미만
    - USDKRW 피처는 SPY 칸에서 제외 (KRW=X가 2003-12 시작 → 학습기간 1년으로 붕괴, 관련성도 낮음)
  ^KS11/^KQ11은 수집만 하고 피처로 쓰지 않는다(자기 종목과 사실상 동일).

피처(고정):
  own: r1, r5, r20, r60, dma20, dma60, dma200 (close/MA-1), rv20, rv60 (일수익 std*sqrt252),
       rv_ratio = rv20/rv60, volu_ratio = 거래량 5일평균/60일평균 (0거래량은 NaN → 행 제외),
       rsi14 (Wilder EWM alpha=1/14), dow (0~4, 순서형 수치 그대로 — 선형/MLP도 동일),
       month_turn (월 마지막 1거래일 또는 첫 3거래일이면 1; 거래일 달력은 사전 공지 정보),
       gap = open[D]/close[D-1]-1, hl_range = (high-low)/close
  context: vix (레벨), vix_ch5 (5세션 로그변화), krw_1d, krw_5d, tnx_ch1 (1세션 차분, %p),
           spy_1d, qqq_1d (KR만; SPY 칸은 qqq_1d만, spy_1d는 자기 r1과 중복이라 제외),
           n225_1d, hsi_1d, oil_1d, gold_1d, dxy_1d
  → KR 28개 / SPY 25개. 모든 모델이 동일한 완전행(NaN 없음)만 사용.

모델(기본값, 테스트로 튜닝 금지, random_state=0):
  logit  = StandardScaler + LogisticRegression(L2, C=1, max_iter=2000)
  hgb    = HistGradientBoostingClassifier(random_state=0)
  rf     = RandomForestClassifier(300, min_samples_leaf=20, random_state=0, n_jobs=-1)
  mlp    = StandardScaler + MLPClassifier((32,16), early_stopping=True, random_state=0, max_iter=500)
  base1  = 매 재학습 시 학습 데이터에서 |AUC-0.5|가 가장 큰 피처 1개 → 그 피처 단일 로지스틱.
워크포워드: expanding, 연 1회 재학습. 첫 테스트 연도 = 종목 시작연도+5
  (069500: 2012, 229200: 2020, SPY: 2005). 퍼지 = 라벨 창이 테스트 시작일 이상에 닿는 학습 행 제거(h행).
  테스트 마지막: 라벨 확정된 마지막 행까지.

거래 규칙(롱/현금, 숏 없음):
  T1: p_up>0.5면 D 종가~D+1 종가 보유. 포지션 변경 1회당 편도 = 왕복비용/2.
  T3: 같은 규칙(5일 예측으로 매일 포지션 결정, 겹치는 예측).
  T2: p_up>0.5면 D 종가 매수 → D+1 시가 매도. 보유한 밤마다 왕복비용 전액.
  비용 왕복: KR ETF 0.10%, SPY 0.44%.
  B&H = 같은 테스트 기간 동일 종목 종가 보유(비용 0). 현금 수익 0, Sharpe는 rf 차감 없이 연율화(sqrt252).
  보조(판정 미사용): 노출 매칭 B&H(= B&H × 평균 보유비율), T2는 매일 오버나이트(비용 포함).
평가: OOS AUC(전 테스트 연도 풀링), 적중률 vs 기저율, CAGR, Sharpe, MaxDD, turnover,
  초과 t = (전략 일수익 - B&H 일수익)의 평균, Newey-West HAC(lags=h).
판정(사전등록):
  통과조건 = 전략 Sharpe > B&H Sharpe AND AUC > 0.52 AND 테스트 전/후반(날짜 중간점) 모두 평균 초과>0
            AND 모델이 자기 base1보다 Sharpe·AUC 모두 높음.
  통과 + t>=3.2 → 확정 / 통과 + 2.0<=t<3.2 → 후보 / 그 외 → 기각. (9칸 x 4모델 = 36검정)
  후보 이상이면 테스트 연도별 permutation importance(AUC) 평균 보고.
누수 점검: (a) 학습 라벨 셔플 플라시보 → OOS AUC≈0.5, (b) 실현 타깃을 피처로 주입한 양성대조 → AUC≈1,
  (c) 정렬 스팟체크: 한쪽 시장만 휴장한 날의 병합 원본 날짜 출력, 모든 context 원본날짜 < D(또는 규칙대로) assert.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "direction_models_20260927"
RAW = OUT / "raw"
OLD = ROOT / "data" / "analysis" / "voltarget_20260924"

TICKERS = ["069500.KS", "229200.KS", "SPY", "QQQ", "^VIX", "KRW=X", "^IRX", "^TNX", "^KS11",
           "^KQ11", "^N225", "^HSI", "CL=F", "GC=F", "DX-Y.NYB"]
OLD_MAP = {"069500.KS": "069500.KS.csv", "229200.KS": "229200.KS.csv", "SPY": "SPY.csv",
           "KRW=X": "KRW_X.csv", "^IRX": "IRX.csv"}
COST_RT = {"069500.KS": 0.0010, "229200.KS": 0.0010, "SPY": 0.0044}
START_YEAR = {"069500.KS": 2007, "229200.KS": 2015, "SPY": 2000}
HORIZON = {"T1": 1, "T2": 1, "T3": 5}


def fname(t: str) -> str:
    return t.replace("^", "").replace("=", "_").replace(".", "_") + ".csv"


def fetch(refresh: bool = False) -> dict[str, pd.DataFrame]:
    import yfinance as yf
    RAW.mkdir(parents=True, exist_ok=True)
    out = {}
    for t in TICKERS:
        p = RAW / fname(t)
        if p.exists() and not refresh:
            out[t] = pd.read_csv(p, index_col=0, parse_dates=True)
            continue
        df = None
        for _ in range(3):
            try:
                d = yf.download(t, start="1995-01-01", auto_adjust=True, progress=False)
                if d is not None and len(d):
                    if isinstance(d.columns, pd.MultiIndex):
                        d.columns = d.columns.get_level_values(0)
                    df = d[["Open", "High", "Low", "Close", "Volume"]].copy()
                    break
            except Exception as e:  # noqa
                print("fetch fail", t, e)
            time.sleep(2)
        if df is None and t in OLD_MAP:
            df = pd.read_csv(OLD / OLD_MAP[t], index_col=0, parse_dates=True)
            print("fallback old copy", t)
        if df is None:
            raise SystemExit(f"no data {t}")
        df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
        df = df[~df.index.duplicated(keep="last")].sort_index()
        df = df.dropna(subset=["Close"])
        df.to_csv(p)
        out[t] = df
    return out


def rsi_wilder(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def own_features(df: pd.DataFrame) -> pd.DataFrame:
    c, o, h, l, v = df.Close, df.Open, df.High, df.Low, df.Volume.replace(0, np.nan)
    lr = np.log(c).diff()
    f = pd.DataFrame(index=df.index)
    for k in (1, 5, 20, 60):
        f[f"r{k}"] = c / c.shift(k) - 1
    for k in (20, 60, 200):
        f[f"dma{k}"] = c / c.rolling(k).mean() - 1
    f["rv20"] = lr.rolling(20).std() * np.sqrt(252)
    f["rv60"] = lr.rolling(60).std() * np.sqrt(252)
    f["rv_ratio"] = f.rv20 / f.rv60
    f["volu_ratio"] = v.rolling(5).mean() / v.rolling(60).mean()
    f["rsi14"] = rsi_wilder(c)
    f["dow"] = df.index.dayofweek.astype(float)
    idx = df.index
    m = idx.month
    nxt = pd.Series(m, index=idx).shift(-1)
    last_day = (nxt != m) & nxt.notna()
    # 마지막 행(미래 달력 미상)은 False; 첫 3거래일
    prv = pd.Series(m, index=idx)
    first_rank = prv.groupby([idx.year, m]).cumcount()
    f["month_turn"] = (last_day.values | (first_rank.values < 3)).astype(float)
    f["gap"] = o / c.shift(1) - 1
    f["hl_range"] = (h - l) / c
    return f


def ctx_series(data: dict) -> dict[str, pd.Series]:
    """각 context 피처를 그 시장 자체 날짜축에서 계산."""
    cl = lambda t: data[t].Close  # noqa
    s = {
        "vix": cl("^VIX"),
        "vix_ch5": np.log(cl("^VIX")).diff(5),
        "krw_1d": cl("KRW=X").pct_change(),
        "krw_5d": cl("KRW=X").pct_change(5),
        "tnx_ch1": cl("^TNX").diff(),
        "spy_1d": cl("SPY").pct_change(),
        "qqq_1d": cl("QQQ").pct_change(),
        "n225_1d": cl("^N225").pct_change(),
        "hsi_1d": cl("^HSI").pct_change(),
        "oil_1d": cl("CL=F").pct_change(),
        "gold_1d": cl("GC=F").pct_change(),
        "dxy_1d": cl("DX-Y.NYB").pct_change(),
    }
    # FX/원자재 데이터 오류(0/음수 가격) 방어
    for k in list(s):
        s[k] = s[k].replace([np.inf, -np.inf], np.nan)
    return s


# (피처, strict) — strict=True: 원본 날짜 < D, False: <= D
ALIGN = {
    "KR": [("vix", True), ("vix_ch5", True), ("krw_1d", True), ("krw_5d", True), ("tnx_ch1", True),
           ("spy_1d", True), ("qqq_1d", True), ("n225_1d", True), ("hsi_1d", True), ("oil_1d", True),
           ("gold_1d", True), ("dxy_1d", True)],
    "US": [("vix", True), ("vix_ch5", True), ("tnx_ch1", True), ("qqq_1d", False), ("n225_1d", False),
           ("hsi_1d", False), ("oil_1d", True), ("gold_1d", True), ("dxy_1d", True)],
}


def align_ctx(target_idx: pd.DatetimeIndex, ser: pd.Series, strict: bool, name: str):
    left = pd.DataFrame({"D": target_idx})
    s = ser.dropna()
    right = pd.DataFrame({"src": s.index, name: s.values})
    m = pd.merge_asof(left, right, left_on="D", right_on="src", allow_exact_matches=not strict,
                      direction="backward")
    # 7일 넘게 묵은 값은 NaN (데이터 단절 방어)
    stale = (m.D - m.src) > pd.Timedelta(days=7)
    m.loc[stale, name] = np.nan
    return m[name].values, m.src.values


def build(data: dict, inst: str):
    df = data[inst]
    if inst == "069500.KS":
        # yfinance 069500은 2007-02~2009-04-16 800일 공백(2007년 8행뿐) → 공백 이전 행 제거(피처 오염 방지)
        df = df[df.index >= "2009-04-17"]
    f = own_features(df)
    mk = "US" if inst == "SPY" else "KR"
    cs = ctx_series(data)
    src_dates = {}
    for name, strict in ALIGN[mk]:
        vals, src = align_ctx(f.index, cs[name], strict, name)
        f[name] = vals
        src_dates[name] = (pd.to_datetime(src), strict)
    c, o = df.Close, df.Open
    y = pd.DataFrame(index=df.index)
    y["T1"] = (c.shift(-1) > c).astype(float).where(c.shift(-1).notna())
    y["T2"] = (o.shift(-1) > c).astype(float).where(o.shift(-1).notna())
    y["T3"] = (c.shift(-5) > c).astype(float).where(c.shift(-5).notna())
    rets = pd.DataFrame(index=df.index)
    rets["c2c_next"] = c.shift(-1) / c - 1  # D 종가~D+1 종가 (D 행에 기록)
    rets["ovn_next"] = o.shift(-1) / c - 1
    # 누수 assert
    for name, (src, strict) in src_dates.items():
        ok = pd.isna(src) | ((src < f.index) if strict else (src <= f.index))
        assert ok.all(), f"alignment leak {inst} {name}"
    f = f.replace([np.inf, -np.inf], np.nan)
    return f, y, rets, src_dates


def make_models():
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {
        "logit": lambda: make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000)),
        "hgb": lambda: HistGradientBoostingClassifier(random_state=0),
        "rf": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=20, random_state=0, n_jobs=-1),
        "mlp": lambda: make_pipeline(StandardScaler(), MLPClassifier(hidden_layer_sizes=(32, 16),
                                                                     early_stopping=True, random_state=0,
                                                                     max_iter=500)),
    }


def base1_fit(X: pd.DataFrame, y: np.ndarray):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    best, bs = None, -1
    for col in X.columns:
        a = roc_auc_score(y, X[col].values)
        if abs(a - 0.5) > bs:
            bs, best = abs(a - 0.5), col
    m = make_pipeline(StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    m.fit(X[[best]].values, y)
    return m, best


def walk_forward(X: pd.DataFrame, y: pd.Series, h: int, first_year: int, models: list[str],
                 max_folds: int | None = None, placebo: bool = False, keep_models: bool = False):
    MK = make_models()
    years = sorted(set(X.index.year))
    years = [yy for yy in years if yy >= first_year]
    if max_folds:
        years = years[:max_folds]
    preds = {m: pd.Series(np.nan, index=X.index) for m in models + ["base1"]}
    chosen, fitted = [], []
    rng = np.random.default_rng(0)
    for yy in years:
        ts = pd.Timestamp(f"{yy}-01-01")
        te = pd.Timestamp(f"{yy + 1}-01-01")
        tr_idx = X.index[X.index < ts]
        tr_idx = tr_idx[:-h] if h > 0 else tr_idx  # purge: 라벨 창이 테스트 시작 이상에 닿는 행 제거
        te_idx = X.index[(X.index >= ts) & (X.index < te)]
        if len(te_idx) == 0 or len(tr_idx) < 250:
            continue
        Xtr, ytr = X.loc[tr_idx], y.loc[tr_idx].values
        if placebo:
            ytr = rng.permutation(ytr)
        Xte = X.loc[te_idx]
        for m in models:
            mdl = MK[m]()
            mdl.fit(Xtr.values, ytr)
            preds[m].loc[te_idx] = mdl.predict_proba(Xte.values)[:, 1]
            if keep_models:
                fitted.append((yy, m, mdl, te_idx))
        bm, col = base1_fit(Xtr, ytr)
        preds["base1"].loc[te_idx] = bm.predict_proba(Xte[[col]].values)[:, 1]
        chosen.append((yy, col))
    return preds, chosen, fitted


def nw_t(x: np.ndarray, lags: int) -> float:
    import statsmodels.api as sm
    x = x[~np.isnan(x)]
    res = sm.OLS(x, np.ones(len(x))).fit(cov_type="HAC", cov_kwds={"maxlags": lags})
    return float(res.tvalues[0])


def perf(r: pd.Series) -> dict:
    eq = (1 + r).cumprod()
    yrs = len(r) / 252
    cagr = eq.iloc[-1] ** (1 / yrs) - 1 if yrs > 0 else np.nan
    sd = r.std()
    sh = r.mean() / sd * np.sqrt(252) if sd > 0 else np.nan
    mdd = (eq / eq.cummax() - 1).min()
    return {"cagr": cagr, "sharpe": sh, "mdd": mdd}


def evaluate(p: pd.Series, y: pd.Series, rets: pd.DataFrame, tgt: str, cost_rt: float):
    from sklearn.metrics import roc_auc_score
    ok = p.notna() & y.notna()
    p, y = p[ok], y[ok]
    rr = rets.loc[p.index]
    auc = roc_auc_score(y.values, p.values)
    pos = (p > 0.5).astype(float)
    hit = float(((p > 0.5).astype(float) == y).mean())
    base = float(y.mean())
    bh = rr.c2c_next
    if tgt == "T2":
        strat = pos * (rr.ovn_next - cost_rt)
        turnover = float(pos.mean())  # 보유한 밤 비율(밤마다 왕복)
        always_ovn = rr.ovn_next - cost_rt
        extra = perf(always_ovn)
        extra_name = "always_ovn"
    else:
        sw = pos.diff().abs().fillna(pos.iloc[0])
        strat = pos * bh - sw * cost_rt / 2
        turnover = float(sw.mean())  # 일 평균 포지션 변경 비율
        extra = perf(bh * pos.mean())
        extra_name = "expo_bh"
    diff = (strat - bh).values
    mid = p.index[0] + (p.index[-1] - p.index[0]) / 2
    h1 = float((strat - bh)[p.index <= mid].mean())
    h2 = float((strat - bh)[p.index > mid].mean())
    ps, pb = perf(strat), perf(bh)
    return {
        "n": int(len(p)), "start": str(p.index[0].date()), "end": str(p.index[-1].date()),
        "auc": auc, "hit": hit, "base_rate": base, "expo": float(pos.mean()),
        "s_cagr": ps["cagr"], "s_sharpe": ps["sharpe"], "s_mdd": ps["mdd"],
        "bh_cagr": pb["cagr"], "bh_sharpe": pb["sharpe"], "bh_mdd": pb["mdd"],
        "turnover": turnover, "t_excess": nw_t(diff, HORIZON[tgt]),
        "ex_h1_bp": h1 * 1e4, "ex_h2_bp": h2 * 1e4,
        f"{extra_name}_sharpe": extra["sharpe"], f"{extra_name}_cagr": extra["cagr"],
    }, strat


def verdict(row: dict, b: dict) -> str:
    passed = (row["s_sharpe"] > row["bh_sharpe"] and row["auc"] > 0.52 and row["ex_h1_bp"] > 0
              and row["ex_h2_bp"] > 0 and row["s_sharpe"] > b["s_sharpe"] and row["auc"] > b["auc"])
    if not passed:
        return "기각"
    if row["t_excess"] >= 3.2:
        return "확정"
    if row["t_excess"] >= 2.0:
        return "후보"
    return "기각"


def spot_check(data, inst, X, src_dates):
    """한쪽 시장만 휴장한 날의 병합 원본 날짜 출력."""
    own = X.index
    other = data["SPY"].index if inst != "SPY" else data["069500.KS"].index
    only_own = own.difference(other)
    only_other = other.difference(own)
    lines = []
    for d in list(only_own[-3:]) + list(only_other[-2:]):
        # 해당 날짜 또는 그 다음 대상 거래일
        dd = own[own >= d][0] if (own >= d).any() else None
        if dd is None:
            continue
        i = own.get_loc(dd)
        s = {k: str(pd.Timestamp(v[0][i]).date()) if not pd.isna(v[0][i]) else "NaN"
             for k, v in src_dates.items() if k in ("spy_1d", "qqq_1d", "vix", "n225_1d", "krw_1d")}
        lines.append(f"  {inst} D={dd.date()} (probe {d.date()}): {s}")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    data = fetch(a.refresh)
    insts = ["069500.KS"] if a.dry else ["069500.KS", "229200.KS", "SPY"]
    tgts = ["T1"] if a.dry else ["T1", "T2", "T3"]
    models = ["logit"] if a.dry else ["logit", "hgb", "rf", "mlp"]
    max_folds = 2 if a.dry else None
    rows, checks, chosen_all = [], [], {}
    strat_store = {}
    for inst in insts:
        f, Y, R, src = build(data, inst)
        full = f.dropna()
        checks += spot_check(data, inst, f, src)
        fy = START_YEAR[inst] + 5
        checks.append(f"  {inst}: 피처 {f.shape[1]}개, 완전행 {full.index[0].date()}~{full.index[-1].date()} "
                      f"({len(full)}행), 첫 테스트 {fy}")
        for tgt in tgts:
            h = HORIZON[tgt]
            y = Y[tgt].reindex(full.index)
            keep = y.notna()
            X, yy = full[keep], y[keep]
            preds, chosen, fitted = walk_forward(X, yy, h, fy, models, max_folds, keep_models=True)
            chosen_all[f"{inst}|{tgt}"] = chosen
            evals = {}
            for m in models + ["base1"]:
                r, s = evaluate(preds[m], yy, R, tgt, COST_RT[inst])
                evals[m] = r
                strat_store[f"{inst}|{tgt}|{m}"] = s
            for m in models:
                row = {"inst": inst, "tgt": tgt, "model": m, **evals[m],
                       "base1_auc": evals["base1"]["auc"], "base1_sharpe": evals["base1"]["s_sharpe"]}
                row["verdict"] = verdict(evals[m], evals["base1"])
                if row["verdict"] in ("후보", "확정"):
                    row["perm_imp"] = perm_importance(fitted, m, X, yy)
                rows.append(row)
                print(f"{inst} {tgt} {m}: AUC {row['auc']:.3f} hit {row['hit']:.3f}/{row['base_rate']:.3f} "
                      f"Sh {row['s_sharpe']:.2f} vs BH {row['bh_sharpe']:.2f} t {row['t_excess']:.2f} "
                      f"h1 {row['ex_h1_bp']:.2f} h2 {row['ex_h2_bp']:.2f} expo {row['expo']:.2f} -> {row['verdict']}",
                      flush=True)
            b = evals["base1"]
            print(f"{inst} {tgt} base1: AUC {b['auc']:.3f} Sh {b['s_sharpe']:.2f} t {b['t_excess']:.2f}", flush=True)
            rows.append({"inst": inst, "tgt": tgt, "model": "base1", **b, "verdict": "(기준)"})
    # 누수 대조: 069500 T1 logit/hgb
    ctrl = leak_controls(data, a.dry)
    OUT.mkdir(parents=True, exist_ok=True)
    tag = "_dry" if a.dry else ""
    pd.DataFrame(rows).to_csv(OUT / f"results{tag}.csv", index=False, encoding="utf-8-sig")
    json.dump({"alignment_checks": checks, "base1_chosen": chosen_all, "leak_controls": ctrl},
              open(OUT / f"diagnostics{tag}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1,
              default=str)
    if not a.dry:
        pd.DataFrame(strat_store).to_csv(OUT / "strategy_daily_returns.csv")
    print("\n".join(checks))
    print(json.dumps(ctrl, ensure_ascii=False, indent=1))


def perm_importance(fitted, m, X, y):
    from sklearn.inspection import permutation_importance
    acc = {}
    for yy, mm, mdl, te_idx in fitted:
        if mm != m:
            continue
        r = permutation_importance(mdl, X.loc[te_idx].values, y.loc[te_idx].values, scoring="roc_auc",
                                   n_repeats=10, random_state=0)
        for c, v in zip(X.columns, r.importances_mean):
            acc.setdefault(c, []).append(v)
    s = {c: float(np.mean(v)) for c, v in acc.items()}
    return dict(sorted(s.items(), key=lambda kv: -kv[1])[:10])


def leak_controls(data, dry):
    from sklearn.metrics import roc_auc_score
    out = {}
    cells = [("069500.KS", "T1"), ("069500.KS", "T2")] if not dry else [("069500.KS", "T1")]
    if not dry:
        cells.append(("SPY", "T1"))
    for inst, tgt in cells:
        f, Y, R, _ = build(data, inst)
        full = f.dropna()
        y = Y[tgt].reindex(full.index)
        X, yy = full[y.notna()], y[y.notna()]
        fy = START_YEAR[inst] + 5
        mf = 2 if dry else None
        pp, _, _ = walk_forward(X, yy, HORIZON[tgt], fy, ["logit", "hgb"], mf, placebo=True)
        Xp = X.copy()
        # 양성대조: 실현 방향(+잡음) 주입
        Xp["LEAK"] = yy.values + np.random.default_rng(1).normal(0, 0.5, len(yy))
        pl, _, _ = walk_forward(Xp, yy, HORIZON[tgt], fy, ["logit"], mf)
        k = f"{inst}|{tgt}"
        for m in ("logit", "hgb"):
            ok = pp[m].notna()
            out[f"{k}|placebo_{m}_auc"] = float(roc_auc_score(yy[ok], pp[m][ok]))
        ok = pl["logit"].notna()
        out[f"{k}|posctrl_logit_auc"] = float(roc_auc_score(yy[ok], pl["logit"][ok]))
    return out


if __name__ == "__main__":
    sys.exit(main())
