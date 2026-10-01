#!/usr/bin/env python3
"""R1 재심 — US swing S1(풀 학습 HGB, 27+풀특성 8종)이 쉐도우 arm 자격이 있나 (read-only, 2026-09-27).

근거: docs/reports/model_research_program_20260927.md §1 R1 · §3 규약,
      docs/reports/model_revival_us_swing_20260927.md (S1 top-3 − 같은 날 풀 +0.995%p, 날짜 t 3.09).
기존 파일은 수정하지 않는다. 원 스크립트의 load_pool / attach_features / fit_score / evaluate / tstat /
POOL_EXTRA를 **그대로 import**해 픽을 동일하게 만든다(같은 anaconda python·sklearn 1.0.2·seeds·seed+w).

## 사전 등록 (전체 실행 전에 고정 — dry-month는 배선 확인용만)

1. 재현: 모집단 xus_fallen3 CLOSED(원 보고서 35,288행 · 256일 · 반기 경계 2026-03-06). 행 수가 다르면
   "정확 재현"이 아니라 "스냅샷 내 재현"으로 표기하고 테스트 창을 ≤2026-09-17로 고정해 차이를 보고한다.
   S1 top-3 − 풀이 +0.995 / t 3.09와 소수 셋째 자리까지 일치해야 2~5단계로 간다(스레드 수 차이로
   셋째 자리 흔들림은 허용·표기). OMP_NUM_THREADS=4.
2. 재측정 — 대상: S1 top-1 / top-3. 비교 정렬: max21 내림차순, dvol 내림차순(단일 피처).
   (a) 같은 날 풀 평균 대비(원 보고서와 동일, evaluate 재사용).
   (b) IWM 같은 창: 진입일(session_date) IWM 시가 → **계약 실제 청산일** IWM 종가. 청산일은 meta.path를
       contract_exit_v2 규칙(TP12 장중 우선·SL25/BE4 종가·BE는 전일까지 봉우리·D0는 종가만·7봉 만기)으로
       재생해 복원하고 meta.grid[tp12_sl25_d7_be] 사유와 대조(불일치 건수 보고). 규약: TP는 장중 청산이라
       청산일 종가는 근사. net_pct에는 비용 0.5%p가 있고 IWM에는 없다 → 초과는 0.5%p 과소평가.
       보조 행: 고정 7세션 창(D0 시가 → D6 종가, 09-12 us_swing_excess와 같은 규약).
       일별 지표 = top-k 픽의 (net − IWM창수익) 평균 → 날짜 클러스터 t.
   (c) 규모·변동성 매칭 대조: 구간 경계 = 전체 CLOSED 풀의 atr_pct(신호봉 피처) 10분위 · feat.dvol 3분위
       (대조 구성 전용 — 경계에 전체 기간 정보가 들어가는 약한 look-ahead를 인정한다. 거래 규칙이 아님).
       픽마다 대조 = 같은 날 풀에서 같은 (ATR 10분위, dvol 3분위) 칸의 픽 자신을 뺀 종목 평균 net.
       칸에 픽 외 ≥2종목 필요, 없으면 ATR 10분위만으로 대체, 그래도 <2면 그 픽은 제외(커버리지 보고).
       일별 지표 = top-k 픽의 (net − 대조) 평균 → 날짜 클러스터 t. 같은 매칭 대조를 max21·dvol 정렬에도 적용.
   (d) 쌍대 비교: 일별 (S1 top-3 평균 net − max21 top-3 평균 net), (S1 − dvol) 평균과 날짜 t.
3. 슬리피지 스트레스: 조건(feat.price < 10 또는 atr_pct > 8)을 만족하는 **모든 행**(픽·풀·대조 동일)에
   왕복 추가 비용 0.3% / 0.6%를 빼고 (a)~(d)를 다시 잰다. 조건은 종목 속성이므로 픽에만 매기지 않는다
   → 매칭 대조 차이는 거의 안 움직이고 IWM 초과·절대 수익에서 효과가 난다(사전 인정). 조건 해당 비율 보고.
4. 안정성: 반기(원 경계 2026-03-06), 분기(달력 분기), 교재 동결 전후(<2026-05-01 / ≥2026-05-01),
   월별 양수 비율 — 지표 = S1 top-3의 풀 대비·매칭 대조 대비·IWM 대비.
5. 용량·현실성: 주문 54만원 ≈ $390(1,380원/$ 가정) 대비 픽의 신호일 거래대금(참여율), 가격 분포
   (<$5, <$10), 라이브 밴드(dvol 100~500M) 안 비율, 하루 체결 가능 픽 수(참여율 ≤1% 기준).

통과(사전 등록, 전부 동시 충족):
  ① S1 top-3 − 매칭 대조, 날짜 t ≥ 3
  ② S1 top-3가 max21 top-3를 이김 = 쌍대 일별 차 평균 > 0 (t 병기; t<1이면 결론에서 그렇게 적는다)
  ③ S1 top-3 IWM 초과(실제 청산 창) 평균 > 0
  ④ 0.6% 스트레스에서 ③ > 0 그리고 ① t ≥ 3 유지
후보 = ①이 t ≥ 2이고 반기 양쪽 양수, ③ > 0 (나머지 미충족). 그 외 = 기각.
통과일 때만 쉐도우 arm 스펙을 쓴다.

사용: python tools/research/research_model_R1_us_swing_s1_20260927.py [--dry-month YYYY-MM] [--reuse]
"""
from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "4")

