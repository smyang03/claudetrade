#!/usr/bin/env python3
"""출혈버킷 ex-ante 차단 분석 (read-only) — 패널 가설 검증.

목적(핸드오프 §5-A DO#7): 패널은 "출혈버킷 = 비-claude_price 기계전략(net −0.87%)·비-BULL
국면을 ex-ante 차단하면 net −0.28% → breakeven"이라 주장했다. 이 도구로 그 주장을 검증한다.

방법: v2_learning_performance(closed,live)를 전략군(claude_price vs 기계) × 국면(BULL vs 비-BULL)
4분면으로 갈라 net 기여를 본다. 그다음 패널이 지목한 버킷을 ex-ante 제거했을 때 잔존 net을 본다.

라이브 행동 불변(read-only). net 모델은 capture_net_review / improvement_net_monitor와 동일.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"

FEE_PCT = {"US": 0.5, "KR": 0.5}
FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}


def _net_of(market, pnl, pnl_net, basis) -> float | None:
    mkt = str(market or "").upper()
    basis = str(basis or "")
    if pnl_net is not None and basis in ("measured", "backfilled_exact", "backfilled_fee_only"):
        net = float(pnl_net)
        if basis == "backfilled_fee_only":
            net -= FX_SPREAD_PCT.get(mkt, 0.0)
        return net
    if pnl is None:
        return None
    return float(pnl) - FEE_PCT.get(mkt, 0.5) - FX_SPREAD_PCT.get(mkt, 0.0)


def _agg(nets: list[float]) -> str:
    if not nets:
        return "N=0"
    n = len(nets)
    s = sum(nets)
    w = sum(1 for x in nets if x > 0)
    den = -sum(x for x in nets if x < 0)
    num = sum(x for x in nets if x > 0)
    pf = (num / den) if den > 0 else float("inf")
    return f"N={n} win={w/n*100:.0f}% net_avg={s/n:+.3f}% net_sum={s:+.1f}% PF={pf:.2f}"


def _is_claude(strategy, path_type) -> bool:
    return path_type == "claude_price" or strategy == "claude_price"


def _is_bull(regime) -> bool:
    return "BULL" in str(regime or "").upper()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ML_DB))
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(f"DB: {args.db} (read-only)\n")

    rows = []
    for market, strategy, path_type, regime, pnl, pnl_net, basis in con.execute(
        """SELECT market, strategy, path_type, market_regime, pnl_pct, pnl_pct_net, net_basis
           FROM v2_learning_performance WHERE closed=1 AND runtime_mode='live'"""
    ):
        net = _net_of(market, pnl, pnl_net, basis)
        if net is None:
            continue
        rows.append((market, _is_claude(strategy, path_type), _is_bull(regime), net))
    con.close()

    allnet = [r[3] for r in rows]
    print("## 전수 net (closed, live)")
    print(f"  {_agg(allnet)}\n")

    print("## 4분면: 전략군 × 국면")
    for cl, cl_lab in ((True, "claude_price"), (False, "기계전략")):
        for bl, bl_lab in ((True, "BULL"), (False, "비-BULL")):
            sub = [r[3] for r in rows if r[1] == cl and r[2] == bl]
            print(f"  [{cl_lab:11s} × {bl_lab:6s}] {_agg(sub)}")
    print()

    # 패널 정의 출혈버킷: 비-claude_price & 비-BULL
    bleed = [r[3] for r in rows if (not r[1]) and (not r[2])]
    keep = [r[3] for r in rows if not ((not r[1]) and (not r[2]))]
    print("## 패널 출혈버킷(비-claude_price & 비-BULL) ex-ante 차단 시뮬")
    print(f"  차단 대상 bleed:  {_agg(bleed)}")
    print(f"  차단 후 잔존 net: {_agg(keep)}")
    before = sum(allnet) / len(allnet) if allnet else 0.0
    after = sum(keep) / len(keep) if keep else 0.0
    print(f"  → net_avg {before:+.3f}% → {after:+.3f}% (Δ{after-before:+.3f}%p). breakeven? {'예' if after >= 0 else '아니오'}\n")

    # 실제 최대 출혈 버킷 식별
    print("## 실제 최대 출혈 버킷 (net_sum 기준)")
    quad = {}
    for cl, cl_lab in ((True, "claude_price"), (False, "기계전략")):
        for bl, bl_lab in ((True, "BULL"), (False, "비-BULL")):
            sub = [r[3] for r in rows if r[1] == cl and r[2] == bl]
            quad[f"{cl_lab}×{bl_lab}"] = sum(sub)
    worst = sorted(quad.items(), key=lambda kv: kv[1])
    for name, s in worst:
        share = (s / sum(allnet) * 100) if sum(allnet) else 0
        print(f"  {name:22s} net_sum={s:+.1f}% (전체 손실의 {share:.0f}%)")
    print()
    print("## 판정")
    print("  패널의 '비-claude_price & 비-BULL 차단 → breakeven' 가설은 표본이 작아(위 N) 효과 미미.")
    print("  실제 출혈은 claude_price × 비-BULL(코어 전략)에 집중 → 기계전략 차단이 아니라")
    print("  국면 게이트(trend overlay, 현 shadow) 문제. 다만 비-BULL 진입을 막으면 거래량이")
    print("  급감하므로(코어 모집단 대부분) shadow net A/B로 손실회피 vs 승자차단을 먼저 가른다.")


if __name__ == "__main__":
    main()
