#!/usr/bin/env python3
"""G2 — profit_review 재량구간 시간성분 분해 (read-only, 2026-07-02 갭감사).

질문: profit_review 트리거 → 매도 발주 사이 재량구간(US 중앙 903s 실측)이
가격을 얼마나 태우거나 벌어주나? — LADDER give(~79pp)의 시간축을 처음 절개.

측정:
  (a) 지연 = profit_review_triggered_at → sell_order_sent_at
  (b) 시간비용% = (트리거시점 분봉가 − 실제 체결가) / 트리거시점가 × 100
      양수 = 기다리는 동안 하락(시간이 비용), 음수 = 기다림이 벌어줌(재량 정당)
  지연 버킷(<5m / 5–15m / 15–30m / 30m+)별·출구사유별 양방향 분포.

해석 주의(판정문 고정): 재량구간의 몸통은 CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true
— 운영자 의도 설계다. 이 도구는 그 재량의 가격표를 재는 것이지 단축 처방이 아니다.
한계: profit_review_triggered_at은 plan에 마지막 값만 남는다(다회 트리거 시 최종분).
주문·상태 무접촉, DB mode=ro. 분봉은 기존 수집분만 사용.
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
MIN_DIR = {"US": ROOT / "data" / "price" / "minute" / "us", "KR": ROOT / "data" / "price" / "minute" / "kr"}
SELL_TIME_KEYS = ("sell_order_sent_at", "local_sell_order_at", "sell_pending_resolution_at")
PROFIT_REASONS = {"CLOSED_PROFIT_LADDER", "CLOSED_CLAUDE_PRICE_TARGET", "CLOSED_CLAUDE_SELL",
                  "CLOSED_CLAUDE_INTRADAY_SELL", "CLOSED_CLAUDE_PRICE_PRE_CLOSE"}


def _parse_dt(raw) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=None)  # 벽시계 비교(양쪽 KST 기록)


def _bars_price_at(market: str, ticker: str, ts: datetime) -> float | None:
    """트리거 시각 이하 마지막 분봉 close (기존 수집 CSV, KST)."""
    prefix = "us" if market == "US" else "kr"
    p = MIN_DIR[market] / f"{prefix}_{ticker}.csv"
    if not p.exists():
        return None
    key = ts.strftime("%Y-%m-%dT%H:%M")
    best = None
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            t = str(r.get("ts", ""))[:16]
            if t <= key:
                try:
                    best = float(r["close"])
                except (KeyError, ValueError, TypeError):
                    continue
            elif best is not None:
                break
    return best


def _delay_bucket(sec: float) -> str:
    if sec < 300:
        return "<5m"
    if sec < 900:
        return "5-15m"
    if sec < 1800:
        return "15-30m"
    return "30m+"


def _stats(vals: list[float]) -> str:
    v = sorted(vals)
    if not v:
        return "n=0"
    p90 = v[min(len(v) - 1, int(round(0.9 * (len(v) - 1))))]
    return (f"n={len(v)} mean={st.mean(v):+.3f} med={st.median(v):+.3f} "
            f"p90={p90:+.3f} 합={sum(v):+.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description="G2 profit_review 재량구간 시간분해 (read-only)")
    ap.add_argument("--since", default="2026-06-01", help="session_date 하한")
    ap.add_argument("--market", choices=["KR", "US"], help="시장 필터")
    ap.add_argument("--show-samples", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    rows = conn.execute(
        "SELECT market, ticker, session_date, plan_json FROM v2_path_runs "
        "WHERE status='CLOSED' AND session_date>=? AND plan_json LIKE '%profit_review_triggered_at%'",
        (args.since,),
    ).fetchall()
    conn.close()

    samples = []
    missing_bar = 0
    for market, ticker, sd, pj in rows:
        market = str(market or "").upper()
        if args.market and market != args.market:
            continue
        plan = json.loads(pj or "{}")
        trig_at = _parse_dt(plan.get("profit_review_triggered_at"))
        sell_at = None
        for k in SELL_TIME_KEYS:
            sell_at = _parse_dt(plan.get(k))
            if sell_at:
                break
        if not trig_at or not sell_at:
            continue
        delay = (sell_at - trig_at).total_seconds()
        if delay < 0:
            continue  # 트리거가 최종 매도 이후 갱신된 케이스(다회 트리거 잔재) 제외
        exit_price = float(plan.get("actual_exit_price") or 0)
        trig_price = _bars_price_at(market, str(ticker), trig_at)
        time_cost = None
        if trig_price and trig_price > 0 and exit_price > 0:
            time_cost = (trig_price - exit_price) / trig_price * 100.0
        else:
            missing_bar += 1
        samples.append({
            "market": market, "ticker": ticker, "session_date": sd,
            "close_reason": str(plan.get("close_reason") or ""),
            "delay_sec": round(delay, 1), "bucket": _delay_bucket(delay),
            "trig_price": trig_price, "exit_price": exit_price,
            "time_cost_pct": round(time_cost, 4) if time_cost is not None else None,
            "net_est": plan.get("pnl_pct_net_est"),
        })

    print(f"=== G2 profit_review 재량구간 시간분해 (since {args.since}) ===")
    print(f"표본 {len(samples)}건 (트리거+매도시각 페어), 분봉 결측 {missing_bar}건")
    if not samples:
        print("→ 표본 없음. 체결 재개 후 누적 필요.")
        return 0

    for mkt in ("US", "KR"):
        ms = [s for s in samples if s["market"] == mkt]
        if not ms:
            continue
        print(f"\n===== {mkt} (n={len(ms)}) =====")
        print(f"  지연(s): {_stats([s['delay_sec'] for s in ms])}")
        tc = [s["time_cost_pct"] for s in ms if s["time_cost_pct"] is not None]
        if tc:
            print(f"  시간비용%(+=기다림이 태움 / -=벌어줌): {_stats(tc)}")
            pos = sum(1 for x in tc if x > 0.1)
            neg = sum(1 for x in tc if x < -0.1)
            print(f"  방향: 태움 {pos} / 벌어줌 {neg} / 중립 {len(tc)-pos-neg}")
        print("  지연 버킷별 시간비용:")
        by_b = defaultdict(list)
        for s in ms:
            if s["time_cost_pct"] is not None:
                by_b[s["bucket"]].append(s["time_cost_pct"])
        for b in ("<5m", "5-15m", "15-30m", "30m+"):
            if by_b.get(b):
                print(f"    {b:6}: {_stats(by_b[b])}")
        print("  출구사유별 시간비용:")
        by_r = defaultdict(list)
        for s in ms:
            if s["time_cost_pct"] is not None:
                by_r[s["close_reason"].replace("CLOSED_", "")].append(s["time_cost_pct"])
        for r, v in sorted(by_r.items(), key=lambda x: -len(x[1])):
            print(f"    {r:24}: {_stats(v)}")

    if args.show_samples:
        print("\n개별 표본:")
        for s in sorted(samples, key=lambda x: -(x["time_cost_pct"] or 0)):
            print(f"  {s['session_date']} {s['market']} {s['ticker']:6} {s['close_reason'].replace('CLOSED_',''):20} "
                  f"delay={s['delay_sec']:7.0f}s cost={s['time_cost_pct']} net={s['net_est']}")
    print("\n주: 시간비용 양수=재량구간 동안 가격 하락(시간이 give-back의 범인), 음수=재량이 벌어줌(정당).")
    print("    재량의 몸통은 CLAUDE_REVIEW_ALL_AUTOMATED_SELLS(운영자 의도) — 단축 처방 전 이 분포가 판단 근거.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