import argparse  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import sqlite3  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402
from contextlib import closing  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research.research_model_revival_us_swing_20260927 import (  # noqa: E402
    DB, MIN_TRAIN_MONTHS, POLICY, POOL_EXTRA, PURGE, attach_features, evaluate, fit_score, load_pool, tstat)
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES  # noqa: E402

OUT = ROOT / "data" / "analysis" / "model_R1_us_swing_s1_20260927"
PRICE = ROOT / "data" / "price" / "us"
TP, SL, BE, HOLD = 12.0, -25.0, 4.0, 7
MID_ORIG = "2026-03-06"
FROZEN = "2026-05-01"
ORDER_USD = 540000 / 1380.0


# ── 청산일 복원 · IWM ─────────────────────────────────────────────────────
def replay_exit(path: dict) -> tuple[int | None, str | None]:
    cl, hi = path.get("close") or [], path.get("high") or []
    if not cl:
        return None, None
    peak = cl[0]
    for i in range(min(HOLD, len(cl))):
        hip = hi[i] if i > 0 else cl[0]
        cp = cl[i]
        if hip >= TP:
            return i, "TP"
        if cp <= SL:
            return i, "SL"
        if peak >= BE and cp <= 0:
            return i, "BE"
        peak = max(peak, hip)
    if len(cl) < HOLD:
        return None, None
    return HOLD - 1, "D_MAT"


def load_extra() -> pd.DataFrame:
    rows = []
    with closing(sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True, timeout=10)) as con:
        for sd, t, meta in con.execute(
                "SELECT session_date, ticker, meta FROM trades WHERE strategy_id='xus_fallen3' AND status='CLOSED'"):
            m = json.loads(meta)
            p = m.get("path") or {}
            i, why = replay_exit(p)
            dates = p.get("dates") or []
            g = (m.get("grid") or {}).get("tp12_sl25_d7_be")
            rows.append({"session_date": sd, "ticker": t, "price": (m.get("feat") or {}).get("price"),
                         "exit_date": dates[i] if i is not None and i < len(dates) else None,
                         "d7_date": dates[HOLD - 1] if len(dates) >= HOLD else None,
                         "exit_why": why, "grid_why": g[1] if g else None})
    return pd.DataFrame(rows)


