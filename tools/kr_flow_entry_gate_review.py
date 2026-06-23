"""KR 수급 진입 게이트 shadow 리뷰 — would_skip(flow-negative) 진입 vs allow 진입 net 비교.

KR_FLOW_ENTRY_GATE_MODE=shadow로 수집된 logs/funnel/kr_flow_entry_gate_*_KR.jsonl을 읽어,
게이트가 would_skip(flow-negative)으로 표시한 진입과 allow 진입의 실현 net(decisions.db)을
비교한다. "flow-negative 진입을 걸렀다면 net이 개선됐나"를 측정하는 검증 도구.

사용: python tools/kr_flow_entry_gate_review.py [--days N]
read-only. 봇/대시보드 실행·API 호출 없음.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUNNEL = os.path.join(ROOT, "logs", "funnel")
DECISIONS_DB = os.path.join(ROOT, "data", "ml", "decisions.db")


def _load_shadow_records() -> list[dict]:
    recs = []
    for f in sorted(glob.glob(os.path.join(FUNNEL, "kr_flow_entry_gate_*_KR.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    return recs


def _realized_net_index() -> dict[tuple, float]:
    """(session_date, ticker) -> 실현 net pct (가장 가까운 청산)."""
    out: dict[tuple, float] = {}
    if not os.path.exists(DECISIONS_DB):
        return out
    con = sqlite3.connect(DECISIONS_DB)
    try:
        rows = con.execute(
            "SELECT ticker, pnl_pct, closed_at FROM v2_learning_performance "
            "WHERE market='KR' AND closed=1 AND pnl_pct IS NOT NULL"
        ).fetchall()
    except Exception:
        rows = []
    con.close()
    for ticker, pnl, closed_at in rows:
        day = str(closed_at or "")[:10]
        if day and ticker is not None:
            out[(day, str(ticker))] = float(pnl)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="최근 N일만(0=전체)")
    args = ap.parse_args()

    recs = _load_shadow_records()
    if not recs:
        print("shadow 레코드 없음 — KR_FLOW_ENTRY_GATE_MODE=shadow로 수집 필요")
        return
    net_idx = _realized_net_index()

    by_decision: dict[str, list[float]] = defaultdict(list)
    matched = 0
    for r in recs:
        sess = str(r.get("session_date") or "")
        ticker = str(r.get("ticker") or "")
        decision = str(r.get("decision") or "")
        # session_date는 청산일과 다를 수 있어 ticker 기준 동일/직후일 매칭 시도
        net = net_idx.get((sess, ticker))
        if net is None:
            # 같은 ticker의 그 날 이후 첫 청산
            cands = sorted([(d, n) for (d, t), n in net_idx.items() if t == ticker and d >= sess])
            net = cands[0][1] if cands else None
        if net is None:
            continue
        matched += 1
        by_decision[decision].append(net)

    print(f"shadow 레코드 {len(recs)}건 / 실현 net 매칭 {matched}건\n")

    def stat(name: str, xs: list[float]) -> None:
        if not xs:
            print(f"  {name:14}: n=0")
            return
        pos = sum(1 for x in xs if x > 0)
        print(f"  {name:14}: n={len(xs):3d} mean={statistics.mean(xs):+.2f}% "
              f"med={statistics.median(xs):+.2f}% 양수율={pos/len(xs)*100:.0f}%")

    print("게이트 판정별 실현 net:")
    for d in ("allow", "would_skip", "skip", "allow_no_flow"):
        stat(d, by_decision.get(d, []))

    allow = by_decision.get("allow", []) + by_decision.get("allow_no_flow", [])
    skip = by_decision.get("would_skip", []) + by_decision.get("skip", [])
    if allow and skip:
        print(f"\n핵심: flow-negative(would_skip) net 평균 {statistics.mean(skip):+.2f}% "
              f"vs allow {statistics.mean(allow):+.2f}%")
        print(f"  → would_skip이 allow보다 낮으면 'flow-negative 제외'가 net 개선(가설 지지).")


if __name__ == "__main__":
    main()
