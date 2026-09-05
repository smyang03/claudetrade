# -*- coding: utf-8 -*-
"""KR 공시 이벤트 레인 러너 — 장중 루프(08:50~15:40) 또는 리플레이 (SHADOW, 2026-09-04).

사용:
  python tools/kr_event_lane_runner.py --loop                 # 장중 감시 (schtask claudetrade_kr_event_lane 08:45)
  python tools/kr_event_lane_runner.py --once                 # 1사이클
  python tools/kr_event_lane_runner.py --replay FILE.jsonl    # 과거 공시 목록 분류·본문 파싱만(시세·유령 없음)

권한: 주문 없음. 원장·텔레그램(critical)·상태 파일만 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.live", override=False)

from runtime import kr_event_lane as kel  # noqa: E402

HEARTBEAT = ROOT / "state" / "kr_event_lane_heartbeat.json"


def _quote(ticker: str):
    try:
        from tools.analysis_quotes import get_quote_kr
        q = get_quote_kr(ticker)
        return q if q and q.get("price") else None
    except Exception:
        return None


def _notify(text: str) -> None:
    try:
        import telegram_reporter as tg
        tg.send(text, parse_mode=None, critical=True)
    except Exception:
        pass


def _heartbeat(extra: dict | None = None) -> None:
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({"pid": os.getpid(), "written_at": kel._iso(), **(extra or {})},
                                        ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


_ENSURED: set = set()


def _ensure_cache_async(ticker: str) -> None:
    """KR 일봉 CSV가 없으면 백그라운드로 생성(중복 방지). 실패는 조용히 — 다음 실행에서 재시도."""
    if ticker in _ENSURED:
        return
    _ENSURED.add(ticker)
    try:
        from tools.ensure_kr_price_cache import missing, ensure
        if not missing([ticker]):
            return
        import threading
        threading.Thread(target=lambda: ensure([ticker], verbose=False), daemon=True).start()
    except Exception:
        pass


open_positions_from_ledger = kel.open_positions_from_ledger


def load_open_positions(session_date: str, st: dict) -> list[dict]:
    """사이클 간 유령 상태(peak/trough/time_checked/last_px)는 state가 정본, 원장은 복구·대사용(09-06 수리).
    원장에 OPEN인데 state에 없으면 원장 행을 쓰고, 원장에서 이미 CLOSE된 건은 state에서 제거한다."""
    ledger = {p["rcept_no"]: p for p in kel.open_positions_from_ledger(session_date)}
    state = {p["rcept_no"]: p for p in (st.get("open_positions") or []) if st.get("session_date") == session_date}
    return [state.get(rno) or ledger[rno] for rno in ledger]


def _age_sec(iso: str | None, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def cycle(session_date: str, st: dict, *, dry: bool = False, now: datetime | None = None) -> dict:
    now = now or kel.now_kst()
    seen: set = set(st.get("seen", []))
    pending: dict = dict(st.get("pending") or {})  # rcept_no → {item, first_seen, attempts, last_try} (본문 지연 재시도)
    open_pos = load_open_positions(session_date, st)
    new_today = sum(1 for r in kel.read_jsonl(kel.PHANTOM_LEDGER)
                    if r.get("event") == "OPEN" and r.get("session_date") == session_date)
    items = kel.dart_list_today(session_date)
    fresh = [i for i in items if i.get("rcept_no") and i["rcept_no"] not in seen]
    entered = 0
    retried = 0

    def _handle(it: dict, row: dict) -> None:
        nonlocal new_today, entered
        if row.get("kind") in ("supply_contract", "bonus_issue", "buyback") and it.get("stock_code"):
            _ensure_cache_async(it["stock_code"])  # 일봉 arm(F6/F7)이 다음날 진입할 수 있게 CSV 선제 생성
        if row.get("decision") == "ENTER" and not dry:
            pos = kel.open_phantom({**it, **row}, row["quote"], notify=_notify)
            if pos:
                open_pos.append(pos); new_today += 1; entered += 1

    # 1) 본문 대기 중인 공시 재시도 (간격 DOC_RETRY_GAP_SEC, 최대 DOC_RETRY_MAX_SEC 뒤 확정)
    for rno, p in sorted(pending.items()):
        if _age_sec(p.get("last_try"), now) < kel.DOC_RETRY_GAP_SEC:
            continue
        final = _age_sec(p.get("first_seen"), now) >= kel.DOC_RETRY_MAX_SEC
        row = kel.process_disclosure(p["item"], session_date=session_date, quote_fn=_quote,
                                     open_n=len(open_pos), new_today=new_today, first_seen=p.get("first_seen"),
                                     doc_attempts=int(p.get("attempts", 0)), final=final, now=now)
        retried += 1
        if row.get("decision") == "PENDING":
            pending[rno] = {**p, "attempts": int(p.get("attempts", 0)) + 1, "last_try": kel._iso(now)}
            continue
        pending.pop(rno, None)
        _handle(p["item"], row)
    # 2) 신규 공시
    for it in sorted(fresh, key=lambda x: x["rcept_no"]):
        seen.add(it["rcept_no"])
        row = kel.process_disclosure(it, session_date=session_date, quote_fn=_quote,
                                     open_n=len(open_pos), new_today=new_today, first_seen=kel._iso(now), now=now)
        if row.get("decision") == "PENDING":
            pending[it["rcept_no"]] = {"item": it, "first_seen": row["ts_detected"], "attempts": 1, "last_try": kel._iso(now)}
            print(f"[KR-EVENT] 본문 대기 {it.get('corp_name')} {it['rcept_no']} — 재시도 예약", flush=True)
            continue
        _handle(it, row)
    # 유령 평가
    open_pos, closed = kel.evaluate_phantoms(open_pos, _quote, notify=_notify, now=now)
    st["seen"] = sorted(seen)[-3000:]
    st["pending"] = pending
    st["open_positions"] = open_pos
    st["session_date"] = session_date
    st["last_cycle_at"] = kel._iso()
    st["open_n"] = len(open_pos)
    kel.save_state(st)
    _heartbeat({"session_date": session_date, "open_n": len(open_pos), "seen_n": len(seen), "pending_n": len(pending)})
    return {"fresh": len(fresh), "entered": entered, "closed": len(closed), "open_n": len(open_pos),
            "pending": len(pending), "retried": retried}


def loop(poll_sec: float) -> int:
    print(f"[KR-EVENT] loop start poll={poll_sec}s authority={kel.AUTHORITY}", flush=True)
    orphans = kel.finalize_orphans(kel.now_kst().strftime("%Y-%m-%d"))
    if orphans:
        print(f"[KR-EVENT] 이전 세션 미청산 유령 {len(orphans)}건 ORPHAN_UNPRICED 마감(손익 표본 제외)", flush=True)
    while True:
        now = kel.now_kst()
        sd = now.strftime("%Y-%m-%d")
        st = kel.load_state()
        if st.get("session_date") != sd:
            st = {"session_date": sd, "seen": []}
        hhmm = now.strftime("%H:%M")
        if now.weekday() >= 5 or hhmm >= "15:41":
            # 장 종료: 본문 대기 건은 SKIP 확정(원장 누락 방지) → 남은 유령 강제 청산(마지막 관측가, 이월 없음) 후 종료
            for rno, p in sorted((st.get("pending") or {}).items()):
                try:
                    kel.process_disclosure(p["item"], session_date=sd, quote_fn=_quote, open_n=0, new_today=0,
                                           first_seen=p.get("first_seen"), doc_attempts=int(p.get("attempts", 0)), final=True)
                except Exception as exc:
                    print(f"[KR-EVENT] pending finalize error {rno}: {exc}", flush=True)
            st["pending"] = {}
            open_pos = load_open_positions(sd, st)
            if open_pos:
                open_pos, closed = kel.evaluate_phantoms(open_pos, _quote, notify=_notify, now=now, force_close=True)
                print(f"[KR-EVENT] 장 종료 강제 청산 {len(closed)}건 (남음 {len(open_pos)})", flush=True)
            st["open_positions"] = open_pos
            kel.save_state(st)
            print(f"[KR-EVENT] session end {sd} — exit", flush=True)
            return 0
        if hhmm < "08:50":
            time.sleep(min(poll_sec, 30)); continue
        try:
            r = cycle(sd, st)
            if r["fresh"] or r["entered"] or r["closed"]:
                print(f"[KR-EVENT] {hhmm} fresh={r['fresh']} enter={r['entered']} close={r['closed']} open={r['open_n']}", flush=True)
        except Exception as exc:
            print(f"[KR-EVENT] cycle error: {exc}", flush=True)
        time.sleep(poll_sec)


def replay(path: Path, limit: int | None) -> int:
    """과거 공시 목록(jsonl: kind/stock/date/name/report/rcept_no) → 분류·본문 파싱 통계. 시세·유령 없음."""
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if limit:
        rows = rows[-limit:]
    import collections
    stats = collections.Counter(); ratios = []
    for r in rows:
        kind, corr = kel.classify_title(r.get("report") or r.get("report_nm") or "")
        if kind != "supply_contract" or corr:
            stats[f"{kind}{'_corr' if corr else ''}"] += 1; continue
        text = kel.dart_document_text(r["rcept_no"])
        if not text:
            stats["doc_fail"] += 1; continue
        f = kel.parse_supply_contract(text)
        if f.get("ratio_pct") is None:
            stats["ratio_missing"] += 1; continue
        ratios.append((r.get("stock") or r.get("stock_code"), r.get("date"), f["ratio_pct"], f.get("related_party")))
        stats["parsed"] += 1
        time.sleep(0.1)
    print("stats", dict(stats))
    if ratios:
        big = [x for x in ratios if x[2] >= kel.CONTRACT["supply_ratio_min_pct"] and not x[3]]
        print(f"공급계약 파싱 {len(ratios)}건 · 비율≥30%&외부 {len(big)}건 ({len(big) / len(ratios) * 100:.1f}%)")
        out = ROOT / "data" / "analysis" / "kr_supply_contract_parsed.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8") as fh:
            for x in ratios:
                fh.write(json.dumps({"stock": x[0], "date": x[1], "ratio_pct": x[2], "related": x[3]}, ensure_ascii=False) + "\n")
        print("saved", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--poll-sec", type=float, default=20.0)
    ap.add_argument("--replay", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry", action="store_true", help="분류·판단·원장만, 유령 진입 없음 (운영 점검용)")
    a = ap.parse_args()
    if a.replay:
        return replay(Path(a.replay), a.limit)
    if a.once:
        sd = kel.now_kst().strftime("%Y-%m-%d")
        st = kel.load_state()
        if st.get("session_date") != sd:
            st = {"session_date": sd, "seen": []}
        print(cycle(sd, st, dry=a.dry))
        return 0
    if a.loop:
        return loop(a.poll_sec)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
