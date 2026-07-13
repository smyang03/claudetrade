"""⚠️ DEPRECATED (2026-07-14) — 시간축 누수가 있어 판정에 쓰지 마라.

이 도구는 플랜 생성 시각을 읽지 않고 **당일 전체 일봉 저가**로 존 도달을 판정한다.
14시에 만든 플랜인데 저가가 09:30이면 체결 불가인데도 "체결"로 센다.
docstring의 "무-lookahead"는 거짓이었다.

★대체: `tools/rr_reject_causal_replay.py` — 플랜 거부 시각 이후 분봉만 본다.
실측 차이(RR 거부 코호트): KR이 −1.82%(이 도구) → **+1.39%(인과 리플레이)** 로 **부호가 반대**였다.
US는 −2.05% → −1.99%로 결론 동일.
진단 참고용으로만 남긴다.
---
RR 거부 코호트 shadow 추적 — 시장별로, 거부된 플랜을 살렸다면 net이 어땠나. read-only.

배경(2026-07-13 진단):
- `PATHB_CONSISTENT_REWARD_RISK=true`가 위험 분모를 존 하단 → 존 상단으로 바꿔 RR 중앙값이
  2.22 → 1.08로 반토막났는데, 차단 임계는 재캘리브레이션되지 않았다.
- ★배선 결함: `execution/safety_gate.py:194`가 `plan.validate(min_confidence=...)`만 넘겨
  `min_reward_risk` 기본값 **1.2**를 쓴다. 즉 env의 `PATHB_MIN_REWARD_RISK_KR=1.1` /
  `PATHB_MIN_REWARD_RISK=1.5`가 **차단 경로에 반영되지 않는다**(생성 경로만 반영).
  → KR은 의도(1.1)보다 엄격, US는 의도(1.5)보다 느슨하게 돌고 있다.
- 시장별 실측: RR 거부 81건 중 US 75 / KR 6. 두 시장의 병이 다르다.

방법(무-lookahead, 비용 포함, fail-closed):
- SAFETY_BLOCKED(reward_risk_below_minimum) payload의 `raw_plan`으로 거부된 플랜을 복원한다.
- 일봉으로 반사실 체결: 세션 당일 저가가 buy_zone_high 이하로 내려오면 존 진입으로 보고
  **buy_zone_high에 체결**(보수적 — 승자 81%가 존 하단 밖에서 체결됐다는 실증과 정합).
- 보유 hold_days 동안: 고가가 sell_target 이상이면 익절, 저가가 stop_loss 이하면 손절.
  같은 날 둘 다 닿으면 **손절 우선**(장중 순서를 모르므로 fail-closed).
- 미도달이면 보유 만기 종가 청산. net = 총수익률 − 왕복비용(KR 0.21% / US 0.50%)."""
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
# 운영자 의도 임계(env). 차단 경로는 현재 1.2 하드코딩이라 이 값이 안 먹는다.
INTENDED_HURDLE = {"KR": 1.1, "US": 1.5}
GATE_HURDLE_ACTUAL = 1.2


def _price_rows(market: str, ticker: str) -> list[dict[str, float]]:
    path = ROOT / "data" / "price" / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    rows: list[dict[str, float]] = []
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


def replay_plan(market: str, ticker: str, session_date: str, plan: dict[str, Any]) -> dict[str, Any] | None:
    try:
        zone_high = float(plan["buy_zone_high"])
        zone_low = float(plan["buy_zone_low"])
        target = float(plan["sell_target"])
        stop = float(plan["stop_loss"])
    except (KeyError, TypeError, ValueError):
        return None
    if zone_high <= stop or target <= zone_high:
        return None
    hold_days = max(1, int(plan.get("hold_days") or 1))
    reward_risk = (target - zone_high) / (zone_high - stop)

    bars = _price_rows(market, ticker)
    if not bars:
        return None
    entry_index = next((i for i, bar in enumerate(bars) if bar["date"] == session_date), None)
    if entry_index is None:
        return None

    entry_bar = bars[entry_index]
    if entry_bar["low"] > zone_high:
        # 존에 닿지 않았다 — 거부하지 않았어도 체결되지 않았을 것이다.
        return {
            "market": market,
            "ticker": ticker,
            "session_date": session_date,
            "reward_risk": reward_risk,
            "filled": False,
            "exit_reason": "zone_not_touched",
            "net_pct": None,
        }

    entry_price = zone_high  # 보수적 체결가
    horizon = bars[entry_index : entry_index + hold_days + 1]
    exit_price, exit_reason = None, "hold_expired"
    for bar in horizon[1:] if len(horizon) > 1 else horizon:
        if bar["low"] <= stop:  # fail-closed: 손절 우선
            exit_price, exit_reason = stop, "stop_loss"
            break
        if bar["high"] >= target:
            exit_price, exit_reason = target, "target"
            break
    if exit_price is None:
        exit_price = horizon[-1]["close"]

    gross = (exit_price / entry_price - 1.0) * 100.0
    return {
        "market": market,
        "ticker": ticker,
        "session_date": session_date,
        "reward_risk": reward_risk,
        "filled": True,
        "exit_reason": exit_reason,
        "net_pct": gross - COST_PCT[market],
        "zone_low": zone_low,
        "entry_price": entry_price,
    }


