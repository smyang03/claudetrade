#!/usr/bin/env python3
"""개선 A2/#2 net 모니터 (read-only) — 2026-06-26 개선묶음 운영 가드.

두 가지를 측정한다. 둘 다 라이브 행동을 바꾸지 않고 net 효과만 관측한다.

(#2) 트렌드 방어 오버레이 net A/B:
  shadow 모드에서 would_skip=true로 표시된 진입(하락추세 차단 후보)의 실제 net을
  (market,ticker,session_date)로 decisions.db에 조인해 집계한다.
  - would_skip 후보의 net이 음수 → 오버레이가 막았으면 손실 회피 = 오버레이 도움(enforce 후보)
  - would_skip 후보의 net이 양수 → 오버레이가 승자를 막음 = 해로움(off 유지/kill)
  => kill 바: skip 후보 net_avg가 KILL_NET_THRESHOLD_PCT 이상(양수쪽)이면 "오버레이 enforce 금지" 권고.

(A4) ready boost net 가드:
  A2(ready=1 PathB ×1.5)의 boost-eligible 모집단(PathB, not_patha_trade_ready=0 ≈ ready=1)의
  net을 추적. 음전 시 A2 토글(PATHB_READY_BOOST_MULT) 수동 롤백 신호.
  (정밀 boost 태깅은 이벤트 스토어에 ready_boost_applied로 남지만, 여기선 모집단 proxy로 본다.)

read 전용: logs/funnel/trend_overlay_*.jsonl + data/ml/decisions.db. 쓰기/주문/네트워크 없음.
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
FUNNEL_DIR = ROOT / "logs" / "funnel"

# 수수료 왕복(%) — capture_net_review와 동일. net_basis='measured'/'backfilled_*'면 pnl_pct_net 우선.
FEE_PCT = {"US": 0.5, "KR": 0.5}
FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}  # US 환전 스프레드 왕복(%) — backfilled_fee_only는 미반영이라 추가 차감
KILL_NET_THRESHOLD_PCT = 0.0  # skip 후보 net_avg가 이 값보다 크면(양수) 오버레이 해로움


def _net_of(market: str, pnl_pct, pnl_pct_net, net_basis) -> float | None:
    mkt = str(market or "").upper()
    basis = str(net_basis or "")
    if pnl_pct_net is not None and basis in ("measured", "backfilled_exact", "backfilled_fee_only"):
        net = float(pnl_pct_net)
        # backfilled_fee_only(US)는 FX 스프레드 미반영 근사 → 정직하게 추가 차감(US net 과대 교정).
        if basis == "backfilled_fee_only":
            net -= FX_SPREAD_PCT.get(mkt, 0.0)
        return net
    if pnl_pct is None:
        return None
    return float(pnl_pct) - FEE_PCT.get(mkt, 0.5) - FX_SPREAD_PCT.get(mkt, 0.0)


def _agg(nets: list[float]) -> str:
    if not nets:
        return "N=0"
    n = len(nets)
    w = sum(1 for x in nets if x > 0)
    num = sum(x for x in nets if x > 0)
    den = -sum(x for x in nets if x < 0)
    pf = (num / den) if den > 0 else float("inf")
    return f"N={n} win={w/n*100:.0f}% net_avg={sum(nets)/n:+.3f}% net_sum={sum(nets):+.1f}% PF={pf:.2f}"


def _load_net_index(con: sqlite3.Connection) -> dict[tuple, float]:
    cur = con.cursor()
    idx: dict[tuple, float] = {}
    for market, sd, ticker, pnl, pnl_net, basis in cur.execute(
        """SELECT market, session_date, ticker, pnl_pct, pnl_pct_net, net_basis
           FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live'"""
    ):
        net = _net_of(market, pnl, pnl_net, basis)
        if net is None:
            continue
        idx[(str(market).upper(), str(sd)[:10], str(ticker))] = net
    return idx


def trend_overlay_ab(con: sqlite3.Connection) -> None:
    print("## (#2) 트렌드 방어 오버레이 net A/B")
    files = sorted(glob.glob(str(FUNNEL_DIR / "trend_overlay_*.jsonl")))
    if not files:
        print("  funnel 로그 없음 (shadow 모드 가동 후 누적). 아직 데이터 없음.\n")
        return
    net_idx = _load_net_index(con)
    skip_nets: dict[str, list[float]] = {"KR": [], "US": []}
    allow_nets: dict[str, list[float]] = {"KR": [], "US": []}
    rows = 0
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                rows += 1
                mkt = str(rec.get("market") or "").upper()
                key = (mkt, str(rec.get("session_date") or "")[:10], str(rec.get("ticker") or ""))
                net = net_idx.get(key)
                if net is None:
                    continue
                (skip_nets if rec.get("would_skip") else allow_nets).setdefault(mkt, []).append(net)
    print(f"  funnel 레코드 {rows}건")
    for mkt in ("KR", "US"):
        sk = skip_nets.get(mkt, [])
        al = allow_nets.get(mkt, [])
        print(f"  [{mkt}] would_skip(차단후보) {_agg(sk)}")
        print(f"  [{mkt}] allowed(허용)        {_agg(al)}")
        if sk:
            avg = sum(sk) / len(sk)
            if avg > KILL_NET_THRESHOLD_PCT:
                print(f"  [{mkt}] ⚠ KILL: 차단후보 net_avg={avg:+.3f}% > {KILL_NET_THRESHOLD_PCT}% → 오버레이가 승자를 막음. enforce 금지/off 유지.")
            else:
                print(f"  [{mkt}] ✓ 차단후보 net_avg={avg:+.3f}% ≤ {KILL_NET_THRESHOLD_PCT}% → 오버레이 손실회피. enforce 후보(표본 충분 시).")
    print()


def ready_boost_guard(con: sqlite3.Connection) -> None:
    print("## (A4) ready boost net 가드 (PathB ready=1 proxy 모집단)")
    cur = con.cursor()
    rows = list(cur.execute(
        """SELECT market, pnl_pct, pnl_pct_net, net_basis, path_type
           FROM v2_learning_performance
           WHERE closed=1 AND runtime_mode='live' AND strategy='claude_price'"""
    ))
    by_mkt: dict[str, list[float]] = {"KR": [], "US": []}
    for market, pnl, pnl_net, basis, _pt in rows:
        net = _net_of(market, pnl, pnl_net, basis)
        if net is None:
            continue
        by_mkt.setdefault(str(market).upper(), []).append(net)
    for mkt in ("KR", "US"):
        nets = by_mkt.get(mkt, [])
        print(f"  [{mkt}] claude_price(PathB) net  {_agg(nets)}")
        if nets and sum(nets) / len(nets) < 0:
            print(f"  [{mkt}] ⚠ net_avg 음수 → A2 boost(×1.5)가 손실 증폭 위험. PATHB_READY_BOOST_MULT 롤백 검토.")
    print("  주: boost 적용분만 보려면 이벤트 스토어 ready_boost_applied 태그 집계 필요(운영 후 분리).\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(ML_DB))
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(f"DB: {args.db} (read-only)\n")
    trend_overlay_ab(con)
    ready_boost_guard(con)
    con.close()


if __name__ == "__main__":
    main()
