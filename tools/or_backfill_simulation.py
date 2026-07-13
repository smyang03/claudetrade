"""개장 레인지(OR) 누수 봉합 + 백필 배선의 효과를 기존 DB로 시뮬레이션한다. read-only.

배경(2026-07-13):
- Path A 5개 전략이 682/682 신호 0. 최대 사유는 opening_range_pullback의 orp_not_formed(range=0.00%).
- 뿌리: PostOpenFeatureSnapshot이 opening_range_high/low를 저장하지 않아 _or_formed가 영영 False.
- 봉합: 스냅샷에 OR 필드 추가 + 백필이 분봉 df에서 OR을 직접 계산 + _ap()에서 백필 호출 배선.

이 도구는 "봉합 전/후"를 DB로 대조한다:
- BEFORE: post_open 스냅샷에 opening_range_high/low가 둘 다 있는 후보만 OR 보유(= _or_formed 가능).
- AFTER : 원천 일봉이 아니라 **분봉 확보 가능성**을 대리 지표로, bar_count가 or_minutes 이상이면
          백필로 OR을 복구할 수 있다고 본다. bar_count가 없는(first_observed) 후보는
          백필 API가 세션 분봉을 새로 받아오므로 복구 가능 후보로 센다(낙관 상한).

★한계(정직 고지): 백필은 KIS 분봉 API를 호출하므로, 과거 세션에 대해 "실제로 몇 건이 성공했을지"는
소급 확인할 수 없다. 그래서 AFTER는 **상한(ceiling)**이다. 하한은 이미 분봉이 있는(bar_count>=or_minutes)
후보만 센 값이다. 두 값을 함께 보고한다.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sqlite3
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OR_MINUTES = {"US": 15, "KR": 10}


def _payload(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def simulate(audit_db: Path, session: str, market: str) -> dict[str, Any]:
    or_minutes = OR_MINUTES.get(market, 10)
    con = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            """
            SELECT ticker, post_open_features_json, claude_trade_ready
            FROM audit_candidate_rows
            WHERE session_date=? AND market=? AND post_open_features_json IS NOT NULL
            """,
            (session, market),
        ).fetchall()
    finally:
        con.close()

    per_ticker: dict[str, dict[str, Any]] = {}
    for ticker, raw, trade_ready in rows:
        features = _payload(raw)
        if not features:
            continue
        state = per_ticker.setdefault(
            str(ticker),
            {"or_before": False, "bars": 0, "quality": Counter(), "trade_ready": False},
        )
        high = features.get("opening_range_high")
        low = features.get("opening_range_low")
        if high and low:
            state["or_before"] = True
        bars = features.get("bar_count")
        if isinstance(bars, (int, float)):
            state["bars"] = max(state["bars"], int(bars))
        state["quality"][str(features.get("data_quality") or "-")] += 1
        if trade_ready:
            state["trade_ready"] = True

    total = len(per_ticker)
    or_before = sum(1 for s in per_ticker.values() if s["or_before"])
    # 하한: 이미 개장 구간 분봉을 확보한 후보 (백필 없이도 OR 계산 가능한 데이터가 손에 있음)
    or_after_floor = sum(1 for s in per_ticker.values() if s["or_before"] or s["bars"] >= or_minutes)
    # 상한: 백필 API가 세션 분봉을 받아올 수 있다고 가정 (모든 후보)
    or_after_ceiling = total

    quality = Counter()
    for state in per_ticker.values():
        quality.update(state["quality"])

    return {
        "session_date": session,
        "market": market,
        "candidates": total,
        "or_minutes": or_minutes,
        "before": {
            "or_available": or_before,
            "or_missing": total - or_before,
            "rate": round(or_before / total, 3) if total else 0.0,
        },
        "after": {
            "or_available_floor": or_after_floor,
            "or_available_ceiling": or_after_ceiling,
            "recovered_floor": or_after_floor - or_before,
            "recovered_ceiling": or_after_ceiling - or_before,
            "rate_floor": round(or_after_floor / total, 3) if total else 0.0,
        },
        "data_quality": dict(quality.most_common(6)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="OR 누수 봉합 효과 시뮬 (read-only)")
    parser.add_argument("--audit-db", default=str(ROOT / "data" / "audit" / "candidate_audit.db"))
    parser.add_argument("--session", default="2026-07-13")
    parser.add_argument("--market", default="KR,US")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    reports = [
        simulate(Path(args.audit_db), args.session, market.strip().upper())
        for market in args.market.split(",")
        if market.strip()
    ]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0

    for report in reports:
        print(f"\n=== {report['market']} {report['session_date']} (OR 창 {report['or_minutes']}분) ===")
        print(f"  후보 {report['candidates']}종목")
        before, after = report["before"], report["after"]
        print(f"  [봉합 전] OR 보유 {before['or_available']}종목 ({before['rate']:.0%}) · OR 없음 {before['or_missing']}종목")
        print(
            f"  [봉합 후] OR 보유 하한 {after['or_available_floor']}종목 ({after['rate_floor']:.0%}) "
            f"/ 상한 {after['or_available_ceiling']}종목"
        )
        print(f"  ★복구: 하한 +{after['recovered_floor']}종목 / 상한 +{after['recovered_ceiling']}종목")
        print(f"  data_quality: {report['data_quality']}")
    print("\n※ 상한은 백필 API가 세션 분봉을 받아온다고 가정한 값이다(소급 검증 불가).")
    print("   하한은 이미 개장 구간 분봉을 확보한 후보만 센 값이다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
