#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""US 시장 급락일 매수 — 견고성 3종 (2026-09-07 밤): ① breadth 문턱 단조성(K=5·전량, hold_d10/tp20/main) ② 낙폭 계단(≤−3/≤−5/≤−7)
③ 같은 신호일에 지수 ETF(SPY·QQQ·IWM·TQQQ) 다음 시가 매수 → 10일 보유(개별주 선택이 필요한가, ETF로 충분한가). 비용 US 0.50."""
from __future__ import annotations

import csv
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
US_DIR = ROOT / "data" / "price" / "us"
OUT = ROOT / "data" / "analysis" / "panic_day_robust.json"
SPLIT = "2026-01-01"
FEE = 0.50


def cell(vals):
    if not vals:
        return {"n": 0}
    by = defaultdict(list)
    for v, s in vals:
        by[s].append(v)
    means = [st.mean(x) for x in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 4 and st.stdev(means) > 0 else None
    top2 = sorted(means, reverse=True)[:2]
    return {"n": len(vals), "sessions": len(by), "mean": round(st.mean(means), 2), "win_pct": round(100.0 * sum(1 for v, _ in vals if v > 0) / len(vals), 1), "t": t,
            "ex_top2": round((sum(means) - sum(top2)) / max(1, len(means) - 2), 2) if len(means) > 2 else None, "worst": round(min(means), 2)}


def fmt(c):
    return f"n={c['n']:5d} 세션={c['sessions']:3d} 세션평균 {c['mean']:+6.2f} 승 {c['win_pct']:5.1f}% t {c['t']} 상위2제외 {c['ex_top2']} 최악 {c['worst']}" if c.get("n") else "n=0"


def load_bars(t):
    rows = []
    p = US_DIR / f"us_{t}.csv"
    if not p.exists():
        return rows
    with p.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    pass
    return sorted(rows)


def main():
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    immature = {r[0] for r in con.execute("SELECT DISTINCT session_date FROM trades WHERE status='OPEN' AND strategy_id='xus_fallen3'")}
    rows = []
    for sd, tk, net, meta in con.execute("SELECT session_date, ticker, net_pct, meta FROM trades WHERE status='CLOSED' AND strategy_id='xus_fallen3'"):
        if sd in immature:
            continue
        m = json.loads(meta); f = m.get("feat") or {}; g = m.get("grid") or {}
        rows.append({"s": sd, "signal": m.get("signal_date"), "main": float(net), "grid": {k: v[0] for k, v in g.items()}, "chg": f.get("chg"), "dvol": f.get("dvol") or 0,
                     "breadth": (m.get("regime") or {}).get("breadth_down_pct")})
    con.close()
    rows = [r for r in rows if r["breadth"] is not None]
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    by_s = defaultdict(list)
    for r in rows:
        by_s[r["s"]].append(r)
    print("① breadth 문턱 단조성 (K=5 거래대금 큰순 / 전량)")
    out["monotonic"] = {}
    for B in (50, 55, 60, 65, 70, 73.5, 77, 80, 85):
        sel_s = {s: [r for r in rs if r["breadth"] >= B and r["chg"] is not None and r["chg"] <= -3] for s, rs in by_s.items()}
        sel_s = {s: rs for s, rs in sel_s.items() if rs}
        line = {}
        for c in ("main", "hold_d10", "tp20_sl25_d10"):
            k5 = [x for rs in sel_s.values() for x in sorted(rs, key=lambda r: -r["dvol"])[:5]]
            allr = [x for rs in sel_s.values() for x in rs]
            v5 = [((r["main"] if c == "main" else r["grid"].get(c)), r["s"]) for r in k5]; v5 = [(v, s) for v, s in v5 if v is not None]
            va = [((r["main"] if c == "main" else r["grid"].get(c)), r["s"]) for r in allr]; va = [(v, s) for v, s in va if v is not None]
            line[c] = {"K5": cell(v5), "all": cell(va)}
        out["monotonic"][B] = line
        print(f"  ≥{B:5}% 세션 {len(sel_s):3d} | main K5 {line['main']['K5'].get('mean')} t{line['main']['K5'].get('t')} | hold_d10 K5 {line['hold_d10']['K5'].get('mean')} t{line['hold_d10']['K5'].get('t')} 전량 {line['hold_d10']['all'].get('mean')} t{line['hold_d10']['all'].get('t')} | tp20 K5 {line['tp20_sl25_d10']['K5'].get('mean')} t{line['tp20_sl25_d10']['K5'].get('t')}")
    print("② 낙폭 계단 (breadth ≥ 73.5, hold_d10)")
    out["chg_ladder"] = {}
    for lo, hi, lab in ((-99, -7, "≤−7"), (-7, -5, "−7~−5"), (-5, -3, "−5~−3")):
        sel = [r for r in rows if r["breadth"] >= 73.5 and r["chg"] is not None and lo <= r["chg"] < hi and "hold_d10" in r["grid"]]
        by = defaultdict(list)
        for r in sel:
            by[r["s"]].append(r)
        k5 = [x for rs in by.values() for x in sorted(rs, key=lambda r: -r["dvol"])[:5]]
        c_all = cell([(r["grid"]["hold_d10"], r["s"]) for r in sel]); c5 = cell([(r["grid"]["hold_d10"], r["s"]) for r in k5])
        out["chg_ladder"][lab] = {"all": c_all, "K5": c5}
        print(f"  {lab:7s} 전량 {fmt(c_all)}\n          K5   {fmt(c5)}")
    print("③ 같은 날 지수 ETF 다음 시가 매수 → N일 보유 (비용 0.50)")
    sessions = sorted(s for s, rs in by_s.items() if any(r["breadth"] >= 73.5 for r in rs))
    out["etf"] = {}
    for etf in ("SPY", "QQQ", "IWM", "TQQQ"):
        b = load_bars(etf); idx = {x[0]: i for i, x in enumerate(b)}
        for hold in (5, 7, 10):
            vals = []
            for s in sessions:
                i = idx.get(s)
                if i is None or i + hold - 1 >= len(b) or b[i][1] <= 0:
                    continue
                vals.append((100.0 * (b[i + hold - 1][4] / b[i][1] - 1.0) - FEE, s))
            c = cell(vals); out["etf"][f"{etf}_d{hold}"] = c
            print(f"  {etf:5s} D{hold:2d} {fmt(c)}")
    out["sessions"] = sessions
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
