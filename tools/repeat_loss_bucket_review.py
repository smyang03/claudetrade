#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""반복손실 확장 shadow 판독 (시니어 검토 5순위, 2026-07-09). read-only.

ticker 단위 shadow(라이브 로그)와 별도로, bucket 단위 would-block을 소급 계산한다:
  - 규칙 A: 동일 (market, primary_bucket)에서 lookback일 내 손실 3회 후 재진입 → would-block
  - 그 would-block 재진입들의 실제 net 합/분포 → enforce 가치 판정 자료
판정 기준: would-block net 합이 음수로 유의(양월 방향 일치)면 enforce 후보 승격.
"""
import argparse
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
LOOKBACK_D = 10
BUCKET_MAX_LOSSES = 3


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lookback-days", type=int, default=LOOKBACK_D)
    ap.add_argument("--max-losses", type=int, default=BUCKET_MAX_LOSSES)
    args = ap.parse_args()

    # 거래: closed live, 시간순
    mc = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    mc.row_factory = sqlite3.Row
    trades = [dict(r) for r in mc.execute(
        """SELECT DISTINCT v2_decision_id, market, session_date, ticker, filled_at,
                  pnl_pct_net, pnl_pct
           FROM v2_learning_performance
           WHERE closed=1 AND filled=1 AND runtime_mode='live' AND pnl_pct IS NOT NULL
           ORDER BY COALESCE(filled_at, session_date)"""
    )]
    mc.close()

    # bucket 매핑: (market, ticker, session_date) → primary_bucket (audit)
    ac = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True)
    bucket_map: dict[tuple, str] = {}
    for mk, tk, sd, pb in ac.execute(
        "SELECT market, ticker, session_date, primary_bucket FROM audit_candidate_rows "
        "WHERE primary_bucket IS NOT NULL AND primary_bucket != ''"
    ):
        bucket_map.setdefault((str(mk).upper(), str(tk), str(sd)), str(pb))
    ac.close()
    print(f"거래 {len(trades)} / bucket 매핑 {len(bucket_map)}")

    def _dt(v, sd):
        try:
            return datetime.fromisoformat(str(v).replace("Z", "+00:00")).replace(tzinfo=None)
        except (TypeError, ValueError):
            return datetime.fromisoformat(f"{sd}T12:00:00")

    # 소급 시뮬: bucket별 손실 이력 창 → would-block 재진입 수집
    loss_hist: dict[tuple, list] = defaultdict(list)  # (mkt,bucket) -> [dt,...]
    would = []
    unmapped = 0
    for t in trades:
        mk = str(t["market"]).upper()
        key3 = (mk, str(t["ticker"]), str(t["session_date"]))
        bucket = bucket_map.get(key3)
        if not bucket:
            unmapped += 1
            continue
        bk = (mk, bucket)
        dt = _dt(t["filled_at"], t["session_date"])
        cutoff = dt - timedelta(days=args.lookback_days)
        recent = [x for x in loss_hist[bk] if x >= cutoff]
        if len(recent) >= args.max_losses:
            net = t["pnl_pct_net"] if t["pnl_pct_net"] is not None else t["pnl_pct"]
            would.append((mk, bucket, str(t["session_date"])[:7], float(net)))
        pnl = t["pnl_pct_net"] if t["pnl_pct_net"] is not None else t["pnl_pct"]
        if pnl is not None and float(pnl) < 0:
            loss_hist[bk].append(dt)

    print(f"bucket 매핑 실패(계산 제외): {unmapped}")
    print(f"\n=== would-block 재진입 (bucket {args.max_losses}회 손실 후, {args.lookback_days}일 창) ===")
    print(f"총 {len(would)}건 | net 합 {sum(w[3] for w in would):+.2f}")
    by_mon = defaultdict(list)
    by_bkt = defaultdict(list)
    for mk, bk, mon, net in would:
        by_mon[f"{mk} {mon}"].append(net)
        by_bkt[f"{mk}:{bk}"].append(net)
    print("-- 월별 (방향 일치 확인용) --")
    for k in sorted(by_mon):
        v = by_mon[k]
        print(f"  {k}: n={len(v)} 합={sum(v):+.2f}")
    print("-- bucket별 상위 --")
    for k in sorted(by_bkt, key=lambda x: sum(by_bkt[x]))[:8]:
        v = by_bkt[k]
        print(f"  {k}: n={len(v)} 합={sum(v):+.2f}")


if __name__ == "__main__":
    main()