def iwm_bars() -> dict:
    df = pd.read_csv(PRICE / "us_IWM.csv", encoding="utf-8-sig")
    return {str(d)[:10]: (float(o), float(c)) for d, o, c in zip(df["date"], df["open"], df["close"])}


def iwm_ret(bars: dict, d0, d1):
    if not isinstance(d0, str) or not isinstance(d1, str) or d0 not in bars or d1 not in bars:
        return np.nan
    return (bars[d1][1] / bars[d0][0] - 1.0) * 100.0


# ── 지표 ──────────────────────────────────────────────────────────────────
def picks(sc: pd.DataFrame, col: str, k: int) -> pd.DataFrame:
    """일별 top-k (col 내림차순, NaN 제외). 원 evaluate와 같은 조건(그날 풀 ≥2, top-3는 n≥3)."""
    out = []
    for d, g0 in sc.groupby("session_date"):
        g = g0.dropna(subset=[col])
        if len(g0) < 2 or len(g) < max(1, k if k > 1 else 1):
            continue
        out.append(g.sort_values(col, ascending=False).iloc[:k])
    return pd.concat(out) if out else sc.iloc[:0]


def matched_control(sc: pd.DataFrame, pk: pd.DataFrame, netc: str) -> pd.Series:
    """픽별 매칭 대조 평균 net(같은 날·같은 ATR10·dvol3 칸, 픽 자신 제외, ≥2 필요 → ATR10만 → 제외)."""
    by_day = {d: g for d, g in sc.groupby("session_date")}
    vals = []
    for idx, r in pk.iterrows():
        g = by_day[r["session_date"]]
        v = np.nan
        if pd.notna(r["atr_dec"]):
            others = g[g.index != idx]
            c1 = others[(others["atr_dec"] == r["atr_dec"]) & (others["dvol_ter"] == r["dvol_ter"])]
            if len(c1) >= 2:
                v = c1[netc].mean()
            else:
                c2 = others[others["atr_dec"] == r["atr_dec"]]
                if len(c2) >= 2:
                    v = c2[netc].mean()
        vals.append(v)
    return pd.Series(vals, index=pk.index)


def daily(pk: pd.DataFrame, val: str) -> pd.Series:
    return pk.dropna(subset=[val]).groupby("session_date")[val].mean()


def summ(s: pd.Series, mid: str = MID_ORIG) -> dict:
    m, t, n = tstat(s.to_numpy())
    h1 = s[s.index < mid].mean()
    h2 = s[s.index >= mid].mean()
    return {"mean": m, "t": t, "n": n, "h1": float(h1), "h2": float(h2),
            "pos_days": float((s > 0).mean()) if len(s) else float("nan")}


def row(name, r):
    return (f"| {name} | {r['n']} | {r['mean']:+.3f} | {r['t']:+.2f} | {r['h1']:+.3f} | {r['h2']:+.3f} | "
            f"{r['pos_days']*100:.0f}% |")


