#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""광범위 급락일 매수의 장기 OOS (2026-09-07 밤) — 15개월(상승장)에서 찾은 구조가 약세장(2008·2011·2015·2018·2020·2022)에서도 사는가.

breadth(우리 유니버스 하락 종목 비율)는 장기 데이터가 없으니 프록시로 지수 일간 낙폭을 쓴다: IWM(소형주) 또는 SPY 일간 ≤ −2.5% / −3%.
전략: 신호 다음날 시가 매수 → N세션(5/10) 뒤 종가 매도, 비용 0.50. 연도별·국면(200일선 위/아래)별로 나눈다.
데이터: yfinance 일봉(2005~). 네트워크 필요. read-only.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "analysis" / "panic_day_long_oos.json"
FEE = 0.50


def cell(vals):
    if not vals:
        return {"n": 0}
    xs = [v for v, _ in vals]
    t = round(st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs))), 2) if len(xs) >= 4 and st.stdev(xs) > 0 else None
    return {"n": len(xs), "mean": round(st.mean(xs), 2), "median": round(st.median(xs), 2), "win_pct": round(100.0 * sum(1 for x in xs if x > 0) / len(xs), 1), "t": t, "worst": round(min(xs), 2)}


def fmt(c):
    return f"n={c['n']:4d} 평균 {c['mean']:+6.2f} 중앙 {c['median']:+6.2f} 승 {c['win_pct']:5.1f}% t {c['t']} 최악 {c['worst']}" if c.get("n") else "n=0"


def main():
    import yfinance as yf
    out = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    data = {}
    for t in ("IWM", "SPY", "QQQ", "TQQQ"):
        df = yf.download(t, start="2005-01-01", progress=False, auto_adjust=False)
        if df is None or len(df) == 0:
            continue
        if hasattr(df.columns, "levels"):
            df.columns = [c[0] for c in df.columns]
        data[t] = [(d.strftime("%Y-%m-%d"), float(r["Open"]), float(r["Close"])) for d, r in df.iterrows() if r["Open"] > 0]
        print(t, len(data[t]), data[t][0][0], data[t][-1][0])
    for sig_t, thr in (("IWM", -2.5), ("IWM", -3.0), ("SPY", -2.0), ("SPY", -3.0)):
        b = data[sig_t]
        closes = [c for _, _, c in b]
        signals = []
        for i in range(200, len(b) - 12):
            r1 = 100.0 * (closes[i] / closes[i - 1] - 1.0)
            if r1 <= thr:
                ma200 = st.mean(closes[i - 199: i + 1])
                signals.append((i, b[i][0], closes[i] >= ma200))
        print(f"\n### 신호 {sig_t} 일간 ≤ {thr}%  n={len(signals)}")
        out[f"{sig_t}{thr}"] = {}
        for buy_t in ("IWM", "SPY", "QQQ", "TQQQ"):
            bb = data.get(buy_t)
            if not bb:
                continue
            idx = {d: i for i, (d, _, _) in enumerate(bb)}
            for hold in (5, 10):
                vals, by_year, by_reg = [], defaultdict(list), defaultdict(list)
                for i, d, above in signals:
                    j = idx.get(d)
                    if j is None or j + hold >= len(bb):
                        continue
                    ret = 100.0 * (bb[j + hold][2] / bb[j + 1][1] - 1.0) - FEE   # 다음날 시가 → hold 세션 뒤 종가
                    vals.append((ret, d)); by_year[d[:4]].append((ret, d)); by_reg["200일선 위" if above else "200일선 아래"].append((ret, d))
                c = cell(vals)
                out[f"{sig_t}{thr}"][f"{buy_t}_d{hold}"] = {"all": c, "by_regime": {k: cell(v) for k, v in by_reg.items()},
                                                              "by_year": {y: cell(v) for y, v in sorted(by_year.items())}}
                print(f"  매수 {buy_t:5s} D{hold:2d} 전체 {fmt(c)} | 위 {fmt(cell(by_reg['200일선 위']))} | 아래 {fmt(cell(by_reg['200일선 아래']))}")
                if buy_t == "IWM" and hold == 10:
                    print("     연도: " + " ".join(f"{y}:{cell(v)['mean']:+.1f}({cell(v)['n']})" for y, v in sorted(by_year.items())))
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("saved", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
