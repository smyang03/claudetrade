"""RR 거부 코호트 인과 리플레이 — 분봉으로 시간축을 지킨다. read-only.

★왜 다시 쓰는가 (2026-07-14):
기존 `rr_reject_shadow_review.py`는 **당일 전체 일봉 저가**로 존 도달을 판정했다. 플랜 생성 시각을
아예 읽지 않으므로, 14시에 만든 플랜인데 저가가 09:30이면 체결 불가인데도 "체결"로 셌다.
docstring의 "무-lookahead"는 거짓이었고, 그 결과 나온 US −2.05% / KR −1.82%로
"RR 게이트가 옳다"고 결론 낼 수 없다.

이 도구는 시간 인과성을 지킨다:
- 플랜 생성 시각 = SAFETY_BLOCKED 이벤트의 `occurred_at` (거부된 바로 그 순간).
- 존 도달 = **그 시각 이후** 분봉의 저가가 buy_zone_high 이하로 내려온 최초 시점.
- 체결가 = buy_zone_high (보수적 — 승자 81%가 존 하단 밖에서 체결된다는 실증과 정합).
- 청산 = 체결 이후 분봉/일봉에서 stop_loss 또는 sell_target 도달. 같은 봉에서 둘 다 닿으면
  손절 우선(봉 내부 순서 불명 → fail-closed). 미도달이면 보유 만기 종가.
- net = 총수익률 − 왕복비용(KR 0.21% / US 0.50%).

분봉이 없는 (시장,티커,세션)은 **판정에서 제외**한다(추정하지 않는다).
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import statistics
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COST_PCT = {"KR": 0.21, "US": 0.50}
KST = timezone(timedelta(hours=9))


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(KST)


def _session_window(market: str, session_date: str) -> tuple[datetime, datetime] | None:
    """세션의 KST 시간창.

    ★US 세션은 자정을 넘는다(22:30~05:00 KST). KST 날짜로만 필터하면 후반부 분봉이 통째로
    빠져 "생성 이후 봉 없음"이 대량 발생한다(실측 45/75). 거래일 기준 창으로 잡는다.
    """
    try:
        day = datetime.strptime(session_date, "%Y-%m-%d").replace(tzinfo=KST)
    except ValueError:
        return None
    if market.upper() == "US":
        return day + timedelta(hours=22), day + timedelta(days=1, hours=6)
    return day + timedelta(hours=8, minutes=30), day + timedelta(hours=16)


def _minute_bars(market: str, ticker: str, session_date: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / "price" / "minute" / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    window = _session_window(market, session_date)
    if window is None:
        return []
    start, end = window
    bars: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            ts = _parse_ts(row.get("ts"))
            if ts is None or not (start <= ts <= end):
                continue
            try:
                bars.append(
                    {
                        "ts": ts,
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    bars.sort(key=lambda item: item["ts"])
    return bars


def _daily_bars(market: str, ticker: str) -> list[dict[str, Any]]:
    path = ROOT / "data" / "price" / market.lower() / f"{market.lower()}_{ticker}.csv"
    if not path.exists():
        return []
    bars: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            try:
                bars.append(
                    {
                        "date": str(row["date"])[:10],
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
    bars.sort(key=lambda item: item["date"])
    return bars


def replay(
    market: str,
    ticker: str,
    session_date: str,
    created_at: datetime,
    plan: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        zone_high = float(plan["buy_zone_high"])
        target = float(plan["sell_target"])
        stop = float(plan["stop_loss"])
    except (KeyError, TypeError, ValueError):
        return None
    if zone_high <= stop or target <= zone_high:
        return None
    hold_days = max(1, int(plan.get("hold_days") or 1))

    minutes = _minute_bars(market, ticker, session_date)
    if not minutes:
        return {"market": market, "ticker": ticker, "session_date": session_date, "verdict": "no_minute_data"}

    # ★시간 인과성: 플랜이 거부된 시각 이후의 분봉만 본다.
    after = [bar for bar in minutes if bar["ts"] >= created_at]
    if not after:
        return {
            "market": market,
            "ticker": ticker,
            "session_date": session_date,
            "verdict": "no_bars_after_creation",
        }

    entry_bar = next((bar for bar in after if bar["low"] <= zone_high), None)
    if entry_bar is None:
        # 생성 이후 존에 닿지 않았다 → 거부하지 않았어도 체결되지 않았을 것이다(손익 0).
        return {
            "market": market,
            "ticker": ticker,
            "session_date": session_date,
            "verdict": "zone_not_touched_after_creation",
            "net_pct": None,
        }

    entry_price = zone_high  # 보수적 체결가

    # 체결 이후 같은 세션 분봉에서 먼저 손절/익절이 나오는지
    exit_price, exit_reason = None, ""
    for bar in after:
        if bar["ts"] < entry_bar["ts"]:
            continue
        if bar["low"] <= stop:  # fail-closed: 봉 내부 순서 불명 → 손절 우선
            exit_price, exit_reason = stop, "stop_loss_intraday"
            break
        if bar["high"] >= target:
            exit_price, exit_reason = target, "target_intraday"
            break

    if exit_price is None:
        # 다음 날부터 일봉으로 이어서 본다 (hold_days까지)
        daily = _daily_bars(market, ticker)
        start = next((i for i, bar in enumerate(daily) if bar["date"] == session_date), None)
        if start is None:
            exit_price, exit_reason = after[-1]["close"], "session_close"
        else:
            horizon = daily[start + 1 : start + 1 + hold_days]
            for bar in horizon:
                if bar["low"] <= stop:
                    exit_price, exit_reason = stop, "stop_loss"
                    break
                if bar["high"] >= target:
                    exit_price, exit_reason = target, "target"
                    break
            if exit_price is None:
                exit_price = horizon[-1]["close"] if horizon else after[-1]["close"]
                exit_reason = "hold_expired"

    gross = (exit_price / entry_price - 1.0) * 100.0
    return {
        "market": market,
        "ticker": ticker,
        "session_date": session_date,
        "verdict": "filled",
        "entry_at": entry_bar["ts"].isoformat(),
        "created_at": created_at.isoformat(),
        "exit_reason": exit_reason,
        "net_pct": round(gross - COST_PCT[market], 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RR 거부 코호트 인과 리플레이 (분봉, read-only)")
    parser.add_argument("--event-db", default=str(ROOT / "data" / "v2_event_store.db"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{args.event_db}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            """
            SELECT market, ticker, session_date, occurred_at, payload_json FROM lifecycle_events
            WHERE event_type='SAFETY_BLOCKED' AND payload_json LIKE '%reward_risk_below_minimum%'
            ORDER BY event_id
            """
        ).fetchall()
    finally:
        con.close()

    results: list[dict[str, Any]] = []
    for market, ticker, session_date, occurred_at, raw in rows:
        try:
            payload = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        plan = payload.get("raw_plan")
        created = _parse_ts(occurred_at)
        if not isinstance(plan, dict) or created is None:
            continue
        item = replay(str(market).upper(), str(ticker), str(session_date), created, plan)
        if item:
            results.append(item)

    report: dict[str, Any] = {"rejected_events": len(rows), "replayed": len(results), "markets": {}}
    for market in ("KR", "US"):
        group = [row for row in results if row["market"] == market]
        if not group:
            continue
        verdicts: dict[str, int] = {}
        for row in group:
            verdicts[row["verdict"]] = verdicts.get(row["verdict"], 0) + 1
        filled = [row["net_pct"] for row in group if row["verdict"] == "filled" and row["net_pct"] is not None]
        block: dict[str, Any] = {"n": len(group), "verdicts": verdicts}
        if filled:
            block.update(
                {
                    "filled_n": len(filled),
                    "mean_net_pct": round(statistics.mean(filled), 3),
                    "median_net_pct": round(statistics.median(filled), 3),
                    "win_rate": round(sum(1 for x in filled if x > 0) / len(filled), 3),
                }
            )
        report["markets"][market] = block

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"RR 거부 {report['rejected_events']}건 | 리플레이 {report['replayed']}건")
    print("(플랜 거부 시각 이후 분봉만 사용 — 시간 인과성 준수)")
    for market, block in report["markets"].items():
        print(f"\n=== {market} (n={block['n']}) ===")
        for verdict, count in sorted(block["verdicts"].items(), key=lambda item: -item[1]):
            print(f"   {verdict:<32} {count}")
        if "mean_net_pct" in block:
            print(
                "   ★살렸다면(체결분 %d건): 평균 net %+.3f%% | 중앙 %+.3f%% | 승률 %.0f%%"
                % (block["filled_n"], block["mean_net_pct"], block["median_net_pct"], 100 * block["win_rate"])
            )
    print("\n※ 분봉 없는 건은 판정에서 제외했다(추정하지 않음).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