def measure(sc: pd.DataFrame, netc: str, tag: str, store: dict) -> list[str]:
    """netc 기준으로 (a)(b)(c)(d) 전부. 반환 = 마크다운 줄."""
    sc = sc.copy()
    sc["pool_mean"] = sc.groupby("session_date")[netc].transform("mean")
    sc["x_pool"] = sc[netc] - sc["pool_mean"]
    sc["x_iwm"] = sc[netc] - sc["iwm_exit"]
    sc["x_iwm7"] = sc[netc] - sc["iwm_d7"]
    L = [f"### {tag}", "", "| 정렬 · k · 대조 | 일수 | 평균(%p) | 날짜 t | 전반 | 후반 | 양수일 |",
         "|---|---|---|---|---|---|---|"]
    tops = {}
    for col, nm in (("m_ext", "S1"), ("max21", "max21↓"), ("dvol", "dvol↓")):
        for k in (1, 3):
            pk = picks(sc, col, k)
            if k == 1:
                pk = pk  # top-1: 원 evaluate와 같게 n≥1
            else:
                pk = pk[pk.groupby("session_date")["ticker"].transform("size") >= 3]
            pk = pk.copy()
            pk["ctrl"] = matched_control(sc, pk, netc)
            pk["x_ctrl"] = pk[netc] - pk["ctrl"]
            tops[(nm, k)] = pk
            for val, lab in (("x_pool", "−풀"), ("x_ctrl", "−매칭대조"), ("x_iwm", "−IWM(실청산창)"),
                             ("x_iwm7", "−IWM(고정7세션)")):
                r = summ(daily(pk, val))
                store[(tag, nm, k, val)] = r
                L.append(row(f"{nm} top-{k} {lab}", r))
            cov = pk["ctrl"].notna().mean()
            store[(tag, nm, k, "ctrl_cov")] = float(cov)
    L += ["", "| 쌍대(top-3, 일별 net 평균 차) | 일수 | 평균(%p) | 날짜 t | 전반 | 후반 | 양수일 |", "|---|---|---|---|---|---|---|"]
    s1 = tops[("S1", 3)].groupby("session_date")[netc].mean()
    for nm in ("max21↓", "dvol↓"):
        o = tops[(nm, 3)].groupby("session_date")[netc].mean()
        j = pd.concat([s1, o], axis=1, join="inner")
        r = summ(j.iloc[:, 0] - j.iloc[:, 1])
        store[(tag, "pair", nm)] = r
        L.append(row(f"S1 − {nm}", r))
    L.append("")
    L.append("매칭 대조 커버리지(top-3 픽 중 대조 확보): " + ", ".join(
        f"{nm} {store[(tag, nm, 3, 'ctrl_cov')]*100:.1f}%" for nm in ("S1", "max21↓", "dvol↓")))
    store[(tag, "tops")] = tops
    store[(tag, "sc")] = sc
    return L


