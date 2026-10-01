"""보유 중 포지션 SELL/HOLD 모델 family — 현행 고정 출구 계약 대비 검증 (2026-09-27).

사전등록 (실행 전 고정 — 결과를 보고 바꾸지 않는다)
===================================================
표본: data/shadow/virtual_books.db trades, status='CLOSED'
  - KR 'xkr_fallen3' : session_date=신호일, 다음 세션 시가 진입, 비용 0.25 왕복, BE락 없음
  - US 'xus_fallen3' : session_date=진입 세션, 비용 0.50 왕복, BE락 4% (전일까지 봉우리 기준)
  계약: tools/virtual_books.py 의 TP=12 / SL=-25 / HOLD_SESSIONS=7 (D0..D6), contract_exit_v2 를 오라클로
  재계산하고 원장 net_pct 와 일치율을 보고한다(불일치 시 재계산값으로 일관 사용).

결정 시점: "보유 k일째" k=1..hold-2(=1..5), 보유 1일째 = 진입일 D0.
  → D0..D4 종가에서 결정(그 종가까지의 봉만 사용), SELL 이면 다음 봉(D1..D5) 시가에 매도.
  그 시점에 계약이 이미 청산됐으면(청산 인덱스 <= j) 결정 시점 아님.
매도 체결가:
  - 주(primary) (b) TP-cap: 다음 시가 >= 진입가×1.12 이면 체결가를 +12%로 캡 (계약도 그 봉에서 TP 12로 청산하므로
    갭 업 초과분은 체결 규약 차이 인공물 — 모델에 공짜 이익을 주지 않는다)
  - 보조 (a) raw: 다음 시가 그대로
  비용: 계약과 동일한 왕복 비용이 양쪽에 붙으므로 개선폭 = gross 차이.
라벨: y = 1 if 계약 최종 net < 지금 매도 net (나머지 보유가 지금 파는 것보다 나쁨).
  회귀 변형: remaining = 계약 최종 net − 지금 매도 net 예측, 예측 < 0 이면 SELL (보조, 메인 검정 아님).
특성(고정): 미실현 %, MFE/MAE(현재까지, 계약 규약: D0는 종가만), 보유일 k, 당일 수익률, 당일 갭,
  진입일 저가/고가 대비 거리, 종목 5/20일 모멘텀, 20일 실현변동성, 거래량비(당일/직전20일 평균),
  지수 당일·5일 수익률, 지수 20일 변동성(KR 069500·229200, US SPY·IWM), 요일(one-hot).
모델(기본 파라미터, random_state=0): logistic, HistGradientBoosting, RandomForest(300, min_samples_leaf=50),
  MLP(32,16, early_stopping). 결측은 median 대치, logistic/MLP는 표준화.
정책: 거래별로 D0..D4 순서대로, 처음 P(y=1) > 0.5(고정) 인 시점에 SELL. 아니면 계약대로.
기준선: b1 조기매도 없음(=현행 계약, 개선 0),
  b2 미실현 <= -X% 이면 매도, X ∈ {never,3,5,7,10,15,20} 을 학습 구간 평균 개선 최대로 선택,
  b3 봉우리(MFE) 대비 종가가 W%p 이상 밀리면 매도, W ∈ {never,3,5,7,10,15} 학습 구간에서 선택.
워크포워드: 월별 확장 재학습. 테스트 월 M = 진입일(D0)이 M인 거래. 학습 = D6 날짜 < M 첫날인 거래(라벨 해소 기준 purge,
  hold 세션 이상 자동 보장). 학습 구간 최소 3개월(첫 D0 월 + 3개월부터 테스트).
평가: 거래별 (정책 net − 계약 net), 진입일(D0) 클러스터 평균 → 일평균의 평균과 t, 시간순 전·후반 부호,
  조기매도 비율, OOS AUC. 다중검정 2시장×4모델=8 메인 검정:
  확정 = 일클러스터 t >= 2.7 AND 최선 단순 기준선(b2/b3 중 테스트 평균 높은 쪽) 대비 쌍대 일클러스터 차이 > 0,
  후보 = 2 <= t < 2.7, 그 외 기각.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import warnings
from collections import defaultdict
from contextlib import closing
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import virtual_books as vb  # noqa: E402

OUT = ROOT / "data" / "analysis" / "exit_models_20260927"
TP, SL, BE = vb.TP, vb.SL, vb.BE
MKT = {
    "KR": dict(sid="xkr_fallen3", fee=vb.FEE_KR, be=False, idx=("069500", "229200")),
    "US": dict(sid="xus_fallen3", fee=vb.FEE_US, be=True, idx=("SPY", "IWM")),
}


def hold_of(sid: str) -> int:
    s = next(x for x in vb.STRATEGIES if x["id"] == sid)
    return int(s.get("hold", vb.HOLD_SESSIONS))


def load_bars(market: str, ticker: str) -> list[tuple]:
    return vb.bars_kr(ticker) if market == "KR" else vb.bars(ticker)


def contract_path(entry: float, win: list[tuple], fee: float, be_lock: bool, hold: int):
    """contract_exit_v2 복제 + 청산 인덱스."""
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= TP:
            return TP - fee, "TP", i
        if cp <= SL:
            return cp - fee, "SL", i
        if be_lock and peak >= BE and cp <= 0:
            return cp - fee, "BE", i
        peak = max(peak, hip)
    return (win[hold - 1][4] - entry) / entry * 100.0 - fee, "D_MAT", hold - 1


def idx_frame(market: str) -> pd.DataFrame:
    frames = []
    for t in MKT[market]["idx"]:
        b = load_bars(market, t)
        df = pd.DataFrame(b, columns=["date", "o", "h", "l", "c", "v"]).set_index("date")
        r = df["c"].pct_change() * 100
        frames.append(pd.DataFrame({
            f"ix_{t}_r1": r,
            f"ix_{t}_r5": (df["c"] / df["c"].shift(5) - 1) * 100,
            f"ix_{t}_vol20": r.rolling(20, min_periods=10).std(),
        }))
    return pd.concat(frames, axis=1)


def build(market: str, limit: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    cfg = MKT[market]
    hold = hold_of(cfg["sid"])
    ndec = hold - 2
    with closing(sqlite3.connect(f"file:{vb.BOOK_DB.as_posix()}?mode=ro", uri=True, timeout=10)) as c:
        rows = c.execute("select session_date, ticker, net_pct from trades where strategy_id=? and status='CLOSED' "
                         "order by session_date, ticker", (cfg["sid"],)).fetchall()
    if limit:
        rows = rows[:limit]
    ix = idx_frame(market)
    stats = dict(n_ledger=len(rows), no_bars=0, short=0, mismatch=0, hold=hold, ndec=ndec)
    trades, dec = [], []
    for sd, tk, lnet in rows:
        b = load_bars(market, tk)
        if not b:
            stats["no_bars"] += 1
            continue
        if market == "KR":
            ei = next((i for i, x in enumerate(b) if x[0] > str(sd)), None)
        else:
            ei = next((i for i, x in enumerate(b) if x[0] == str(sd)), None)
        if ei is None or len(b) < ei + hold or b[ei][1] <= 0:
            stats["short"] += 1
            continue
        win = b[ei: ei + hold]
        entry = float(win[0][1])
        net, reason, xi = contract_path(entry, win, cfg["fee"], cfg["be"], hold)
        oracle = vb.contract_exit_v2(entry, win, fee=cfg["fee"], be_lock=cfg["be"], hold=hold)
        assert oracle is not None and abs(oracle[0] - net) < 1e-9, (tk, sd)
        if lnet is None or abs(float(lnet) - net) > 0.01:
            stats["mismatch"] += 1
        tid = len(trades)
        trades.append(dict(tid=tid, ticker=tk, d0=win[0][0], d_end=win[-1][0], month=win[0][0][:7],
                           contract=net, reason=reason, xi=xi, ledger=lnet))
        closes = np.array([x[4] for x in b], float)
        vols = np.array([x[5] for x in b], float)
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
            sell_raw = (nxt_open - entry) / entry * 100 - cfg["fee"]
            sell_cap = min((nxt_open - entry) / entry * 100, TP) - cfg["fee"]
            pc = closes[gi - 1] if gi >= 1 else np.nan
            r20 = np.diff(np.log(closes[max(0, gi - 20): gi + 1])) * 100
            vprev = vols[max(0, gi - 20): gi]
            f = dict(
                tid=tid, j=j, date=d, unreal=(cl - entry) / entry * 100, mfe=mfe, mae=mae, k=j + 1,
                ret1=(cl / pc - 1) * 100 if pc > 0 else np.nan,
                gap=(o / pc - 1) * 100 if pc > 0 else np.nan,
                from_d0lo=(cl / d0_lo - 1) * 100, from_d0hi=(cl / d0_hi - 1) * 100,
                mom5=(cl / closes[gi - 5] - 1) * 100 if gi >= 5 and closes[gi - 5] > 0 else np.nan,
                mom20=(cl / closes[gi - 20] - 1) * 100 if gi >= 20 and closes[gi - 20] > 0 else np.nan,
                rv20=float(np.std(r20)) if len(r20) >= 10 else np.nan,
                volr=v / vprev.mean() if len(vprev) >= 10 and vprev.mean() > 0 else np.nan,
                dow=date.fromisoformat(d).weekday(),
                sell_raw=sell_raw, sell_cap=sell_cap,
            )
            if d in ix.index:
                f.update(ix.loc[d].to_dict())
            dec.append(f)
    T = pd.DataFrame(trades)
    D = pd.DataFrame(dec)
    D = D.merge(T[["tid", "contract", "d_end", "month", "d0"]], on="tid")
    for dow in range(5):
        D[f"dow{dow}"] = (D["dow"] == dow).astype(float)
    stats["n_trades"] = len(T)
    stats["n_dec"] = len(D)
    return T, D, stats


FEATS_BASE = ["unreal", "mfe", "mae", "k", "ret1", "gap", "from_d0lo", "from_d0hi", "mom5", "mom20", "rv20", "volr",
              "dow0", "dow1", "dow2", "dow3", "dow4"]


def feats(D: pd.DataFrame) -> list[str]:
    return FEATS_BASE + [c for c in D.columns if c.startswith("ix_")]


def make_models():
    from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    return {
        "logistic": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                                  LogisticRegression(max_iter=2000, random_state=0)),
        "hgb": HistGradientBoostingClassifier(random_state=0),
        "rf": make_pipeline(SimpleImputer(strategy="median"),
                            RandomForestClassifier(n_estimators=300, min_samples_leaf=50, n_jobs=4, random_state=0)),
        "mlp": make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             MLPClassifier(hidden_layer_sizes=(32, 16), early_stopping=True, random_state=0,
                                           max_iter=200)),
    }


def policy_first(D: pd.DataFrame, sell_mask: np.ndarray, sell_col: str) -> pd.Series:
    """거래별 첫 SELL 시점 체결 → 정책 net. 반환: tid -> policy net (SELL 없는 거래는 누락)."""
    s = D.loc[sell_mask, ["tid", "j", sell_col]].sort_values(["tid", "j"]).drop_duplicates("tid")
    return s.set_index("tid")[sell_col]


def rule_mask(D: pd.DataFrame, kind: str, p) -> np.ndarray:
    if p is None:
        return np.zeros(len(D), bool)
    if kind == "b2":
        return (D["unreal"] <= -p).to_numpy()
    return ((D["mfe"] - D["unreal"]) >= p).to_numpy()


def improvement(T: pd.DataFrame, pol: pd.Series) -> pd.Series:
    net = T.set_index("tid")["contract"].copy()
    base = net.copy()
    net.loc[pol.index] = pol.values
    return net - base


def day_stats(T: pd.DataFrame, imp: pd.Series) -> dict:
    df = pd.DataFrame({"d0": T.set_index("tid").loc[imp.index, "d0"].values, "imp": imp.values})
    g = df.groupby("d0")["imp"].mean().sort_index()
    n = len(g)
    m = g.mean()
    sd = g.std(ddof=1)
    t = m / (sd / math.sqrt(n)) if n > 1 and sd > 0 else float("nan")
    h = n // 2
    return dict(n_trades=len(df), n_days=n, mean_trade=float(df["imp"].mean()), mean_day=float(m), t=float(t),
                h1=float(g.iloc[:h].mean()), h2=float(g.iloc[h:].mean()), daily=g)


def paired_t(a: pd.Series, b: pd.Series) -> tuple[float, float]:
    d = (a - b).dropna()
    if len(d) < 2 or d.std(ddof=1) == 0:
        return float(d.mean()) if len(d) else float("nan"), float("nan")
    return float(d.mean()), float(d.mean() / (d.std(ddof=1) / math.sqrt(len(d))))


def run_market(market: str, limit: int | None, max_months: int | None) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.metrics import roc_auc_score
    T, D, stats = build(market, limit)
    X_cols = feats(D)
    out = dict(stats=stats)
    for fill in ("sell_cap", "sell_raw"):
        D[f"y_{fill}"] = (D["contract"] < D[fill]).astype(int)
        D[f"rem_{fill}"] = D["contract"] - D[fill]
    months = sorted(T["month"].unique())
    first = pd.Period(months[0])
    test_months = [m for m in months if pd.Period(m) >= first + 3]
    if max_months:
        test_months = test_months[:max_months]
    res = {fill: defaultdict(dict) for fill in ("sell_cap", "sell_raw")}
    preds = {fill: {name: [] for name in ("logistic", "hgb", "rf", "mlp", "hgb_reg")} for fill in res}
    chosen = {fill: {"b2": [], "b3": []} for fill in res}
    Tm_all = T[T["month"].isin(test_months)]
    for m in test_months:
        mstart = f"{m}-01"
        tr = D[D["d_end"] < mstart]
        te = D[D["month"] == m]
        Ttr = T[T["d_end"] < mstart]
        if len(te) == 0 or len(tr) < 1000:
            continue
        for fill in res:
            y = tr[f"y_{fill}"].to_numpy()
            for name, mdl in make_models().items():
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    mdl.fit(tr[X_cols].to_numpy(float), y)
                    p = mdl.predict_proba(te[X_cols].to_numpy(float))[:, 1]
                preds[fill][name].append(pd.Series(p, index=te.index))
            reg = HistGradientBoostingRegressor(random_state=0).fit(tr[X_cols].to_numpy(float),
                                                                     tr[f"rem_{fill}"].to_numpy())
            preds[fill]["hgb_reg"].append(pd.Series(reg.predict(te[X_cols].to_numpy(float)), index=te.index))
            for kind, grid in (("b2", [None, 3, 5, 7, 10, 15, 20]), ("b3", [None, 3, 5, 7, 10, 15])):
                best, bp = -1e9, None
                for p in grid:
                    v = improvement(Ttr, policy_first(tr, rule_mask(tr, kind, p), fill)).mean()
                    if v > best + 1e-12:
                        best, bp = v, p
                chosen[fill][kind].append((m, bp))
                res[fill][kind].setdefault("pol", []).append(policy_first(te, rule_mask(te, kind, bp), fill))
        print(f"[{market}] {m} train_dec={len(tr)} test_dec={len(te)} "
              f"b2={chosen['sell_cap']['b2'][-1][1]} b3={chosen['sell_cap']['b3'][-1][1]}", flush=True)
    Dt = D[D["month"].isin(test_months)]
    for fill in res:
        table = {}
        daily = {}
        for kind in ("b2", "b3"):
            pol = pd.concat(res[fill][kind]["pol"]) if res[fill][kind].get("pol") else pd.Series(dtype=float)
            imp = improvement(Tm_all, pol)
            st = day_stats(Tm_all, imp)
            daily[kind] = st.pop("daily")
            st["sold_share"] = len(pol) / len(Tm_all)
            st["params"] = chosen[fill][kind]
            table[kind] = st
        for name in ("logistic", "hgb", "rf", "mlp", "hgb_reg"):
            p = pd.concat(preds[fill][name]).reindex(Dt.index)
            mask = (p < 0).to_numpy() if name == "hgb_reg" else (p > 0.5).to_numpy()
            pol = policy_first(Dt, mask, fill)
            imp = improvement(Tm_all, pol)
            st = day_stats(Tm_all, imp)
            daily[name] = st.pop("daily")
            st["sold_share"] = len(pol) / len(Tm_all)
            if name != "hgb_reg":
                st["auc"] = float(roc_auc_score(Dt[f"y_{fill}"], p))
                for thr in (0.6, 0.7):
                    pol2 = policy_first(Dt, (p > thr).to_numpy(), fill)
                    s2 = day_stats(Tm_all, improvement(Tm_all, pol2))
                    st[f"thr{thr}"] = dict(mean_trade=s2["mean_trade"], t=s2["t"], sold=len(pol2) / len(Tm_all))
            table[name] = st
        best_b = max(("b2", "b3"), key=lambda k: table[k]["mean_day"])
        for name in ("logistic", "hgb", "rf", "mlp", "hgb_reg"):
            dm, dt = paired_t(daily[name], daily[best_b])
            table[name]["vs_best_baseline"] = dict(baseline=best_b, diff_day=dm, t=dt)
        table["_base"] = dict(label_rate=float(Dt[f"y_{fill}"].mean()), n_test_trades=len(Tm_all),
                              n_test_dec=len(Dt), contract_mean=float(Tm_all["contract"].mean()),
                              test_months=[test_months[0], test_months[-1]])
        out[fill] = table
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--markets", default="KR,US")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-months", type=int, default=None)
    ap.add_argument("--tag", default="full")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    allres = {}
    for mk in a.markets.split(","):
        allres[mk] = run_market(mk, a.limit, a.max_months)
        print(json.dumps({"market": mk, "stats": allres[mk]["stats"]}, ensure_ascii=False))
        (OUT / f"results_{a.tag}_{mk}.json").write_text(
            json.dumps(allres[mk], ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (OUT / f"results_{a.tag}.json").write_text(json.dumps(allres, ensure_ascii=False, indent=1, default=str),
                                                encoding="utf-8")
    for mk, r in allres.items():
        for fill in ("sell_cap", "sell_raw"):
            tb = r[fill]
            print(f"\n== {mk} {fill} base={tb['_base']}")
            for name, st in tb.items():
                if name.startswith("_"):
                    continue
                print(f"{name:9s} mean/trade={st['mean_trade']:+.3f} day={st['mean_day']:+.3f} t={st['t']:+.2f} "
                      f"h1={st['h1']:+.3f} h2={st['h2']:+.3f} sold={st['sold_share']:.1%} "
                      f"auc={st.get('auc', float('nan')):.3f} vsB={st.get('vs_best_baseline')}"
                      f" thr={ {k: v for k, v in st.items() if k.startswith('thr')} }")


if __name__ == "__main__":
    main()
