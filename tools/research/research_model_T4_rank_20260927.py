#!/usr/bin/env python3
"""T4 순위 학습(가격) — LambdaMART가 같은 날 급락 풀 안에서 순위 가치를 뽑아내나 (read-only, 2026-09-27).

근거: docs/reports/model_research_program_20260927.md §2 T4 · §3 규약,
      docs/reports/model_revival_us_swing_20260927.md(피처·워크포워드),
      docs/reports/model_R1_us_swing_s1_20260927.md(S1 = max21 한 줄 정렬, 동점 결함 → 결정적 동점 규칙),
      docs/reports/universe_bench_20260923.md(KR 규모매칭 계약 벤치).
기존 파일은 수정하지 않는다(원 스크립트 함수는 import만). 연구 venv: C:/Users/Unknown/research_venv
(LightGBM 4.6.0 · sklearn 1.6.1 — 원 revival/R1은 anaconda sklearn 1.0.2였다. HGB 파라미터는 같다).

## 사전 등록 (전체 실행 전에 고정 — --dry-month는 배선 확인용이고 판정에 쓰지 않는다)

질문: 점별(pointwise) 분류기·회귀기와 단일 피처 정렬이 못 뽑은 "같은 날 풀 안 순위" 가치를
      진짜 순위 학습(LambdaMART)이 뽑는가 — 아키텍처 검증. 새 정보는 없다(가격만).

모집단(시장별, data/shadow/virtual_books.db trades, status='CLOSED'):
  US = strategy_id 'xus_fallen3'  — session_date = 진입일(신호일은 meta.signal_date).
  KR = strategy_id 'xkr_fallen3'  — session_date = 신호일, 진입 = 다음 세션 시가.
  결과 = net_pct(계약 TP12/SL25/D7, US BE4·비용 0.5, KR BE 없음·비용 0.25).
  KR net_pct는 meta.path 재생(TP 장중 12 → 11.75, 종가 ≤−25, 7봉 만기 종가, −0.25)과 37,602/37,602 일치 확인(실행 전).
  쿼리 = session_date 하루. 테스트일 중 그날 풀에 OPEN 행이 하나라도 있으면 그날 제외(일내 생존편향 방지).

피처(시장 내 모든 모델 동일 행렬):
  FEAT = meta.feat 수치 17종(price, chg, gap, dvol, dvol20, vol_spike, ibs, rv20, mom20, from_high20, ma20_disc,
         cum5, min1_in5, ret60, max21, hi_break_n, down_streak) + close_pos_up(bool→0/1)
         — tools/discovery_pools.featurize가 신호봉에서 만든 값 그대로.
  FEAT_r = FEAT 각각의 같은 날 풀 내 백분위 순위(groupby(session_date).rank(pct=True)) — 풀 상대 순위.
  REG  = meta.regime(idx_ret20, idx_above_ma20, breadth_down_pct) + pool_n. 하루 상수라 LambdaRank에는
         정보가 없다(쿼리 내 분산 0). 점별 모델 공정성을 위해 넣는다.
  US는 여기에 원 모델 27종(YAHOO_FEATURES, revival attach_features, 신호봉 기준)을 더한다.
  → 랭커도 max21·dvol을 본다(단일 정렬 대비 불공정 방지).

라벨:
  (a) 같은 날 net_pct의 5단계 분위 관련도: rel = ceil(rank(net, method='average', pct=True)·5) − 1 ∈ {0..4}.
      동점 net(예: TP 11.5)은 같은 관련도.
  (b) 문자 그대로("net − 같은 날 풀 평균"의 같은 날 분위)는 (a)와 **완전히 같다**(하루 상수를 빼도 일내 순위 불변)
      → 퇴화. 해소: (b) = 초과 x = net − 같은 날 풀 평균을 **학습창 전체의 x 5분위 경계**로 등급화(0..4).
      하루 안 순위뿐 아니라 초과 크기(크게 이긴 날은 4등급이 많음)를 담는다. 경계는 학습 행으로만 계산.
  점별 모델 라벨: (a) 회귀 = net, 분류 = net ≥ 0.25(원 모델 목표) / (b) 회귀 = x, 분류 = x > 0.

모델(파라미터 고정, 튜닝 없음):
  RANK = lightgbm.LGBMRanker(objective='lambdarank', 기본값, random_state=0, n_jobs=2), fit eval_at=[3]
         (검증셋 없음 → 지표 설정만). 학습 행은 session_date 정렬 후 group 크기 전달, 1건 쿼리는 학습에서 뺀다.
  LGBR = lightgbm.LGBMRegressor(기본값, random_state=0, n_jobs=2) — 점별 회귀.
  HGBC = _make_models("HGB")의 분류기(원 패밀리 파라미터), config seeds 3종(seed+w) 확률 평균.
  단일 정렬: max21 내림차순, dvol 내림차순(해당 피처 NaN은 후보에서 빼고 풀 평균에는 남김).
  동점 규칙(전 모델 공통): sort_values([점수, ticker], ascending=[False, True]).

워크포워드: 월별 확장창, 테스트월 첫 세션 인덱스 f → 학습 = 세션 인덱스 ≤ f−8(퍼지 8세션), 최소 3개 달력월
  (첫 테스트월 = 네 번째 월). 학습 행에는 OPEN 제외 규칙을 적용하지 않는다(CLOSED만 존재).

평가(테스트일별, 풀 n ≥ 3):
  ① top-3 평균 net − 같은 날 풀 평균 (날짜 클러스터 t), top-1 병기(보조).
  ② KR: top-3 픽의 (net − 규모매칭 계약 벤치) 평균. 벤치 = research_universe_bench_20260923와 같은 정의 —
     신호일 적격(이력 ≥25봉·거래대금 ≥20억·종가 ≥1,000원) 전 종목을 dvol20 십분위로 나누고 같은 십분위에
     같은 계약(contract_exit_v2(다음 시가, 7봉, fee 0.25, be_lock=False, TP12/SL25)) 평균, 칸 ≥20종목.
     픽 자신이 칸에 포함된다(09-23 벤치와 동일 — 비교 가능성 우선).
     US: top-3 픽의 (net − IWM 실제 청산 창) 평균. 청산일 = R1 replay_exit(meta.path), IWM = 진입일 시가 →
     청산일 종가. net에만 비용 0.5가 있어 IWM 초과는 ~0.5%p 과소평가(R1과 같은 한계).
  ③ 전/후반: 시장별 테스트일 중앙 날짜로 분할.
  ④ 쌍대: 일별 (RANK top-3 평균 net − 비교 모델 top-3 평균 net), 비교 = 같은 라벨의 LGBR, 같은 라벨의 HGBC,
     max21 정렬(참고로 dvol 정렬도 병기). 같은 날·같은 풀이라 풀 평균·지수 벤치는 상쇄된다.

통과(칸 = 시장 × 라벨, 4칸, 칸마다 전부 동시 충족):
  - RANK − LGBR, RANK − HGBC, RANK − max21 쌍대 날짜 t 각각 ≥ 2.7
  - 세 쌍대 차 모두 전·후반 평균 > 0
  - RANK top-3 − 풀 전·후반 평균 > 0
  후보 = 세 쌍대 차 평균 모두 > 0이고 최소 t ≥ 2이며 RANK top-3 − 풀 양 반기 > 0(통과 미달).
  그 외 = 기각. 전체 판정: 통과 칸 ≥1 → 확정(단, 다른 시장 같은 라벨 칸 결과를 병기) / 후보 칸 ≥1 → 후보 / 그 외 기각.
  KR 규모매칭·US IWM 초과는 판정 조건이 아니라 "순위 가치가 돈이 되나"의 보고 항목이다.

한계(사전 인정): 생존편향(현재 상장 가격 캐시), 테스트 약 13개월·한 국면, 수정주가, 소형주 슬리피지 미반영.

사용: research_venv python tools/research/research_model_T4_rank_20260927.py [--dry-month YYYY-MM] [--market US|KR]
"""
from __future__ import annotations

