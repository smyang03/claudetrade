from __future__ import annotations

"""후보 원장 체결축 귀속 — 복구 + 오귀속 교정.

왜 필요한가:
  `audit_candidate_rows.filled_count`는 2026-05-08 이후 전량 0이었다. 그런데
  lifecycle_events에는 5월 254건 · 6월 306건의 실제 체결이 있다. 후보→체결 귀속이
  2.5개월 끊겨 있었고, 그 죽은 컬럼을 0으로 읽으면 "체결 0건"이라는 잘못된 진단이 나온다.

2026-07-23 1차 backfill의 결함 (이 파일이 그 교정판이다):
  1차는 "세션 내 최초 executable 행"을 골랐다. 두 가지가 틀렸다.
    - canonical의 `route`(path_b/plan_a)와 후보 행의 route 계열을 **대조하지 않았다**.
      그래서 path_b 체결이 PlanA.probe 행에 붙었다(IREN).
    - "최초"를 골라 체결 시각보다 **이른 행**에 붙였다. PathB.wait 행이 뒤에 있어도 놓쳤다.
    - `no_submit_reason_code`가 있는 행(= 주문을 내지 않았다고 명시된 행)을 배제하지 않아
      "NO_SIGNAL이면서 FILLED"인 자기모순 행이 생겼다.
  결과: 246건 중 55건(22.4%) 오귀속.

귀속 규칙 (교정판):
  1. canonical `route`로 기대 계열을 정한다.
       path_b  → PathB.wait
       plan_a  → PlanA.buy / PlanA.probe / PlanA.add
       그 외(unknown/빈값 = sleeve) → **귀속하지 않는다**
  2. `no_submit_reason_code`가 있는 행은 제외한다. 주문을 안 냈다고 원장이 말하고 있다.
  3. 남은 행 중 `known_at`이 체결 시각 **직전으로 가장 가까운** 행을 고른다.
  4. 맞는 행이 없으면 **억지로 붙이지 않고** 미귀속으로 남긴다.
     틀린 귀속은 빈 칸보다 나쁘다 — 분석이 조용히 오염된다.

  python tools/backfill_candidate_fill_attribution.py --audit
  python tools/backfill_candidate_fill_attribution.py --repair            (dry-run)
  python tools/backfill_candidate_fill_attribution.py --repair --apply
"""

import argparse
import json
import shutil
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"

PATHB_ROUTES = {"PathB.wait"}
PLANA_ROUTES = {"PlanA.buy", "PlanA.probe", "PlanA.add"}
BACKFILL_SOURCE = "canonical_backfill"
BACKFILL_SOURCE_V2 = "canonical_backfill_v2"


