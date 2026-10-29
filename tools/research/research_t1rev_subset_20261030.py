# -*- coding: utf-8 -*-
"""T1-rev 본검정 (사전등록 docs/reports/preregistration_q4_2026_20261022.md §2 + 개정 1·2, 2026-10-30).

표본: T1 I1 선정 이벤트 2017~2026 ∩ 국내 발행인 ∩ 매출 SUE 산출 가능.
매출 SUE = (분기 매출 − 전년 동분기) / 직전 8개 분기 YoY 변화 표준편차. 분기 값은 원 공시(최소 filed), Q4 = FY − 9M YTD.
같은 방향 = sign(매출 SUE) == sign(EPS sue1). 지표 = gA20 − 0.005(IWM 대비 net).
주 판정 = 주간 (같은 방향 평균 − 반대 방향 평균) 계열의 NW t(lag 4). 합격 = t ≥ 2.64 AND 2017~21·2022~26 같은 부호 AND 같은 방향 ≥ 연 25건.

usage: research_t1rev_subset_20261030.py <trades_I1.csv.gz> <i1_filer.csv.gz> <table.pkl.gz> <cf_dir> <out_dir>
"""
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
TR, FILER, TABLE, CF, OUT = [Path(a) for a in sys.argv[1:6]]
OUT.mkdir(parents=True, exist_ok=True)
COST = 0.005
FORMS = ("10-Q", "10-K", "10-Q/A", "10-K/A")
_cf = {}


def cf(cik):
    if cik not in _cf:
        fp = CF / f"{cik}.json"
        _cf[cik] = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
    return _cf[cik]


def dur(e):
    return (date.fromisoformat(e["end"]) - date.fromisoformat(e["start"])).days if e.get("start") and e.get("end") else None


def quarter_series(entries, before: date):
    """한 태그의 분기 계열 {end: (val, filed)} — 각 분기는 원 공시(최소 filed), filed < before 만. Q4는 FY − 9M YTD."""
    ents = [e for e in entries if e.get("form") in FORMS and e.get("val") is not None and e.get("filed")
            and date.fromisoformat(e["filed"]) < before]
    q = {}
    for e in ents:
        if 80 <= (dur(e) or 0) <= 100:
            k, f = e["end"], date.fromisoformat(e["filed"])
            if k not in q or f < q[k][1]:
                q[k] = (float(e["val"]), f)
    fys = {}
    for e in ents:
        if 350 <= (dur(e) or 0) <= 380:
            k, f = (e["start"], e["end"]), date.fromisoformat(e["filed"])
            if k not in fys or f < fys[k][1]:
                fys[k] = (float(e["val"]), f)
    for (st_, en), (v, f) in fys.items():
        if en in q:
            continue
        ytd = [e for e in ents if e.get("start") == st_ and 260 <= (dur(e) or 0) <= 285]
        if ytd:
            y0 = min(ytd, key=lambda x: x["filed"])
            q[en] = (v - float(y0["val"]), max(f, date.fromisoformat(y0["filed"])))
    return q


def current_quarter(j, D):
    """이벤트일 D의 해당 분기(tag, end, val) — 게이트와 같은 규칙: end ∈ [D−100, D−10], filed ≥ D−3, 최소 filed."""
    lo, hi, fmin = D - timedelta(100), D - timedelta(10), D - timedelta(3)
    best = None
    for t, es in j["tags"].items():
        for e in es:
            if e.get("form") not in FORMS or e.get("val") is None or not e.get("filed") or not e.get("end"):
                continue
            end, f = date.fromisoformat(e["end"]), date.fromisoformat(e["filed"])
            if not (lo <= end <= hi and f >= fmin):
                continue
            d = dur(e) or 0
            if 80 <= d <= 100 or 350 <= d <= 380:
                key = (f, 0 if d <= 100 else 1, t)
                if best is None or key < best[0]:
                    best = (key, t, e["end"])
    if best is None:
        return None
    _, t, end = best
    q = quarter_series(j["tags"][t], D + timedelta(400))     # 해당 분기 값 자체는 발표 직후 공시(게이트가 일치 확인)
    if end not in q:
        return None
    return t, end, q[end][0]


def near(series, target: date, tol=20):
    best = None
    for k, (v, _) in series.items():
        dd = abs((date.fromisoformat(k) - target).days)
        if dd <= tol and (best is None or dd < best[0]):
            best = (dd, v)
    return None if best is None else best[1]


def rev_sue(cik, D):
    j = cf(cik)
    if not j or not j.get("tags"):
        return None, "cf_empty"
    cur = current_quarter(j, D)
    if cur is None:
        return None, "no_cur"
    t, end, val = cur
    hist = quarter_series(j["tags"][t], D)               # 과거 분기는 D 이전 공시분만(PIT)
    e0 = date.fromisoformat(end)
    ly = near(hist, e0 - timedelta(365))
    if ly is None:
        return None, "no_ly"
    chg = []
    for k in range(1, 9):
        ek = e0 - timedelta(91 * k)
        a, b = near(hist, ek), near(hist, ek - timedelta(365))
        if a is None or b is None:
            return None, "short_hist"
        chg.append(a - b)
    sd = float(np.std(chg, ddof=1))
    if not sd > 0:
        return None, "sd0"
    return (val - ly) / sd, "ok"


