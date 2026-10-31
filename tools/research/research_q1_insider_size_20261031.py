# -*- coding: utf-8 -*-
"""2027 Q1 사전등록 검정 — US 내부자 군집(U5r_all) 시총 구간 차이 (docs/reports/preregistration_q1_2027_draft_20261031.md, 10-31 등록 e080392).

기준은 등록 문서 그대로다. 이 스크립트가 정한 구현 세부(등록 문서에 없던 것, 성과 보기 전 결정):
- AR63 스프레드가 없는 이벤트는 주 행에서 뺀다(비용을 정할 수 없다). 건수는 보고한다.
- 펀드 판정은 q1_fund_split.py와 같다(SIC 공란·0000 = 펀드, CIK는 Form 4 당시 issuerCik 우선).
- 차이 계열 = 진입일(t0)별 [소형 평균 − 중형 평균], 두 구간이 모두 있는 날만. NW lag 60.
수익 칼럼 정의(research_k1_us_smartmoney_20260929.py attach): ret60 = t0 시가 → t0+59 종가, sz60 = 신호일 dv20 십분위 평균 같은 창.
입력은 런타임 파일(data/analysis/…, 커밋 안 함). 출력 data/analysis/q1_insider_size_2027/q1_test_result.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "analysis" / "q1_insider_size_2027"
sys.path.insert(0, str(ROOT / "tools" / "research"))
from research_k1_us_smartmoney_20260929 import nw_t  # noqa: E402

BASE_COST = 0.0044
SPREAD_FLOOR = 0.0010
T_PASS = 2.64
LP = re.compile(r"(\bL\.?P\.?|PARTNERS,? LP)\s*$", re.I)


def classify(cik, sic):
    v = sic.get(str(int(cik))) if cik is not None and cik == cik else None
    if not v or "err" in v:
        return "미상"
    if LP.search(v.get("name") or ""):
        return "MLP"
    if (v.get("sic") or "") in ("", "0000"):
        return "펀드"
    if v.get("sic") == "6798":
        return "REIT"
    return "운영회사"


def load() -> pd.DataFrame:
    e = pd.read_csv(ROOT / "data/analysis/k1_us_20260929/events_U5r_all.csv.gz",
                    usecols=["ticker", "date", "t0", "ret60", "iwm60", "sz60"])
    s = pd.read_csv(D / "events_U5r_all_size_spread.csv.gz",
                    usecols=["ticker", "date", "mcap", "spread_ar63", "spread_cs21"]).drop_duplicates(["ticker", "date"])
    e = e.merge(s, on=["ticker", "date"], how="left")
    sic = json.loads((D / "sic.json").read_text(encoding="utf-8"))
    tc = json.loads((D / "ticker_cik.json").read_text(encoding="utf-8"))
    iss = json.loads((D / "issuer_cik_by_event.json").read_text(encoding="utf-8"))
    e["cls"] = [classify(iss.get(f"{t}|{d}") or tc.get(t), sic) for t, d in zip(e.ticker, e.date)]
    e["bucket"] = pd.cut(e.mcap, [0, 3e8, 2e9, 1e10, np.inf], right=False, labels=["S1", "S2", "M", "L"])
    e["size"] = e.bucket.map({"S1": "S", "S2": "S", "M": "M", "L": "L"})
    e["year"] = e.t0.str[:4].astype(int)
    return e


def excess(d: pd.DataFrame, spread: pd.Series, bench: str = "sz60") -> pd.Series:
    return d.ret60 - BASE_COST - np.maximum(spread, SPREAD_FLOOR) - d[bench]


def diff_series(d: pd.DataFrame, col: str) -> pd.Series:
    g = d.groupby(["t0", "size"])[col].mean().unstack()
    g = g.dropna(subset=["S", "M"]) if {"S", "M"} <= set(g.columns) else g.iloc[0:0]
    return (g["S"] - g["M"]).sort_index()


def summarize(x: pd.Series) -> dict:
    x = x.dropna()
    return {"n": int(len(x)), "mean_pct": round(float(x.mean()) * 100, 3) if len(x) else None,
            "nw_t": round(float(nw_t(x.values, 60)), 2) if len(x) > 5 else None}


def level(d: pd.DataFrame, col: str) -> dict:
    """구간 수준 — 진입일 코호트 평균, NW lag 60(기술 통계, 판정 제외)."""
    coh = d.dropna(subset=[col]).groupby("t0")[col].mean().sort_index()
    out = summarize(coh)
    out["events"] = int(d[col].notna().sum())
    return out


def main() -> int:
    e = load()
    res: dict = {"events_total": int(len(e)), "class_counts": e.cls.value_counts().to_dict()}
    d = e[e.cls != "펀드"].copy()
    cov = float(d.mcap.notna().mean())
    res["coverage_ex_fund"] = round(cov * 100, 2)
    d = d[d.mcap.notna()].copy()
    res["no_ar63_dropped"] = int(d.spread_ar63.isna().sum())
    d = d[d.spread_ar63.notna() & d.ret60.notna() & d.sz60.notna()].copy()
    d["x"] = excess(d, d.spread_ar63)
    d["x_iwm"] = excess(d, d.spread_ar63, "iwm60")
    d["x_cs"] = excess(d, d.spread_cs21.fillna(d.spread_ar63))
    d["x_ar2"] = excess(d, d.spread_ar63 * 2)
    cnt = {f"{b}_{p}": int(((d["size"] == b) & ((d.year <= 2019) if p == "IS" else (d.year >= 2020))).sum())
           for b in ("S", "M") for p in ("IS", "OOS")}
    res["bucket_counts"] = cnt
    untestable = []
    if cov < 0.85:
        untestable.append(f"커버리지 {cov:.1%} < 85%")
    untestable += [f"{k} {v}건 < 100" for k, v in cnt.items() if v < 100]
    res["untestable"] = untestable

    ds = diff_series(d, "x")
    main_ = summarize(ds)
    is_ = summarize(ds[ds.index.str[:4].astype(int) <= 2019])
    oos = summarize(ds[ds.index.str[:4].astype(int) >= 2020])
    small_iwm = level(d[d["size"] == "S"], "x_iwm")
    res["main"] = {"diff": main_, "IS": is_, "OOS": oos, "small_vs_iwm": small_iwm}
    conds = {"t>=2.64": bool(main_["nw_t"] is not None and main_["nw_t"] >= T_PASS),
             "IS>0 & OOS>0": bool((is_["mean_pct"] or 0) > 0 and (oos["mean_pct"] or 0) > 0),
             "small_vs_iwm>0": bool((small_iwm["mean_pct"] or 0) > 0)}
    res["conditions"] = conds
    res["verdict"] = "검정 불가" if untestable else ("통과" if all(conds.values()) else "기각")

    aux = {}
    for b in ("S1", "S2", "M", "L"):
        g = d[d.bucket == b]
        aux[b] = {"size_matched": level(g, "x"), "vs_iwm": level(g, "x_iwm"),
                  "ar63_median_pct": round(float(g.spread_ar63.median()) * 100, 2)}
    aux["diff_cost_cs21"] = summarize(diff_series(d, "x_cs"))
    aux["diff_cost_ar63x2"] = summarize(diff_series(d, "x_ar2"))
    aux["diff_by_year"] = {int(y): round(float(v) * 100, 2) for y, v in ds.groupby(ds.index.str[:4].astype(int)).mean().items()}
    res["aux"] = aux
    (D / "q1_test_result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
