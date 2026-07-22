from __future__ import annotations

"""후보 원장 체결축 backfill — 2026-05-08 이후 끊긴 후보→체결 귀속을 복구한다.

왜 필요한가:
  `audit_candidate_rows.filled_count`는 2026-05-08 이후 전량 0이고 `entry_price`도
  비어 있다. 그런데 lifecycle_events에는 5월 254건 · 6월 306건 · 7월 12건의 실제
  체결이 있다. 즉 후보→체결 귀속이 2.5개월째 끊겨 있었고, 그 죽은 컬럼을 0으로 읽으면
  "체결 0건"이라는 잘못된 진단이 나온다(2026-07-22에 실제로 그렇게 됐다).

  이 축이 살아나야 "어떤 후보가 돈을 벌었는가"를 학습·검증할 수 있다.

귀속 규칙:
  1순위  execution_decision_id 정확 일치 (canonical v2_decision_id)
  2순위  market + ticker + session_date 일치
  같은 조합에 후보 행이 여럿이면 **최초 executable 행**을 고른다.
  가장 최근 WATCH 행에 붙이면 "어떤 action/route가 체결을 만들었는가"가 흐려진다.

  귀속 근거를 `payload_json.fill_attribution`에 남겨 사후 추적이 가능하게 한다.

기본은 dry-run이다. 실제 기록은 --apply 필요.

  python tools/backfill_candidate_fill_attribution.py --since 2026-05-01
  python tools/backfill_candidate_fill_attribution.py --since 2026-05-01 --apply
"""

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"

# 이 route들은 실제 실행 경로다. 귀속은 여기에 먼저 붙인다.
EXECUTABLE_ROUTES = ("PlanA.buy", "PlanA.probe", "PlanA.add", "PathB.wait")


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def load_fills(since: str) -> list[sqlite3.Row]:
    con = _ro(ML_DB)
    # close_reason은 canonical이 아니라 v2_learning_performance에 있다.
    return con.execute(
        "SELECT c.v2_decision_id, c.market, c.ticker, c.session_date, c.filled, c.closed, "
        "c.entry_price, c.first_exit_price, c.last_exit_price, c.pnl_pct, c.pnl_pct_net, "
        "c.earliest_fill_at, c.first_closed_at, c.first_fill_event_id, "
        "l.close_reason AS close_reason "
        "FROM v2_canonical_performance c "
        "LEFT JOIN v2_learning_performance l ON l.v2_decision_id = c.v2_decision_id "
        "WHERE c.filled=1 AND c.session_date>=? "
        "ORDER BY c.session_date", (since,)).fetchall()


def pick_target_row(con: sqlite3.Connection, fill: sqlite3.Row) -> tuple[str | None, str]:
    """귀속할 후보 행 하나를 고르고, 어떤 규칙으로 골랐는지 함께 반환한다."""
    dec = fill["v2_decision_id"]
    rows = con.execute(
        "SELECT candidate_key, route_route, known_at, claude_action "
        "FROM audit_candidate_rows WHERE execution_decision_id=?", (dec,)).fetchall()
    rule = "execution_decision_id"
    if not rows:
        rows = con.execute(
            "SELECT candidate_key, route_route, known_at, claude_action "
            "FROM audit_candidate_rows WHERE market=? AND ticker=? AND session_date=?",
            (fill["market"], fill["ticker"], fill["session_date"])).fetchall()
        rule = "market_ticker_session"
    if not rows:
        return None, "no_candidate_row"

    # 최초 executable 행 우선. 없으면 가장 이른 행.
    execs = [r for r in rows if str(r["route_route"] or "") in EXECUTABLE_ROUTES]
    pool = execs or rows
    pool = sorted(pool, key=lambda r: str(r["known_at"] or ""))
    if not execs:
        rule += "+no_executable_row"
    return pool[0]["candidate_key"], rule