def nw_t(x, lag=4):
    x = np.asarray(x, float)
    n = len(x)
    if n < 10:
        return float("nan")
    m = x.mean()
    u = x - m
    g0 = (u @ u) / n
    s = g0
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(m / math.sqrt(s / n)) if s > 0 else float("nan")


tr = pd.read_csv(TR)
fl = pd.read_csv(FILER)[["ticker", "date", "filer", "cik"]]
tb = pd.read_pickle(TABLE)[["ticker", "date", "sue1"]]
for d in (tr, fl, tb):
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d")
x = tr.merge(fl, on=["ticker", "date"], how="left").merge(tb.drop_duplicates(["ticker", "date"]), on=["ticker", "date"], how="left")
print("I1", len(x), "filer", x.filer.value_counts().to_dict())
x = x[x.filer == "domestic"].copy()
st, why = [], []
for r in x.itertuples():
    v, w = rev_sue(int(r.cik), date.fromisoformat(r.date)) if r.cik == r.cik else (None, "no_cik")
    st.append(v); why.append(w)
x["rev_sue"], x["rev_status"] = st, why
print("domestic", len(x), "rev status", x.rev_status.value_counts().to_dict())
y = x[x.rev_status.eq("ok") & x.sue1.notna() & (x.sue1 != 0) & (x.rev_sue != 0)].copy()
y["same"] = np.sign(y.rev_sue) == np.sign(y.sue1)
y["net"] = y.gA20 - COST
y["wk"] = pd.to_datetime(y.entry_date).dt.to_period("W-SUN").astype(str)
y["year"] = pd.to_datetime(y.entry_date).dt.year
y.to_csv(OUT / "subset_events.csv", index=False)


def stats(d):
    g = d.groupby(["wk", "same"]).net.mean().unstack()
    g = g.dropna()
    diff = (g[True] - g[False]) if len(g) else pd.Series(dtype=float)
    return {"n": int(len(d)), "n_same": int(d.same.sum()), "n_opp": int((~d.same).sum()),
            "mean_same": round(float(d[d.same].net.mean()), 5) if d.same.any() else None,
            "mean_opp": round(float(d[~d.same].net.mean()), 5) if (~d.same).any() else None,
            "diff_event": round(float(d[d.same].net.mean() - d[~d.same].net.mean()), 5) if d.same.any() and (~d.same).any() else None,
            "weeks_both": int(len(diff)), "diff_weekly_mean": round(float(diff.mean()), 5) if len(diff) else None,
            "nw_t": round(nw_t(diff.values), 3) if len(diff) else None}


# 보조: 주 클러스터 회귀 t
def cluster_t(d):
    X = np.column_stack([np.ones(len(d)), d.same.astype(float).values])
    yv = d.net.values
    b = np.linalg.lstsq(X, yv, rcond=None)[0]
    e = yv - X @ b
    XtX = np.linalg.inv(X.T @ X)
    meat = np.zeros((2, 2))
    for _, idx in d.groupby("wk").indices.items():
        s = X[idx].T @ e[idx]
        meat += np.outer(s, s)
    V = XtX @ meat @ XtX
    return round(float(b[1] / math.sqrt(V[1, 1])), 3), round(float(b[1]), 5)


res = {"all": stats(y), "early_2017_2021": stats(y[y.year <= 2021]), "late_2022_2026": stats(y[y.year >= 2022]),
       "by_year": {int(k): stats(g) for k, g in y.groupby("year")}}
res["cluster_t"], res["cluster_coef"] = cluster_t(y)
years = 9 + 10 / 12
res["same_per_year"] = round(res["all"]["n_same"] / years, 1)
a, e1, e2 = res["all"], res["early_2017_2021"], res["late_2022_2026"]
c1 = a["nw_t"] is not None and a["nw_t"] >= 2.64
c2 = (e1["diff_weekly_mean"] or 0) * (e2["diff_weekly_mean"] or 0) > 0 and (a["diff_weekly_mean"] or 0) * (e1["diff_weekly_mean"] or 0) > 0
c3 = res["same_per_year"] >= 25
res["criteria"] = {"t_ge_2.64": c1, "two_halves_same_sign": c2, "same_ge_25_per_year": c3}
res["verdict"] = "통과" if (c1 and c2 and c3) else "기각"
(OUT / "subset_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps({k: res[k] for k in ("all", "early_2017_2021", "late_2022_2026", "cluster_t", "cluster_coef",
                                      "same_per_year", "criteria", "verdict")}, ensure_ascii=False, indent=1))
print("by_year diff", {k: v["diff_event"] for k, v in res["by_year"].items()})
