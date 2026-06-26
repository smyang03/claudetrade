#!/usr/bin/env python3
"""Phase B ladder A/B 검증 (read-only) — B(peak-trail) vs A(현행) 실현 net 비교.
구현(_pathb_ladder_ab_enforce_b)과 동일: md5(decision_id)%2==1=B. 배포 전엔 A~B여야 정상.
배포일 이후 --since 로 보면 실제 A/B 효과. Δ(B-A)<0이면 {MKT}_LADDER_AB_MODE=off.
"""
from __future__ import annotations
import argparse, hashlib, sqlite3
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
FEE = {"US": 0.7, "KR": 0.5}
def _net(market, pnl, pnl_net, basis):
    mkt = str(market or "").upper(); b = str(basis or "")
    if pnl_net is not None and b in ("measured", "backfilled_exact", "backfilled_fee_only"):
        n = float(pnl_net)
        if b == "backfilled_fee_only" and mkt == "US":
            n -= 0.2
        return n
    return float(pnl) - FEE.get(mkt, 0.7) if pnl is not None else None
def _group(vid):
    if not vid:
        return "?"
    return "B" if int(hashlib.md5(str(vid).encode("utf-8")).hexdigest(), 16) % 2 == 1 else "A"
def _agg(ns):
    if not ns:
        return "N=0"
    n = len(ns); s = sum(ns); w = sum(1 for x in ns if x > 0)
    den = -sum(x for x in ns if x < 0); num = sum(x for x in ns if x > 0)
    pf = (num / den) if den > 0 else 99.0
    return "N=%d win=%.0f%% net_avg=%+.3f%% net_sum=%+.1f%% PF=%.2f" % (n, w / n * 100, s / n, s, pf)
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ML_DB))
    ap.add_argument("--market", default="US")
    ap.add_argument("--since", default="")
    args = ap.parse_args()
    con = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True, timeout=3)
    con.execute("PRAGMA busy_timeout=3000")
    print("DB: %s  market=%s  since=%s" % (args.db, args.market, args.since or "(all)"))
    q = ("SELECT v2_decision_id, market, close_reason, pnl_pct, pnl_pct_net, net_basis "
         "FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live' AND market=? "
         "AND (path_type='claude_price' OR strategy='claude_price')")
    params = [args.market]
    if args.since:
        q += " AND session_date >= ?"; params.append(args.since)
    by = {"A": [], "B": []}; ladder = {"A": [], "B": []}
    for vid, market, cr, pnl, pnl_net, basis in con.execute(q, params):
        net = _net(market, pnl, pnl_net, basis)
        if net is None:
            continue
        g = _group(vid)
        if g not in by:
            continue
        by[g].append(net)
        if cr == "CLOSED_PROFIT_LADDER":
            ladder[g].append(net)
    con.close()
    print("## claude_price net (A=current / B=peak-trail)")
    for g in ("A", "B"):
        print("  [%s] %s" % (g, _agg(by[g])))
    if by["A"] and by["B"]:
        print("  delta(B-A) = %+.3f%%p" % (sum(by["B"]) / len(by["B"]) - sum(by["A"]) / len(by["A"])))
    print("## PROFIT_LADDER only")
    for g in ("A", "B"):
        print("  [%s] %s" % (g, _agg(ladder[g])))
    if ladder["A"] and ladder["B"]:
        print("  delta(B-A) ladder = %+.3f%%p" % (sum(ladder["B"]) / len(ladder["B"]) - sum(ladder["A"]) / len(ladder["A"])))
    print("note: pre-deploy A~B. --since deploy_date for real effect.")
if __name__ == "__main__":
    main()
