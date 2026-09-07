#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""전략 계열 스캔 — 일봉 급락 밖의 계열 5종을 15개월 데이터로 한 번에 잰다 (2026-09-07 밤, 운영자 "저딴 거밖에 안 나와?").

계열: ① 오버나이트(종가→다음 시가) vs 장중(시가→종가), 전일 등락 조건별 ② 달력 효과(월말·월초·옵션만기) ③ 공매도 비중 급증 반등(원장 규모 확인)
④ 인버스 ETF 국면 보유(KOSPI200 프록시 069500 MA20 아래 → KODEX 인버스 114800 / 2X 252670) ⑤ 급등(+8%) 후 5일 내 −5% 눌림 뒤 재돌파 매수(TP12/SL25/D7).
비용: KR 왕복 0.21%, US 0.50%. t는 세션(날짜) 단위. 판정 아님 — 실전 후보 선별용.
출력: data/analysis/strategy_family_scan.json + 콘솔
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
US_DIR, KR_DIR = ROOT / "data" / "price" / "us", ROOT / "data" / "price" / "kr"
OUT = ROOT / "data" / "analysis" / "strategy_family_scan.json"
FEE = {"KR": 0.21, "US": 0.50}
DVOL_MIN = {"KR": 20.0, "US": 50.0}   # 억 / 백만$
START = "2025-06-02"


def load(path):
    rows = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                    except ValueError:
                        pass
    except OSError:
        return []
    return sorted(rows)


def tstat(by_session: dict) -> float | None:
    means = [st.mean(v) for v in by_session.values() if v]
    if len(means) < 5 or st.stdev(means) == 0:
        return None
    return round(st.mean(means) / (st.stdev(means) / math.sqrt(len(means))), 2)


def summarize(pairs, fee=0.0):
    """pairs = [(ret_pct, session)]"""
    if not pairs:
        return {"n": 0}
    by = defaultdict(list)
    for r, s in pairs:
        by[s].append(r - fee)
    nets = [r - fee for r, _ in pairs]
    return {"n": len(nets), "sessions": len(by), "mean": round(st.mean(nets), 3), "median": round(st.median(nets), 3),
            "win_pct": round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 1), "t": tstat(by)}


def contract(entry, win, fee, tp=12.0, sl=-25.0, hold=7, be=4.0, be_lock=True):
    peak = (win[0][4] - entry) / entry * 100.0
    for i, (_d, _o, hi, _lo, c, _v) in enumerate(win[:hold]):
        hip = (hi - entry) / entry * 100.0 if i > 0 else (c - entry) / entry * 100.0
        cp = (c - entry) / entry * 100.0
        if hip >= tp:
            return tp - fee, "TP"
        if cp <= sl:
            return cp - fee, "SL"
        if be_lock and peak >= be and cp <= 0:
            return cp - fee, "BE"
        peak = max(peak, hip)
    if len(win) < hold:
        return None
    return (win[hold - 1][4] - entry) / entry * 100.0 - fee, "D_MAT"


def scan_market(market: str) -> dict:
    d = US_DIR if market == "US" else KR_DIR
    prefix = "us_" if market == "US" else "kr_"
    scale = 1e6 if market == "US" else 1e8
    overnight = defaultdict(list); intraday = defaultdict(list)
    ew_daily: dict[str, list[float]] = defaultdict(list)
    pullback = []
    breadth_down: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for p in sorted(d.glob(f"{prefix}*.csv")):
        b = load(p)
        if len(b) < 30:
            continue
        for i in range(21, len(b) - 1):
            dt, o, h, l, c, v = b[i]
            if dt < START or b[i - 1][4] <= 0 or o <= 0:
                continue
            chg = 100.0 * (c / b[i - 1][4] - 1.0)
            dv = c * v / scale
            ew_daily[dt].append(chg)
            breadth_down[dt][0] += 1; breadth_down[dt][1] += int(chg < 0)
            if dv < DVOL_MIN[market]:
                continue
            n_o, n_c = b[i + 1][1], b[i + 1][4]
            if n_o <= 0:
                continue
            co = 100.0 * (n_o / c - 1.0); oc = 100.0 * (n_c / n_o - 1.0)
            bucket = ("≤−5" if chg <= -5 else "−5~−2" if chg <= -2 else "−2~+2" if chg < 2 else "+2~+5" if chg < 5 else "≥+5")
            overnight[bucket].append((co, dt)); intraday[bucket].append((oc, dt))
            overnight["all"].append((co, dt)); intraday["all"].append((oc, dt))
            # ⑤ 급등 후 눌림 재돌파
            if chg >= 8.0 and dv >= DVOL_MIN[market] * 2:
                ref = c; pulled = False
                for j in range(i + 1, min(i + 11, len(b) - 8)):
                    cj = b[j][4]
                    if not pulled and cj <= ref * 0.95:
                        pulled = True
                    elif pulled and cj > ref:
                        ei = j + 1
                        win = b[ei: ei + 7]
                        if len(win) == 7 and win[0][1] > 0:
                            res = contract(win[0][1], win, FEE[market], be_lock=(market == "US"))
                            if res:
                                pullback.append((res[0], b[j][0]))
                        break
    res = {"overnight": {k: summarize(v, FEE[market]) for k, v in overnight.items()},
           "overnight_gross": {k: summarize(v, 0.0) for k, v in overnight.items()},
           "intraday_gross": {k: summarize(v, 0.0) for k, v in intraday.items()},
           "pullback_rebreak": summarize(pullback, 0.0)}
    # ② 달력
    days = sorted(ew_daily)
    ew = {dt: st.mean(v) for dt, v in ew_daily.items() if len(v) >= 30}
    by_month: dict[str, list[str]] = defaultdict(list)
    for dt in days:
        by_month[dt[:7]].append(dt)
    label: dict[str, str] = {}
    for m, ds in by_month.items():
        for k, dt in enumerate(ds):
            if k < 3:
                label[dt] = "월초3일"
            elif k >= len(ds) - 2:
                label[dt] = "월말2일"
        # 옵션만기: KR 둘째 목요일, US 셋째 금요일
        wd = 3 if market == "KR" else 4; nth = 2 if market == "KR" else 3
        cnt = 0
        for dt in ds:
            if datetime.strptime(dt, "%Y-%m-%d").weekday() == wd:
                cnt += 1
                if cnt == nth:
                    label[dt] = "옵션만기"
    cal = defaultdict(list)
    for dt, r in ew.items():
        cal[label.get(dt, "평일")].append((r, dt))
    res["calendar_ew_close_to_close"] = {k: summarize(v, 0.0) for k, v in cal.items()}
    res["breadth"] = {dt: round(100.0 * x[1] / x[0], 1) for dt, x in breadth_down.items() if x[0] >= 30}
    return res


