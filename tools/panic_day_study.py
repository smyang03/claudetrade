#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""시장 급락일(breadth) × 급락 풀 전량 — alpha_hunt 생존 셀의 실전 재계산 (2026-09-07 밤).

셀: US 전일 ≤−3% 풀 × 신호일 하락 종목 비율(breadth_down_pct) ≥ B → 다음 세션 시가 매수. 계약 격자 전부(주계약 TP12/SL25/D7 포함).
자름: breadth 문턱(60/65/70/73.5/80), 세션 수, 세션당 후보, K=1/3/5(거래대금 큰순)·전량, 반기, 상위 2세션 제외, 낙폭 계단, KR 동일 셀.
실행 현실: 세션당 후보 수(전량은 불가) → K=1~5의 성적이 실전 성적이다.
"""
from __future__ import annotations

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
OUT = ROOT / "data" / "analysis" / "panic_day_study.json"
SPLIT = "2026-01-01"


def cell(vals):
    if not vals:
        return {"n": 0}
    by = defaultdict(list)
    for v, s in vals:
        by[s].append(v)
    means = [st.mean(x) for x in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 4 and st.stdev(means) > 0 else None
    top2 = sorted(means, reverse=True)[:2]
    nets = [v for v, _ in vals]
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 2), "session_mean": round(st.mean(means), 2), "median": round(st.median(nets), 2),
            "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": t,
            "ex_top2_session_mean": round((sum(means) - sum(top2)) / max(1, len(means) - 2), 2) if len(means) > 2 else None,
            "worst_session": round(min(means), 2), "per_session": round(len(nets) / len(by), 1)}


def load(pool):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    immature = {(r[0], r[1]) for r in con.execute("SELECT DISTINCT strategy_id, session_date FROM trades WHERE status='OPEN' AND strategy_id=?", (pool,))}
    rows = []
    for sd, tk, net, meta in con.execute("SELECT session_date, ticker, net_pct, meta FROM trades WHERE status='CLOSED' AND strategy_id=?", (pool,)):
        if (pool, sd) in immature:
            continue
        m = json.loads(meta); f = m.get("feat") or {}; g = m.get("grid") or {}
        rows.append({"s": sd, "t": tk, "main": float(net), "grid": {k: v[0] for k, v in g.items()}, "chg": f.get("chg"), "dvol": f.get("dvol"),
                     "breadth": (m.get("regime") or {}).get("breadth_down_pct"), "idx_ret20": (m.get("regime") or {}).get("idx_ret20"),
                     "rank": (m.get("ranks") or {}).get("dvol_desc"), "half": "H1" if sd < SPLIT else "H2", "signal": m.get("signal_date")})
    con.close()
    return rows


def fmt(c):
    return (f"n={c['n']:5d} 세션={c['sessions']:3d} 건평균 {c['mean']:+6.2f} 세션평균 {c['session_mean']:+6.2f} 승 {c['win_pct']:5.1f}% t {c['t']} "
            f"상위2제외 {c['ex_top2_session_mean']} 최악세션 {c['worst_session']} /일 {c['per_session']}") if c.get("n") else "n=0"


def analyze(pool, label):
    rows = [r for r in load(pool) if r["breadth"] is not None and r["chg"] is not None and r["chg"] <= -3]
    print(f"\n##### {label} ({pool}) rows={len(rows)}")
    res = {}
    for B in (60, 65, 70, 73.5, 80):
        sel = [r for r in rows if r["breadth"] >= B]
        by_s = defaultdict(list)
        for r in sel:
            by_s[r["s"]].append(r)
        res[B] = {}
        print(f"--- breadth ≥ {B}%  세션 {len(by_s)} (H1 {sum(1 for s in by_s if s < SPLIT)} / H2 {sum(1 for s in by_s if s >= SPLIT)})")
        for c in ("main", "tp12_sl25_d7_be", "tp12_sl25_d5_be", "tp20_sl25_d10", "tp12_sl25_d10", "hold_d5", "hold_d10", "tp8_sl10_d3"):
            for K in ("all", 1, 3, 5):
                if K == "all":
                    pick = sel
                else:
                    pick = [x for rs in by_s.values() for x in sorted(rs, key=lambda r: -(r["dvol"] or 0))[:K]]
                vals = [((r["main"] if c == "main" else r["grid"].get(c)), r["s"]) for r in pick]
                vals = [(v, s) for v, s in vals if v is not None]
                cc = cell(vals); h1 = cell([x for x in vals if x[1] < SPLIT]); h2 = cell([x for x in vals if x[1] >= SPLIT])
                res[B][f"{c}|K{K}"] = {"all": cc, "H1": h1, "H2": h2}
                if c in ("main", "hold_d10", "tp20_sl25_d10", "tp12_sl25_d10") and B in (70, 73.5):
                    print(f"  {c:16s} K={str(K):3s} 전체 {fmt(cc)}")
                    print(f"  {'':16s}       H1 {fmt(h1)}")
                    print(f"  {'':16s}       H2 {fmt(h2)}")
        # 세션 목록(어떤 날인가)
        if B == 73.5:
            sess = sorted(by_s)
            print("  세션(진입일):", ", ".join(sess))
            res[B]["sessions_list"] = sess
    return res


def main():
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    out["US"] = analyze("xus_fallen3", "US 급락 ≤−3% × 시장 급락일")
    out["KR"] = analyze("xkr_fallen3", "KR 급락 ≤−3% × 시장 급락일")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
