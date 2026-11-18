"""T1 hi52 forward 검증 (사전등록 docs/reports/preregistration_t1_hi52_20261118.md) — 분석 전용, 원장 무변경.

backfill  : 11-18 이전 selected_I1 신호(obs.hi52 없음)의 hi52를 yfinance 수정 종가로 소급 → data/analysis/t1_hi52_forward/hi52_backfill.json
evaluate  : settle hold=20 결합 → low(≤0.486) − 비low 차, 20세션 블록 클러스터 t, 판정(합격/기각/관측)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data" / "shadow" / "us_earnings_t1_shadow.jsonl"
OUT = ROOT / "data" / "analysis" / "t1_hi52_forward"
CUT = 0.486
START = "2026-09-29"


def _rows():
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def _signals(rows):
    return [r for r in rows if r.get("kind") == "signal" and r.get("selected_I1") and r.get("session", "") >= START]


def hi52_from_closes(closes: pd.Series, entry: str) -> tuple[float | None, int]:
    c = closes[closes.index < pd.Timestamp(entry)].dropna().tail(252)
    if not len(c):
        return None, 0
    return float(c.iloc[-1] / c.max()), int(len(c))


def cmd_backfill() -> int:
    import yfinance as yf
    OUT.mkdir(parents=True, exist_ok=True)
    fp = OUT / "hi52_backfill.json"
    have = json.loads(fp.read_text()) if fp.exists() else {}
    todo = [s for s in _signals(_rows()) if "obs" not in s and f"{s['ticker']}|{s['session']}" not in have]
    for s in todo:
        start = (pd.Timestamp(s["session"]) - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
        h = yf.download(s["ticker"].replace(".", "-"), start=start, end=s["session"], auto_adjust=True, progress=False)
        if h is None or not len(h):
            have[f"{s['ticker']}|{s['session']}"] = {"hi52": None, "hi52_n": 0}; continue
        cl = h["Close"].iloc[:, 0] if isinstance(h["Close"], pd.DataFrame) else h["Close"]
        cl.index = pd.DatetimeIndex(cl.index.date)
        v, n = hi52_from_closes(cl, s["session"])
        have[f"{s['ticker']}|{s['session']}"] = {"hi52": v, "hi52_n": n}
    fp.write_text(json.dumps(have, indent=0))
    print(f"[hi52] 소급 {len(todo)}건 → 누계 {len(have)}")
    return 0


def block_t(diff_x: np.ndarray, blk: np.ndarray) -> float:
    """상수항 OLS의 블록 클러스터 t(= 평균 / 블록 합 잔차의 클러스터 SE)."""
    y = np.asarray(diff_x, float)
    u = y - y.mean()
    S = pd.Series(u).groupby(blk).sum()
    G = len(S)
    if G < 3:
        return float("nan")
    se = math.sqrt((S ** 2).sum() * G / (G - 1)) / len(y)
    return float(y.mean() / se) if se > 0 else float("nan")


def evaluate(rows) -> dict:
    bf = json.loads((OUT / "hi52_backfill.json").read_text()) if (OUT / "hi52_backfill.json").exists() else {}
    st = {(r["ticker"], r["session"]): r for r in rows if r.get("kind") == "settle" and r.get("hold") == 20
          and r.get("net_excess") is not None}
    recs = []
    for s in _signals(rows):
        h = (s.get("obs") or {}).get("hi52")
        if h is None:
            h = (bf.get(f"{s['ticker']}|{s['session']}") or {}).get("hi52")
        x = st.get((s["ticker"], s["session"]))
        if h is None or x is None:
            continue
        recs.append({"session": s["session"], "low": h <= CUT, "net": x["net_excess"], "top": bool(s.get("selected_I2")),
                     "iwm": x.get("iwm")})
    if not recs:
        return {"n": 0, "verdict": "관측(정산 0건)"}
    d = pd.DataFrame(recs)
    sess = sorted(d.session.unique())
    bdays = pd.bdate_range(sess[0], sess[-1])
    ordn = {str(x.date()): i for i, x in enumerate(bdays)}
    d["blk"] = d.session.map(lambda s: ordn.get(s, 0) // 20)
    lo, hi = d[d.low], d[~d.low]
    diff = lo.net.mean() - hi.net.mean() if len(lo) and len(hi) else float("nan")
    # 차이의 클러스터 t: 회귀 net ~ low 의 low 계수와 같다 → 두 집단 평균 차를 블록 클러스터로
    X = np.column_stack([np.ones(len(d)), d.low.astype(float)])
    y = d.net.values
    XtXi = np.linalg.pinv(X.T @ X); b = XtXi @ X.T @ y; u = y - X @ b
    meat = sum(np.outer(X[d.blk.values == g].T @ u[d.blk.values == g], X[d.blk.values == g].T @ u[d.blk.values == g])
               for g in d.blk.unique())
    G = d.blk.nunique()
    t = float(b[1] / math.sqrt((XtXi @ meat @ XtXi)[1, 1] * G / (G - 1))) if G > 2 else float("nan")
    n_low = int(len(lo))
    if n_low >= 50 and diff > 0 and t >= 2.0:
        verdict = "합격"
    elif (n_low >= 50 and diff <= 0) or (n_low >= 100 and t < 1.0):
        verdict = "기각"
    else:
        verdict = "관측"
    return {"n": int(len(d)), "n_low": n_low, "mean_low": float(lo.net.mean()) if n_low else None,
            "mean_rest": float(hi.net.mean()) if len(hi) else None, "diff": float(diff), "t_block": t,
            "median_diff": float(lo.net.median() - hi.net.median()) if n_low and len(hi) else None,
            "top_diff": float(d[d.top & d.low].net.mean() - d[d.top & ~d.low].net.mean()) if d.top.any() else None,
            "regime": regime_split(d),
            "verdict": verdict}


MKT_DN = -0.03   # 보유 중 IWM 시가→시가 수익률 이 값 미만 = 하락장(사후 표시 전용, 판정 기준 아님)


def regime_split(d: pd.DataFrame) -> dict:
    """11-18 운영자 '진행' — 판정은 불변, 원인 구분용 표시만: 보유 중 하락장(IWM < −3%) vs 평상시의 low−비low 차.
    역사 선택분(2017–26): 하락장 low +0.5% vs 비low +1.4%, 평상시 +7.0% vs +1.0%."""
    out = {}
    iw = pd.to_numeric(d["iwm"], errors="coerce") if "iwm" in d else pd.Series(np.nan, index=d.index)
    for name, m in (("하락장", iw < MKT_DN), ("평상시", iw >= MKT_DN), ("IWM 결측", iw.isna())):
        g = d[m.values]
        lo, hi = g[g.low], g[~g.low]
        out[name] = {"n": int(len(g)), "n_low": int(len(lo)),
                     "mean_low": float(lo.net.mean()) if len(lo) else None,
                     "mean_rest": float(hi.net.mean()) if len(hi) else None,
                     "diff": float(lo.net.mean() - hi.net.mean()) if len(lo) and len(hi) else None}
    return out


def cmd_evaluate() -> int:
    r = evaluate(_rows())
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["backfill", "evaluate"])
    a = ap.parse_args()
    sys.exit(cmd_backfill() if a.cmd == "backfill" else cmd_evaluate())
