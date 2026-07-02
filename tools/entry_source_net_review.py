#!/usr/bin/env python3
"""P1 — 스크리너 소스별 진입/net 분해 누적 리뷰 (read-only, 2026-07-02 풀·선정·hold 토론 합의).

질문: 풀 소스 세그먼트(most_actives/day_gainers/...)별로 풀 점유→진입 점유(전환율)→
진입 net이 어떻게 갈리나? 반월 분리로 부호 안정성 추적.

판정문 고정(debate_pool_selection_hold_quality_20260702.md):
- 배합 enforce 기각(최고 세그먼트도 진입 net 음수, 세그먼트→net 전달률 ~28%,
  농축 원인=fill 역학 11.7% vs 4.5%). 이 도구는 다국면 누적 관찰 전용.
- kill: 7월 반월 재측정에서 most_actives vs 나머지 net 격차 부호 반전 시 P1 축 폐기.

DB가 원장이므로 상태 파일 없이 매 실행이 전 구간 누적 조회다. mode=ro.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EVENT_DB = ROOT / "data" / "v2_event_store.db"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def _half(sd: str) -> str:
    return sd[:7] + ("-H1" if sd[8:10] <= "15" else "-H2")


def _stats(vals: list[float]) -> str:
    v = [x for x in vals if x is not None]
    if not v:
        return "n=0"
    return f"n={len(v)} mean={st.mean(v):+.2f} med={st.median(v):+.2f} 합={sum(v):+.1f}"


def main() -> int:
    ap = argparse.ArgumentParser(description="P1 소스별 진입/net 분해 (read-only)")
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--market", choices=["KR", "US"], default="US")
    args = ap.parse_args()

    # 진입(CLOSED) 로드
    c1 = sqlite3.connect(f"file:{EVENT_DB.resolve().as_posix()}?mode=ro", uri=True, timeout=15)
    c1.execute("PRAGMA busy_timeout=10000")
    entries = []  # (sd, ticker, net)
    for tk, sd, pj in c1.execute(
        "SELECT ticker, session_date, plan_json FROM v2_path_runs "
        "WHERE status='CLOSED' AND market=? AND session_date>=?",
        (args.market, args.since),
    ):
        d = json.loads(pj or "{}")
        ne = d.get("pnl_pct_net_est")
        entries.append((str(sd), str(tk), ne if ne is not None else d.get("pnl_pct")))
    c1.close()

    # 소스 매핑 + 풀 점유 (세션·티커 유니크)
    c2 = sqlite3.connect(f"file:{AUDIT_DB.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    c2.execute("PRAGMA busy_timeout=20000")
    pool = defaultdict(set)  # source -> {(sd,ticker)}
    for sd, tk, src in c2.execute(
        "SELECT DISTINCT session_date, ticker, candidate_source FROM audit_candidate_rows "
        "WHERE market=? AND session_date>=?",
        (args.market, args.since),
    ):
        pool[str(src) if src else "unknown"].add((str(sd), str(tk)))
    # 귀속 규칙: 한 (세션,티커)가 여러 소스로 등장하면 명명 소스 우선(unknown이 가리는 것 방지),
    # 명명 소스가 복수면 사전순 첫 소스(결정론). 이 규칙이 실행마다 동일해야 반월 추적이 유효.
    key_sources = defaultdict(set)
    for s, keys in pool.items():
        for k in keys:
            key_sources[k].add(s)
    src_of = {}
    for k, ss in key_sources.items():
        named = sorted(s for s in ss if s not in ("unknown", "None", ""))
        src_of[k] = named[0] if named else "unknown"
    c2.close()

    # 집계
    by_src = defaultdict(list)          # source -> [net]
    by_half_src = defaultdict(list)     # (half, source) -> [net]
    for sd, tk, ne in entries:
        s = src_of.get((sd, tk), "unmapped")
        by_src[s].append(ne)
        by_half_src[(_half(sd), s)].append(ne)

    total_entries = len(entries)
    total_pool = sum(len(v) for v in pool.values())
    print(f"=== P1 소스별 진입/net 분해 ({args.market}, since {args.since}) ===")
    print(f"진입 {total_entries}건 / 풀 ticker-day {total_pool}건\n")
    print(f"{'source':16} {'풀점유':>7} {'진입점유':>8} {'전환율':>7}  net")
    for s in sorted(by_src, key=lambda x: -len(by_src[x])):
        pool_n = len(pool.get(s, set()))
        ent_n = len(by_src[s])
        conv = 100 * ent_n / pool_n if pool_n else 0
        print(f"{s:16} {100*pool_n/total_pool if total_pool else 0:6.1f}% {100*ent_n/total_entries:7.1f}% "
              f"{conv:6.1f}%  {_stats(by_src[s])}")

    print("\n[반월 분리 — most_actives vs 나머지 (kill 감시: 격차 부호)]")
    halves = sorted({h for h, _ in by_half_src})
    for h in halves:
        ma = by_half_src.get((h, "most_actives"), [])
        rest = [x for (hh, s), v in by_half_src.items() if hh == h and s != "most_actives" for x in v]
        ma_v = [x for x in ma if x is not None]
        rest_v = [x for x in rest if x is not None]
        if ma_v and rest_v:
            gap = st.mean(ma_v) - st.mean(rest_v)
            print(f"  {h}: most_actives {_stats(ma_v)}  | 나머지 {_stats(rest_v)}  | 격차 {gap:+.2f}pp")
        else:
            print(f"  {h}: ma n={len(ma_v)} / 나머지 n={len(rest_v)} — 비교 불능")
    print("\n주: 관찰 전용(배합 enforce 기각 판정). 격차 부호가 반월 연속 반전되면 P1 축 폐기.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
