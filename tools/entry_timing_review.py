"""진입시각 관측 리뷰 — logs/funnel/entry_timing_*.jsonl을 시장별 분리 집계.
가능하면 v2_canonical_performance(closed live)와 join해 버킷별 우리 net을 보여준다.

사용: python tools/entry_timing_review.py
근거: C 장초반 진입축 forward +0.17 (soft gate 완화는 기각 이력 — 관측만).
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
from collections import defaultdict

FUNNEL = os.path.join("logs", "funnel")
DECISIONS_DB = os.path.join("data", "ml", "decisions.db")


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def main() -> int:
    rows = []
    for f in sorted(glob.glob(os.path.join(FUNNEL, "entry_timing_*.jsonl"))):
        try:
            for line in open(f, encoding="utf-8"):
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
        except OSError:
            continue
    if not rows:
        print("진입시각 기록 없음 — 실진입이 쌓이면 채워짐(prospective).")
        return 0

    net = {}
    try:
        con = sqlite3.connect(f"file:{DECISIONS_DB}?mode=ro", uri=True)
        con.execute("pragma busy_timeout=5000")
        q = (
            "select market, session_date, ticker, coalesce(pnl_pct_net, pnl_pct) "
            "from v2_canonical_performance where runtime_mode='live' and closed=1"
        )
        for m, d, t, p in con.execute(q):
            if p is not None:
                net[(str(m), str(d), str(t))] = float(p)
        con.close()
    except Exception:
        pass

    print(f"진입시각 기록 {len(rows)}건\n")
    for mkt in sorted({r.get("market") or "?" for r in rows}):
        sub = [r for r in rows if (r.get("market") or "?") == mkt]
        print(f"===== {mkt} (n={len(sub)}) =====")
        by_bucket = defaultdict(list)
        for r in sub:
            by_bucket[str(r.get("bucket") or "unknown")].append(r)
        for bucket, rs in sorted(by_bucket.items()):
            pnls = []
            for r in rs:
                day = str(r.get("written_at") or "")[:10]
                key = (mkt, day, str(r.get("ticker") or ""))
                if key in net:
                    pnls.append(net[key])
            m = _mean(pnls)
            net_txt = f" | net n={len(pnls)} avg={m:+.3f}%" if m is not None else ""
            print(f"  {bucket:16} n={len(rs):>4}{net_txt}")
        print()
    print("판정 규율: 시장별로만 판정. soft gate 완화 결정은 운영자 확인 필수.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