_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _parse_dt(value) -> datetime | None:
    """ISO 문자열을 tz-aware datetime으로. tz가 없으면 KST로 본다(원장 기본)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone(timedelta(hours=9)))
    return dt


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def load_fills(since: str) -> list[sqlite3.Row]:
    con = _ro(ML_DB)
    return con.execute(
        "SELECT c.v2_decision_id, c.market, c.ticker, c.session_date, c.route, c.path_type, "
        "c.strategy, c.entry_price, c.last_exit_price, c.pnl_pct, c.pnl_pct_net, "
        "c.earliest_fill_at, c.first_fill_event_id, l.close_reason AS close_reason "
        "FROM v2_canonical_performance c "
        "LEFT JOIN v2_learning_performance l ON l.v2_decision_id = c.v2_decision_id "
        "WHERE c.filled=1 AND c.session_date>=? ORDER BY c.session_date", (since,)).fetchall()


def expected_routes(canon_route: str) -> set[str] | None:
    """canonical route → 후보 행에서 기대되는 route 계열. None이면 귀속 대상 아님."""
    r = str(canon_route or "").strip().lower()
    if r == "path_b":
        return PATHB_ROUTES
    if r == "plan_a":
        return PLANA_ROUTES
    return None  # unknown/빈값 = sleeve 등 후보 파이프라인 밖


def _plan_anchor(decision_id: str) -> tuple[str | None, str]:
    """체결을 만든 결정의 **앵커 시각**을 lifecycle에서 찾는다.

    ★ 핵심: 체결 시각이 아니라 **플랜/주문이 만들어진 시각**이 정답이다.
      PathB는 플랜 생성 → 존 도달 대기 → 체결이라 둘이 수십 분 벌어진다.
      FILLED payload의 selection_snapshot_ts는 *체결 시점*의 최신 스냅샷이라
      원 결정 행과 다르다(IREN: 플랜 22:54 vs 체결시 스냅샷 23:10).

    우선순위: PATHB_SELECTION_RECONCILE의 selection_snapshot_ts(플랜과 같은 시각)
             → CLAUDE_PRICE_PLAN_CREATED.occurred_at
             → ORDER_SENT.occurred_at
    """
    if not EVENT_DB.exists() or not decision_id:
        return None, "no_event_db"
    con = _ro(EVENT_DB)
    try:
        rows = con.execute(
            "SELECT event_type, occurred_at, payload_json FROM lifecycle_events "
            "WHERE decision_id=? ORDER BY occurred_at", (decision_id,)).fetchall()
    except sqlite3.Error:
        return None, "event_query_failed"
    finally:
        con.close()
    if not rows:
        return None, "no_lifecycle_event"

    plan_at = None
    for r in rows:
        if str(r["event_type"] or "") == "CLAUDE_PRICE_PLAN_CREATED":
            plan_at = r["occurred_at"]
            break
    if plan_at:
        # 같은 시각의 RECONCILE이 실제 스냅샷 ts를 들고 있으면 그게 가장 정확하다.
        pdt = _parse_dt(plan_at)
        for r in rows:
            if str(r["event_type"] or "") != "PATHB_SELECTION_RECONCILE":
                continue
            rdt = _parse_dt(r["occurred_at"])
            if pdt is None or rdt is None or abs((rdt - pdt).total_seconds()) > 120:
                continue
            try:
                snap = (json.loads(r["payload_json"]) or {}).get("selection_snapshot_ts")
            except (TypeError, ValueError):
                snap = None
            if snap:
                return str(snap), "reconcile_snapshot_ts"
        return str(plan_at), "plan_created_at"

    for r in rows:
        if str(r["event_type"] or "") == "ORDER_SENT":
            return str(r["occurred_at"]), "order_sent_at"
    return None, "no_anchor_event"


def resolve_target(con: sqlite3.Connection, fill: sqlite3.Row) -> tuple[str | None, str]:
    """체결을 만든 후보 행을 고른다. 못 고르면 (None, 사유).

    route 계열로 거르지 않는다 — PathB는 selection이 WATCH인 스냅샷에서도 플랜을
    만든다(NVDA 2026-07-06 실증). 계열로 거르면 그 사실을 지우고 미귀속이 된다.
    대신 **앵커 시각에 가장 가까운 행**을 고른다. 그게 실제 원 결정 행이다.
    """
    if expected_routes(fill["route"]) is None:
        return None, f"non_candidate_lane(route={fill['route'] or 'empty'})"

    rows = con.execute(
        "SELECT candidate_key, known_at, route_route, no_submit_reason_code, claude_action "
        "FROM audit_candidate_rows WHERE market=? AND ticker=? AND session_date=?",
        (fill["market"], fill["ticker"], fill["session_date"])).fetchall()
    if not rows:
        return None, "no_candidate_row"

    # 주문을 안 냈다고 원장이 명시한 행은 체결 주체가 될 수 없다.
    cands = [r for r in rows if not str(r["no_submit_reason_code"] or "").strip()]
    if not cands:
        return None, "all_rows_no_submit"

    anchor_raw, anchor_kind = _plan_anchor(str(fill["v2_decision_id"] or ""))
    anchor = _parse_dt(anchor_raw) or _parse_dt(fill["earliest_fill_at"])
    if anchor is None:
        pick = max(cands, key=lambda r: (_parse_dt(r["known_at"]) or _EPOCH))
        return pick["candidate_key"], "latest_row(no_anchor)"

    # ★ known_at은 KST(+09:00), 이벤트는 UTC(+00:00)다. tz-aware로 파싱해 비교한다.
    #   문자열 비교하면 22:22+09:00 > 14:21+00:00 이 되어 항상 어긋난다.
    scored = [(r, _parse_dt(r["known_at"])) for r in cands]
    scored = [(r, dt) for r, dt in scored if dt is not None]
    if not scored:
        return None, "no_parsable_known_at"

    # 같은 known_at에 행이 여럿이면(스냅샷이 여러 벌 기록됨) 기대 계열 route를 가진 행을
    # 우선한다. 정보량이 더 큰 행이 실제 결정을 담고 있다.
    want = expected_routes(fill["route"]) or set()

    def rank(r) -> int:
        route = str(r["route_route"] or "")
        if route in want:
            return 2
        if route and route != "WATCH":
            return 1
        return 0

    before = [(r, dt) for r, dt in scored if dt <= anchor]
    if before:
        newest = max(dt for _, dt in before)
        tied = [r for r, dt in before if dt == newest]
        pick = max(tied, key=rank)
        rule = f"anchor:{anchor_kind}+nearest_before"
    else:
        oldest = min(dt for _, dt in scored)
        tied = [r for r, dt in scored if dt == oldest]
        pick = max(tied, key=rank)
        rule = f"anchor:{anchor_kind}+earliest_after"
    return pick["candidate_key"], rule


def audit(since: str) -> list[tuple]:
    """기존 귀속의 정합성 검사. 내가 쓴 행만 대상으로 한다."""
    a = _ro(AUDIT_DB)
    fills = {f["v2_decision_id"]: f for f in load_fills(since)}
    bad: list[tuple] = []
    kinds: Counter = Counter()
    total = 0
    # ★fills는 since 이후만 로드하므로 검사 대상 행도 since로 맞춘다. 안 그러면 since 이전
    #  귀속(fills에 없음)이 전부 f=None→"파이프라인 밖" 위반으로 오판된다(2026-07-24 실측:
    #  since=7/18이면 245건 100% 위반, since=5/01이면 0건 — 순전히 스코프 불일치 착시였다).
    #  이 착시로 --apply 했으면 멀쩡한 귀속을 대량 해제할 뻔했다.
    for r in a.execute(
        "SELECT candidate_key, market, ticker, session_date, claude_action, route_route, "
        "no_submit_reason_code, payload_json FROM audit_candidate_rows "
        "WHERE filled_count>0 AND session_date>=?",
        (str(since or ""),),
    ):
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        fa = payload.get("fill_attribution") if isinstance(payload, dict) else None
        if not isinstance(fa, dict) or fa.get("source") not in (BACKFILL_SOURCE, BACKFILL_SOURCE_V2):
            continue
        total += 1
        f = fills.get(fa.get("v2_decision_id"))
        want = expected_routes(f["route"]) if f is not None else None
        problems = []
        if str(r["no_submit_reason_code"] or "").strip():
            problems.append(f"NO_SUBMIT({r['no_submit_reason_code']})")
        if want is None:
            problems.append("후보 파이프라인 밖 체결인데 귀속됨")
        # ※ route 계열 불일치는 위반이 아니다 — PathB는 WATCH 스냅샷에서도 플랜을 만든다.
        #    앵커 시각으로 고른 행이면 route가 WATCH여도 그게 실제 원 결정 행이다.
        if problems:
            kinds[" + ".join(problems)] += 1
            bad.append((r["candidate_key"], r["market"], r["ticker"], r["session_date"],
                        r["claude_action"], r["route_route"], r["no_submit_reason_code"],
                        fa.get("v2_decision_id")))
    print(f"  backfill이 쓴 행 {total}건 · 정합성 위반 {len(bad)}건 "
          f"({len(bad)/max(total,1)*100:.1f}%)")
    for k, v in kinds.most_common(10):
        print(f"    {v:4d}  {k}")
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="후보 원장 체결 귀속 복구·교정")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--audit", action="store_true", help="정합성 검사만")
    ap.add_argument("--repair", action="store_true", help="오귀속 해제 후 재귀속")
    ap.add_argument("--reset", action="store_true",
                    help="backfill이 쓴 귀속을 전부 해제 후 현재 규칙으로 재귀속"
                         "(resolver를 바꿨을 때 두 규칙이 섞이는 것을 막는다)")
    ap.add_argument("--apply", action="store_true", help="실제 기록(기본 dry-run)")
    args = ap.parse_args()

    if not AUDIT_DB.exists() or not ML_DB.exists():
        print("원장 없음")
        return 1

    print(f"=== 체결 귀속 (since {args.since}) ===\n")
    print("[정합성 검사]")
    bad = audit(args.since)
    if args.audit:
        return 0
    if args.reset:
        # resolver를 바꿨으면 이전 규칙으로 쓴 것을 전부 걷어내고 다시 건다.
        # 두 규칙이 섞인 원장은 어느 쪽 근거로 읽어야 할지 알 수 없다.
        a = _ro(AUDIT_DB)
        bad = []
        for r in a.execute(
            "SELECT candidate_key, market, ticker, session_date, claude_action, route_route, "
            "no_submit_reason_code, payload_json FROM audit_candidate_rows WHERE filled_count>0"
        ):
            try:
                payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
            except (TypeError, ValueError):
                payload = {}
            fa = payload.get("fill_attribution") if isinstance(payload, dict) else None
            if isinstance(fa, dict) and str(fa.get("source") or "").startswith(BACKFILL_SOURCE):
                bad.append((r["candidate_key"], r["market"], r["ticker"], r["session_date"],
                            r["claude_action"], r["route_route"], r["no_submit_reason_code"],
                            fa.get("v2_decision_id")))
        a.close()
        print(f"\n[reset] backfill이 쓴 귀속 {len(bad)}건 전부 해제 대상")
    if not (args.repair or args.reset):
        print("\n--repair 로 교정, --audit 으로 검사만")
        return 0

    fills = load_fills(args.since)
    ro = _ro(AUDIT_DB)
    plans: list[tuple] = []
    reasons: Counter = Counter()
    for f in fills:
        key, rule = resolve_target(ro, f)
        reasons[rule] += 1
        if key:
            plans.append((key, f, rule))
    ro.close()

    print(f"\n[재귀속 계획] canonical 체결 {len(fills)}건")
    for k, v in reasons.most_common():
        print(f"  {k:44s} {v}")
    print(f"  → 귀속 가능 {len(plans)}건 · 미귀속 {len(fills)-len(plans)}건(억지로 붙이지 않음)")

    if not args.apply:
        print(f"\n[dry-run] 해제 대상 {len(bad)}건 · 재귀속 {len(plans)}건. 실제 적용은 --apply")
        return 0

    backup = AUDIT_DB.with_suffix(f".db.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(AUDIT_DB, backup)
    print(f"\n백업 생성: {backup.name}")

    con = sqlite3.connect(AUDIT_DB, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row

    # 1) 내가 쓴 오귀속 해제 — 내 서명이 있는 행만 건드린다
    cleared = 0
    for key, *_ in bad:
        row = con.execute("SELECT payload_json FROM audit_candidate_rows WHERE candidate_key=?",
                          (key,)).fetchone()
        try:
            payload = json.loads(row["payload_json"]) if row and row["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        prev = payload.pop("fill_attribution", None)
        payload["fill_attribution_reverted"] = {
            "reason": "route_or_no_submit_mismatch",
            "previous": prev,
            "reverted_at": datetime.now().isoformat(timespec="seconds"),
        }
        con.execute(
            "UPDATE audit_candidate_rows SET filled_count=0, first_fill_at=NULL, "
            "entry_price=NULL, exit_price=NULL, pnl_pct=NULL, exit_reason=NULL, "
            "execution_event_id=NULL, payload_json=?, updated_at=? WHERE candidate_key=?",
            (json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds"), key))
        cleared += 1
    con.commit()
    print(f"오귀속 해제 {cleared}건")

    # 2) 규칙에 맞는 행에 재귀속
    updated = 0
    for key, f, rule in plans:
        cur = con.execute(
            "SELECT filled_count, payload_json FROM audit_candidate_rows WHERE candidate_key=?",
            (key,)).fetchone()
        if cur and (cur["filled_count"] or 0) > 0:
            continue
        try:
            payload = json.loads(cur["payload_json"]) if cur and cur["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        payload["fill_attribution"] = {
            "source": BACKFILL_SOURCE_V2,
            "rule": rule,
            "v2_decision_id": f["v2_decision_id"],
            "canonical_route": f["route"],
            "canonical_path_type": f["path_type"],
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
            "payload_json=?, updated_at=? WHERE candidate_key=?",
            (f["earliest_fill_at"], f["entry_price"], f["last_exit_price"],
             f["pnl_pct_net"] if f["pnl_pct_net"] is not None else f["pnl_pct"],
             f["close_reason"], str(f["first_fill_event_id"] or ""), f["v2_decision_id"],
             json.dumps(payload, ensure_ascii=False),
             datetime.now().isoformat(timespec="seconds"), key))
        updated += 1
    con.commit()
    con.close()
    print(f"재귀속 {updated}건")

    print("\n[적용 후 재검사]")
    audit(args.since)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