def main() -> int:
    ap = argparse.ArgumentParser(description="후보 원장 체결축 backfill")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--apply", action="store_true", help="실제 기록(기본은 dry-run)")
    args = ap.parse_args()

    if not AUDIT_DB.exists() or not ML_DB.exists():
        print("원장 없음")
        return 1

    fills = load_fills(args.since)
    print(f"=== 체결축 backfill (since {args.since}) ===")
    print(f"canonical 체결 {len(fills)}건\n")

    ro = _ro(AUDIT_DB)
    plans: list[tuple] = []
    rules: Counter = Counter()
    for f in fills:
        key, rule = pick_target_row(ro, f)
        rules[rule] += 1
        if key:
            plans.append((key, f, rule))
    ro.close()

    print("귀속 규칙별 건수")
    for k, v in rules.most_common():
        print(f"  {k:36s} {v}")

    # 이미 채워져 있는 행은 건드리지 않는다(재실행 안전)
    ro = _ro(AUDIT_DB)
    todo = []
    for key, f, rule in plans:
        cur = ro.execute(
            "SELECT filled_count, entry_price FROM audit_candidate_rows WHERE candidate_key=?",
            (key,)).fetchone()
        if cur and (cur["filled_count"] or 0) > 0:
            continue
        todo.append((key, f, rule))
    ro.close()
    print(f"\n갱신 대상 {len(todo)}건 (이미 채워진 행 제외)")

    for key, f, rule in todo[:8]:
        print(f"  {f['session_date']} {f['market']:3s} {f['ticker']:8s} "
              f"entry={f['entry_price']} exit={f['last_exit_price']} "
              f"net={f['pnl_pct_net']} rule={rule}")
    if len(todo) > 8:
        print(f"  ... 외 {len(todo)-8}건")

    if not args.apply:
        print("\n[dry-run] 실제 기록하려면 --apply")
        return 0

    backup = AUDIT_DB.with_suffix(
        f".db.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(AUDIT_DB, backup)
    print(f"\n백업 생성: {backup.name}")

    con = sqlite3.connect(AUDIT_DB, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    updated = 0
    for key, f, rule in todo:
        row = con.execute(
            "SELECT payload_json FROM audit_candidate_rows WHERE candidate_key=?",
            (key,)).fetchone()
        try:
            payload = json.loads(row["payload_json"]) if row and row["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["fill_attribution"] = {
            "source": "canonical_backfill",
            "rule": rule,
            "v2_decision_id": f["v2_decision_id"],
            "backfilled_at": datetime.now().isoformat(timespec="seconds"),
        }
        con.execute(
            "UPDATE audit_candidate_rows SET "
            "filled_count=COALESCE(NULLIF(filled_count,0), 1), "
            "first_fill_at=COALESCE(first_fill_at, ?), "
            "entry_price=COALESCE(entry_price, ?), "
            "exit_price=COALESCE(exit_price, ?), "
            "pnl_pct=COALESCE(pnl_pct, ?), "
            "exit_reason=COALESCE(NULLIF(exit_reason,''), ?), "
            "execution_event_id=COALESCE(NULLIF(execution_event_id,''), ?), "
            "execution_decision_id=COALESCE(NULLIF(execution_decision_id,''), ?), "
            "payload_json=?, updated_at=? "
            "WHERE candidate_key=?",
            (f["earliest_fill_at"], f["entry_price"], f["last_exit_price"],
             f["pnl_pct_net"] if f["pnl_pct_net"] is not None else f["pnl_pct"],
             f["close_reason"], str(f["first_fill_event_id"] or ""),
             f["v2_decision_id"], json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds"), key),
        )
        updated += 1
    con.commit()
    con.close()
    print(f"갱신 완료 {updated}건")

    ro = _ro(AUDIT_DB)
    chk = ro.execute(
        "SELECT COUNT(*), SUM(filled_count>0), SUM(entry_price IS NOT NULL) "
        "FROM audit_candidate_rows WHERE session_date>=?", (args.since,)).fetchone()
    print(f"검증: since {args.since} 행 {chk[0]} · filled>0 {chk[1]} · entry_price {chk[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
