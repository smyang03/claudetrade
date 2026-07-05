#!/usr/bin/env python
"""net_after_fx 소급 백필 — 청산 시점 타이밍 결측으로 net 필드가 빠진 CLOSED run 복구.

배경(2026-07-05 측정): pnl_pct_net_after_fx_est는 6월 중순 코드 도입 후에만 생성되고,
그마저 mark_closed 호출 시점에 진입가/수량이 아직 plan에 없으면 _close_cost_meta가
{}를 반환해 net 필드가 통째 결측됐다(이후 broker reconcile이 entry/qty를 채워도
net 재계산 트리거 없음). 결과: net_after_fx 커버리지 US 31%·KR 17%. 최적화할 net을
못 재는 측정 사각의 근본이다.

이 스크립트는 status='CLOSED'이고 net_after_fx가 없지만 entry/qty/exit가 지금 다 존재하는
run에 한해, 생산자 execution/claude_price_sell_manager._close_cost_meta와 **동일 공식·동일
rate 헬퍼**로 net을 소급 계산해 plan에 merge한다. status·트리거·주문·매매 로직 무접촉.
백필분은 net_backfilled_offline=True로 표식해 라이브 실측과 구분한다.

주의:
  - 진입가는 plan.actual_entry_price를 그대로 쓴다(주문가일 수 있음). entry_price_source를
    함께 기록해 근사임을 남긴다.
  - pct 필드(net_est·net_after_fx)는 FX rate와 무관해 정확. krw 필드는 usd_krw_at_fill이
    있을 때만 기록.
  - 4·5월 청산은 애초 필드가 코드에 없던 시절이라 대상이 아니되, 입력이 있으면 동일하게 복구된다.
  - 기본 dry-run. 실제 기록은 --apply. read-then-write, WAL이라 라이브 봇 동시 읽기 안전.

사용:
  python tools/backfill_net_after_fx.py            # dry-run(요약만)
  python tools/backfill_net_after_fx.py --apply     # 실제 기록
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 생산자와 동일한 비용 상수 헬퍼를 재사용(드리프트 방지)
from execution.claude_price_sell_manager import (
    _fee_rates_for_market,
    _fx_spread_rate_per_side,
)
from lifecycle.event_store import EventStore

NET_FIELD = "pnl_pct_net_after_fx_est"


def _compute_net(plan: dict) -> dict | None:
    """_close_cost_meta의 pct/krw 공식을 그대로 복제. 입력 부족 시 None(생산자의 return {} 대응)."""
    market = str(plan.get("_market") or "").upper()
    entry = float(plan.get("actual_entry_price") or 0)
    qty = float(plan.get("filled_qty") or 0)
    exit_px = float(plan.get("actual_exit_price") or 0)
    if entry <= 0 or exit_px <= 0 or qty <= 0:
        return None
    buy_rate, sell_rate = _fee_rates_for_market(market)
    fee_pct_round_trip = (buy_rate + sell_rate) * 100.0
    fx_spread_rate = _fx_spread_rate_per_side(market)
    fx_spread_pct_round_trip = fx_spread_rate * 2.0 * 100.0
    pnl_pct_gross = (exit_px / entry - 1.0) * 100.0
    pnl_pct_net_est = pnl_pct_gross - fee_pct_round_trip
    src = str(plan.get("entry_price_source") or "plan_recorded")
    meta: dict = {
        "fee_pct_round_trip": round(fee_pct_round_trip, 4),
        "pnl_pct_net_est": round(pnl_pct_net_est, 4),
        "fx_spread_pct_round_trip": round(fx_spread_pct_round_trip, 4),
        NET_FIELD: round(pnl_pct_net_est - fx_spread_pct_round_trip, 4),
        "net_backfilled_offline": True,
        "net_backfill_entry_source": src,
    }
    # krw 필드는 FX(usd_krw_at_fill) 있을 때만
    if market == "US":
        entry_fx = float(plan.get("usd_krw_at_fill") or 0)
        if entry_fx > 0:
            exit_fx = entry_fx  # 오프라인엔 exit FX 미상 → 진입 FX로 근사(pct 필드엔 무영향)
            entry_cost_krw = entry * qty * entry_fx
            exit_value_krw = exit_px * qty * exit_fx
        else:
            entry_cost_krw = exit_value_krw = 0.0
    else:
        entry_cost_krw = entry * qty
        exit_value_krw = exit_px * qty
    if entry_cost_krw > 0 and exit_value_krw > 0:
        fee_krw_est = entry_cost_krw * buy_rate + exit_value_krw * sell_rate
        fx_spread_krw_est = (entry_cost_krw + exit_value_krw) * fx_spread_rate
        meta["fee_krw_est"] = round(fee_krw_est, 0)
        meta["pnl_krw_net_est"] = round(exit_value_krw - entry_cost_krw - fee_krw_est, 0)
        meta["fx_spread_krw_est"] = round(fx_spread_krw_est, 0)
        meta["pnl_krw_net_after_fx_est"] = round(
            exit_value_krw - entry_cost_krw - fee_krw_est - fx_spread_krw_est, 0
        )
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="net_after_fx 소급 백필 (기본 dry-run)")
    ap.add_argument("--apply", action="store_true", help="실제 DB 기록(기본은 dry-run)")
    ap.add_argument("--db", default=None, help="v2_event_store.db 경로 override")
    args = ap.parse_args()

    ro_store = EventStore(args.db, read_only=True, initialize=False)
    with ro_store.connect() as conn:
        conn.execute("PRAGMA busy_timeout=8000")
        rows = conn.execute(
            "SELECT path_run_id, market, session_date, plan_json "
            "FROM v2_path_runs WHERE status='CLOSED'"
        ).fetchall()

    todo: list[tuple[str, dict]] = []
    skip = Counter()
    for r in rows:
        try:
            plan = json.loads(r["plan_json"]) if r["plan_json"] else {}
        except Exception:
            plan = {}
        if plan.get(NET_FIELD) is not None:
            skip["already_has_net"] += 1
            continue
        plan["_market"] = r["market"]
        meta = _compute_net(plan)
        if meta is None:
            skip["input_missing(entry/qty/exit)"] += 1
            continue
        todo.append((r["path_run_id"], meta))

    print("=== net_after_fx 백필 대상 분석 ===")
    print(f"  CLOSED 총 {len(rows)}건")
    for k, v in skip.items():
        print(f"  skip {k}: {v}")
    print(f"  백필 대상: {len(todo)}건")
    if todo:
        sample = todo[:5]
        print("  샘플(net_after_fx):", [(pid[:14], m[NET_FIELD]) for pid, m in sample])

    if not args.apply:
        print("\n[dry-run] --apply 없이는 기록하지 않음.")
        return 0

    wr_store = EventStore(args.db, read_only=False, initialize=False)
    n = 0
    for pid, meta in todo:
        wr_store.update_path_run(pid, plan=meta, merge_plan=True)
        n += 1
    print(f"\n[apply] {n}건 net 필드 소급 merge 완료(status·트리거 무접촉).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