import os

os.environ["OMP_NUM_THREADS"] = "2"   # R1 모듈의 setdefault(4)보다 먼저

import argparse  # noqa: E402
import gc  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sqlite3  # noqa: E402
import statistics as st  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402
from contextlib import closing  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import lightgbm as lgb  # noqa: E402

from tools.research.research_model_R1_us_swing_s1_20260927 import iwm_bars, iwm_ret, load_extra  # noqa: E402
from tools.research.research_model_revival_us_swing_20260927 import POLICY, attach_features  # noqa: E402
from tools.research.research_universe_bench_20260923 import load_bars  # noqa: E402
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES  # noqa: E402
from tools.us_swing_model_matrix_experiment import _make_models  # noqa: E402
from tools.virtual_books import FEE_KR, contract_exit_v2  # noqa: E402

DB = ROOT / "data" / "shadow" / "virtual_books.db"
KR_DIR = ROOT / "data" / "price" / "kr"
OUT = ROOT / "data" / "analysis" / "model_T4_rank_20260927"
PURGE, MIN_TRAIN_MONTHS, K = 8, 3, 3
PASS_T, CAND_T = 2.7, 2.0
FEAT = ["price", "chg", "gap", "dvol", "dvol20", "vol_spike", "ibs", "rv20", "mom20", "from_high20", "ma20_disc",
        "cum5", "min1_in5", "ret60", "max21", "hi_break_n", "down_streak", "close_pos_up"]
