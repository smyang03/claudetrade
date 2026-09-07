#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 급등 오버나이트(종가 매수 → 다음날 시가 매도) 견고성 — strategy_family_scan에서 나온 신호의 실전 후보 검증 (2026-09-07 밤).

관측: KR 전일 ≥+5% 종목의 종가→다음 시가 gross +0.90%(t 11), 비용 0.21% 후 +0.69%(t 8.6), 309세션. 장중(시가→종가)은 −0.82%.
여기서 자르는 것: 등락 계단(5~10/10~20/20~29/상한가≥29 — 상한가는 종가 매수 불가), 반기, 거래대금 계단, 국면(069500 MA20), 세션당 후보 수,
상위 2세션 제외, 갭 분포(꼬리), 그리고 실행 규약(15:20 이후 종가 근처 매수 가정 → 종가 대비 슬리피지 민감도 +0.1/+0.3%).
출력: data/analysis/kr_overnight_study.json + 콘솔. 판정 아님 — 실전 후보 구성용.
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
KR_DIR = ROOT / "data" / "price" / "kr"
OUT = ROOT / "data" / "analysis" / "kr_overnight_study.json"
FEE = 0.21
START = "2025-06-02"
DVOL_MIN = 20.0


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


def cell(pairs, slip=0.0):
    if not pairs:
        return {"n": 0}
    by = defaultdict(list)
    nets = []
    for r, s in pairs:
        v = r - FEE - slip; nets.append(v); by[s].append(v)
    means = [st.mean(v) for v in by.values()]
    t = round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2) if len(means) >= 5 and st.stdev(means) > 0 else None
    top2 = sorted(means, reverse=True)[:2]
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "median": round(st.median(nets), 3),
            "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": t,
            "per_session": round(len(nets) / len(by), 1),
            "mean_ex_top2_sessions": round((sum(means) - sum(top2)) / max(1, len(means) - 2), 3) if len(means) > 2 else None,
            "p10": round(sorted(nets)[int(len(nets) * 0.1)], 2), "p90": round(sorted(nets)[int(len(nets) * 0.9)], 2)}


