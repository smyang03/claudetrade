"""게이트 효과 사후검증 리포트.

audit_candidate_rows(어떤 selection 결정을 했는가)와
candidate_counterfactual_paths(그 후보가 사후에 어떤 path였는가)를
candidate_counterfactual_link 헬퍼로 연결해 매칭률과 outcome 요약을 출력한다.

candidate_key 직접 조인은 식별 단위 차이로 0건이므로,
(session_date, market, ticker) + known_at 근접 매칭을 쓴다.

예시:
    python tools/report_candidate_counterfactual_link.py --market KR --start 2026-06-03 --end 2026-06-10
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_counterfactual_link import link_candidate_counterfactual

DEFAULT_DB = ROOT / "data" / "audit" / "candidate_audit.db"


def _decision_rows(conn: sqlite3.Connection, market: str | None, start: str | None, end: str | None) -> list[dict[str, Any]]:
    filters = ["1=1"]
    params: list[Any] = []
    if market:
        filters.append("UPPER(market) = ?")
        params.append(market.upper())
    if start:
        filters.append("session_date >= ?")
        params.append(start)
    if end:
        filters.append("session_date <= ?")
        params.append(end)
    sql = f"""
        SELECT DISTINCT session_date, market, ticker, known_at,
               route_final_action, why_not_watch, recommended_strategy
        FROM audit_candidate_rows
        WHERE {' AND '.join(filters)}
    """
    cols = ("session_date", "market", "ticker", "known_at", "route_final_action", "why_not_watch", "recommended_strategy")
    return [dict(zip(cols, r)) for r in conn.execute(sql, params).fetchall()]


def _is_blocked(action: Any) -> bool:
    return str(action or "").strip().upper() in {"", "WATCH", "NO_SIGNAL"}


def build_report(db_path: Path, market: str | None, start: str | None, end: str | None, runup_threshold: float) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = _decision_rows(conn, market, start, end)
        total = len(rows)
        matched = 0
        basis_counter: Counter[str] = Counter()
        missed_runup: list[dict[str, Any]] = []
        for row in rows:
            link = link_candidate_counterfactual(
                conn, row["session_date"], row["market"], row["ticker"], row["known_at"]
            )
            basis_counter[link["match_basis"]] += 1
            if not link["matched"]:
                continue
            matched += 1
            # 차단된 결정인데 사후 best path runup이 임계 이상 → 놓친 runup 후보
            if _is_blocked(row["route_final_action"]) and link["best_runup_pct"] is not None:
                if link["best_runup_pct"] >= runup_threshold:
                    missed_runup.append({
                        "session_date": row["session_date"],
                        "market": row["market"],
                        "ticker": row["ticker"],
                        "action": row["route_final_action"] or "(blank)",
                        "why_not_watch": row["why_not_watch"],
                        "best_runup_path": link["best_runup_path"],
                        "best_runup_pct": round(link["best_runup_pct"], 2),
                        "match_basis": link["match_basis"],
                    })
        # audit는 사이클마다 행이 중복되므로 (종목-일)당 best_runup 최대 1건만 남긴다
        dedup: dict[tuple, dict[str, Any]] = {}
        for m in missed_runup:
            key = (m["session_date"], m["market"], m["ticker"])
            if key not in dedup or m["best_runup_pct"] > dedup[key]["best_runup_pct"]:
                dedup[key] = m
        missed_runup = list(dedup.values())
        missed_runup.sort(key=lambda d: d["best_runup_pct"], reverse=True)
        return {
            "scope": {"market": market or "ALL", "start": start, "end": end, "runup_threshold": runup_threshold},
            "decisions": total,
            "matched": matched,
            "match_rate": round(matched / total, 4) if total else 0.0,
            "match_basis": dict(basis_counter),
            "missed_runup_count": len(missed_runup),
            "missed_runup_top": missed_runup[:20],
        }
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="audit ↔ counterfactual 매칭 사후검증 리포트")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--market", choices=["KR", "US"], default=None)
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--runup-threshold", type=float, default=4.0, help="놓친 runup 판정 임계(%)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build_report(Path(args.db), args.market, args.start, args.end, args.runup_threshold)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    s = report
    print(f"[counterfactual link] market={s['scope']['market']} {s['scope']['start']}~{s['scope']['end']}")
    print(f"  decisions={s['decisions']} matched={s['matched']} match_rate={s['match_rate']:.1%}")
    print(f"  match_basis={s['match_basis']}")
    print(f"  missed_runup(>={s['scope']['runup_threshold']}%)={s['missed_runup_count']}")
    for m in s["missed_runup_top"]:
        print(f"    {m['session_date']} {m['market']} {m['ticker']} act={m['action']} "
              f"best={m['best_runup_path']}({m['best_runup_pct']}%) basis={m['match_basis']}")


if __name__ == "__main__":
    main()