REG = ["idx_ret20", "idx_above_ma20", "breadth_down_pct", "pool_n"]
SID = {"US": "xus_fallen3", "KR": "xkr_fallen3"}


# ── 데이터 ────────────────────────────────────────────────────────────────
def load_pool(mkt: str) -> tuple[pd.DataFrame, set]:
    rows, open_days = [], set()
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        for sd, t, st_, net, meta in con.execute(
                "SELECT session_date, ticker, status, net_pct, meta FROM trades WHERE strategy_id=?", (SID[mkt],)):
            if st_ != "CLOSED":
                open_days.add(sd)
                continue
            m = json.loads(meta)
            f, rg = m.get("feat") or {}, m.get("regime") or {}
            rows.append({"session_date": sd, "ticker": str(t), "signal_date": m.get("signal_date"), "net": net,
                         **{k: f.get(k) for k in FEAT}, "idx_ret20": rg.get("idx_ret20"),
                         "idx_above_ma20": rg.get("idx_above_ma20"), "breadth_down_pct": rg.get("breadth_down_pct"),
                         "pool_n": m.get("pool_n")})
    df = pd.DataFrame(rows)
    for c in FEAT + REG + ["net"]:
        df[c] = pd.to_numeric(df[c].map(lambda v: float(v) if isinstance(v, bool) else v), errors="coerce")
    return df, open_days


def add_ranks(df: pd.DataFrame) -> list[str]:
    g = df.groupby("session_date")
    cols = []
    for c in FEAT:
        df[c + "_r"] = g[c].rank(pct=True)
        cols.append(c + "_r")
    return cols


