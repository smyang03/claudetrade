"""진입 추격 폭 관측기 — 진입가가 관측가보다 얼마나 비싼가 (capture 병목 계측). read-only.

2026-07-24 종목검토 발견: 275280 관측가 40745 → 진입가 45085 = +10.7% 추격, forward(관측가
기준) +9.45%를 다 먹고 진입해 mfe −1.09%로 물림. 관측 시점 상승 기회를 추격으로 반납하는
capture 병목의 종목 메커니즘. 그런데 추격 폭이 어느 원장에도 안 붙어(audit entry_price 0건)
forward를 뒤져서야 봤다. 여기서 진입가(v2_learning)와 관측가(FORWARD_MEASURED base_close)를
ticker+세션으로 조인해 추격 폭을 상시 집계한다. 라이브 무영향.

추격 폭 = 진입가/관측가 − 1. forward_capture = 추격이 관측시점 forward를 얼마나 먹었나.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"


def _observed(since: str) -> dict:
    """ticker+session → (base_close, 대표 forward%). FORWARD_MEASURED에서."""
    if not EVENT_DB.exists():
        return {}
    c = sqlite3.connect(str(EVENT_DB))
    c.execute("PRAGMA busy_timeout=5000")
    c.row_factory = sqlite3.Row
    out: dict = {}
    for r in c.execute(
        "SELECT ticker, session_date, payload_json FROM lifecycle_events "
        "WHERE event_type='FORWARD_MEASURED' AND session_date>=?", (since,)):
        try:
            p = json.loads(r["payload_json"])
        except Exception:
            continue
        base = p.get("base_close")
        fr = p.get("forward_returns") or {}
        if not base or not fr:
            continue
        h = sorted(fr.keys(), key=lambda x: int("".join(filter(str.isdigit, x)) or 99))[0]
        out[(str(r["ticker"]), str(r["session_date"])[:10])] = (float(base), float(fr[h]))
    c.close()
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default="2026-07-01")
    args = p.parse_args()

    observed = _observed(args.since)
    c = sqlite3.connect(str(DECISIONS_DB))
    c.execute("PRAGMA busy_timeout=5000")
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT ticker, market, entry_price, strategy, session_date, pnl_pct_net "
        "FROM v2_learning_performance WHERE entry_price>0 AND session_date>=?",
        (args.since,)).fetchall()
    c.close()

    recs = []
    for r in rows:
        key = (str(r["ticker"]), str(r["session_date"])[:10])
        obs = observed.get(key)
        if obs is None:
            continue
        base, fwd = obs
        if base <= 0:
            continue
        chase = (float(r["entry_price"]) / base - 1.0) * 100.0
        recs.append({"market": r["market"], "ticker": r["ticker"], "strategy": r["strategy"] or "",
                     "chase_pct": chase, "obs_forward_pct": fwd, "net": r["pnl_pct_net"]})

    print(f"=== 진입 추격 폭 (진입가 vs 관측가 base_close, since {args.since}) ===")
    print(f"조인된 진입 {len(recs)}건 (v2_learning entry_price × FORWARD_MEASURED base_close)\n")
    if not recs:
        print("  매칭 0건 — 진입가/관측가 조인 데이터 부족. 라이브 배선(진입시 추격폭 기록)이 근본.")
        return 0
    chases = [x["chase_pct"] for x in recs]
    print(f"  전체: 추격 중앙 {statistics.median(chases):+.2f}% · 평균 {statistics.mean(chases):+.2f}% · "
          f">3% {sum(1 for x in chases if x>3)}건 · >5% {sum(1 for x in chases if x>5)}건")
    # 시장/전략별
    from collections import defaultdict
    by = defaultdict(list)
    for x in recs:
        by[(x["market"], x["strategy"])].append(x["chase_pct"])
    print("  시장×전략별 추격 중앙값:")
    for (m, s), v in sorted(by.items(), key=lambda kv: -statistics.median(kv[1])):
        if len(v) >= 2:
            print(f"    {m} {s or '-'}: 중앙 {statistics.median(v):+.2f}% (n={len(v)})")
    # 추격이 forward를 먹은 사례 (추격>forward의 절반)
    print("  ★추격이 관측 상승분을 먹은 top:")
    recs.sort(key=lambda x: x["chase_pct"], reverse=True)
    for x in recs[:6]:
        print(f"    {x['market']} {x['ticker']}: 추격 {x['chase_pct']:+.1f}% vs 관측forward {x['obs_forward_pct']:+.1f}% "
              f"({x['strategy'] or '-'})")
    print("\n※ 추격 폭이 forward에 근접할수록 관측 상승 기회를 진입에서 반납. 라이브 배선(진입시 기록)"
          "\n  으로 표본 축적 후 추격 캡 임계 검증(운영자 승인).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
