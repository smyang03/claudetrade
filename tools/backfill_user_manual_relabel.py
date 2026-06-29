#!/usr/bin/env python3
"""과거 CLOSED_USER_MANUAL 행을 raw_reason에서 진짜 사유로 재라벨 (라벨 진실복원 백필).

배경: v2_close_reason default=USER_MANUAL + sync가 raw_reason(event store에 보존) 무시 →
Claude 장중매도(intraday_review_sell)·프리세션 매도가 "운영자 수동매도"로 오라벨됨(N=34, net −46.7%p).
forward 수정(v2_lifecycle_runtime.py 매핑 + default UNKNOWN)은 신규만 고침. 이 도구가 과거를 복원.

방법: v2_learning_performance/v2_canonical_performance의 close_reason='CLOSED_USER_MANUAL' 행을
event store lifecycle_events payload의 raw_reason으로 조인 → v2_close_reason 재매핑 → UPDATE.
진짜 manual(raw_reason='manual')은 USER_MANUAL 유지. 기본 dry-run, --apply로 기록.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ML_DB = ROOT / "data" / "ml" / "decisions.db"
EV_DB = ROOT / "data" / "v2_event_store.db"

from runtime.v2_lifecycle_runtime import v2_close_reason

TABLES = ("v2_learning_performance", "v2_canonical_performance")


def build_raw_reason_map(ev: sqlite3.Connection) -> dict:
    """decision_id -> raw_reason (CLOSED 이벤트 payload에서)."""
    out = {}
    for did, pj in ev.execute(
        "SELECT decision_id, payload_json FROM lifecycle_events "
        "WHERE event_type LIKE '%CLOSE%'"
    ):
        if not did or not pj:
            continue
        try:
            p = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            continue
        rr = p.get("raw_reason") or p.get("reason") or p.get("close_reason_raw")
        if rr:
            out[did] = str(rr)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    ev = sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True)
    rawmap = build_raw_reason_map(ev)
    ev.close()
    print(f"event store raw_reason 맵: {len(rawmap)}건")

    con = sqlite3.connect(str(ML_DB)) if args.apply else sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}\n")

    for table in TABLES:
        cols = {d[1] for d in con.execute(f"PRAGMA table_info({table})")}
        if "close_reason" not in cols:
            print(f"[{table}] close_reason 컬럼 없음 — 건너뜀")
            continue
        rows = list(con.execute(
            f"SELECT v2_decision_id, market FROM {table} "
            f"WHERE close_reason='CLOSED_USER_MANUAL'"
        ))
        updates = []
        unresolved = 0
        transitions = Counter()
        for did, _mkt in rows:
            rr = rawmap.get(did)
            if not rr:
                unresolved += 1
                continue
            new_label = v2_close_reason(rr)
            if new_label == "CLOSED_USER_MANUAL":
                continue  # 진짜 manual 유지
            transitions[f"{rr} -> {new_label}"] += 1
            updates.append((new_label, did))

        print(f"[{table}] USER_MANUAL {len(rows)}건 → 재라벨 {len(updates)}건 (raw 미해결 {unresolved})")
        for k, v in transitions.most_common():
            print(f"    {k}: {v}")

        if args.apply and updates:
            con.executemany(
                f"UPDATE {table} SET close_reason=? WHERE v2_decision_id=?",
                updates,
            )
            con.commit()
    con.close()

    # v2_path_runs(event store, plan_json 내 close_reason) — 과거 백필이 누락하던 경로 (2026-06-30 추가).
    # ML_DB(v2_learning/canonical)만 고쳐 v2_event_store.db 원본은 USER_MANUAL 잔존하던 것 교정.
    evw = sqlite3.connect(str(EV_DB)) if args.apply else sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True)
    evw.execute("PRAGMA busy_timeout=5000")
    pr_rows = list(evw.execute(
        "SELECT path_run_id, decision_id, plan_json FROM v2_path_runs WHERE status='CLOSED'"
    ))
    pr_updates = []
    pr_unres = 0
    pr_trans = Counter()
    for prid, did, pj in pr_rows:
        try:
            d = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("close_reason") != "CLOSED_USER_MANUAL":
            continue
        rr = rawmap.get(did)
        if not rr:
            pr_unres += 1
            continue
        nl = v2_close_reason(rr)
        if nl == "CLOSED_USER_MANUAL":
            continue  # 진짜 manual 유지
        d["close_reason"] = nl
        if d.get("pending_close_reason") == "CLOSED_USER_MANUAL":
            d["pending_close_reason"] = nl
        pr_trans[f"{rr} -> {nl}"] += 1
        pr_updates.append((json.dumps(d, ensure_ascii=False), prid))

    print(f"[v2_path_runs] USER_MANUAL → 재라벨 {len(pr_updates)}건 (raw 미해결 {pr_unres})")
    for k, v in pr_trans.most_common():
        print(f"    {k}: {v}")
    if args.apply and pr_updates:
        evw.executemany("UPDATE v2_path_runs SET plan_json=? WHERE path_run_id=?", pr_updates)
        evw.commit()
    evw.close()

    if not args.apply:
        print("\n→ 기록: python tools/backfill_user_manual_relabel.py --apply")


if __name__ == "__main__":
    main()