def kr_size_bench(dates: set, pool_keys: set) -> dict:
    """(date, ticker) → 규모매칭 계약 벤치(같은 날 dvol20 십분위 평균, 칸 ≥20). 09-23 universe_bench와 같은 정의."""
    by_date = defaultdict(list)   # d -> [(ticker, dv20, ret|None)]
    for p in sorted(KR_DIR.glob("kr_*.csv")):
        t = p.stem[3:]
        b = load_bars(p)
        for i in range(25, len(b) - 1):
            d, _o, _h, _l, c, v = b[i]
            if d not in dates or c < 1000.0 or c * v / 1e8 < 20.0 or b[i + 1][1] <= 0:
                continue
            dv20 = st.mean(x[4] * x[5] for x in b[i - 19: i + 1]) / 1e8
            r_ = contract_exit_v2(b[i + 1][1], b[i + 1: i + 8], fee=FEE_KR, be_lock=False, tp=12.0, sl=-25.0, hold=7)
            by_date[d].append((t, dv20, r_[0] if r_ is not None else None))
        del b
    out = {}
    for d, v in by_date.items():
        order = sorted(v, key=lambda r: r[1])
        dec_of, acc = {}, defaultdict(lambda: [0.0, 0])
        for j, (t, _dv, r) in enumerate(order):
            dec = min(9, j * 10 // len(order))
            dec_of[t] = dec
            if r is not None:
                acc[dec][0] += r
                acc[dec][1] += 1
        for t, dec in dec_of.items():
            if (d, t) in pool_keys:
                a = acc.get(dec)
                out[(d, t)] = a[0] / a[1] if a and a[1] >= 20 else np.nan
    return out


# ── 라벨·모델 ─────────────────────────────────────────────────────────────
def rel_a(df: pd.DataFrame) -> np.ndarray:
    pct = df.groupby("session_date")["net"].rank(method="average", pct=True)
    return (np.ceil(pct.to_numpy() * 5) - 1).clip(0, 4).astype(int)


def rel_b(train: pd.DataFrame) -> np.ndarray:
    edges = np.quantile(train["x"].to_numpy(), [0.2, 0.4, 0.6, 0.8])
    return np.searchsorted(edges, train["x"].to_numpy(), side="right").astype(int)


def fit_ranker(train: pd.DataFrame, test: pd.DataFrame, feats: list[str], rel: np.ndarray) -> np.ndarray:
    tr = train.assign(_rel=rel)
    tr = tr[tr.groupby("session_date")["net"].transform("size") >= 2].sort_values("session_date", kind="stable")
    grp = tr.groupby("session_date", sort=True).size().to_numpy()
    m = lgb.LGBMRanker(objective="lambdarank", random_state=0, n_jobs=2, verbose=-1)
    m.fit(tr[feats], tr["_rel"].to_numpy(), group=grp, eval_at=[3])
    return m.predict(test[feats])


def fit_lgbr(train, test, feats, y) -> np.ndarray:
    m = lgb.LGBMRegressor(random_state=0, n_jobs=2, verbose=-1)
    m.fit(train[feats], y)
    return m.predict(test[feats])


def fit_hgbc(train, test, feats, y, seeds, w) -> np.ndarray:
    ps = []
    for s in seeds:
        _reg, clf = _make_models("HGB", s + w)
        clf.fit(train[feats], y)
        ps.append(clf.predict_proba(test[feats])[:, 1])
    return np.mean(ps, axis=0)


# ── 평가 ──────────────────────────────────────────────────────────────────
def tstat(v):
    v = np.asarray([x for x in v if x is not None and np.isfinite(x)], float)
    if len(v) < 3 or v.std(ddof=1) == 0:
        return (float(v.mean()) if len(v) else float("nan")), float("nan"), len(v)
    return float(v.mean()), float(v.mean() / (v.std(ddof=1) / math.sqrt(len(v)))), len(v)


def daily_picks(sc: pd.DataFrame, col: str, bench: str | None) -> pd.DataFrame:
    """일별 top-3/top-1 지표. 대조 풀 평균 = 그날 전체 풀(점수 결측 포함)."""
    rec = []
    for d, g0 in sc.groupby("session_date", sort=True):
        if len(g0) < K:
            continue
        pm = g0["net"].mean()
        g = g0.dropna(subset=[col]).sort_values([col, "ticker"], ascending=[False, True], kind="mergesort")
        if len(g) < K:
            continue
        top = g.iloc[:K]
        r = {"d": d, "top3": top["net"].mean(), "x_pool": top["net"].mean() - pm, "x1_pool": g["net"].iloc[0] - pm}
        if bench:
            xb = (top["net"] - top[bench]).dropna()
            r["x_bench"] = xb.mean() if len(xb) else np.nan
        rec.append(r)
    return pd.DataFrame(rec).set_index("d")


def summ(s: pd.Series, mid: str) -> dict:
    m, t, n = tstat(s.to_numpy())
    h1 = s[s.index < mid].dropna()
    h2 = s[s.index >= mid].dropna()
    return {"mean": m, "t": t, "n": n, "h1": float(h1.mean()) if len(h1) else float("nan"),
            "h2": float(h2.mean()) if len(h2) else float("nan")}


def f_(r: dict) -> str:
    return f"{r['mean']:+.3f} (t {r['t']:+.2f}) · {r['h1']:+.3f}/{r['h2']:+.3f}"


# ── 실행 ──────────────────────────────────────────────────────────────────
def run_market(mkt: str, dry_month: str | None, seeds: list[int]) -> tuple[list[str], dict]:
    df, open_days = load_pool(mkt)
    print(f"[{mkt}] CLOSED {len(df)} · OPEN 포함 세션 {len(open_days)}")
    feats = FEAT + add_ranks(df) + REG
    if mkt == "US":
        df = attach_features(df)
        ok = df["change_pct"].notna()
        bad = ((df.loc[ok, "change_pct"] - df.loc[ok, "chg"]).abs() > 0.05).sum()
        print(f"[US] 27피처 결합 {ok.sum()}/{len(df)} · chg 불일치 {bad}")
        feats = YAHOO_FEATURES + feats
        ex = load_extra()[["session_date", "ticker", "exit_date"]]
        ex["ticker"] = ex["ticker"].astype(str)
        df = df.merge(ex, on=["session_date", "ticker"], how="left")
        bars = iwm_bars()
        df["bench"] = [iwm_ret(bars, a, b) for a, b in zip(df["session_date"], df["exit_date"])]
    df = df.loc[:, ~df.columns.duplicated()].copy()
    df["x"] = df["net"] - df.groupby("session_date")["net"].transform("mean")
    sessions = sorted(df["session_date"].unique())
    df["si"] = df["session_date"].map({d: i for i, d in enumerate(sessions)})
    df["month"] = df["session_date"].str[:7]
    months = sorted(df["month"].unique())
    test_months = [dry_month] if dry_month else months[MIN_TRAIN_MONTHS:]
    for c in feats:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float32")

    parts = []
    for w, mth in enumerate(test_months):
        test = df[(df["month"] == mth) & ~df["session_date"].isin(open_days)].copy()
        if test.empty:
            continue
        train = df[df["si"] <= int(test["si"].min()) - PURGE]
        if len(train) < 500:
            continue
        ra = rel_a(train)
        rb = rel_b(train)
        test["RANK_a"] = fit_ranker(train, test, feats, ra)
        test["RANK_b"] = fit_ranker(train, test, feats, rb)
        test["LGBR_a"] = fit_lgbr(train, test, feats, train["net"].to_numpy())
        test["LGBR_b"] = fit_lgbr(train, test, feats, train["x"].to_numpy())
        test["HGBC_a"] = fit_hgbc(train, test, feats, (train["net"] >= 0.25).astype(int), seeds, w)
        test["HGBC_b"] = fit_hgbc(train, test, feats, (train["x"] > 0).astype(int), seeds, w)
        print(f"  [{mkt}] {mth}: 학습 {len(train)} ({train['session_date'].nunique()}일) 테스트 {len(test)} "
              f"({test['session_date'].nunique()}일)")
        parts.append(test)
        gc.collect()
    sc = pd.concat(parts, ignore_index=True)
    del df, parts
    gc.collect()

    if mkt == "KR":
        print("[KR] 규모매칭 계약 벤치 계산 …")
        sb = kr_size_bench(set(sc["session_date"]), set(zip(sc["session_date"], sc["ticker"])))
        sc["bench"] = [sb.get((d, t), np.nan) for d, t in zip(sc["session_date"], sc["ticker"])]
        del sb
        gc.collect()
    sc["S_max21"] = sc["max21"]
    sc["S_dvol"] = sc["dvol"]
    OUT.mkdir(parents=True, exist_ok=True)
    keep = ["session_date", "ticker", "net", "x", "bench", "max21", "dvol",
            "RANK_a", "RANK_b", "LGBR_a", "LGBR_b", "HGBC_a", "HGBC_b"]
    if not dry_month:
        sc[keep].to_csv(OUT / f"scored_{mkt}.csv", index=False, encoding="utf-8-sig")

    models = ["RANK_a", "LGBR_a", "HGBC_a", "RANK_b", "LGBR_b", "HGBC_b", "S_max21", "S_dvol"]
    dp = {m: daily_picks(sc, m, "bench") for m in models}
    days = sorted(dp["RANK_a"].index)
    mid = days[len(days) // 2]
    blab = "−규모매칭 계약" if mkt == "KR" else "−IWM 실청산창"
    res = {"market": mkt, "rows": int(len(sc)), "days": len(days), "first": days[0], "last": days[-1], "mid": mid,
           "open_excluded_days": len(open_days), "bench_cov": float(sc["bench"].notna().mean()), "models": {},
           "paired": {}, "cells": {}}
    L = [f"### {mkt} — 테스트 {days[0]} ~ {days[-1]} · {len(days)}일 · {len(sc):,}건 · 반기 경계 {mid} · "
         f"벤치 커버리지 {res['bench_cov']:.1%}", "",
         f"| 모델 | top-3 − 풀 (t) · 전반/후반 | top-1 − 풀 (t) | top-3 {blab} (t) · 전반/후반 |",
         "|---|---|---|---|"]
    for m in models:
        a = summ(dp[m]["x_pool"], mid)
        b1 = summ(dp[m]["x1_pool"], mid)
        c = summ(dp[m]["x_bench"], mid)
        res["models"][m] = {"x_pool": a, "x1_pool": b1, "x_bench": c}
        L.append(f"| {m} | {f_(a)} | {b1['mean']:+.3f} ({b1['t']:+.2f}) | {f_(c)} |")
    L += ["", "| 칸 | 쌍대(일별 top-3 net 차) | 평균 (t) · 전반/후반 |", "|---|---|---|"]
    for lab in ("a", "b"):
        rk = dp[f"RANK_{lab}"]["top3"]
        legs = {}
        for other in (f"LGBR_{lab}", f"HGBC_{lab}", "S_max21", "S_dvol"):
            diff = (rk - dp[other]["top3"]).dropna()
            r = summ(diff, mid)
            res["paired"][f"RANK_{lab}-{other}"] = r
            legs[other] = r
            L.append(f"| {mkt}·{lab} | RANK_{lab} − {other} | {f_(r)} |")
        own = res["models"][f"RANK_{lab}"]["x_pool"]
        core = [legs[f"LGBR_{lab}"], legs[f"HGBC_{lab}"], legs["S_max21"]]
        passed = all(r["t"] >= PASS_T and r["h1"] > 0 and r["h2"] > 0 for r in core) and own["h1"] > 0 and own["h2"] > 0
        cand = all(r["mean"] > 0 for r in core) and min(r["t"] for r in core) >= CAND_T and own["h1"] > 0 and own["h2"] > 0
        res["cells"][f"{mkt}·{lab}"] = "통과" if passed else ("후보" if cand else "기각")
    L += ["", "| 칸 | 판정 |", "|---|---|"] + [f"| {k} | {v} |" for k, v in res["cells"].items()]
    return L, res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-month", default=None)
    ap.add_argument("--market", choices=["US", "KR"], default=None)
    a = ap.parse_args()
    seeds = [int(s) for s in json.loads(POLICY.read_text(encoding="utf-8")).get("seeds", [20260710])]
    L, allres = [f"# T4 결과 (lightgbm {lgb.__version__})", ""], {}
    for mkt in ([a.market] if a.market else ["US", "KR"]):
        lines, res = run_market(mkt, a.dry_month, seeds)
        print("\n".join(lines))
        L += lines + [""]
        allres[mkt] = res
        gc.collect()
    cells = {k: v for r in allres.values() for k, v in r["cells"].items()}
    verdict = "확정" if "통과" in cells.values() else ("후보" if "후보" in cells.values() else "기각")
    L.append(f"**전체 판정(사전 등록): {verdict}** — {cells}")
    print(L[-1])
    if not a.dry_month:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "results.md").write_text("\n".join(L) + "\n", encoding="utf-8")
        (OUT / "results.json").write_text(json.dumps({"verdict": verdict, "cells": cells, **allres},
                                                     ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