def load_rejected(event_db: Path) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{event_db}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            """
            SELECT market, ticker, session_date, payload_json FROM lifecycle_events
            WHERE event_type='SAFETY_BLOCKED' AND payload_json LIKE '%reward_risk_below_minimum%'
            ORDER BY event_id
            """
        ).fetchall()
    finally:
        con.close()
    output: list[dict[str, Any]] = []
    for market, ticker, session_date, raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        plan = payload.get("raw_plan")
        if not isinstance(plan, dict):
            continue
        output.append(
            {
                "market": str(market or "").upper(),
                "ticker": str(ticker or ""),
                "session_date": str(session_date or ""),
                "plan": plan,
            }
        )
    return output


def summarize(results: list[dict[str, Any]], market: str) -> dict[str, Any]:
    group = [row for row in results if row["market"] == market]
    filled = [row for row in group if row["filled"] and row["net_pct"] is not None]
    nets = [row["net_pct"] for row in filled]
    intended = INTENDED_HURDLE.get(market, GATE_HURDLE_ACTUAL)
    # ⚠️ raw_plan은 정규화 전 원본이라 여기서 재계산한 RR은 게이트가 실제로 판정한 RR과 다를 수 있다
    # (실측: US 거부 75건 중 52건이 재계산상 1.2 이상). 그래서 이 분할은 참고용이며,
    # 판정에 쓰는 값은 코호트 전체의 반사실 net이다.
    below_intended = [row["net_pct"] for row in filled if row["reward_risk"] < intended]
    above_intended = [row["net_pct"] for row in filled if row["reward_risk"] >= intended]
    output: dict[str, Any] = {
        "market": market,
        "rejected_n": len(group),
        "zone_touched_n": len(filled),
        "zone_not_touched_n": len(group) - len(filled),
        "gate_hurdle_actual": GATE_HURDLE_ACTUAL,
        "intended_hurdle": intended,
        "recomputed_rr_caveat": "raw_plan 기준 재계산 — 게이트 판정 RR과 불일치 가능. 분할은 참고용.",
    }
    if nets:
        output.update(
            {
                "mean_net_pct": round(statistics.mean(nets), 3),
                "median_net_pct": round(statistics.median(nets), 3),
                "win_rate": round(sum(1 for x in nets if x > 0) / len(nets), 3),
                "exit_reasons": {
                    reason: sum(1 for row in filled if row["exit_reason"] == reason)
                    for reason in sorted({row["exit_reason"] for row in filled})
                },
            }
        )
    if below_intended:
        output["below_intended_hurdle"] = {
            "n": len(below_intended),
            "mean_net_pct": round(statistics.mean(below_intended), 3),
        }
    if above_intended:
        output["above_intended_hurdle"] = {
            "n": len(above_intended),
            "mean_net_pct": round(statistics.mean(above_intended), 3),
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="RR 거부 코호트 반사실 shadow (read-only)")
    parser.add_argument("--event-db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--market", default="KR,US")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    markets = [value.strip().upper() for value in args.market.split(",") if value.strip()]
    rejected = load_rejected(Path(args.event_db))
    results: list[dict[str, Any]] = []
    skipped = 0
    for item in rejected:
        if item["market"] not in markets:
            continue
        replay = replay_plan(item["market"], item["ticker"], item["session_date"], item["plan"])
        if replay is None:
            skipped += 1
            continue
        results.append(replay)

    report = {
        "rejected_events": len(rejected),
        "replayed": len(results),
        "skipped_no_price_or_invalid": skipped,
        "markets": [summarize(results, market) for market in markets],
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"RR 거부 이벤트 {report['rejected_events']}건 | 리플레이 {report['replayed']} | 스킵 {skipped}")
    print(f"(차단 임계 실제={GATE_HURDLE_ACTUAL} — env의 시장별 임계는 safety_gate에 안 먹는다)")
    for summary in report["markets"]:
        print(f"\n=== {summary['market']} (의도 임계 {summary['intended_hurdle']}) ===")
        print(f"  거부 {summary['rejected_n']}건 | 존 진입 {summary['zone_touched_n']} | 미진입 {summary['zone_not_touched_n']}")
        if "mean_net_pct" in summary:
            print(
                "  ★살렸다면: 평균 net %+.3f%% | 중앙 %+.3f%% | 승률 %.0f%%"
                % (summary["mean_net_pct"], summary["median_net_pct"], 100 * summary["win_rate"])
            )
            print(f"  청산사유: {summary['exit_reasons']}")
        for key in ("below_intended_hurdle", "above_intended_hurdle"):
            if key in summary:
                block = summary[key]
                print("  %-24s n=%-3d 평균 net %+.3f%%" % (key, block["n"], block["mean_net_pct"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
