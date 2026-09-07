#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""알파 사냥 2 — 탐색 원장에 없는 조건을 CSV에서 직접 (2026-09-07 밤).
① 급락(≤−5%) 다음날 시가 갭 조건(09:00에 알 수 있는 정보) — 갭 계단별 시가 진입 TP12/SL25/D7
② 섹터 상대 급락: 종목 등락 − 섹터 등가중 등락(개별 악재 vs 업종 동반) 계단
③ 진입 시점: 다음날 시가 vs 다음날 종가(하루 더 기다림) vs 이틀 뒤 시가
④ 주도주 눌림: ret60 ≥ 30% & 종가 > MA50 & 20일 고점 대비 −10~−4% → 다음 시가, 계약 TP8/SL8/D7과 TP12/SL25/D7
전부 H1(2025-06~12)/H2(2026-01~09) 분리, 세션 t. 비용 KR 0.21 / US 0.50.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = {"US": ROOT / "data" / "price" / "us", "KR": ROOT / "data" / "price" / "kr"}
SECTOR_MAP = ROOT / "data" / "sector_map.json"
OUT = ROOT / "data" / "analysis" / "alpha_hunt_csv.json"
FEE = {"KR": 0.21, "US": 0.50}
DVOL_MIN = {"KR": 20.0, "US": 50.0}
START = "2025-06-02"
SPLIT = "2026-01-01"


def load(path):
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    pass
    return sorted(rows)


def contract(entry, win, fee, tp=12.0, sl=-25.0, hold=7, be=4.0, be_lock=True):
    if not win or entry <= 0:
        return None
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= tp:
            return tp - fee
        if cp <= sl:
            return cp - fee
        if be_lock and peak >= be and cp <= 0:
            return cp - fee
        peak = max(peak, hip)
    if len(win) < hold:
        return None
    return (win[hold - 1][4] - entry) / entry * 100.0 - fee


def cell(vals):
    if not vals:
        return {"n": 0}
    by = defaultdict(list)
    for v, s in vals:
        by[s].append(v)
    means = [st.mean(x) for x in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 5 and st.stdev(means) > 0 else None
    nets = [v for v, _ in vals]
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": t}


def split(vals):
    return {"all": cell(vals), "H1": cell([x for x in vals if x[1] < SPLIT]), "H2": cell([x for x in vals if x[1] >= SPLIT])}


def scan(market):
    sm = json.loads(SECTOR_MAP.read_text(encoding="utf-8")).get(market, {})
    sector_of = {t: str(v.get("sector") or "") for t, v in sm.items()}
    d = DIRS[market]; prefix = "us_" if market == "US" else "kr_"; scale = 1e6 if market == "US" else 1e8
    fee = FEE[market]; be = market == "US"
    bars = {}
    for p in sorted(d.glob(f"{prefix}*.csv")):
        b = load(p)
        if len(b) >= 80:
            bars[p.stem[len(prefix):]] = b
    # 섹터 등가중 일별 등락
    sec_day = defaultdict(lambda: defaultdict(list))
    for t, b in bars.items():
        sec = sector_of.get(t)
        if not sec:
            continue
        for i in range(1, len(b)):
            if b[i - 1][4] > 0 and b[i][0] >= START:
                sec_day[b[i][0]][sec].append(100.0 * (b[i][4] / b[i - 1][4] - 1.0))
    sec_ret = {dt: {s: st.mean(v) for s, v in m.items() if len(v) >= 3} for dt, m in sec_day.items()}
    res = {"gap": defaultdict(list), "sector_rel": defaultdict(list), "entry_timing": defaultdict(list), "leader_pb": defaultdict(list), "gap_x_sector": defaultdict(list)}
    for t, b in bars.items():
        for i in range(60, len(b) - 12):
            dt, o, h, l, c, v = b[i]
            if dt < START or b[i - 1][4] <= 0 or c <= 0:
                continue
            chg = 100.0 * (c / b[i - 1][4] - 1.0); dv = c * v / scale
            if dv < DVOL_MIN[market]:
                continue
            nxt = b[i + 1]
            if nxt[1] <= 0:
                continue
            if chg <= -5.0:
                gap = 100.0 * (nxt[1] / c - 1.0)
                gb = "≤−3" if gap <= -3 else "−3~−1" if gap <= -1 else "−1~+1" if gap < 1 else "+1~+3" if gap < 3 else "≥+3"
                r_open = contract(nxt[1], b[i + 1: i + 8], fee, be_lock=be)
                if r_open is not None:
                    res["gap"][gb].append((r_open, dt))
                    sec = sector_of.get(t); sr = sec_ret.get(dt, {}).get(sec) if sec else None
                    if sr is not None:
                        rel = chg - sr
                        rb = "개별(섹터 대비 ≤−4)" if rel <= -4 else "개별약(−4~−2)" if rel <= -2 else "동반(>−2)"
                        res["sector_rel"][rb].append((r_open, dt))
                        res["gap_x_sector"][f"{gb} & {rb}"].append((r_open, dt))
                    res["entry_timing"]["next_open"].append((r_open, dt))
                # 다음날 종가 진입(하루 대기)
                r_close = contract(nxt[4], b[i + 2: i + 9], fee, be_lock=be)
                if r_close is not None:
                    res["entry_timing"]["next_close"].append((r_close, dt))
                # 이틀 뒤 시가
                if i + 2 < len(b) and b[i + 2][1] > 0:
                    r_o2 = contract(b[i + 2][1], b[i + 2: i + 9], fee, be_lock=be)
                    if r_o2 is not None:
                        res["entry_timing"]["open_d2"].append((r_o2, dt))
            # 주도주 눌림
            ret60 = 100.0 * (c / b[i - 60][4] - 1.0) if b[i - 60][4] > 0 else None
            ma50 = st.mean(x[4] for x in b[i - 49: i + 1])
            hi20 = max(x[2] for x in b[i - 19: i + 1]); fh20 = 100.0 * (c / hi20 - 1.0)
            if ret60 is not None and ret60 >= 30 and c > ma50 and -10 <= fh20 <= -4:
                r8 = contract(nxt[1], b[i + 1: i + 8], fee, tp=8.0, sl=-8.0, be_lock=False)
                r12 = contract(nxt[1], b[i + 1: i + 8], fee, be_lock=be)
                if r8 is not None:
                    res["leader_pb"]["TP8/SL8"].append((r8, dt))
                if r12 is not None:
                    res["leader_pb"]["TP12/SL25"].append((r12, dt))
    return {k: {kk: split(vv) for kk, vv in v.items()} for k, v in res.items()}


def fmt(c):
    return f"n={c.get('n', 0):6d} s={c.get('sessions', 0):3d} {c.get('mean', 0):+6.2f}% 승{c.get('win_pct', 0):5.1f} t{c.get('t')}" if c.get("n") else "n=0"


def main() -> int:
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    for mk in ("KR", "US"):
        r = scan(mk); out[mk] = r
        print(f"\n##### {mk}")
        for fam, cells in r.items():
            print(f"[{fam}]")
            for k in sorted(cells):
                v = cells[k]
                print(f"  {k:28s} all {fmt(v['all'])} | H1 {fmt(v['H1'])} | H2 {fmt(v['H2'])}")
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
