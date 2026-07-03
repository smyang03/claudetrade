#!/usr/bin/env python3
"""반응형 tape 게이트 검증 — 진입시점 실제 지수로 net 분할 (no-lookahead, 2026-07-03).

배경: US 진입 net을 실제 SPY 방향(종가까지, lookahead)으로 가르면 +0.86%p — Claude mode
예측(+0.52%p)보다 강한 분별. 하지만 종가는 미래. 이 도구는 **진입 시점까지의 지수**
(세션 개장→진입시각, 사실만)로 다시 갈라 실전 게이트(무lookahead) 값어치를 잰다.

질문: "진입 순간 지수가 이미 빨간색(개장 대비 하락)이면 net이 나쁜가?" = 실제 tape 반응
게이트가 출혈(하락일 진입 -0.86)을 잡을 수 있나. 예측 아니라 사실 기반 → 무알파 벽 밖.

정직: 진입시점 지수는 사실이나, "그 뒤 더 빠질지"는 예측 아님(게이트는 방어지 진입선별 아님).
방어 천장 = 본전(상승 tape 진입도 net ~0). 흑자 레버 아님. yfinance SPY 5m(분석용). 매매 무접촉.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "v2_event_store.db"


def main() -> int:
    ap = argparse.ArgumentParser(description="반응형 tape 게이트 검증 (no-lookahead)")
    ap.add_argument("--since", default="2026-05-04")  # yfinance 5m ~60일 한계
    ap.add_argument("--thr", type=float, default=-0.1, help="빨간tape 판정 임계(개장대비 %)")
    args = ap.parse_args()

    import warnings
    warnings.filterwarnings("ignore")

    # SPY 5m: 로컬 아카이브(data/price/minute/us/us_SPY.csv) 우선, 없으면 yfinance(60일).
    session_open = {}   # session_date(개장일 KST) -> open price
    bars = []           # (kst_iso_min, session_key, close)
    spy_csv = ROOT / "data" / "price" / "minute" / "us" / "us_SPY.csv"

    def _skey(hour, iso):
        # US 세션: 22:30 KST 시작 → 익일 05:00. 세션키=개장일(22시 이후=당일, 06시 전=전일)
        if hour >= 12:
            return iso[:10]
        from datetime import datetime as _dt, timedelta as _td
        return (_dt.fromisoformat(iso[:10]) - _td(days=1)).strftime("%Y-%m-%d")

    if spy_csv.exists():
        import csv as _csv
        with open(spy_csv, encoding="utf-8-sig") as f:
            for r in _csv.DictReader(f):
                ts = str(r["ts"])
                if ts[:10] < args.since:
                    continue
                try:
                    c = float(r["close"]); hh = int(ts[11:13])
                except (KeyError, ValueError, IndexError):
                    continue
                skey = _skey(hh, ts)
                if skey not in session_open:
                    session_open[skey] = c
                bars.append((ts[:16], skey, c))
    else:
        import yfinance as yf
        df = yf.download("SPY", start=args.since, interval="5m", progress=False, auto_adjust=False)
        if df.empty:
            print("SPY 5m 없음(로컬·yfinance 모두)")
            return 0
        df.index = df.index.tz_convert("Asia/Seoul")
        for ts, r in df.iterrows():
            c = float(r["Close"].iloc[0] if hasattr(r["Close"], "iloc") else r["Close"])
            iso = ts.strftime("%Y-%m-%dT%H:%M")
            skey = _skey(ts.hour, iso)
            if skey not in session_open:
                session_open[skey] = c
            bars.append((iso, skey, c))
    bars.sort()

    def index_move_at(entry_iso: str):
        """진입시각까지의 지수 이동(개장→진입, %). 진입 이후 바는 안 봄(no-lookahead)."""
        key = entry_iso[:16]
        prior = None
        skey = None
        for tkey, sk, c in bars:
            if tkey <= key:
                prior = c
                skey = sk
            else:
                break
        if prior is None or skey is None or skey not in session_open:
            return None
        o = session_open[skey]
        return (prior / o - 1) * 100 if o > 0 else None

    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.execute("PRAGMA busy_timeout=8000")
    rows = con.execute(
        "SELECT plan_json FROM v2_path_runs WHERE status='CLOSED' AND market='US' AND session_date>=?",
        (args.since,),
    ).fetchall()
    con.close()

    buk = defaultdict(list)
    nomatch = 0
    detail = []
    for (pj,) in rows:
        d = json.loads(pj or "{}")
        efa = str(d.get("entry_filled_at") or "")
        net = d.get("pnl_pct_net_est")
        if net is None and d.get("pnl_pct") is not None:
            net = float(d["pnl_pct"]) - 0.70  # net_est 결측(5월) 시 gross−US왕복비용 근사
        if not efa or net is None:
            continue
        im = index_move_at(efa)
        if im is None:
            nomatch += 1
            continue
        b = "빨간tape(개장대비↓)" if im < args.thr else ("초록tape(↑)" if im > 0.1 else "평평")
        buk[b].append(net)
        detail.append((efa[5:16], round(im, 2), round(net, 2), b))

    print(f"=== 반응형 tape 게이트: 진입시점 지수(no-lookahead)별 net (since {args.since}, 임계 {args.thr}%) ===")
    print(f"매칭 {sum(len(v) for v in buk.values())}건 / 지수미매칭 {nomatch}")

    def s(v):
        return f"n={len(v):3d} net평균 {st.mean(v):+.2f} 중앙 {st.median(v):+.2f} 승률 {100*sum(1 for x in v if x>0)/len(v):.0f}%" if v else "n=0"

    for b in ("빨간tape(개장대비↓)", "평평", "초록tape(↑)"):
        if buk.get(b):
            print(f"  {b:20} {s(buk[b])}")
    red = buk.get("빨간tape(개장대비↓)", [])
    rest = buk.get("평평", []) + buk.get("초록tape(↑)", [])
    if red and rest:
        gap = st.mean(rest) - st.mean(red)
        print(f"\n  격차(비빨강 − 빨강) net평균: {gap:+.2f}%p  (빨강 n={len(red)})")
        print(f"  = 빨간tape 진입을 막았다면 회피한 평균 손실. 무lookahead 사실 기반.")
        print("  판정 문턱(고정): 격차 >= +0.5%p AND 빨강 n>=30 → 반응형 tape 게이트 shadow 논의")
        print("                   격차 < +0.3 또는 n<20 → 각하(mode 방어로 충분)")
    print("\n주: 진입시점 지수=사실(예측 아님) → 무알파 벽 밖 방어 후보. 단 천장=본전(초록 진입도 흑자 아님).")
    print("    yfinance 5m ~60일 한계로 5월초부터. 실전 게이트는 진입 순간 지수로 동일 계산 가능.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
