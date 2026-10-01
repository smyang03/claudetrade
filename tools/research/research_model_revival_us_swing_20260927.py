#!/usr/bin/env python3
"""US swing 선별 모델 부활 시험 — 모델에게 고르게 하면 예측력이 있나 (read-only, 2026-09-27).

운영자 요청: "예전에 만든 모델을 되살려, 모델이 결정하게 하면 예측력이 있는지 본다" — 모델 #2 = US swing 모델
(09-01 '역선별'로 제거, docs/reports/system_diagnosis_and_selection_20260901.md §4).

## 사전 등록 (결과를 보기 전에 고정)

모집단: data/shadow/virtual_books.db trades, strategy_id='xus_fallen3'(전일 ≤−3%·거래대금 ≥50M 전량).
  결과 = net_pct(라이브 계약 셀 tp12_sl25_d7_be, 비용 0.5 반영 — meta.grid로 일치 검증).
  특성 원천: 원 모델의 피처 27종(YAHOO_FEATURES)을 tools/build_us_yahoo_point_in_time.py의 같은 함수
  (build_ticker_frame·_benchmark_features)로 data/price/us CSV에서 **meta.signal_date(신호봉)** 기준 재계산.
  session_date(진입일)의 봉은 미래 정보라 쓰지 않는다. 정렬 검증: change_pct≈feat.chg, gap_pct≈feat.gap.

모델(주): 원 모델 패밀리 그대로 — HGB 회귀 + HGB 분류(_make_models("HGB") 파라미터 고정),
  분류 목표 = net_pct ≥ 0.25, 점수 = 0.5·일내 회귀순위 + 0.5·일내 확률순위(_alpha "combo"),
  seeds = config/us_swing_accelerated.json seeds(3종) 평균. 튜닝 없음.
보조(다중검정 주의, 판정에 쓰지 않음):
  S1 확장 피처 = 27종 + 풀 특성(dvol, dvol20, max21, ibs, vol_spike, cum5, down_streak, from_high20) — 원 모델엔 dvol이 없었다.
  S2 원 모델 as-built 이식 = 교재(us_yahoo_point_in_time, 128종목, ~2026-04-02, D5 계약 라벨)로 학습 → 풀 채점.
  S3 라이브 문턱 부분집합(chg ≤ −5) 안에서 주 모델 점수로 순위(대조 = 그날 ≤−5 부분집합 평균).

워크포워드: 월별 확장창 재학습. 테스트 월 첫 세션 인덱스 f에 대해 학습 = 세션 인덱스 ≤ f−8 (D7 보유 + 1 퍼지,
  세션 달력 기준). 최소 학습 이력 = 3개 달력월(첫 테스트 월 2025-09). 교재(S2)는 session_date < 테스트월 첫날 − 16일.
테스트일 포함: 그날 풀에 OPEN 건이 하나라도 있으면 제외(일내 생존편향 방지).
지표(모두 '같은 날 풀 평균' 대비 = 선별 전용 완전 대조, 시장·국면 제거):
  top-1(n≥2), top-3(n≥3), top-decile/bottom-decile(n≥10, k=ceil(n/10)) − 풀 평균; 날짜 클러스터 평균·t.
  일별 Spearman(점수, net_pct)(n≥5) 평균과 t; 일별 Spearman(점수, mfe) — 09-01 "변동폭 학습" 재현 확인.
기준선: dvol 내림차순(meta.ranks.dvol_desc), 라이브 규칙(chg≤−5·dvol 100~500·max21≥8·dvol 1등, 대조는 전체 풀 평균).
통과 = 주 모델 top-3 − 풀, 날짜 클러스터 t ≥ 2 **그리고** 테스트일 중앙 날짜로 나눈 전/후반 모두 양수.
  아니면 기각(t<1 또는 반기 부호 불일치) / 후보(1≤t<2이고 양 반기 양수).

## 추가 사전 등록 — 변형 모델 계열 (운영자 추가 요청, 변형 결과를 보기 전에 고정. 주 모델 결과는 이미 본 뒤)

아키텍처 3종 × 데이터/라벨 2종 = 6변형. 피처 = 주 모델과 같은 27종, 결측은 학습창 중앙값 대치(sklearn 1.0.2의
GB/RF/Logit은 NaN 불가), 로지스틱만 표준화 추가. 파라미터 고정·튜닝 없음.
  LOGIT  = LogisticRegression(C=1.0, max_iter=2000)
  GB     = GradientBoosting{Classifier|Regressor}(기본값, random_state=0)
  RF     = RandomForest{Classifier|Regressor}(n_estimators=300, min_samples_leaf=5, random_state=0, n_jobs=-1)
  A(원 라벨)  = 분류 목표 net_pct ≥ 0.25(원 모델 target), 점수 = P(1). (LOGIT/GB/RF 모두 분류기)
  B(초과 라벨) = y = net_pct − 같은 날 풀 평균. GB/RF는 회귀(점수 = 예측 초과), LOGIT은 y>0 분류(점수 = P).
  학습 = xus_fallen3 전 행(주 모델과 같은 워크포워드·퍼지·OPEN 제외 규칙).
평가 = 주 모델과 동일(top-3 − 같은 날 풀 평균, 날짜 클러스터 t). 6개 동시 검정이므로
  **통과 = top-3 − 풀 날짜 t ≥ 2.64 그리고 전/후반 모두 양수.** 비교용 단일 피처 기준선 = dvol 내림차순
  (라이브 규칙의 정렬 키 US_SWING_PICK_ORDER=dvol_desc), 같은 표에 병기.

사용: python tools/research/research_model_revival_us_swing_20260927.py [--dry-month YYYY-MM] [--no-s2] [--variants]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_us_yahoo_point_in_time import _benchmark_features, _read_price, build_ticker_frame  # noqa: E402
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES  # noqa: E402
from tools.us_swing_model_matrix_experiment import _make_models  # noqa: E402

DB = ROOT / "data" / "shadow" / "virtual_books.db"
PRICE = ROOT / "data" / "price" / "us"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
CELL = "tp12_sl25_d7_be"
PURGE = 8
MIN_TRAIN_MONTHS = 3
POOL_EXTRA = ["dvol", "dvol20", "max21", "ibs", "vol_spike", "cum5", "down_streak", "from_high20"]


def load_pool() -> tuple[pd.DataFrame, set]:
    rows, open_days = [], set()
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        for sd, t, st_, net, meta in con.execute(
                "SELECT session_date, ticker, status, net_pct, meta FROM trades WHERE strategy_id='xus_fallen3'"):
            if st_ != "CLOSED":
                open_days.add(sd)
                continue
            m = json.loads(meta)
            f = m.get("feat") or {}
            g = (m.get("grid") or {}).get(CELL)
            rows.append({"session_date": sd, "ticker": t, "signal_date": m.get("signal_date"), "net": net,
                         "grid_net": g[0] if g else None, "mfe": m.get("mfe"),
                         "dvol_rank": (m.get("ranks") or {}).get("dvol_desc"),
                         **{k: f.get(k) for k in POOL_EXTRA + ["chg", "gap"]}})
    df = pd.DataFrame(rows)
    for c in POOL_EXTRA + ["chg", "gap", "net", "grid_net", "mfe", "dvol_rank"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, open_days


def attach_features(df: pd.DataFrame) -> pd.DataFrame:
    bench = _benchmark_features(PRICE)
    parts = []
    for t, g in df.groupby("ticker"):
        p = PRICE / f"us_{t}.csv"
        if not p.exists():
            parts.append(g)
            continue
        fr = build_ticker_frame(_read_price(p))
        cols = ["date"] + [c for c in YAHOO_FEATURES if c in fr.columns and not c.startswith(("spy_", "qqq_", "iwm_", "relative_"))]
        parts.append(g.merge(fr[cols], left_on="signal_date", right_on="date", how="left"))
    out = pd.concat(parts, ignore_index=True)
    out = out.merge(bench, left_on="signal_date", right_on="date", how="left", suffixes=("", "_b"))
    for w in (5, 20, 60):
        out[f"relative_strength_qqq_{w}d_pct"] = out[f"momentum_{w}d_pct"] - out[f"qqq_momentum_{w}d_pct"]
    return out.replace([np.inf, -np.inf], np.nan)


def fit_score(train: pd.DataFrame, test: pd.DataFrame, feats: list[str], seeds: list[int], w: int,
              label: str = "net") -> np.ndarray:
    y = train[label].to_numpy(float)
    tgt = (y >= 0.25).astype(int)
    preds, probs = [], []
    for s in seeds:
        reg, clf = _make_models("HGB", s + w)
        reg.fit(train[feats], y)
        clf.fit(train[feats], tgt)
        preds.append(reg.predict(test[feats]))
        probs.append(clf.predict_proba(test[feats])[:, 1])
    t = test[["session_date"]].copy()
    t["p"] = np.mean(preds, axis=0)
    t["q"] = np.mean(probs, axis=0)
    return (0.5 * t.groupby("session_date")["p"].rank(pct=True)
            + 0.5 * t.groupby("session_date")["q"].rank(pct=True)).to_numpy()


def tstat(v):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], float)
    if len(v) < 3 or v.std(ddof=1) == 0:
        return (float(v.mean()) if len(v) else float("nan")), float("nan"), len(v)
    return float(v.mean()), float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))), len(v)


def evaluate(scored: pd.DataFrame, col: str, ascending: bool = False) -> dict:
    """scored: 테스트 행, col=점수(높을수록 매수, NaN=선택 불가). 일별 선별 − 같은 날 전체 풀 평균."""
    per = defaultdict(list)
    for d, g0 in scored.groupby("session_date"):
        pm = g0["net"].mean()          # 대조 = 그날 (전달된) 풀 전체 평균 — 점수 결측 여부와 무관
        g = g0.dropna(subset=[col])
        n = len(g)
        if len(g0) < 2 or n < 1:
            continue
        o = g.sort_values(col, ascending=ascending)
        per["d"].append(d)
        per["top1"].append(o["net"].iloc[0] - pm)
        per["top3"].append(o["net"].iloc[:3].mean() - pm if n >= 3 else None)
        k = math.ceil(n / 10)
        per["topdec"].append(o["net"].iloc[:k].mean() - pm if n >= 10 else None)
        per["botdec"].append(o["net"].iloc[-k:].mean() - pm if n >= 10 else None)
        sgn = 1 if not ascending else -1
        per["rho"].append(sgn * spearmanr(g[col], g["net"])[0] if n >= 5 else None)
        per["rho_mfe"].append(sgn * spearmanr(g[col], g["mfe"])[0] if n >= 5 and g["mfe"].notna().sum() >= 5 else None)
        per["n"].append(n)
    res = {"days": len(per["d"]), "trades": int(sum(per["n"]))}
    days = per["d"]
    mid = sorted(days)[len(days) // 2] if days else ""
    for key in ("top1", "top3", "topdec", "botdec", "rho", "rho_mfe"):
        m, t, n = tstat(per[key])
        h1 = [v for d, v in zip(days, per[key]) if v is not None and d < mid]
        h2 = [v for d, v in zip(days, per[key]) if v is not None and d >= mid]
        res[key] = (m, t, n, float(np.mean(h1)) if h1 else float("nan"), float(np.mean(h2)) if h2 else float("nan"))
    res["mid"] = mid
    return res


def fmt(name: str, r: dict) -> list[str]:
    out = []
    for key, lab in (("top1", "top-1"), ("top3", "top-3"), ("topdec", "상위10%"), ("botdec", "하위10%"),
                     ("rho", "Spearman(net)"), ("rho_mfe", "Spearman(MFE)")):
        m, t, n, h1, h2 = r[key]
        out.append(f"| {name} | {lab} | {n} | {m:+.3f} | {t:+.2f} | {h1:+.3f} | {h2:+.3f} |")
    return out


def _variant_models(arch: str, kind: str):
    from sklearn.ensemble import (GradientBoostingClassifier, GradientBoostingRegressor,
                                  RandomForestClassifier, RandomForestRegressor)
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    imp = SimpleImputer(strategy="median")
    if arch == "LOGIT":
        return make_pipeline(imp, StandardScaler(), LogisticRegression(C=1.0, max_iter=2000))
    if arch == "GB":
        return make_pipeline(imp, GradientBoostingClassifier(random_state=0) if kind == "clf"
                             else GradientBoostingRegressor(random_state=0))
    common = dict(n_estimators=300, min_samples_leaf=5, random_state=0, n_jobs=-1)
    return make_pipeline(imp, RandomForestClassifier(**common) if kind == "clf" else RandomForestRegressor(**common))


def run_variants(df: pd.DataFrame, test_months: list[str], open_days: set) -> list[str]:
    df = df.copy()
    df["excess"] = df["net"] - df.groupby("session_date")["net"].transform("mean")
    names = [(a, l) for a in ("LOGIT", "GB", "RF") for l in ("A", "B")]
    scored = []
    for mth in test_months:
        test = df[(df["month"] == mth) & ~df["session_date"].isin(open_days)].copy()
        if test.empty:
            continue
        train = df[df["si"] <= int(test["si"].min()) - PURGE]
        X, Xt = train[YAHOO_FEATURES], test[YAHOO_FEATURES]
        for arch, lab in names:
            if lab == "A":
                y, kind = (train["net"] >= 0.25).astype(int), "clf"
            elif arch == "LOGIT":
                y, kind = (train["excess"] > 0).astype(int), "clf"
            else:
                y, kind = train["excess"], "reg"
            m = _variant_models(arch, kind).fit(X, y)
            test[f"v_{arch}_{lab}"] = m.predict_proba(Xt)[:, 1] if kind == "clf" else m.predict(Xt)
        print(f"  [변형] {mth}: 학습 {len(train)} 테스트 {len(test)}")
        scored.append(test)
    sc = pd.concat(scored, ignore_index=True)
    sc["neg_dvol_rank"] = -sc["dvol_rank"]
    L = ["| 변형 | 일수 | top-3−풀 (%p) | 날짜 t | 전반 | 후반 | top-1−풀 | 하위10%−풀 | Spearman(net) (t) | 판정(t≥2.64·양반기) |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for col, nm in [(f"v_{a}_{l}", f"{a}-{l}") for a, l in names] + [("neg_dvol_rank", "기준선 dvol 내림차순")]:
        r = evaluate(sc, col)
        m, t, n, h1, h2 = r["top3"]
        ok = "통과" if (t >= 2.64 and h1 > 0 and h2 > 0) else "기각"
        if col == "neg_dvol_rank":
            ok = "(기준선)"
        L.append(f"| {nm} | {n} | {m:+.3f} | {t:+.2f} | {h1:+.3f} | {h2:+.3f} | {r['top1'][0]:+.3f} ({r['top1'][1]:+.2f}) | "
                 f"{r['botdec'][0]:+.3f} ({r['botdec'][1]:+.2f}) | {r['rho'][0]:+.3f} ({r['rho'][1]:+.2f}) | {ok} |")
    return L


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-month", default=None)
    ap.add_argument("--no-s2", action="store_true")
    ap.add_argument("--variants", action="store_true", help="변형 6종만 실행")
    a = ap.parse_args()
    seeds = [int(s) for s in json.loads(POLICY.read_text(encoding="utf-8")).get("seeds", [20260710])]

    pool, open_days = load_pool()
    mism = (pool["net"] - pool["grid_net"]).abs().gt(0.01).sum()
    print(f"풀 CLOSED {len(pool)}행 · OPEN 포함 세션 {len(open_days)}개 · net≠grid[{CELL}] {mism}건")
    df = attach_features(pool)
    ok = df["change_pct"].notna()
    bad_chg = ((df.loc[ok, "change_pct"] - df.loc[ok, "chg"]).abs() > 0.05).sum()
    bad_gap = ((df.loc[ok, "gap_pct"] - df.loc[ok, "gap"]).abs() > 0.05).sum()
    print(f"피처 결합 {ok.sum()}/{len(df)} · 정렬 불일치 chg {bad_chg} gap {bad_gap}")
    cov = {c: round(float(df[c].notna().mean()), 3) for c in YAHOO_FEATURES}
    print("피처 커버리지(낮은 것):", {k: v for k, v in cov.items() if v < 0.95})
    if ok.sum() and (bad_chg + bad_gap) > 0.01 * ok.sum():
        print("정렬 불일치 1% 초과 — 중단")
        return 1

    sessions = sorted(df["session_date"].unique())
    sidx = {d: i for i, d in enumerate(sessions)}
    df["si"] = df["session_date"].map(sidx)
    df["month"] = df["session_date"].str[:7]
    months = sorted(df["month"].unique())
    test_months = months[MIN_TRAIN_MONTHS:]
    if a.dry_month:
        test_months = [a.dry_month]
    feats_ext = YAHOO_FEATURES + POOL_EXTRA

    if a.variants:
        print("\n".join(run_variants(df, test_months, open_days)))
        return 0

    # S2 교재
    tb = None
    if not a.no_s2:
        from tools.us_swing_model_matrix_experiment import _build_frame
        tb = _build_frame()
        tb["session_date"] = tb["session_date"].astype(str)
        print(f"교재 {len(tb)}행 {tb['session_date'].min()}~{tb['session_date'].max()} (D5 계약 라벨)")

    scored = []
    for w, mth in enumerate(test_months):
        test = df[(df["month"] == mth) & ~df["session_date"].isin(open_days)].copy()
        if test.empty:
            continue
        f = int(test["si"].min())
        train = df[df["si"] <= f - PURGE]
        test["m_main"] = fit_score(train, test, YAHOO_FEATURES, seeds, w)
        test["m_ext"] = fit_score(train, test, feats_ext, seeds, w)
        if tb is not None:
            cut = (pd.Timestamp(mth + "-01") - pd.Timedelta(days=16)).strftime("%Y-%m-%d")
            ttr = tb[tb["session_date"] < cut]
            test["m_tb"] = fit_score(ttr.assign(net=ttr["label_contract"]), test, YAHOO_FEATURES, seeds, w) if len(ttr) > 500 else np.nan
        print(f"  {mth}: 학습 {len(train)}행(~{sessions[f - PURGE] if f - PURGE >= 0 else '-'}) 테스트 {len(test)}행 {test['session_date'].nunique()}일")
        scored.append(test)
    sc = pd.concat(scored, ignore_index=True)
    sc["neg_dvol_rank"] = -sc["dvol_rank"]
    live = sc["chg"].le(-5) & sc["dvol"].between(100, 500) & sc["max21"].ge(8)
    sc["live_score"] = np.where(live, sc["dvol"], np.nan)
    npool = sc.groupby("session_date").size()
    print("일별 풀 n 분포:", npool.describe().round(1).to_dict())

    L = ["| 모델 | 지표 | 일수 | 평균(−풀, %p) | 날짜 t | 전반 | 후반 |", "|---|---|---|---|---|---|---|"]
    res_main = evaluate(sc, "m_main")
    L += fmt("주: HGB 27피처(풀 학습)", res_main)
    L += fmt("기준선: dvol 내림차순", evaluate(sc, "neg_dvol_rank"))
    rl = evaluate(sc, "live_score")
    L += [x for x in fmt("기준선: 라이브 규칙", rl) if "top-1" in x]
    L += fmt("S1: HGB 27+풀특성", evaluate(sc, "m_ext"))
    if tb is not None:
        L += fmt("S2: 교재 학습 as-built", evaluate(sc, "m_tb"))
    sub = sc[sc["chg"].le(-5)]
    L += fmt("S3: 주 모델, chg≤−5 부분집합", evaluate(sub, "m_main"))
    print("\n".join(L))
    m, t, n, h1, h2 = res_main["top3"]
    verdict = "통과" if (t >= 2 and h1 > 0 and h2 > 0) else ("후보" if (t >= 1 and h1 > 0 and h2 > 0) else "기각")
    print(f"\n테스트 {sc['session_date'].min()}~{sc['session_date'].max()} · {res_main['days']}일 · {res_main['trades']}건 · 반기 경계 {res_main['mid']}")
    print(f"사전등록 판정(주 모델 top-3): {verdict} (mean {m:+.3f} t {t:+.2f} 전반 {h1:+.3f} 후반 {h2:+.3f})")
    pm = sc.groupby("session_date")["net"].mean()
    print(f"참고: 풀 일평균 net {pm.mean():+.3f}% (날짜 t {tstat(pm.to_numpy())[1]:+.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