def stability(store: dict, tag: str) -> list[str]:
    tops = store[(tag, "tops")]
    pk = tops[("S1", 3)]
    L = ["| 구간 | 일수 | −풀 | −매칭대조 (t) | −IWM 실청산창 (t) |", "|---|---|---|---|---|"]
    ser = {v: daily(pk, v) for v in ("x_pool", "x_ctrl", "x_iwm")}

    def seg(name, mask_fn):
        parts = {v: s[[mask_fn(d) for d in s.index]] for v, s in ser.items()}
        a, b, c = (tstat(parts[v].to_numpy()) for v in ("x_pool", "x_ctrl", "x_iwm"))
        L.append(f"| {name} | {a[2]} | {a[0]:+.3f} ({a[1]:+.2f}) | {b[0]:+.3f} ({b[1]:+.2f}) | {c[0]:+.3f} ({c[1]:+.2f}) |")

    seg(f"전반 <{MID_ORIG}", lambda d: d < MID_ORIG)
    seg(f"후반 ≥{MID_ORIG}", lambda d: d >= MID_ORIG)
    days = sorted(ser["x_pool"].index)
    qs = sorted({f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}" for d in days})
    for q in qs:
        seg(q, lambda d, q=q: f"{d[:4]}Q{(int(d[5:7]) - 1) // 3 + 1}" == q)
    seg(f"교재 갱신기 <{FROZEN}", lambda d: d < FROZEN)
    seg(f"교재 동결 후 ≥{FROZEN}", lambda d: d >= FROZEN)
    L += ["", "| 월 | 일수 | −풀 | −매칭대조 | −IWM 실청산창 |", "|---|---|---|---|---|"]
    pos = defaultdict(int)
    months = sorted({d[:7] for d in days})
    for mth in months:
        vals = []
        for v in ("x_pool", "x_ctrl", "x_iwm"):
            s = ser[v][[d[:7] == mth for d in ser[v].index]]
            vals.append(s.mean())
            pos[v] += int(s.mean() > 0)
        n = int(sum(d[:7] == mth for d in days))
        L.append(f"| {mth} | {n} | {vals[0]:+.3f} | {vals[1]:+.3f} | {vals[2]:+.3f} |")
    L.append(f"| **양수 월 비율** | {len(months)}개월 | {pos['x_pool']}/{len(months)} | {pos['x_ctrl']}/{len(months)} | "
             f"{pos['x_iwm']}/{len(months)} |")
    return L


def capacity(store: dict, tag: str, sc_all: pd.DataFrame) -> list[str]:
    pk = store[(tag, "tops")][("S1", 3)]
    part = ORDER_USD / (pk["dvol"] * 1e6) * 100
    L = ["| 항목 | S1 top-3 픽 | 풀 전체 |", "|---|---|---|"]
    L.append(f"| 픽 수 / 일수 | {len(pk)} / {pk['session_date'].nunique()} | {len(sc_all)} |")
    L.append(f"| 신호일 거래대금 최소 / 중앙($M) | {pk['dvol'].min():.1f} / {pk['dvol'].median():.1f} | "
             f"{sc_all['dvol'].min():.1f} / {sc_all['dvol'].median():.1f} |")
    L.append(f"| 주문 ${ORDER_USD:.0f} 참여율 최대 / 중앙 | {part.max():.5f}% / {part.median():.6f}% | - |")
    L.append(f"| 참여율 ≤1% 충족 비율 | {(part <= 1).mean()*100:.1f}% | - |")
    L.append(f"| 가격 < $5 | {(pk['price'] < 5).mean()*100:.1f}% | {(sc_all['price'] < 5).mean()*100:.1f}% |")
    L.append(f"| 가격 < $10 | {(pk['price'] < 10).mean()*100:.1f}% | {(sc_all['price'] < 10).mean()*100:.1f}% |")
    L.append(f"| atr_pct > 8 | {(pk['atr_pct'] > 8).mean()*100:.1f}% | {(sc_all['atr_pct'] > 8).mean()*100:.1f}% |")
    L.append(f"| 스트레스 조건(가격<$10 or ATR>8) | {pk['stress'].mean()*100:.1f}% | {sc_all['stress'].mean()*100:.1f}% |")
    L.append(f"| 라이브 밴드(dvol 100~500M) 안 | {pk['dvol'].between(100, 500).mean()*100:.1f}% | "
             f"{sc_all['dvol'].between(100, 500).mean()*100:.1f}% |")
    L.append(f"| 가격 중앙 / 10분위 | ${pk['price'].median():.2f} / ${pk['price'].quantile(.1):.2f} | "
             f"${sc_all['price'].median():.2f} / ${sc_all['price'].quantile(.1):.2f} |")
    L.append(f"| ATR 중앙 | {pk['atr_pct'].median():.2f}% | {sc_all['atr_pct'].median():.2f}% |")
    return L


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-month", default=None)
    ap.add_argument("--reuse", action="store_true", help="저장된 scored.csv 재사용(모델 재학습 생략)")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / "scored.csv"
    L: list[str] = []

    pool, open_days = load_pool()
    print(f"풀 CLOSED {len(pool)}행 (원 보고서 35,288) · OPEN 포함 세션 {len(open_days)}")
    if a.reuse and cache.exists():
        sc = pd.read_csv(cache, float_precision="round_trip")
    else:
        seeds = [int(s) for s in json.loads(POLICY.read_text(encoding="utf-8")).get("seeds", [20260710])]
        df = attach_features(pool)
        sessions = sorted(df["session_date"].unique())
        sidx = {d: i for i, d in enumerate(sessions)}
        df["si"] = df["session_date"].map(sidx)
        df["month"] = df["session_date"].str[:7]
        months = sorted(df["month"].unique())
        test_months = [a.dry_month] if a.dry_month else months[MIN_TRAIN_MONTHS:]
        feats_ext = YAHOO_FEATURES + POOL_EXTRA
        scored = []
        for w, mth in enumerate(months[MIN_TRAIN_MONTHS:]):
            if mth not in test_months:
                continue
            test = df[(df["month"] == mth) & ~df["session_date"].isin(open_days)].copy()
            if test.empty:
                continue
            f = int(test["si"].min())
            train = df[df["si"] <= f - PURGE]
            test["m_ext"] = fit_score(train, test, feats_ext, seeds, w)   # w = 원 스크립트와 같은 월 인덱스
            print(f"  {mth}: 학습 {len(train)} 테스트 {len(test)}행 {test['session_date'].nunique()}일", flush=True)
            scored.append(test)
        sc = pd.concat(scored, ignore_index=True)
        keep = ["session_date", "ticker", "net", "mfe", "dvol", "max21", "dvol_rank", "atr_pct", "m_ext"]
        sc = sc[keep]
        if not a.dry_month:
            sc.to_csv(cache, index=False)

    # 1. 재현
    rep = evaluate(sc, "m_ext")
    m, t, n, h1, h2 = rep["top3"]
    m1, t1, n1, a1, b1 = rep["top1"]
    L += ["## 1. 재현", "",
          f"풀 CLOSED {len(pool)}행 · 테스트 {sc['session_date'].min()}~{sc['session_date'].max()} · "
          f"{rep['days']}일 · {rep['trades']}건 · 반기 경계 {rep['mid']}", "",
          "| 지표 | 원 보고서 | 재현 |", "|---|---|---|",
          f"| S1 top-3 − 풀 | +0.995 (t 3.09) | {m:+.3f} (t {t:+.2f}) 반기 {h1:+.3f}/{h2:+.3f} |",
          f"| S1 top-1 − 풀 | +1.096 (t 1.71) | {m1:+.3f} (t {t1:+.2f}) 반기 {a1:+.3f}/{b1:+.3f} |", ""]
    print("\n".join(L))
    if a.dry_month:
        return 0

    # 부가 필드
    ex = load_extra()
    sc = sc.merge(ex, on=["session_date", "ticker"], how="left")
    mism = int(((sc["exit_why"] != sc["grid_why"]) & sc["grid_why"].notna()).sum())
    bars = iwm_bars()
    sc["iwm_exit"] = [iwm_ret(bars, d0, d1) for d0, d1 in zip(sc["session_date"], sc["exit_date"])]
    sc["iwm_d7"] = [iwm_ret(bars, d0, d1) for d0, d1 in zip(sc["session_date"], sc["d7_date"])]
    # 매칭 구간(전체 CLOSED 풀 경계 — 신호봉 atr_pct는 attach_features에서만 나오므로 테스트 행 + 학습용 전 풀로 계산)
    full = attach_features(pool)[["session_date", "ticker", "atr_pct", "dvol"]]
    ae = np.nanquantile(full["atr_pct"], np.linspace(0, 1, 11))
    de = np.nanquantile(full["dvol"], [0, 1 / 3, 2 / 3, 1])
    sc["atr_dec"] = np.where(sc["atr_pct"].notna(), np.clip(np.searchsorted(ae, sc["atr_pct"], side="right") - 1, 0, 9), np.nan)
    sc["dvol_ter"] = np.clip(np.searchsorted(de, sc["dvol"], side="right") - 1, 0, 2)
    sc["stress"] = (sc["price"] < 10) | (sc["atr_pct"] > 8)
    for c in (0.3, 0.6):
        sc[f"net_s{c}"] = sc["net"] - c * sc["stress"].astype(float)
    L += [f"청산일 복원: 사유 불일치(격자 대비) {mism}건 / {len(sc)} · IWM 실청산창 결측 {int(sc['iwm_exit'].isna().sum())} · "
          f"고정7세션창 결측 {int(sc['iwm_d7'].isna().sum())}",
          f"ATR 10분위 경계: {np.round(ae, 2).tolist()} · dvol 3분위 경계($M): {np.round(de, 1).tolist()}", ""]

    store: dict = {}
    L += ["## 2. 재측정 (비용 0.5%p 포함 net)", ""] + measure(sc, "net", "기본", store) + [""]
    L += ["## 3. 슬리피지 스트레스", "",
          f"조건 해당: 풀 {sc['stress'].mean()*100:.1f}% · S1 top-3 픽 {store[('기본', 'tops')][('S1', 3)]['stress'].mean()*100:.1f}% "
          f"· max21 top-3 픽 {store[('기본', 'tops')][('max21↓', 3)]['stress'].mean()*100:.1f}%", ""]
    for c in (0.3, 0.6):
        L += measure(sc, f"net_s{c}", f"+{c}% 스트레스", store) + [""]
    L += ["## 4. 안정성 (S1 top-3, 기본 비용)", ""] + stability(store, "기본") + [""]
    L += ["## 4b. 안정성 (S1 top-3, +0.6% 스트레스)", ""] + stability(store, "+0.6% 스트레스") + [""]
    L += ["## 5. 용량·현실성", ""] + capacity(store, "기본", sc) + [""]

    # 판정
    c1 = store[("기본", "S1", 3, "x_ctrl")]
    c2 = store[("기본", "pair", "max21↓")]
    c3 = store[("기본", "S1", 3, "x_iwm")]
    s3 = store[("+0.6% 스트레스", "S1", 3, "x_iwm")]
    s1 = store[("+0.6% 스트레스", "S1", 3, "x_ctrl")]
    legs = {"① 매칭대조 t≥3": c1["t"] >= 3, "② max21 쌍대 >0": c2["mean"] > 0, "③ IWM 초과 >0": c3["mean"] > 0,
            "④ 0.6% 스트레스(IWM>0·매칭 t≥3)": (s3["mean"] > 0) and (s1["t"] >= 3)}
    if all(legs.values()):
        verdict = "통과"
    elif c1["t"] >= 2 and c1["h1"] > 0 and c1["h2"] > 0 and c3["mean"] > 0:
        verdict = "후보"
    else:
        verdict = "기각"
    L += ["## 판정 (사전 등록)", "", "| 조건 | 값 | 충족 |", "|---|---|---|",
          f"| ① S1 top-3 − 매칭대조 t ≥ 3 | {c1['mean']:+.3f} (t {c1['t']:+.2f}) | {legs['① 매칭대조 t≥3']} |",
          f"| ② S1 − max21 top-3 쌍대 > 0 | {c2['mean']:+.3f} (t {c2['t']:+.2f}) | {legs['② max21 쌍대 >0']} |",
          f"| ③ S1 top-3 − IWM(실청산창) > 0 | {c3['mean']:+.3f} (t {c3['t']:+.2f}) | {legs['③ IWM 초과 >0']} |",
          f"| ④ +0.6%: IWM > 0 · 매칭 t ≥ 3 | IWM {s3['mean']:+.3f} (t {s3['t']:+.2f}) · 매칭 {s1['mean']:+.3f} (t {s1['t']:+.2f}) | "
          f"{legs['④ 0.6% 스트레스(IWM>0·매칭 t≥3)']} |", "", f"**판정: {verdict}**"]
    text = "\n".join(L)
    (OUT / "results.md").write_text(text, encoding="utf-8")
    flat = {" | ".join(map(str, k)): v for k, v in store.items() if isinstance(v, float) or (isinstance(v, dict) and "mean" in v)}
    (OUT / "results.json").write_text(json.dumps({"verdict": verdict, "metrics": flat}, ensure_ascii=False, indent=1,
                                                 default=float), encoding="utf-8")
    store[("기본", "tops")][("S1", 3)].to_csv(OUT / "s1_top3_picks.csv", index=False)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
