from __future__ import annotations

"""꼬리 capture 설계 red-team — 최적정책(act=4,give=3)을 국면별·청산일별로 분해.

반문: +33%p가 강세장 in-sample 아니냐? 하락국면에도 이기나? 오버나잇(갭리스크) 필요분은?
거래별로 actual vs trail을 내고, (a)보유창 지수방향(상승/하락) (b)청산이 당일이냐 오버나잇이냐로 분해.
"""

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


def sim_trail(path, entry, hard, act, give):
    """returns (net_pct, exit_idx). path=[(ts,high,low,close)]."""
    hardlv = entry * (1 - hard / 100)
    peak = entry; active = False
    for i, (ts, high, low, close) in enumerate(path):
        if low <= hardlv:
            return (hardlv / entry - 1) * 100, i
        peak = max(peak, high)
        if not active and peak >= entry * (1 + act / 100):
            active = True
        if active and low <= peak * (1 - give / 100):
            return (peak * (1 - give / 100) / entry - 1) * 100, i
    return (path[-1][3] / entry - 1) * 100, len(path) - 1


def main() -> int:
    import yfinance as yf
    mk_arg = sys.argv[1] if len(sys.argv) > 1 else "US"
    bench_sym = "QQQ" if mk_arg == "US" else "^KS11"
    bd = yf.download(bench_sym, start="2026-04-20", end="2026-06-18", interval="1d", progress=False, auto_adjust=False)
    bc = bd["Close"]
    if hasattr(bc, "columns"):
        bc = bc.iloc[:, 0]
    B = [(x.date().isoformat(), float(v)) for x, v in zip(bd.index, bc.values)]

    def bench_dir(d0, d1):
        p0 = p1 = None
        for dt, px in B:
            if dt <= d0:
                p0 = px
            if dt <= d1:
                p1 = px
        if not p0 or not p1:
            return None
        return (p1 / p0 - 1) * 100

    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    rows = [dict(zip(["ticker", "filled_at", "closed_at", "entry", "net", "gross"], r)) for r in con.execute(
        f"SELECT ticker,filled_at,closed_at,entry_price,pnl_pct_net,pnl_pct FROM v2_learning_performance "
        f"WHERE closed=1 AND runtime_mode='live' AND market='{mk_arg}' AND filled_at IS NOT NULL AND entry_price>0").fetchall()]
    con.close()
    by = defaultdict(list)
    for r in rows:
        by[str(r["ticker"])].append(r)

    buckets = defaultdict(lambda: {"actual": 0.0, "trail": 0.0, "n": 0})
    exitday = defaultdict(lambda: {"actual": 0.0, "trail": 0.0, "n": 0})
    for ticker, pl in by.items():
        starts = [parse_utc(p["filled_at"]) for p in pl]
        s = min(starts) - timedelta(days=1); e = max(starts) + timedelta(days=4)
        try:
            df = yf.download(ticker, start=s.date(), end=e.date(), interval="5m", progress=False, auto_adjust=False)
        except Exception:
            df = None
        if df is None or len(df) == 0:
            continue
        hi, lo, cl = df["High"], df["Low"], df["Close"]
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
        H, L, C = list(hi.values), list(lo.values), list(cl.values)
        for p in pl:
            f = parse_utc(p["filled_at"]); end = f + timedelta(days=3)
            path = [(idx[i], float(H[i]), float(L[i]), float(C[i])) for i in range(len(idx)) if f <= idx[i] <= end]
            if len(path) < 2:
                continue
            entry = float(p["entry"]); actual = p["net"] if p["net"] is not None else p["gross"]
            if actual is None:
                continue
            tnet, eidx = sim_trail(path, entry, 2.0, 4.0, 3.0)
            d0 = str(p["filled_at"])[:10]
            bdir = bench_dir(d0, str(p["closed_at"])[:10])
            reg = "?" if bdir is None else ("지수상승" if bdir >= 0 else "지수하락")
            buckets[reg]["actual"] += actual; buckets[reg]["trail"] += tnet; buckets[reg]["n"] += 1
            # 청산이 진입 당일인가 오버나잇인가
            same_day = path[eidx][0].date() == f.date()
            k = "당일청산" if same_day else "오버나잇"
            exitday[k]["actual"] += actual; exitday[k]["trail"] += tnet; exitday[k]["n"] += 1
        time.sleep(0.25)

    print(f"=== {mk_arg} red-team: trail(act4,give3) vs actual ===")
    print("[국면별]")
    for reg, d in buckets.items():
        print(f"  {reg:8} n={d['n']:3} actual {d['actual']:+7.1f} | trail {d['trail']:+7.1f} | 차 {d['trail']-d['actual']:+.1f}")
    print("[청산일별]")
    for k, d in exitday.items():
        print(f"  {k:8} n={d['n']:3} actual {d['actual']:+7.1f} | trail {d['trail']:+7.1f} | 차 {d['trail']-d['actual']:+.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
