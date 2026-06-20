from __future__ import annotations

"""Cluster-halt counterfactual ledger (read-only 측정 전용).

cluster halt(당일 손절이 임계 N개 누적되면 신규 진입을 차단하는 라이브 게이트,
STOP_CLUSTER_HARD_BLOCK_COUNT 기본 4)가 실제로 손실을 막았을지, 아니면 회복기
이익(right-tail)을 잘랐을지를 과거 청산 원장으로 사후 측정한다.

토론(2026-06-21 멀티에이전트)에서 cluster halt는 "살아남은 1순위 레버"로 합의됐으나,
유일한 미해결이 "인과 미증명 + right-tail(회복기) 자를 위험"(codex 경고: KR 6/17 8건
+20.12%가 회복기)이었다. 이 도구가 enforce 전 그 검증을 제공한다.

방법: 각 (시장, 세션)에서 손절성 청산을 closed_at 순으로 정렬해 N번째 손절 시각 T를
halt 발동 시점으로 본다. 그 세션에서 T 이후 진입(filled_at > T)한 거래를 "halt였으면
막혔을 진입"으로 보고 그 실제 net 합을 계산한다.
  - net 합이 음수  → halt가 손실을 막았을 것 (cluster halt 이득)
  - net 합이 양수  → halt가 이익을 놓쳤을 것 (right-tail 손해, 위험)

입력은 로컬 sqlite(v2_learning_performance)뿐 — broker/API/Claude 호출 없음. net 컬럼이
비면 gross(pnl_pct)로 fallback하므로 결과는 측정 전용이며 라이브 게이팅에 직접 쓰면 안 된다.
한계: session_date 단위 근사라 multi-day hold 진입은 정확히 분리되지 않을 수 있다(출력에 명시).
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"

# 손절성 청산(누수 집합). cluster halt가 카운트하는 stop과 동일 계열.
STOP_REASONS = {"CLOSED_LOSS_CAP", "CLOSED_HARD_STOP", "CLOSED_CLAUDE_PRICE_STOP"}

# 라이브 게이트 기본 임계(trading_bot.py STOP_CLUSTER_HARD_BLOCK_COUNT)
DEFAULT_THRESHOLD = 4


def _net(row: dict[str, Any]) -> float:
    """net 우선, 없으면 gross fallback (capture_net_review와 동일 규율)."""
    v = row.get("pnl_pct_net")
    if v is None or v == "":
        v = row.get("pnl_pct")
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class ClusterDay:
    market: str
    session_date: str
    stop_count: int
    halt_at: str
    blocked_entries: int
    blocked_net_sum: float
    blocked_winners: int
    blocked_losers: int
    biggest_winner_blocked: float  # right-tail 경고 지표
    tickers: list[str]


def analyze(rows: list[dict[str, Any]], threshold: int = DEFAULT_THRESHOLD) -> tuple[list[ClusterDay], list[float]]:
    """세션별 cluster halt counterfactual 계산.

    Returns (cluster_days, blocked_nets_all).
    """
    sessions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        sessions[(str(r.get("market") or ""), str(r.get("session_date") or ""))].append(r)

    clusters: list[ClusterDay] = []
    blocked_all: list[float] = []
    for (market, sdate), trs in sessions.items():
        stops = sorted(
            (t for t in trs if t.get("close_reason") in STOP_REASONS and t.get("closed_at")),
            key=lambda t: str(t.get("closed_at")),
        )
        if len(stops) < threshold:
            continue
        halt_at = str(stops[threshold - 1].get("closed_at"))
        # halt 발동(=N번째 손절) 시각 이후 진입(filled_at > T)한 거래 = 막혔을 진입
        blocked = [
            t for t in trs
            if t.get("filled_at") and str(t.get("filled_at")) > halt_at
        ]
        nets = [_net(t) for t in blocked]
        blocked_all.extend(nets)
        clusters.append(
            ClusterDay(
                market=market,
                session_date=sdate,
                stop_count=len(stops),
                halt_at=halt_at,
                blocked_entries=len(blocked),
                blocked_net_sum=round(sum(nets), 2),
                blocked_winners=sum(1 for n in nets if n > 0),
                blocked_losers=sum(1 for n in nets if n <= 0),
                biggest_winner_blocked=round(max(nets), 2) if nets else 0.0,
                tickers=[str(t.get("ticker") or "") for t in blocked],
            )
        )
    clusters.sort(key=lambda c: (c.market, c.session_date))
    return clusters, blocked_all


def _load_rows(ml_db: Path, market: str | None) -> list[dict[str, Any]]:
    sql = (
        "SELECT market, session_date, filled_at, closed_at, close_reason, ticker, "
        "pnl_pct, pnl_pct_net FROM v2_learning_performance WHERE closed=1"
    )
    params: list[Any] = []
    if market:
        sql += " AND market=?"
        params.append(market)
    with sqlite3.connect(ml_db) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def build_report(rows: list[dict[str, Any]], threshold: int) -> dict[str, Any]:
    clusters, blocked_all = analyze(rows, threshold)
    by_market: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cluster_days": 0, "blocked_entries": 0, "blocked_net_sum": 0.0, "right_tail_blocked": 0.0}
    )
    for c in clusters:
        m = by_market[c.market]
        m["cluster_days"] += 1
        m["blocked_entries"] += c.blocked_entries
        m["blocked_net_sum"] = round(m["blocked_net_sum"] + c.blocked_net_sum, 2)
        m["right_tail_blocked"] = round(max(m["right_tail_blocked"], c.biggest_winner_blocked), 2)
    total_net = round(sum(blocked_all), 2)
    return {
        "threshold": threshold,
        "total_cluster_days": len(clusters),
        "total_blocked_entries": len(blocked_all),
        "total_blocked_net_sum": total_net,
        "interpretation": (
            "halt가 손실을 막았을 것(이득)" if total_net < 0
            else "halt가 이익을 놓쳤을 것(right-tail 손해, 위험)" if total_net > 0
            else "중립"
        ),
        "by_market": dict(by_market),
        "cluster_days": [asdict(c) for c in clusters],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cluster-halt counterfactual ledger (read-only)")
    ap.add_argument("--db", type=Path, default=DEFAULT_ML_DB, help="decisions.db 경로")
    ap.add_argument("--market", choices=["US", "KR"], default=None, help="시장 필터(기본 전체)")
    ap.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD, help="halt 발동 손절 누적 임계(기본 4)")
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    if not args.db.exists():
        print(f"[error] DB 없음: {args.db}", file=sys.stderr)
        return 2

    rows = _load_rows(args.db, args.market)
    report = build_report(rows, args.threshold)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"== Cluster-halt counterfactual (threshold={report['threshold']}, 손절 {report['threshold']}개 누적 후 진입 차단 가정) ==")
    print(f"총 cluster day: {report['total_cluster_days']} / 막혔을 진입: {report['total_blocked_entries']}건")
    print(f"막혔을 진입 net 합: {report['total_blocked_net_sum']}%p → {report['interpretation']}")
    print()
    for market, m in sorted(report["by_market"].items()):
        print(f"[{market}] cluster {m['cluster_days']}일 / 막혔을 진입 {m['blocked_entries']}건 / "
              f"net합 {m['blocked_net_sum']}%p / 막혔을 최대이익 +{m['right_tail_blocked']}%p(right-tail 경고)")
    print()
    print("세션별:")
    for c in report["cluster_days"]:
        print(f"  {c['market']} {c['session_date']} | 손절 {c['stop_count']}개 → halt@{c['halt_at']} | "
              f"막혔을진입 {c['blocked_entries']}건(승{c['blocked_winners']}/패{c['blocked_losers']}) "
              f"net {c['blocked_net_sum']}%p / 최대이익 +{c['biggest_winner_blocked']}%p")
    print()
    print("주의: net 컬럼 부재 시 gross fallback, session_date 단위 근사(multi-day hold 부정확 가능). 측정 전용.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
