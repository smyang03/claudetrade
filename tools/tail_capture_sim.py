from __future__ import annotations

"""꼬리 capture 시뮬레이터 — path-aware 트레일링 청산 vs 실제.

뼈대(memory §21): 시스템은 꼬리-수확기. net=상위10% 꼬리. 꼬리의 24%(+67%p≈전체net)를 샌다.
이 도구는 진입 후 5분봉 *실제 경로*로 트레일링 청산을 시뮬해서, "큰 MFE 보인 포지션에 wide stop"이
꼬리를 살리는지(러너 보존) 6/14처럼 죽이는지(눌림에 털림) path-aware로 가른다.

정책: 하드스톱(entry×(1−hard))은 항상. 트레일은 peak가 activation 넘은 뒤 활성 → peak×(1−give)
하회 시 청산. 안 걸리면 윈도우 끝(EOD+N) 종가. read-only(원장)+yfinance, 라이브 무접촉.
"""

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ML_DB = ROOT / "data" / "ml" / "decisions.db"


def parse_utc(s):
    dt = datetime.fromisoformat(str(s))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def sim_trail(path_hl, entry, hard_pct, activation_pct, give_pct):
    """path_hl=[(high,low,close)] 시간순. 트레일링+하드스톱 청산가(% net) 반환."""
    hard = entry * (1 - hard_pct / 100)
    peak = entry
    active = False
    for high, low, close in path_hl:
        # 하드스톱 우선(보수: 같은 바에서 둘 다면 손절)
        if low <= hard:
            return (hard / entry - 1) * 100
        peak = max(peak, high)
        if not active and peak >= entry * (1 + activation_pct / 100):
            active = True
        if active:
            trail = peak * (1 - give_pct / 100)
            if low <= trail:
                return (trail / entry - 1) * 100
    # 미청산 → 마지막 종가
    return (path_hl[-1][2] / entry - 1) * 100 if path_hl else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["US", "KR", "ALL"])
    ap.add_argument("--hard", type=float, default=2.0, help="하드스톱 %")
    ap.add_argument("--days-fwd", type=int, default=2)
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    import yfinance as yf

    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    q = ("SELECT market,ticker,filled_at,closed_at,entry_price,pnl_pct_net,pnl_pct "
         "FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live' "
         "AND filled_at IS NOT NULL AND entry_price>0")
    if args.market != "ALL":
        q += f" AND market='{args.market}'"
    rows = [dict(zip(["market", "ticker", "filled_at", "closed_at", "entry_price", "net", "gross"], r))
            for r in con.execute(q).fetchall()]
    con.close()

    by_tk = defaultdict(list)
    for r in rows:
        by_tk[(r["market"], str(r["ticker"]))].append(r)

    # 정책 그리드: (activation, give)
    grid = [(0, 1.5), (0, 3), (2, 2), (2, 3), (4, 3), (4, 5), (6, 4), (8, 5)]
    sums = {g: 0.0 for g in grid}
    actual_sum = 0.0
    n_used = 0

    for (market, ticker), pl in by_tk.items():
        starts = [parse_utc(p["filled_at"]) for p in pl]
        s = min(starts) - timedelta(days=1)
        e = max(starts) + timedelta(days=args.days_fwd + 2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval="5m",
                                progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            continue
        hi, lo, cl = df["High"], df["Low"], df["Close"]
        for col in (hi, lo, cl):
            pass
        try:
            if hasattr(hi, "columns"):
                hi = hi.iloc[:, 0]; lo = lo.iloc[:, 0]; cl = cl.iloc[:, 0]
        except Exception:
            pass
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        hv, lv, cv = list(hi.values), list(lo.values), list(cl.values)
        for p in pl:
            f = parse_utc(p["filled_at"])
            end = f + timedelta(days=args.days_fwd + 1)
            mask = [(idx[i] >= f) and (idx[i] <= end) for i in range(len(idx))]
            path = [(float(hv[i]), float(lv[i]), float(cv[i])) for i in range(len(idx)) if mask[i]]
            if len(path) < 2:
                continue
            entry = float(p["entry_price"])
            actual = p["net"] if p["net"] is not None else p["gross"]
            if actual is None:
                continue
            actual_sum += actual
            n_used += 1
            for (a, gpct) in grid:
                sums[(a, gpct)] += sim_trail(path, entry, args.hard, a, gpct)
        time.sleep(args.sleep)

    print(f"=== 꼬리 capture 시뮬 ({args.market}, n={n_used}, 하드스톱 {args.hard}%, +{args.days_fwd}d) ===")
    print(f"  실제(actual) net합: {actual_sum:+.1f}%p")
    print(f"  {'정책(activation/give)':22} net합     vs실제")
    for g in grid:
        print(f"  trail act={g[0]}% give={g[1]}%      {sums[g]:+8.1f}   {sums[g]-actual_sum:+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
