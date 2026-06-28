from __future__ import annotations

"""trade_ready 승격 실패 사유 리뷰 (read-only).

전환 깔때기(conversion_funnel_review)에서 최대 누수가 "승격(ranking→ready)"으로 나왔다.
이 도구는 candidate_audit(audit_candidate_latest_rows)에서 승격 안 된 후보
(claude_trade_ready=0)의 실패 사유를 집계해 "왜 승격이 안 되나"를 규명한다:

  - prompt_excluded_reason: 프롬프트 진입 전 제외(hard_cap_cutoff=후보 cap 초과로 평가 못함 등)
  - failed_ready_reasons_json: 평가 후 ready 미승격 사유(strategy_feasibility/late_entry 등)

카테고리로 묶어 "후보 cap에 잘림 vs 전략조건 미충족 vs 타이밍/증거" 비중을 본다. 처방은
selection 영역이라(리랭킹 backfire 교훈) 이 도구는 진단만, 변경하지 않는다. read-only.
"""

import argparse
import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
TABLE = "audit_candidate_latest_rows"


def _categorize(reason: str) -> str:
    r = reason.lower()
    if "strategy_feasibility" in r:
        return "전략조건 미충족(strategy_feasibility)"
    if "late_entry" in r or "stale" in r:
        return "타이밍 지남(late/stale)"
    if "evidence_ceiling" in r:
        return "증거 부족(evidence_ceiling)"
    if "data_quality" in r or "data_insufficient" in r or "not_confirmed" in r:
        return "데이터/확정 미비"
    if "price_cap" in r or "overextended" in r:
        return "가격 캡/과확장"
    if "pathb_waiting" in r or "active_order" in r:
        return "PathB 대기/잠금"
    return "기타"


@dataclass
class PromoteFail:
    market: str
    not_ready_n: int
    prompt_excluded: list[tuple[str, int]]
    failed_reason_top: list[tuple[str, int]]
    failed_category: list[tuple[str, int]]
    route_action: list[tuple[str, int]]


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    conn.execute("PRAGMA busy_timeout=25000")
    return conn


def analyze(conn: sqlite3.Connection, market: str) -> PromoteFail:
    rows = conn.execute(
        f"SELECT failed_ready_reasons_json, prompt_excluded_reason, route_final_action "
        f"FROM {TABLE} WHERE market=? AND COALESCE(claude_trade_ready,0)=0",
        (market,),
    ).fetchall()
    reason_cnt: Counter = Counter()
    cat_cnt: Counter = Counter()
    excl_cnt: Counter = Counter()
    route_cnt: Counter = Counter()
    for j, excl, route in rows:
        excl_cnt[str(excl or "(none)")[:40]] += 1
        route_cnt[str(route or "(empty)")[:24]] += 1
        if not j:
            continue
        items: list[str] = []
        try:
            v = json.loads(j)
            if isinstance(v, list):
                items = [str(x) for x in v]
            elif isinstance(v, dict):
                items = [str(k) for k in v]
            else:
                items = [str(v)]
        except (json.JSONDecodeError, TypeError):
            items = [str(j)[:50]]
        for it in items:
            reason_cnt[it[:50]] += 1
            cat_cnt[_categorize(it)] += 1
    return PromoteFail(
        market=market, not_ready_n=len(rows),
        prompt_excluded=excl_cnt.most_common(8),
        failed_reason_top=reason_cnt.most_common(12),
        failed_category=cat_cnt.most_common(),
        route_action=route_cnt.most_common(8),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="trade_ready 승격 실패 사유 리뷰 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    audit_db = Path(args.audit_db)
    if not audit_db.exists():
        print(f"[ERR] DB 없음: {audit_db}")
        return 2

    markets = ["KR", "US"] if args.market == "both" else [args.market]
    conn = _connect_ro(audit_db)
    try:
        results = [analyze(conn, m) for m in markets]
    finally:
        conn.close()

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        print("=== trade_ready 승격 실패 사유 리뷰 (claude_trade_ready=0) ===")
        for r in results:
            print(f"\n[{r.market}] 승격 안 됨 {r.not_ready_n}행")
            print(f"  prompt_excluded: {r.prompt_excluded}")
            print(f"  사유 카테고리: {r.failed_category}")
            print(f"  사유 top: {r.failed_reason_top}")
            print(f"  route_final_action: {r.route_action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
