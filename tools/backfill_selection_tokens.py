#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""audit_claude_calls selection 토큰 백필 (C1-③ 과거분, 2026-07-09).

배경: selection_meta_live 행은 도입(5/9)부터 토큰 미배선(전방 수리 495abf5).
raw_calls 덤프(select_tickers, tokens 실측)와 시장+시각 근접 1:1 그리디 매칭으로 복원.
- 1:1 배정(같은 raw를 두 행에 중복 기록 금지) → 토큰 합계가 실제 API 소비와 일치.
- 허용 오차 180초, 밖이면 미매칭=0 유지(정직). 기본 dry-run, --apply로 기록.
"""
import argparse
import glob
import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "audit" / "candidate_audit.db"
RAW_GLOB = str(ROOT / "logs" / "raw_calls" / "*select_tickers*.json")
SINCE = "2026-05-09"
TOL_S = 180.0

_FN = re.compile(r"(\d{8})_(KR|US)_select_tickers_(\d{6})(\d{6})_")


def _fn_dt(path: str):
    m = _FN.search(Path(path).name)
    if not m:
        return None, None
    d, mkt, hms, us = m.groups()
    try:
        return datetime.strptime(d + hms + us, "%Y%m%d%H%M%S%f"), mkt
    except ValueError:
        return None, None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-rerun", action="store_true",
                    help="이미 백필된 DB에 재실행 허용(위험: 같은 raw가 다른 행에 재배정=이중계상). 기본 차단")
    args = ap.parse_args()

    # raw 인덱스 (파일명에서 시각·시장 — json 로드는 매칭된 것만)
    raws = []
    for p in glob.glob(RAW_GLOB):
        dt, mkt = _fn_dt(p)
        if dt and mkt and dt.strftime("%Y-%m-%d") >= SINCE:
            raws.append([dt, mkt, p])
    raws.sort()
    print(f"raw select_tickers({SINCE}+): {len(raws)}")

    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    # 재실행 하드가드(7/9): 도구는 무상태 1:1 그리디라, 1차 실행 후 재실행하면 이미 소비된
    # raw가 차순위 행에 재배정돼 토큰 이중계상된다. 백필 흔적이 있으면 apply 차단.
    already = cur.execute(
        "SELECT COUNT(*) FROM audit_claude_calls WHERE label='selection_meta_live' "
        "AND COALESCE(input_tokens,0)>0 AND called_at >= ?", (SINCE,)
    ).fetchone()[0]
    if already > 0 and args.apply and not args.allow_rerun:
        print(f"차단: 이미 백필된 행 {already}건 존재 — 재실행은 이중계상 위험. --allow-rerun으로만 강제 가능")
        return
    raw_rows = list(cur.execute(
        "SELECT call_id, market, called_at, payload_json FROM audit_claude_calls "
        "WHERE label='selection_meta_live' AND COALESCE(input_tokens,0)=0 "
        "AND called_at >= ?", (SINCE,)
    ))
    # 외부 검토 ③(7/9): API 콜이 없어 0이 정직한 행(smart_skip 재사용·rule_direct)은 백필 제외
    rows = []
    for cid, mkt, at, pj in raw_rows:
        try:
            p = json.loads(pj or "{}")
        except Exception:
            p = {}
        if bool(p.get("smart_skip_reused")) or bool(p.get("_smart_skip_reused")) \
                or bool(p.get("_selection_rule_direct")) or bool(p.get("_full_claude_call_skipped")):
            continue
        rows.append((cid, mkt, at))
    print(f"제외(정직한 0콜): {len(raw_rows) - len(rows)}")
    # called_at '+09:00' aware KST → naive KST
    targets = []
    for cid, mkt, at in rows:
        try:
            dt = datetime.fromisoformat(str(at)).replace(tzinfo=None)
        except ValueError:
            continue
        targets.append([dt, str(mkt or "").upper(), cid, False])  # [dt, mkt, call_id, matched]
    targets.sort()
    print(f"audit 대상(토큰0): {len(targets)}")

    updates, unmatched_raw = [], 0
    for rdt, rmkt, rpath in raws:
        best, best_gap = None, TOL_S + 1
        for t in targets:
            if t[3] or t[1] != rmkt:
                continue
            gap = abs((t[0] - rdt).total_seconds())
            if gap < best_gap:
                best, best_gap = t, gap
        if best is None or best_gap > TOL_S:
            unmatched_raw += 1
            continue
        try:
            tok = (json.load(open(rpath, encoding="utf-8")).get("tokens") or {})
        except Exception:
            unmatched_raw += 1
            continue
        it, ot = int(tok.get("input") or 0), int(tok.get("output") or 0)
        if it <= 0 and ot <= 0:
            unmatched_raw += 1
            continue
        best[3] = True
        updates.append((it, ot, best[2]))

    matched_tok_in = sum(u[0] for u in updates)
    print(f"매칭 {len(updates)} / raw 미매칭 {unmatched_raw} / audit 미매칭 {sum(1 for t in targets if not t[3])}")
    print(f"복원 토큰: input {matched_tok_in:,} / output {sum(u[1] for u in updates):,}")
    if args.apply and updates:
        cur.executemany(
            "UPDATE audit_claude_calls SET input_tokens=?, output_tokens=? WHERE call_id=?", updates
        )
        con.commit()
        print("기록 완료")
    elif not args.apply:
        print("→ 실제 기록: --apply")


if __name__ == "__main__":
    main()
