#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""알파 사냥 — 탐색 원장(15만 행·계약 격자)에서 H1 선택 → H2 검증으로 살아남는 (풀 × 조건 × 계약)을 찾는다 (2026-09-07 밤, 운영자 "찾을 때까지").

방법: 후보 셀 = 풀 × (단일 특성 10분위 | 두 특성 5분위 교차) × 계약(격자 10종). H1(2025-06~12)에서 세션 t ≥ 2 & n ≥ 60 & 평균 > 0.5%인 셀만
후보로 올리고, H2(2026-01~09)에서 세션 t ≥ 1.5 & 평균 > 0.3% & 상위2세션 제외 평균 > 0이면 생존. 생존 셀은 실행 가능성(세션당 건수·거래대금)까지 낸다.
출력: data/analysis/alpha_hunt.json + 콘솔(생존 셀만).
"""
from __future__ import annotations

import itertools
import json
import math
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "shadow" / "virtual_books.db"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
OUT = ROOT / "data" / "analysis" / "alpha_hunt.json"
SPLIT = "2026-01-01"
FEATS = ("chg", "dvol", "max21", "ibs", "from_high20", "rv20", "ma20_disc", "cum5", "vol_spike", "gap", "mom20", "down_streak", "rank_dvol", "breadth", "idx_ret20", "hi_break_n")
H1_MIN_N, H1_T, H1_MEAN = 60, 2.0, 0.5
H2_T, H2_MEAN = 1.5, 0.3


def cell(vals):
    """vals = [(net, session)]"""
    if not vals:
        return {"n": 0}
    by = defaultdict(list)
    for v, s in vals:
        by[s].append(v)
    means = [st.mean(x) for x in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 5 and st.stdev(means) > 0 else None
    top2 = sorted(means, reverse=True)[:2]
    nets = [v for v, _ in vals]
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1),
            "t": t, "mean_ex_top2": round((sum(means) - sum(top2)) / max(1, len(means) - 2), 3) if len(means) > 2 else None,
            "per_session": round(len(nets) / len(by), 1)}


def load():
    sm = json.loads(SECTOR_MAP.read_text(encoding="utf-8")) if SECTOR_MAP.exists() else {}
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    immature = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT strategy_id, session_date FROM trades WHERE status='OPEN' AND strategy_id LIKE 'x%'")}
    rows = []
    for sid, sd, tk, net, meta in con.execute("SELECT strategy_id, session_date, ticker, net_pct, meta FROM trades WHERE status='CLOSED' AND strategy_id LIKE 'x%'"):
        if (sid, sd) in immature:
            continue
        m = json.loads(meta); f = m.get("feat") or {}; g = m.get("grid") or {}
        if not g:
            continue
        mk = "KR" if sid.startswith("xkr") else "US"
        r = {"pool": sid, "s": sd, "t": tk, "half": "H1" if sd < SPLIT else "H2", "main": float(net),
             "sector": (sm.get(mk) or {}).get(str(tk), {}).get("sector") or "(미분류)",
             "grid": {k: v[0] for k, v in g.items()}, "rank_dvol": (m.get("ranks") or {}).get("dvol_desc"),
             "breadth": (m.get("regime") or {}).get("breadth_down_pct"), "idx_ret20": (m.get("regime") or {}).get("idx_ret20")}
        for k in FEATS:
            if k not in r:
                r[k] = f.get(k)
        rows.append(r)
    con.close()
    return rows


def quantile_edges(vals, k):
    s = sorted(vals)
    return [s[int(len(s) * i / k)] for i in range(1, k)]


def bucketize(rows, key, k):
    vals = [r[key] for r in rows if r.get(key) is not None]
    if len(vals) < 100:
        return None
    edges = [-1e18] + quantile_edges(vals, k) + [1e18]
    return [(lo, hi) for lo, hi in zip(edges[:-1], edges[1:]) if lo != hi]


def sel(rows, cond):
    return [r for r in rows if cond(r)]


def hunt(rows):
    pools = sorted({r["pool"] for r in rows})
    contracts = sorted({k for r in rows for k in r["grid"]})
    survivors, tested = [], 0
    for pool in pools:
        pr = [r for r in rows if r["pool"] == pool]
        h1 = [r for r in pr if r["half"] == "H1"]; h2 = [r for r in pr if r["half"] == "H2"]
        if len(h1) < 300 or len(h2) < 300:
            continue
        conds = [("all", lambda r: True)]
        for key in FEATS:
            b = bucketize(h1, key, 10)
            if not b:
                continue
            for lo, hi in b:
                conds.append((f"{key}∈[{lo:.2f},{hi:.2f})", (lambda key, lo, hi: lambda r: r.get(key) is not None and lo <= r[key] < hi)(key, lo, hi)))
        # 두 특성 5분위 교차 (주요 축만)
        axes = ("chg", "dvol", "vol_spike", "ibs", "from_high20", "ma20_disc", "breadth", "rank_dvol")
        for a, b_ in itertools.combinations(axes, 2):
            ba, bb = bucketize(h1, a, 5), bucketize(h1, b_, 5)
            if not ba or not bb:
                continue
            for (lo1, hi1), (lo2, hi2) in itertools.product(ba, bb):
                conds.append((f"{a}∈[{lo1:.2f},{hi1:.2f}) & {b_}∈[{lo2:.2f},{hi2:.2f})",
                              (lambda a, lo1, hi1, b_, lo2, hi2: lambda r: r.get(a) is not None and r.get(b_) is not None and lo1 <= r[a] < hi1 and lo2 <= r[b_] < hi2)(a, lo1, hi1, b_, lo2, hi2)))
        for sec in {r["sector"] for r in h1}:
            conds.append((f"sector=={sec}", (lambda sec: lambda r: r["sector"] == sec)(sec)))
        for label, cond in conds:
            s1 = sel(h1, cond)
            if len(s1) < H1_MIN_N:
                continue
            s2 = sel(h2, cond)
            for c in contracts:
                tested += 1
                v1 = [(r["grid"][c], r["s"]) for r in s1 if c in r["grid"]]
                c1 = cell(v1)
                if c1.get("n", 0) < H1_MIN_N or c1["t"] is None or c1["t"] < H1_T or c1["mean"] < H1_MEAN:
                    continue
                v2 = [(r["grid"][c], r["s"]) for r in s2 if c in r["grid"]]
                c2 = cell(v2)
                if c2.get("n", 0) < 40 or c2["t"] is None or c2["t"] < H2_T or c2["mean"] < H2_MEAN or (c2["mean_ex_top2"] or -1) <= 0:
                    continue
                survivors.append({"pool": pool, "cond": label, "contract": c, "h1": c1, "h2": c2,
                                  "score": round(min(c1["t"], c2["t"]) * min(c1["mean"], c2["mean"]), 3)})
    survivors.sort(key=lambda x: -x["score"])
    return survivors, tested


def main() -> int:
    rows = load()
    print(f"rows {len(rows)}")
    surv, tested = hunt(rows)
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "rows": len(rows), "cells_tested": tested,
           "criteria": {"h1": [H1_MIN_N, H1_T, H1_MEAN], "h2": [40, H2_T, H2_MEAN, "ex_top2>0"]}, "survivors": surv}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"tested {tested} cells → survivors {len(surv)}")
    for x in surv[:40]:
        h1, h2 = x["h1"], x["h2"]
        print(f"{x['pool']:13s} {x['contract']:16s} {x['cond'][:60]:60s} H1 n={h1['n']:5d} {h1['mean']:+.2f} t{h1['t']} | H2 n={h2['n']:5d} {h2['mean']:+.2f} 승{h2['win_pct']} t{h2['t']} ex2 {h2['mean_ex_top2']} /일 {h2['per_session']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
