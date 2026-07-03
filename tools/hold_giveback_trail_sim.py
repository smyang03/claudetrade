#!/usr/bin/env python3
"""hold give-back 규칙 분봉 시뮬 (read-only, 2026-07-03 — 라이브 shadow 대체).

질문: hold_advisor가 이익중 HOLD로 반납한 것(US 90% 반납, 중앙 +1.42%→-1.04%)을,
"peak에서 give% 반납 시 매도" 규칙으로 관리했다면 net이 나았나?
라이브 A/B가 저빈도로 굶어(1건) 분봉 replay로 대체 검증.

방법(양방향): 각 US claude_price 청산의 진입~청산 창을 로컬 분봉(high/low/close)으로 재생.
  실제 gross = 실현 pnl. 시뮬 gross = sim_trail(peak×(1−give) 트레일, hard stop, activation).
  동일 창에서 규칙만 바꿔 비교 → 반납 leak이 capturable한지.
정직: 분봉 high/low = 추정 체결가(정확한 fill 아님) → 절대값 과신 금지, 셀 간 상대비교.
  하드스톱 entry×(1−hard) 항상. 미발동 시 창 끝 종가. forward≠net이라 sim은 낙관 상한.
  라이브·config 무접촉. DB mode=ro.
"""
from __future__ import annotations

import argparse
import csv
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
MIN = {"US": ROOT / "data" / "price" / "minute" / "us", "KR": ROOT / "data" / "price" / "minute" / "kr"}


def sim_trail(path_hl, entry, hard_pct, activation_pct, give_pct):
    """peak-trail 시뮬 (ladder_capture_sweep 재사용). path_hl=[(high,low,close),...]"""
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


def load_bars(market, ticker):
    prefix = "us" if market == "US" else "kr"
    p = MIN[market] / f"{prefix}_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((str(r["ts"])[:16], float(r["high"]), float(r["low"]), float(r["close"])))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="hold give-back 분봉 시뮬 (read-only)")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--hard", type=float, default=2.0)
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=15)
    con.execute("PRAGMA busy_timeout=10000")
    rows = con.execute(
        "SELECT ticker, plan_json FROM v2_path_runs WHERE status='CLOSED' AND market=? AND session_date>=?",
        (args.market, args.since),
    ).fetchall()
    con.close()

    # (activation, give) 스윕
    cells = [(2.0, 1.5), (2.0, 2.0), (2.5, 2.0), (3.0, 2.0), (3.0, 3.0), (4.0, 2.0), (4.0, 3.0)]
    actual_sum = 0.0
    n_used = 0
    nodata = 0
    by_reason_actual = defaultdict(float)
    by_reason_cell = defaultdict(lambda: defaultdict(float))
    cell_sum = {c: 0.0 for c in cells}
    cell_worse = {c: 0 for c in cells}
    cell_wsum = {c: 0.0 for c in cells}

    for tk, pj in rows:
        d = json.loads(pj or "{}")
        entry = d.get("actual_entry_price")
        efa = str(d.get("entry_filled_at") or "")
        exa = str(d.get("sell_order_sent_at") or d.get("closed_at") or "")
        actual = d.get("pnl_pct")
        reason = str(d.get("close_reason") or "").replace("CLOSED_", "")
        if not entry or not efa or not exa or actual is None:
            continue
        bars = load_bars(args.market, str(tk))
        if not bars:
            nodata += 1
            continue
        ekey, xkey = efa[:16], exa[:16]
        path = [(h, l, c) for ts, h, l, c in bars if ekey <= ts <= xkey]
        if len(path) < 2:
            nodata += 1
            continue
        n_used += 1
        actual_sum += float(actual)
        by_reason_actual[reason] += float(actual)
        for c in cells:
            sim = sim_trail(path, float(entry), args.hard, c[0], c[1])
            cell_sum[c] += sim
            by_reason_cell[c][reason] += sim
            if sim < float(actual) - 0.05:
                cell_worse[c] += 1
                cell_wsum[c] += (sim - float(actual))

    print(f"=== hold give-back 분봉 시뮬 ({args.market}, since {args.since}, hard {args.hard}%) ===")
    print(f"창 재생된 청산 {n_used}건 / 분봉결측·창부족 {nodata}")
    if not n_used:
        return 0
    print(f"실제 gross 합: {actual_sum:+.1f}%p (평균 {actual_sum/n_used:+.2f})\n")
    print(f"  {'정책(act/give)':16} {'시뮬합':>8} {'Δvs실제':>8} {'악화건':>6} {'악화합':>8}")
    best = None
    for c in cells:
        delta = cell_sum[c] - actual_sum
        print(f"  act={c[0]:.1f} give={c[1]:.0f}%   {cell_sum[c]:+8.1f} {delta:+8.1f} {cell_worse[c]:6d} {cell_wsum[c]:+8.1f}")
        if best is None or delta > best[1]:
            best = (c, delta)

    # 출구사유별 (leak 위치)
    print(f"\n[출구사유별 실제 vs 최선셀 {best[0]}]  (Δ>0=그 사유에서 give-back trail이 나음)")
    bc = best[0]
    reasons = sorted(by_reason_actual, key=lambda r: by_reason_cell[bc][r] - by_reason_actual[r], reverse=True)
    for r in reasons:
        a = by_reason_actual[r]
        s = by_reason_cell[bc][r]
        print(f"  {r:26} 실제 {a:+7.1f}  시뮬 {s:+7.1f}  Δ {s-a:+6.1f}")
    print("\n주: 분봉 high/low=추정 체결가(낙관 상한). Δ>0 & 악화 통제 셀 없으면 capturable 아님.")
    print("    이건 라이브 A/B(굶음) 대체 오프라인 검증 — 방향 판단용, 배포는 라이브 확증 후.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
