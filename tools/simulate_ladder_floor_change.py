from __future__ import annotations

"""profit_ladder floor 변경(Phase 2)의 효과를 yfinance 5분봉 경로로 정밀 시뮬한다.

과거 PROFIT_LADDER 청산 종목의 진입~청산 구간 5분봉을 받아, 현행 floor 정책과 신규 정책
(tier1 entry→entry*1.006, tier2 *1.005→*1.010)을 동일 경로에 적용해 청산가/net 차이를
측정한다. 같은 경로에 두 정책을 적용하므로 경로 가정 오차는 상쇄되고 정책 차이만 남는다.

한계: PROFIT_LADDER로 청산된 건만 본다(floor 변경 직접 영향 범위). loss_cap/hard_stop이
ladder보다 먼저 트리거되는 경로 상호작용은 근사로 무시한다. 봉 내 high→low 순서를 가정한다.
"""

import argparse
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"

TIERS = (1.2, 2.0, 3.0, 4.0)
CUR = {"t1": 0.0, "t2": 0.005, "gb3": 0.010, "gb4": 0.012}
NEW = {"t1": 0.006, "t2": 0.010, "gb3": 0.010, "gb4": 0.012}
FEE_PCT = 0.5


def parse_utc(s: str) -> datetime:
    dt = datetime.fromisoformat(str(s))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def simulate(bars, entry, p) -> float | None:
    """bars: [(high, low, close)]. floor 터치 시 floor 청산가 반환, 미청산 시 마지막 close."""
    if entry <= 0 or not bars:
        return None
    peak = entry
    for high, low, close in bars:
        peak = max(peak, high)
        mfe = (peak / entry - 1.0) * 100.0
        if mfe >= TIERS[3]:
            floor = peak * (1.0 - p["gb4"])
        elif mfe >= TIERS[2]:
            floor = peak * (1.0 - p["gb3"])
        elif mfe >= TIERS[1]:
            floor = entry * (1.0 + p["t2"])
        elif mfe >= TIERS[0]:
            floor = entry * (1.0 + p["t1"])
        else:
            floor = 0.0
        if floor > 0 and low <= floor:
            return floor
    return bars[-1][2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    import yfinance as yf

    conn = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT v2_decision_id,market,ticker,filled_at,closed_at,entry_price,pnl_pct "
        "FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live' "
        "AND close_reason='CLOSED_PROFIT_LADDER' AND filled_at IS NOT NULL AND closed_at IS NOT NULL "
        "AND entry_price>0"
    ).fetchall()]
    conn.close()

    by_tk = defaultdict(list)
    for r in rows:
        by_tk[(r["market"], str(r["ticker"]))].append(r)

    cur_nets, new_nets, actual_nets = [], [], []
    detail = []
    for (market, ticker), pl in by_tk.items():
        starts = [parse_utc(p["filled_at"]) for p in pl]
        ends = [parse_utc(p["closed_at"]) for p in pl]
        s = min(starts) - timedelta(days=1)
        e = max(ends) + timedelta(days=2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval=args.interval, progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            continue
        high = df["High"]; low = df["Low"]; close = df["Close"]
        for col in (high, low, close):
            if hasattr(col, "columns"):
                pass
        if hasattr(high, "columns"):
            high = high.iloc[:, 0]; low = low.iloc[:, 0]; close = close.iloc[:, 0]
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        for p in pl:
            f = parse_utc(p["filled_at"]); cl = parse_utc(p["closed_at"]) + timedelta(hours=6)
            mask = (idx >= f) & (idx <= cl)
            if int(mask.sum()) < 2:
                continue
            entry = float(p["entry_price"])
            bars = list(zip([float(x) for x in high[mask]], [float(x) for x in low[mask]], [float(x) for x in close[mask]]))
            cur_exit = simulate(bars, entry, CUR)
            new_exit = simulate(bars, entry, NEW)
            if cur_exit is None or new_exit is None:
                continue
            cur_net = (cur_exit / entry - 1.0) * 100.0 - FEE_PCT
            new_net = (new_exit / entry - 1.0) * 100.0 - FEE_PCT
            actual_net = float(p["pnl_pct"]) - FEE_PCT
            cur_nets.append(cur_net); new_nets.append(new_net); actual_nets.append(actual_net)
            detail.append((ticker, round(actual_net, 2), round(cur_net, 2), round(new_net, 2)))
        time.sleep(args.sleep)

    n = len(cur_nets)
    print(f"=== profit_ladder floor 변경 시뮬 (PROFIT_LADDER 청산 {n}건, yfinance {args.interval} 경로) ===")
    if n:
        print(f"  실제 net 평균:        {mean(actual_nets):+.2f}%")
        print(f"  현행정책 시뮬 net평균: {mean(cur_nets):+.2f}%  (경로 시뮬 baseline)")
        print(f"  신규정책 시뮬 net평균: {mean(new_nets):+.2f}%")
        print(f"  => 신규-현행 차이:    {mean(new_nets)-mean(cur_nets):+.2f}%p")
        better = sum(1 for c, nw in zip(cur_nets, new_nets) if nw > c + 0.01)
        worse = sum(1 for c, nw in zip(cur_nets, new_nets) if nw < c - 0.01)
        print(f"  건별: 개선 {better} / 악화 {worse} / 동일 {n-better-worse}")
        print("\n  종목 | 실제net | 현행시뮬 | 신규시뮬")
        for t, a, c, nw in sorted(detail, key=lambda x: x[3] - x[2])[:12]:
            print(f"    {t}: {a:+.2f} | {c:+.2f} | {nw:+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
