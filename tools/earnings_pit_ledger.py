# -*- coding: utf-8 -*-
"""US 어닝 point-in-time 원장 (F4 PEAD 데이터 계약, 2026-09-04).

문제(Codex 09-03): data/earnings_calendar.json은 D−3~D+14 롤링 캐시라 덮어써지고, estimate/actual/hour의
"언제 처음 알았나"가 없다 → PEAD 백테스트가 lookahead를 막지 못한다.
해법: 캐시를 읽을 때마다 (symbol, date) 단위로 값이 처음 보이거나 바뀐 것만 append-only로 남긴다.
  행: {symbol, date, hour, eps_estimate, eps_actual, first_seen_at(UTC), src_fetched_at, change}
  change ∈ new / estimate / actual / hour / revised(값 변경). 원값은 절대 덮지 않는다.
실행: 관측 체인 ⑪(07:20·16:20) + schtask claudetrade_earnings_pit 22:15(US 개장 전, BMO actual 포착).
사용: python tools/earnings_pit_ledger.py [--calendar PATH] [--ledger PATH] [--state PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALENDAR = ROOT / "data" / "earnings_calendar.json"
LEDGER = ROOT / "data" / "shadow" / "earnings_pit_ledger.jsonl"
STATE = ROOT / "state" / "earnings_pit_last.json"
FIELDS = ("hour", "eps_estimate", "eps_actual")


def _load_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def diff_rows(calendar: dict, last: dict, *, now_utc: datetime | None = None) -> tuple[list[dict], dict]:
    """캘린더 vs 마지막 스냅샷 → 변경 행 목록과 새 스냅샷. 순수 함수(테스트 대상)."""
    now = (now_utc or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    src_fetched = calendar.get("fetched_at")
    by = calendar.get("by_symbol") or {}
    new_last: dict = {}
    rows: list[dict] = []
    for sym, entry in by.items():
        entries = entry if isinstance(entry, list) else [entry]
        for e in entries:
            if not isinstance(e, dict) or not e.get("date"):
                continue
            key = f"{sym}|{e['date']}"
            cur = {k: e.get(k) for k in FIELDS}
            new_last[key] = cur
            prev = last.get(key)
            if prev is None:
                rows.append({"symbol": sym, "date": e["date"], **cur, "first_seen_at": now,
                             "src_fetched_at": src_fetched, "change": "new"})
                continue
            for k in FIELDS:
                if prev.get(k) != cur.get(k):
                    kind = k if prev.get(k) in (None, "") else "revised"
                    rows.append({"symbol": sym, "date": e["date"], **cur, "first_seen_at": now,
                                 "src_fetched_at": src_fetched, "change": kind, "changed_field": k,
                                 "prev": prev.get(k)})
    # 롤링 창에서 사라진 키는 스냅샷에 유지(다시 나타나면 revised 판정 가능)
    for key, v in last.items():
        new_last.setdefault(key, v)
    return rows, new_last


def run(calendar_path: Path = CALENDAR, ledger_path: Path = LEDGER, state_path: Path = STATE) -> int:
    cal = _load_json(calendar_path)
    if not cal:
        print("[PIT] earnings_calendar.json 없음/비어 있음 — 기록 없음")
        return 0
    last = _load_json(state_path)
    rows, new_last = diff_rows(cal, last)
    if rows:
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        with ledger_path.open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(new_last, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, state_path)
    kinds = {}
    for r in rows:
        kinds[r["change"]] = kinds.get(r["change"], 0) + 1
    print(f"[PIT] 어닝 PIT 원장 +{len(rows)}행 {kinds} · 추적 키 {len(new_last)}")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--calendar", default=str(CALENDAR))
    ap.add_argument("--ledger", default=str(LEDGER))
    ap.add_argument("--state", default=str(STATE))
    a = ap.parse_args()
    run(Path(a.calendar), Path(a.ledger), Path(a.state))
    return 0


if __name__ == "__main__":
    sys.exit(main())
