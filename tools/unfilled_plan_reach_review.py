"""미체결 플랜 소급 검토 — 눌림 존에 도달했나, 못 산 게 손해였나. read-only.

2026-07-13 진단으로 드러난 구조:
- 6/29 이후 생성된 v2_path_runs 94건이 **전부** `origin_action=PULLBACK_WAIT`,
  `origin_route=pathb_wait_only`다. 즉시 매수 플랜이 **0건**이다.
- 그중 체결 3 / 취소 79 / 만료 12. 눌림 존에 가격이 오지 않으면 그대로 소멸한다.

그래서 두 개를 나눠 물어야 한다:
  (A) 존에 닿았는데 취소돼서 못 샀다 → 그 취소가 옳았나?
  (B) 존에 아예 안 닿았다 → 눌림을 기다리지 말고 참조가에 샀으면 어땠나?

무-lookahead·비용 포함·fail-closed:
- 체결가는 (A) buy_zone_high(보수적), (B) reference_price.
- 보유 hold_days 동안 고가>=sell_target 익절, 저가<=stop_loss 손절.
  같은 날 둘 다 닿으면 손절 우선(장중 순서 불명 → fail-closed).
- 미도달이면 만기 종가 청산. net = 수익률 − 왕복비용(KR 0.21% / US 0.50%).
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COST_PCT = {"KR": 0.21, "US": 0.50}


def _bars(market: str, ticker: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / "price" / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                rows.append(
                    {
                        "date": str(row["date"])[:10],
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    rows.sort(key=lambda item: item["date"])
    return rows


def _exit_net(bars: list[dict[str, Any]], start: int, entry: float, target: float, stop: float, hold: int, market: str) -> tuple[float, str]:
    horizon = bars[start : start + hold + 1]
    exit_price, reason = None, "hold_expired"
    for bar in horizon[1:] or horizon:
        if bar["low"] <= stop:  # fail-closed
            exit_price, reason = stop, "stop_loss"
            break
        if bar["high"] >= target:
            exit_price, reason = target, "target"
            break
    if exit_price is None:
        exit_price = horizon[-1]["close"]
    gross = (exit_price / entry - 1.0) * 100.0
    return gross - COST_PCT[market], reason


def review_plan(market: str, ticker: str, session: str, plan: dict[str, Any], status: str) -> dict[str, Any] | None:
    try:
        zone_high = float(plan["buy_zone_high"])
        target = float(plan["sell_target"])
        stop = float(plan["stop_loss"])
        reference = float(plan.get("reference_price") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if zone_high <= stop or target <= zone_high:
        return None
    hold = max(1, int(plan.get("hold_days") or 1))
    bars = _bars(market, ticker)
    if not bars:
        return None
    start = next((i for i, bar in enumerate(bars) if bar["date"] == session), None)
    if start is None:
        return None

    window = bars[start : start + hold + 1]
    # ★도달 판정은 두 개를 분리해야 한다.
    #  - same_day: 플랜이 실제로 살아있던 당일에 존에 닿았나 (시스템의 EXPIRED 판정과 비교 가능)
    #  - within_hold: 보유기간 전체 어느 날이든 닿았나 (넓게 잡으면 과대평가된다)
    # 처음에 within_hold만 봤더니 EXPIRED(=시스템이 미도달로 판정)까지 100% 도달로 나와 모순이었다.
    zone_reached_same_day = bars[start]["low"] <= zone_high
    zone_reached_within_hold = any(bar["low"] <= zone_high for bar in window)

    row: dict[str, Any] = {
        "market": market,
        "ticker": ticker,
        "session_date": session,
        "status": status,
        "cancel_reason": str(plan.get("cancel_reason") or ("EXPIRED" if status == "EXPIRED" else "")),
        "zone_reached": zone_reached_same_day,
        "zone_reached_within_hold": zone_reached_within_hold,
        "net_if_zone_filled": None,
        "net_if_bought_at_reference": None,
    }
    zone_reached = zone_reached_same_day
    if zone_reached:
        net, reason = _exit_net(bars, start, zone_high, target, stop, hold, market)
        row["net_if_zone_filled"] = round(net, 3)
        row["zone_exit_reason"] = reason
    if reference > stop:
        net, reason = _exit_net(bars, start, reference, target, stop, hold, market)
        row["net_if_bought_at_reference"] = round(net, 3)
        row["reference_exit_reason"] = reason
    return row


def _stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 3),
        "median": round(statistics.median(values), 3),
        "win_rate": round(sum(1 for x in values if x > 0) / len(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="미체결 플랜 존 도달률·소급 수익 (read-only)")
    parser.add_argument("--event-db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--since", default="2026-06-29")
    parser.add_argument("--market", default="KR,US")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    markets = [value.strip().upper() for value in args.market.split(",") if value.strip()]
    con = sqlite3.connect(f"file:{args.event_db}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            """
            SELECT market, ticker, session_date, status, plan_json FROM v2_path_runs
            WHERE session_date>=? AND status IN ('CANCELLED','EXPIRED') AND plan_json IS NOT NULL
            """,
            (args.since,),
        ).fetchall()
    finally:
        con.close()

    results: list[dict[str, Any]] = []
    skipped = 0
    for market, ticker, session, status, raw in rows:
        market = str(market or "").upper()
        if market not in markets:
            continue
        try:
            plan = json.loads(raw or "{}")
        except (TypeError, ValueError):
            skipped += 1
            continue
        review = review_plan(market, str(ticker), str(session), plan, str(status))
        if review is None:
            skipped += 1
            continue
        results.append(review)

    report: dict[str, Any] = {"unfilled_n": len(rows), "reviewed": len(results), "skipped": skipped, "markets": {}}
    for market in markets:
        group = [row for row in results if row["market"] == market]
        if not group:
            continue
        reached = [row for row in group if row["zone_reached"]]
        zone_nets = [row["net_if_zone_filled"] for row in reached if row["net_if_zone_filled"] is not None]
        ref_nets = [row["net_if_bought_at_reference"] for row in group if row["net_if_bought_at_reference"] is not None]
        by_reason: dict[str, Any] = {}
        for reason in sorted({row["cancel_reason"] for row in group}):
            subset = [
                row["net_if_zone_filled"]
                for row in group
                if row["cancel_reason"] == reason and row["net_if_zone_filled"] is not None
            ]
            by_reason[reason] = {
                "n": sum(1 for row in group if row["cancel_reason"] == reason),
                "zone_reached_n": sum(1 for row in group if row["cancel_reason"] == reason and row["zone_reached"]),
                "net_if_filled": _stats(subset),
            }
        within = [row for row in group if row["zone_reached_within_hold"]]
        report["markets"][market] = {
            "unfilled_n": len(group),
            "zone_reached_same_day_n": len(reached),
            "zone_reach_rate_same_day": round(len(reached) / len(group), 3),
            "zone_reached_within_hold_n": len(within),
            "zone_reached_n": len(reached),
            "zone_reach_rate": round(len(reached) / len(group), 3),
            "net_if_zone_filled": _stats(zone_nets),
            "net_if_bought_at_reference": _stats(ref_nets),
            "by_cancel_reason": by_reason,
        }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"미체결 플랜 {report['unfilled_n']}건 | 리뷰 {report['reviewed']} | 스킵 {skipped}")
    for market, summary in report["markets"].items():
        print(f"\n=== {market} 미체결 {summary['unfilled_n']}건 ===")
        print(f"  눌림 존 도달(당일): {summary['zone_reached_same_day_n']}/{summary['unfilled_n']} = {summary['zone_reach_rate_same_day']:.0%}"
              f"  | 보유기간 내 도달: {summary['zone_reached_within_hold_n']}")
        zone = summary["net_if_zone_filled"]
        ref = summary["net_if_bought_at_reference"]
        if zone.get("n"):
            print(f"  (A) 존에 닿았는데 못 산 것 → 샀다면 net 평균 {zone['mean']:+.3f}% 중앙 {zone['median']:+.3f}% 승률 {zone['win_rate']:.0%} (n={zone['n']})")
        if ref.get("n"):
            print(f"  (B) 눌림 안 기다리고 참조가 매수 → net 평균 {ref['mean']:+.3f}% 중앙 {ref['median']:+.3f}% 승률 {ref['win_rate']:.0%} (n={ref['n']})")
        print("  취소사유별 (존 도달 / 샀다면 net):")
        for reason, block in summary["by_cancel_reason"].items():
            stats = block["net_if_filled"]
            mean = f"{stats['mean']:+.3f}%" if stats.get("n") else "-"
            print(f"     {reason[:38]:<38} n={block['n']:<3} 도달 {block['zone_reached_n']:<3} net {mean}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
