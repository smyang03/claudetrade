from __future__ import annotations

"""체결 → 후보 행 귀속 (공용 규칙).

왜 별도 모듈인가:
  같은 규칙을 두 곳이 써야 한다.
    - 라이브: 세션 중 주기적으로 체결을 후보 원장에 반영
    - 사후:   tools/backfill_candidate_fill_attribution.py 로 과거 구간 복구
  규칙이 갈리면 원장이 두 기준으로 섞인다. 2026-07-23에 실제로 그 사고가 났다.

핵심 규칙 — 앵커는 체결 시각이 아니라 **플랜/주문이 만들어진 시각**이다:
  PathB는 플랜 생성 → 존 도달 대기 → 체결이라 둘이 수십 분 벌어진다.
  체결 시각으로 후보 행을 찾으면 그 사이에 쌓인 다른 스냅샷에 붙는다.
  FILLED payload의 selection_snapshot_ts도 *체결 시점* 스냅샷이라 원 결정 행이 아니다.

  앵커 우선순위:
    PATHB_SELECTION_RECONCILE.selection_snapshot_ts (플랜과 같은 시각)
    → CLAUDE_PRICE_PLAN_CREATED.occurred_at
    → ORDER_SENT.occurred_at

주의 (실측으로 확인한 함정):
  - `known_at`은 KST(+09:00), lifecycle 이벤트는 UTC(+00:00)다. 문자열 비교하면
    22:22+09:00 > 14:21+00:00 이 되어 항상 어긋난다. 반드시 tz-aware로 파싱한다.
  - route 계열(PathB.wait 등)로 후보를 거르면 안 된다. PathB는 selection이 WATCH인
    스냅샷에서도 플랜을 만든다(NVDA 2026-07-06). 계열로 거르면 그 사실이 지워진다.
  - `no_submit_reason_code`가 있는 행은 제외한다. 주문을 안 냈다고 원장이 말하고 있다.
  - 맞는 행이 없으면 **비워 둔다.** 틀린 귀속은 빈 칸보다 나쁘다.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EVENT_DB = ROOT / "data" / "v2_event_store.db"

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_KST = timezone(timedelta(hours=9))

# 후보 파이프라인 소산으로 볼 route. 그 외(sleeve 등)는 귀속 대상이 아니다.
CANDIDATE_LANES = {"path_b", "plan_a"}
PATHB_ROUTES = {"PathB.wait"}
PLANA_ROUTES = {"PlanA.buy", "PlanA.probe", "PlanA.add"}


def parse_dt(value: Any) -> datetime | None:
    """ISO 문자열 → tz-aware datetime. tz가 없으면 KST로 본다(원장 기본)."""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=_KST)


def expected_routes(canon_route: str) -> set[str] | None:
    """canonical route → 기대되는 후보 route 계열. None이면 귀속 대상 아님."""
    r = str(canon_route or "").strip().lower()
    if r == "path_b":
        return set(PATHB_ROUTES)
    if r == "plan_a":
        return set(PLANA_ROUTES)
    return None


def _event_con() -> sqlite3.Connection | None:
    if not EVENT_DB.exists():
        return None
    con = sqlite3.connect(f"file:{EVENT_DB}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def plan_anchor(decision_id: str, con: sqlite3.Connection | None = None) -> tuple[str | None, str]:
    """결정의 앵커 시각을 lifecycle에서 찾는다. (시각, 근거) 반환."""
    if not decision_id:
        return None, "no_decision_id"
    owned = con is None
    if owned:
        con = _event_con()
    if con is None:
        return None, "no_event_db"
    try:
        rows = con.execute(
            "SELECT event_type, occurred_at, payload_json FROM lifecycle_events "
            "WHERE decision_id=? ORDER BY occurred_at", (decision_id,)).fetchall()
    except sqlite3.Error:
        return None, "event_query_failed"
    finally:
        if owned:
            con.close()
    if not rows:
        return None, "no_lifecycle_event"

    plan_at = next((r["occurred_at"] for r in rows
                    if str(r["event_type"] or "") == "CLAUDE_PRICE_PLAN_CREATED"), None)
    if plan_at:
        pdt = parse_dt(plan_at)
        for r in rows:
            if str(r["event_type"] or "") != "PATHB_SELECTION_RECONCILE":
                continue
            rdt = parse_dt(r["occurred_at"])
            if pdt is None or rdt is None or abs((rdt - pdt).total_seconds()) > 120:
                continue
            try:
                snap = (json.loads(r["payload_json"]) or {}).get("selection_snapshot_ts")
            except (TypeError, ValueError):
                snap = None
            if snap:
                return str(snap), "reconcile_snapshot_ts"
        return str(plan_at), "plan_created_at"

    order_at = next((r["occurred_at"] for r in rows
                     if str(r["event_type"] or "") == "ORDER_SENT"), None)
    if order_at:
        return str(order_at), "order_sent_at"
    return None, "no_anchor_event"


def resolve_fill_target(
    audit_con: sqlite3.Connection,
    *,
    market: str,
    ticker: str,
    session_date: str,
    decision_id: str,
    canonical_route: str,
    fill_at: Any = None,
    event_con: sqlite3.Connection | None = None,
) -> tuple[str | None, str]:
    """체결을 만든 후보 행의 candidate_key를 고른다. 못 고르면 (None, 사유).

    audit_con은 row_factory=sqlite3.Row 여야 한다.
    """
    if expected_routes(canonical_route) is None:
        return None, f"non_candidate_lane(route={canonical_route or 'empty'})"

    rows = audit_con.execute(
        "SELECT candidate_key, known_at, route_route, no_submit_reason_code "
        "FROM audit_candidate_rows WHERE market=? AND ticker=? AND session_date=?",
        (str(market or "").upper(), str(ticker or ""), str(session_date or ""))).fetchall()
    if not rows:
        return None, "no_candidate_row"

    cands = [r for r in rows if not str(r["no_submit_reason_code"] or "").strip()]
    if not cands:
        return None, "all_rows_no_submit"

    anchor_raw, anchor_kind = plan_anchor(decision_id, event_con)
    anchor = parse_dt(anchor_raw) or parse_dt(fill_at)
    if anchor is None:
        pick = max(cands, key=lambda r: (parse_dt(r["known_at"]) or _EPOCH))
        return pick["candidate_key"], "latest_row(no_anchor)"

    scored = [(r, parse_dt(r["known_at"])) for r in cands]
    scored = [(r, dt) for r, dt in scored if dt is not None]
    if not scored:
        return None, "no_parsable_known_at"

    want = expected_routes(canonical_route) or set()

    def rank(row) -> int:
        route = str(row["route_route"] or "")
        if route in want:
            return 2
        if route and route != "WATCH":
            return 1
        return 0

    before = [(r, dt) for r, dt in scored if dt <= anchor]
    if before:
        newest = max(dt for _, dt in before)
        pick = max((r for r, dt in before if dt == newest), key=rank)
        return pick["candidate_key"], f"anchor:{anchor_kind}+nearest_before"
    oldest = min(dt for _, dt in scored)
    pick = max((r for r, dt in scored if dt == oldest), key=rank)
    return pick["candidate_key"], f"anchor:{anchor_kind}+earliest_after"


def collect_session_fills(session_date: str, market: str,
                          runtime_mode: str = "live") -> list[dict[str, Any]]:
    """세션의 FILLED 이벤트를 decision 단위로 모은다(중복 이벤트는 하나로 접는다).

    같은 체결에 FILLED가 2건씩 발행되므로(2026-07 실측) decision_id 기준으로 dedupe한다.
    이벤트 수로 체결을 세면 2배가 된다.
    """
    con = _event_con()
    if con is None:
        return []
    try:
        rows = con.execute(
            "SELECT decision_id, ticker, occurred_at, payload_json, event_uuid "
            "FROM lifecycle_events WHERE event_type='FILLED' AND session_date=? "
            "AND market=? AND runtime_mode=? ORDER BY occurred_at",
            (str(session_date or ""), str(market or "").upper(),
             str(runtime_mode or "live").lower())).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        dec = str(r["decision_id"] or "")
        if not dec or dec in out:
            continue
        try:
            payload = json.loads(r["payload_json"]) if r["payload_json"] else {}
        except (TypeError, ValueError):
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out[dec] = {
            "decision_id": dec,
            "ticker": str(r["ticker"] or ""),
            "fill_at": r["occurred_at"],
            "event_uuid": str(r["event_uuid"] or ""),
            "canonical_route": str(payload.get("entry_route") or ""),
            "path_type": str(payload.get("path_type") or ""),
            "path_run_id": str(payload.get("path_run_id") or ""),
            "entry_price": payload.get("fill_price_native") or payload.get("price"),
            "strategy_used": str(payload.get("strategy_used") or ""),
        }
    return list(out.values())
