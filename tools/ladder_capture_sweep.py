#!/usr/bin/env python3
"""Phase A — PROFIT_LADDER capture leak 오프라인 활성 스윕 (read-only).

설계: design_profit_ladder_shadow_ab_20260626.md. tail_capture_sim.sim_trail 로직 재사용.
질문: PROFIT_LADDER로 청산된 트레이드를, "엔진이 활성 X%에서 소유(peak×(1−give) 트레일)"
했다면 실현이 어땠나 vs 실제 ladder 실현. net Δ>0 & 반전손실(워스닝) 통제되는 (activation,give)이 있나?

정직: yfinance 5m high는 추정 상단(체결 가능가 아님) → 절대값 과신 금지, 셀 *간* 상대비교.
하드스톱(loss_cap 대리) entry×(1−hard) 항상. 미청산 시 윈도우 끝 종가. 라이브 무접촉.
actual은 gross pnl_pct(시뮬도 가격기반 ~gross라 동일 기준). 수수료는 단일 청산이라 양쪽 유사.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"


def parse_utc(s):
    dt = datetime.fromisoformat(str(s))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def sim_trail(path_hl, entry, hard_pct, activation_pct, give_pct):
    hard = entry * (1 - hard_pct / 100)
    peak = entry
    active = False
    for high, low, close in path_hl:
        if low <= hard:
            return (hard / entry - 1) * 100
        peak = max(peak, high)
        if not active and peak >= entry * (1 + activation_pct / 100):
            active = True
        if active:
            trail = peak * (1 - give_pct / 100)
            if low <= trail:
                return (trail / entry - 1) * 100
    return (path_hl[-1][2] / entry - 1) * 100 if path_hl else 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["US", "KR", "ALL"])
    ap.add_argument("--hard", type=float, default=2.0)
    ap.add_argument("--days-fwd", type=int, default=1)
    ap.add_argument("--close-reason", default="CLOSED_PROFIT_LADDER")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()
    import yfinance as yf

    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    q = ("SELECT market,ticker,filled_at,closed_at,entry_price,pnl_pct "
         "FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live' "
         "AND filled_at IS NOT NULL AND entry_price>0 AND close_reason=?")
    params = [args.close_reason]
    if args.market != "ALL":
        q += " AND market=?"
        params.append(args.market)
    rows = [dict(zip(["market", "ticker", "filled_at", "closed_at", "entry_price", "gross"], r))
            for r in con.execute(q, params).fetchall()]
    con.close()
    print(f"대상 {args.close_reason} {args.market}: {len(rows)}건")

    by_tk = defaultdict(list)
    for r in rows:
        by_tk[(r["market"], str(r["ticker"]))].append(r)

    # 활성 미세 스윕 × give
    grid = [(a, g) for a in (2.0, 2.5, 3.0, 3.5, 4.0) for g in (2.0, 3.0)]
    per = {g: [] for g in grid}       # 시뮬 실현 리스트
    actuals = []

    for (market, ticker), pl in by_tk.items():
        s = min(parse_utc(p["filled_at"]) for p in pl) - timedelta(days=1)
        e = max(parse_utc(p["filled_at"]) for p in pl) + timedelta(days=args.days_fwd + 2)
        cands = [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]
        df = None
        for c in cands:
            try:
                d = yf.download(c, start=s.date(), end=e.date(), interval="5m", progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            continue
        hi, lo, cl = df["High"], df["Low"], df["Close"]
        if hasattr(hi, "columns"):
            hi, lo, cl = hi.iloc[:, 0], lo.iloc[:, 0], cl.iloc[:, 0]
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass
        hv, lv, cv = list(hi.values), list(lo.values), list(cl.values)
        for p in pl:
            f = parse_utc(p["filled_at"])
            end = f + timedelta(days=args.days_fwd + 1)
            path = [(float(hv[i]), float(lv[i]), float(cv[i])) for i in range(len(idx)) if f <= idx[i] <= end]
            if len(path) < 2 or p["gross"] is None:
                continue
            entry = float(p["entry_price"])
            actuals.append(float(p["gross"]))
            for g in grid:
                per[g].append(sim_trail(path, entry, args.hard, g[0], g[1]))
        time.sleep(args.sleep)

    n = len(actuals)
    asum = sum(actuals)
    print(f"\n=== Phase A 결과 (n={n}, 하드 {args.hard}%, +{args.days_fwd}d, gross 기준) ===")
    print(f"  실제 ladder gross 합: {asum:+.1f}%p (평균 {asum/n if n else 0:+.2f}%)\n")
    print(f"  {'정책 act/give':16} {'시뮬합':>8} {'Δ vs실제':>9} {'개선/악화':>9} {'악화반납합':>10}")
    for g in grid:
        sims = per[g]
        ssum = sum(sims)
        improved = sum(1 for a, sm in zip(actuals, sims) if sm > a + 0.01)
        worsened = sum(1 for a, sm in zip(actuals, sims) if sm < a - 0.01)
        worse_sum = sum(min(0.0, sm - a) for a, sm in zip(actuals, sims))  # 악화분만 합(반전손실)
        print(f"  act={g[0]:.1f}% give={g[1]:.0f}%  {ssum:+8.1f} {ssum-asum:+8.1f} {improved:3d}/{worsened:<3d}   {worse_sum:+9.1f}")
    print("\n  주: 시뮬은 yfinance 5m high 기준(추정 상단, 일부 un-capturable). Δ>0 & 악화반납 통제 셀이")
    print("      없으면 leak은 capturable 아님 → Phase B 안 감. KR은 설계상 제외(claude_price net 양수 보존).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