def main() -> int:
    idx = load(KR_DIR / "kr_069500.csv")
    ma = {}
    for i in range(20, len(idx)):
        ma[idx[i][0]] = idx[i][4] < st.mean(x[4] for x in idx[i - 19: i + 1])   # True = MA20 아래
    rows = []
    for p in sorted(KR_DIR.glob("kr_*.csv")):
        b = load(p)
        t = p.stem[3:]
        for i in range(21, len(b) - 1):
            dt, o, h, l, c, v = b[i]
            pc = b[i - 1][4]
            if dt < START or pc <= 0 or o <= 0 or c < 1000:
                continue
            chg = 100.0 * (c / pc - 1.0)
            if chg < 5.0:
                continue
            dv = c * v / 1e8
            if dv < DVOL_MIN:
                continue
            n_o, n_c = b[i + 1][1], b[i + 1][4]
            if n_o <= 0:
                continue
            rows.append({"t": t, "s": dt, "chg": chg, "dvol": dv, "co": 100.0 * (n_o / c - 1.0), "oc": 100.0 * (n_c / n_o - 1.0),
                         "vol_spike": v / st.mean(x[5] for x in b[i - 20: i]) if st.mean(x[5] for x in b[i - 20: i]) > 0 else None,
                         "close_pos": (c - l) / (h - l) if h > l else None, "below_ma20": ma.get(dt),
                         "half": "H1" if dt < "2026-01-01" else "H2", "limit_up": chg >= 29.0})
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fee_rt_pct": FEE, "n": len(rows)}
    P = lambda sel: [(r["co"], r["s"]) for r in sel]
    base = [r for r in rows if not r["limit_up"]]
    res["all_ge5"] = cell(P(rows)); res["ex_limit_up"] = cell(P(base))
    res["by_chg"] = {lab: cell(P([r for r in base if lo <= r["chg"] < hi])) for lo, hi, lab in ((5, 7, "5~7"), (7, 10, "7~10"), (10, 15, "10~15"), (15, 20, "15~20"), (20, 29, "20~29"))}
    res["limit_up_only"] = cell(P([r for r in rows if r["limit_up"]]))
    res["by_half"] = {h: cell(P([r for r in base if r["half"] == h])) for h in ("H1", "H2")}
    res["by_dvol"] = {lab: cell(P([r for r in base if lo <= r["dvol"] < hi])) for lo, hi, lab in ((20, 50, "20~50억"), (50, 100, "50~100억"), (100, 300, "100~300억"), (300, 1e9, "≥300억"))}
    res["by_regime"] = {"below_ma20": cell(P([r for r in base if r["below_ma20"] is True])), "above_ma20": cell(P([r for r in base if r["below_ma20"] is False]))}
    res["by_close_pos"] = {lab: cell(P([r for r in base if r["close_pos"] is not None and lo <= r["close_pos"] < hi])) for lo, hi, lab in ((0, 0.5, "하단"), (0.5, 0.8, "중상"), (0.8, 1.01, "고가권"))}
    res["by_vol_spike"] = {lab: cell(P([r for r in base if r["vol_spike"] is not None and lo <= r["vol_spike"] < hi])) for lo, hi, lab in ((0, 2, "<2배"), (2, 5, "2~5배"), (5, 1e9, "≥5배"))}
    res["slippage"] = {f"+{s}%": cell(P(base), slip=s) for s in (0.1, 0.3, 0.5)}
    res["intraday_next_day_gross"] = cell([(r["oc"] + FEE, r["s"]) for r in base])   # 참고: 시가 매수→종가(비용 0 처리)
    # 세션당 후보 수(실행 용량)
    per = defaultdict(int)
    for r in base:
        per[r["s"]] += 1
    res["candidates_per_session"] = {"mean": round(st.mean(per.values()), 1), "median": st.median(per.values()), "max": max(per.values())}
    # 세션당 거래대금 상위 K만 (실행 현실: K=1/3/5)
    by_s = defaultdict(list)
    for r in base:
        by_s[r["s"]].append(r)
    for k in (1, 3, 5):
        sel = [x for s, rs in by_s.items() for x in sorted(rs, key=lambda r: -r["dvol"])[:k]]
        res[f"top{k}_by_dvol"] = cell(P(sel))
        sel2 = [x for s, rs in by_s.items() for x in sorted(rs, key=lambda r: -r["chg"])[:k]]
        res[f"top{k}_by_chg"] = cell(P(sel2))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    f = lambda c: f"n={c.get('n', 0):6d} 세션={c.get('sessions', 0):3d} 평균 {c.get('mean', 0):+6.3f}% 중앙 {c.get('median', 0):+6.3f} 승 {c.get('win_pct', 0):5.1f}% t {c.get('t')} 상위2세션제외 {c.get('mean_ex_top2_sessions')} p10 {c.get('p10')} p90 {c.get('p90')}" if c.get("n") else "n=0"
    print("전체 ≥+5%", f(res["all_ge5"])); print("상한가 제외", f(res["ex_limit_up"])); print("상한가만  ", f(res["limit_up_only"]))
    for name in ("by_chg", "by_half", "by_dvol", "by_regime", "by_close_pos", "by_vol_spike", "slippage"):
        print(f"[{name}]")
        for k, v in res[name].items():
            print(f"  {k:8s} {f(v)}")
    print("다음날 장중(시가→종가) gross", f(res["intraday_next_day_gross"]))
    print("세션당 후보", res["candidates_per_session"])
    for k in (1, 3, 5):
        print(f"거래대금 상위{k}", f(res[f"top{k}_by_dvol"])); print(f"등락 상위{k}  ", f(res[f"top{k}_by_chg"]))
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
