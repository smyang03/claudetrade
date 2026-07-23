"""BUY_READY RR 게이트 시뮬 — RR 1.5 하드 게이트가 볼록 소폭승을 거르나. read-only.

★질문(2026-07-23 2차):
프롬프트는 "대부분 소폭승, 소수 러너 = 비대칭 사라" 인데, 코드는 각 BUY_READY 플랜에
사전 RR>=1.5 를 강제한다. 손절 2%면 목표 3%(=RR1.5) 이상이어야 통과 → 정직한 소폭목표
(+2%, RR1.0)는 거부된다. 이게 실제로 우리 net을 깎는가, 지키는가?

방법(무-lookahead, rr_reject_causal_replay 엔진 재사용):
- 대상: US in_prompt 후보 중 ret_5m_pct > MIN_RET5 (극단 모멘텀). BUY_READY 후보 근사.
- 진입: known_at 이후 최초 분봉의 close = 즉시매수 체결가(현재가 진입).
- 플랜: stop = entry*(1-s), target = entry*(1+s*RR). s=손절폭, RR 그리드.
- 청산: 진입 이후 분봉에서 stop/target 최초 도달(같은 봉 동시=손절 우선, fail-closed).
  미도달이면 세션 종가(hold_days=1, intraday BUY_READY 성격).
- net = gross - 왕복비용(US 0.50%).
- 분봉 없는 (티커,세션)은 제외(추정 금지).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.rr_reject_causal_replay import _minute_bars, _daily_bars, _parse_ts, COST_PCT

AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def load_candidates(market: str, min_ret5: float, limit: int | None) -> list[dict]:
    c = sqlite3.connect(str(AUDIT_DB))
    c.execute("PRAGMA busy_timeout=5000")
    rows = c.execute(
        "SELECT ticker, session_date, known_at, post_open_features_json "
        "FROM audit_candidate_rows WHERE market=? AND in_prompt=1 "
        "AND known_at IS NOT NULL AND post_open_features_json LIKE '%ret_5m_pct%'",
        (market,),
    ).fetchall()
    c.close()
    out = []
    for ticker, session, known_at, fj in rows:
        try:
            f = json.loads(fj)
        except Exception:
            continue
        r5 = f.get("ret_5m_pct")
        if r5 is None or float(r5) <= min_ret5:
            continue
        out.append({"ticker": ticker, "session_date": str(session)[:10],
                    "known_at": known_at, "ret5": float(r5)})
        if limit and len(out) >= limit:
            break
    return out


def entry_price(market: str, ticker: str, session: str, known_at: str):
    """known_at 이후 최초 분봉 close = 즉시매수 체결가."""
    bars = _minute_bars(market, ticker, session)
    if not bars:
        return None, None
    ka = _parse_ts(known_at)
    if ka is None:
        return None, None
    after = [b for b in bars if b["ts"] >= ka]
    if not after:
        return None, None
    return after[0]["close"], after


def replay_plan(entry: float, after: list[dict], stop_pct: float, rr: float, cost: float,
                market: str = "US", ticker: str = "", session_date: str = "", hold_days: int = 1):
    """즉시진입 후 stop/target 최초도달. hold_days>1이면 다음날 일봉으로 이어봄.

    hold_days=1: 세션 intraday만(당일청산). hold_days>=2: 세션 이후 일봉 stop/target
    추적, 미도달이면 hold_days 마지막 일봉 종가. 실제 BUY_READY는 멀티데이 보유 가능.
    """
    stop = entry * (1 - stop_pct / 100.0)
    target = entry * (1 + (stop_pct * rr) / 100.0)
    # 1) 진입 세션 intraday
    for b in after:
        if b["low"] <= stop:  # fail-closed
            return round((stop / entry - 1.0) * 100.0 - cost, 4)
        if b["high"] >= target:
            return round((target / entry - 1.0) * 100.0 - cost, 4)
    if hold_days <= 1:
        exit_price = after[-1]["close"]  # 당일청산
        return round((exit_price / entry - 1.0) * 100.0 - cost, 4)
    # 2) 다음날부터 일봉 hold_days-1 만큼
    daily = _daily_bars(market, ticker)
    start = next((i for i, bar in enumerate(daily) if bar["date"] == session_date), None)
    if start is None:
        exit_price = after[-1]["close"]
        return round((exit_price / entry - 1.0) * 100.0 - cost, 4)
    horizon = daily[start + 1: start + 1 + (hold_days - 1)]
    for bar in horizon:
        if bar["low"] <= stop:
            return round((stop / entry - 1.0) * 100.0 - cost, 4)
        if bar["high"] >= target:
            return round((target / entry - 1.0) * 100.0 - cost, 4)
    exit_price = horizon[-1]["close"] if horizon else after[-1]["close"]
    return round((exit_price / entry - 1.0) * 100.0 - cost, 4)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--market", default="US", choices=["US", "KR"])
    p.add_argument("--min-ret5", type=float, default=5.0)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--stops", default="1.5,2.0,2.5,3.0")
    p.add_argument("--rrs", default="1.0,1.5,2.0")
    p.add_argument("--hold-days", type=int, default=1)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()

    market = args.market
    cost = COST_PCT[market]
    stops = [float(x) for x in args.stops.split(",")]
    rrs = [float(x) for x in args.rrs.split(",")]

    cands = load_candidates(market, args.min_ret5, args.limit or None)
    # 진입가·분봉 확보된 것만
    resolved = []
    no_data = 0
    for cd in cands:
        e, after = entry_price(market, cd["ticker"], cd["session_date"], cd["known_at"])
        if e is None or not after:
            no_data += 1
            continue
        resolved.append((cd, e, after))

    print(f"{market} ret_5m>{args.min_ret5:g} 후보 {len(cands)}건 | 분봉확보 {len(resolved)}건 | 제외 {no_data}건")
    print(f"(진입=known_at 이후 최초 분봉 close, 즉시매수. 청산=stop/target 최초도달 or 세션종가. cost {cost}%)")
    print()
    results = {}
    for stop_pct in stops:
        for rr in rrs:
            nets = [replay_plan(e, after, stop_pct, rr, cost, market=market,
                                ticker=_cd["ticker"], session_date=_cd["session_date"],
                                hold_days=args.hold_days) for (_cd, e, after) in resolved]
            nets = [n for n in nets if n is not None]
            if not nets:
                continue
            wins = sum(1 for n in nets if n > 0)
            block = {
                "stop_pct": stop_pct, "rr": rr, "target_pct": round(stop_pct * rr, 2),
                "n": len(nets), "mean_net": round(statistics.mean(nets), 4),
                "median_net": round(statistics.median(nets), 4),
                "win_rate": round(wins / len(nets), 3), "total_net": round(sum(nets), 2),
            }
            results[f"s{stop_pct}_rr{rr}"] = block

    # RR 게이트 통과 여부 표시: RR>=1.5 가 게이트(US). RR<1.5 는 거부되는 플랜.
    print(f"{'손절%':>6} {'RR':>5} {'목표%':>6} {'게이트':>6} {'n':>5} {'평균net':>9} {'중앙net':>9} {'승률':>6} {'총net':>9}")
    for stop_pct in stops:
        for rr in rrs:
            b = results.get(f"s{stop_pct}_rr{rr}")
            if not b:
                continue
            gate = "통과" if rr >= 1.5 else "거부"
            print(f"{b['stop_pct']:>6.1f} {b['rr']:>5.1f} {b['target_pct']:>6.2f} {gate:>6} "
                  f"{b['n']:>5} {b['mean_net']:>+9.3f} {b['median_net']:>+9.3f} "
                  f"{b['win_rate']*100:>5.0f}% {b['total_net']:>+9.1f}")

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
