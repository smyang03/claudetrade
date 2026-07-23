"""라이브 데이터 흐름 모니터 — 오늘 수정분 forward 반영 + 흐름 무결성. read-only.

재실행용. US 세션 중 반복 호출해 아래를 데이터로 확인한다:
  1. 단일 인스턴스 + 스택 + ERROR
  2. trainer_tier forward 기입 (커밋 9988a32 수정 검증 — 오늘 세션 후보에 값 붙나)
  3. rel_vol_shadow — ticker_selection_log(1차) vs candidate_audit(직렬화 갭) 최근
  4. US excursion — live 포지션 observed_*
  5. BUY_READY 발동 + 우리 net (도전 검증)
  6. shadow 원장 (early-path tighten 등)
"""

from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def _q(db, sql, params=()):
    if not os.path.exists(db):
        return []
    c = sqlite3.connect(db)
    c.execute("PRAGMA busy_timeout=5000")
    try:
        return c.execute(sql, params).fetchall()
    except Exception as e:
        return [("ERR", str(e))]
    finally:
        c.close()


def main() -> int:
    now = datetime.now(KST)
    print(f"=== 라이브 데이터흐름 모니터 {now.strftime('%Y-%m-%d %H:%M:%S')} KST ===\n")

    # 1. 스택
    try:
        import psutil
        bots = [p.info["pid"] for p in psutil.process_iter(["pid", "name", "cmdline"])
                if "python" in str(p.info.get("name") or "").lower()
                and any("trading_bot.py" in str(c) for c in (p.info.get("cmdline") or []))
                and "--live" in (p.info.get("cmdline") or [])]
        print(f"[1] 라이브봇: {bots} ({'단일 OK' if len(bots)==1 else '★인스턴스 이상'})")
    except Exception as e:
        print(f"[1] psutil ERR {e}")

    logs = sorted(glob.glob(str(ROOT / "logs/system/live_trading_*.log")), reverse=True)
    if logs:
        with open(logs[0], encoding="utf-8", errors="ignore") as f:
            tail = f.readlines()[-400:]
        errs = [l.strip() for l in tail if "[ERROR" in l or "[CRITICAL" in l]
        errs = [e for e in errs if "OHLC" not in e and "rate limit" not in e][-3:]
        print(f"    최근 ERROR: {len(errs)}건" + ("" if not errs else "\n      " + "\n      ".join(e[-100:] for e in errs)))

    audit = str(ROOT / "data/audit/candidate_audit.db")
    today = now.strftime("%Y-%m-%d")
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # 2. trainer_tier forward (오늘/어제 세션 후보에 값)
    print("\n[2] trainer_tier forward 기입 (커밋 9988a32 검증):")
    for sd in (today, yday):
        r = _q(audit, "SELECT count(*) tot, sum(case when trainer_tier is not null then 1 else 0 end) tt "
                      "FROM audit_candidate_rows WHERE session_date=?", (sd,))
        if r and r[0][0]:
            print(f"    {sd}: 후보 {r[0][0]} · trainer_tier 기입 {r[0][1]}")

    # 3. rel_vol_shadow 두 sink
    print("\n[3] rel_vol_shadow (1차=selection_log / 갭=candidate_audit):")
    r1 = _q(str(ROOT / "data/ticker_selection_log.db"),
            "SELECT count(*) FROM ticker_selection_log WHERE rel_vol_shadow is not null "
            "AND substr(created_at,1,10)>=?", (yday,))
    print(f"    ticker_selection_log 최근(≥{yday}): {r1[0][0] if r1 else '?'} non-null")
    r2 = _q(audit, "SELECT count(*) FROM audit_candidate_rows WHERE rel_vol_shadow is not null "
                   "AND session_date>=?", (yday,))
    print(f"    candidate_audit 최근(≥{yday}): {r2[0][0] if r2 else '?'} non-null (0이면 직렬화 갭 지속)")

    # 4. US excursion
    print("\n[4] US live 포지션 excursion:")
    pf = ROOT / "state/live_open_positions.json"
    if pf.exists():
        d = json.load(open(pf, encoding="utf-8"))
        pl = d if isinstance(d, list) else d.get("positions", d.get("open", []))
        if isinstance(pl, dict):
            pl = list(pl.values())
        for pos in pl:
            if pos.get("display_currency") == "USD":
                obs = [k for k in pos if k.startswith("observed_")]
                print(f"    {pos.get('ticker')}(US): observed키 {len(obs)} mfe={pos.get('observed_mfe_pct')}")

    # 5. BUY_READY 발동 (single_symbol_judge funnel)
    print("\n[5] BUY_READY 발동 (오늘 US 세션):")
    fjs = sorted(glob.glob(str(ROOT / "logs/funnel/single_symbol_judge_*.jsonl")), reverse=True)
    ba = {"BUY_READY": 0, "PULLBACK_WAIT": 0, "WAIT_RECHECK": 0, "REJECT": 0, "shadow": 0}
    if fjs:
        with open(fjs[0], encoding="utf-8", errors="ignore") as f:
            for line in f.readlines()[-2000:]:
                try:
                    o = json.loads(line)
                    a = str(o.get("action") or o.get("final_action") or "")
                    if a in ba:
                        ba[a] += 1
                    if o.get("immediate_buy_gate") == "shadow_observe" or o.get("immediate_buy_shadow"):
                        ba["shadow"] += 1
                except Exception:
                    continue
        print(f"    {os.path.basename(fjs[0])}: {ba}")
    else:
        print("    single_symbol_judge funnel 없음")

    # 6. lifecycle 오늘 FILLED/PLAN (US)
    print("\n[6] lifecycle 오늘(US 세션):")
    ev = _q(str(ROOT / "data/v2_event_store.db"),
            "SELECT event_type, count(*) FROM lifecycle_events "
            "WHERE market='US' AND substr(occurred_at,1,10)>=? "
            "AND event_type IN ('CLAUDE_PRICE_PLAN_CREATED','ORDER_SENT','FILLED','SAFETY_BLOCKED','CLOSED') "
            "GROUP BY event_type", (yday,))
    print("   ", dict(ev) if ev else "없음")
    print("\n=== 모니터 끝 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