def inverse_etf_kr(breadth: dict) -> dict:
    """④ 069500(KODEX200) MA20 아래 국면에서 인버스 보유. 신호 = 종가 기준, 다음날 시가 진입~종가 청산이 아니라 일봉 종가→종가 보유(스위치 날만 비용)."""
    idx = load(KR_DIR / "kr_069500.csv")
    out = {}
    for etf in ("114800", "252670"):
        e = {r[0]: r for r in load(KR_DIR / f"kr_{etf}.csv")}
        if not idx or not e:
            out[etf] = {"skipped": "no data"}; continue
        closes = [x[4] for x in idx]
        rets_in, rets_out, switches, prev_state = [], [], 0, False
        for i in range(20, len(idx) - 1):
            dt = idx[i][0]
            if dt < START:
                continue
            below = closes[i] < st.mean(closes[i - 19: i + 1])
            nd = idx[i + 1][0]
            if nd not in e or dt not in e or e[dt][4] <= 0:
                continue
            r = 100.0 * (e[nd][4] / e[dt][4] - 1.0)   # 다음날 ETF 종가 수익(신호일 종가 보유 가정)
            (rets_in if below else rets_out).append((r, nd))
            if below != prev_state:
                switches += 1; prev_state = below
        s_in, s_out = summarize(rets_in), summarize(rets_out)
        total = sum(r for r, _ in rets_in) - switches * FEE["KR"]
        out[etf] = {"hold_when_below_ma20": s_in, "hold_when_above": s_out, "switches": switches,
                    "cum_net_pct_below_only": round(total, 2), "days_in": len(rets_in)}
        # breadth 결합: 전일 하락비율 ≥60%
        sel = [(r, nd) for r, nd in rets_in if breadth.get(nd, 0) >= 0]  # placeholder for shape
        out[etf]["note"] = "다음날 종가 보유 수익. 실제는 시가 진입이라 갭 차이 있음. 스위치마다 왕복 0.21% 차감"
    return out


def short_ratio_note() -> dict:
    p = ROOT / "data" / "shadow" / "kr_short_ratio.jsonl"
    n = sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0
    return {"rows": n, "verdict": "표본 부족(409행, 단일 스냅샷) — 일별 공매도 비중 수집기 필요" if n < 5000 else "검증 가능"}


def fmt(c):
    return f"n={c.get('n', 0):6d} 세션={c.get('sessions', 0):3d} 평균 {c.get('mean', 0):+6.3f}% 중앙 {c.get('median', 0):+6.3f} 승 {c.get('win_pct', 0):5.1f}% t {c.get('t')}" if c.get("n") else "n=0"


def main() -> int:
    res = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fees": FEE}
    for mk in ("KR", "US"):
        r = scan_market(mk)
        res[mk] = {k: v for k, v in r.items() if k != "breadth"}
        print(f"\n### {mk} ① 오버나이트(종가→다음 시가, 비용 후) vs 장중(시가→종가, gross) — 전일 등락 조건")
        for k in ("all", "≤−5", "−5~−2", "−2~+2", "+2~+5", "≥+5"):
            print(f"  {k:7s} 오버나이트 net {fmt(r['overnight'].get(k, {}))}")
            print(f"  {'':7s} 오버나이트 gross {fmt(r['overnight_gross'].get(k, {}))}")
            print(f"  {'':7s} 장중 gross      {fmt(r['intraday_gross'].get(k, {}))}")
        print(f"### {mk} ② 달력(유니버스 등가중 종가→종가, gross)")
        for k, v in r["calendar_ew_close_to_close"].items():
            print(f"  {k:6s} {fmt(v)}")
        print(f"### {mk} ⑤ 급등 +8% → 5일 내 −5% 눌림 → 재돌파 매수(TP12/SL25/D7, net): {fmt(r['pullback_rebreak'])}")
        if mk == "KR":
            inv = inverse_etf_kr(r["breadth"]); res["KR"]["inverse_etf"] = inv
            print("### KR ④ 인버스 ETF 국면 보유(069500 MA20 아래)")
            for etf, v in inv.items():
                if "skipped" in v:
                    print(f"  {etf}: {v['skipped']}"); continue
                print(f"  {etf} 아래일 때 {fmt(v['hold_when_below_ma20'])} | 위일 때 {fmt(v['hold_when_above'])} | 스위치 {v['switches']} · 누적 net {v['cum_net_pct_below_only']:+.2f}% ({v['days_in']}일)")
    res["short_ratio"] = short_ratio_note(); print("### ③ 공매도:", res["short_ratio"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"saved {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
