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
    """정규장: 네이버 폴링(KRX). 시간외 단계: KIS NX(넥스트레이드) 현재가."""
    if _PHASE.get("phase") == "NXT":
        return kel.kis_quote_nx(ticker)
    try:
        from tools.analysis_quotes import get_quote_kr
        q = get_quote_kr(ticker)
        if q and q.get("price"):
            q["venue"] = "KRX"
            return q
        return None
    except Exception:
        return None


_PHASE: dict = {"phase": "KRX"}


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


# ── 3시점 관측 원장 (탈락 공시 포함) ─────────────────────────────────────────
def _obs_start(obs: dict, it: dict, row: dict, session_date: str, now: datetime) -> None:
    """대상 종류 공시를 처음 봤을 때 감지 시점 가격을 적는다(PENDING이든 확정이든)."""
    rno = it["rcept_no"]
    if rno in obs or row.get("kind") not in kel.OBS_KINDS or row.get("is_correction"):
        return
    q = row.get("quote_detect") or {}
    obs[rno] = {"rcept_no": rno, "stock_code": it.get("stock_code"), "corp_name": it.get("corp_name"), "kind": row.get("kind"),
                "session_date": session_date, "venue": _PHASE.get("phase", "KRX"), "t_detect": row.get("ts_detected") or kel._iso(now),
                "px_detect": q.get("price"), "t_detect_quote": q.get("quoted_at"),   # 감지 시점 시세(없으면 결측 유지)
                "t_doc": None, "px_doc": None, "t_decide": None, "px_decide": None,
                "decision": None, "reason": None, "fields": {}, "out": {}}


def _obs_decide(obs: dict, rno: str, row: dict) -> None:
    o = obs.get(rno)
    if not o or row.get("decision") == "PENDING":
        return
    qd = row.get("quote_doc") or {}
    qz = row.get("quote") or {}
    # 단계별로 실제 받은 시세만 적는다. 결측은 결측(다른 단계 값으로 메우지 않음 — Codex 3차).
    o.update({"t_doc": qd.get("quoted_at") or row.get("ts_parsed"), "px_doc": qd.get("price"),
              "t_decide": qz.get("quoted_at") or row.get("ts_decided"), "px_decide": qz.get("price"),
              "decision": row.get("decision"), "reason": row.get("reason"),
              "fields": {k: (row.get("fields") or {}).get(k) for k in ("ratio_pct", "amount_krw", "related_party", "ratio_per_share")},
              "latency_sec": row.get("latency_sec"), "proc_sec": row.get("proc_sec"), "doc_attempts": row.get("doc_attempts"),
              "nx_last_trade_age_min": qz.get("last_trade_age_min")})


def _obs_fill(obs: dict, quote_fn, now: datetime, *, session_end: bool = False, venue: str | None = None,
              keep: set | None = None) -> list[dict]:
    """만기 지난 결과 칸 채우기. EOD(KRX 15:20 / NXT 19:55) 이후 px_1520|px_1955, 단계 종료(session_end)에 px_close를
    채우고 원장에 쓴 뒤 상태에서 제거. venue를 주면 그 venue의 관측만 마감한다. keep(본문 대기 중)은 마감하지 않고 이월."""
    done = []
    for rno, o in list(obs.items()):
        v = o.get("venue") or "KRX"
        if venue is not None and v != venue:
            continue
        if session_end and keep and rno in keep:
            o["carried_over"] = True
            continue
        c = kel.CONTRACT_NXT if v == "NXT" else kel.CONTRACT
        eod = now.replace(hour=int(c["eod_exit_hhmm"][:2]), minute=int(c["eod_exit_hhmm"][3:]), second=0, microsecond=0)
        ek = kel.eod_key(v)
        t0 = datetime.fromisoformat(o["t_detect"])
        need = [f"px_{h}m" for h in kel.OBS_HORIZONS_MIN if f"px_{h}m" not in o["out"] and now >= t0 + timedelta(minutes=h)]
        if now >= eod and ek not in o["out"]:
            need.append(ek)
        if session_end and "px_close" not in o["out"]:
            need.append("px_close")
        if not need:
            continue
        q = quote_fn(o["stock_code"]) if o.get("stock_code") else None
        px = float(q["price"]) if q and q.get("price") else None
        for k in need:
            if px is not None or session_end:
                o["out"][k] = px
                o["out"][f"t_{k[3:]}"] = kel._iso(now)
        if session_end:
            base = o.get("px_detect"); dec = o.get("px_decide")
            for k in ("px_5m", "px_30m", ek, "px_close"):
                v = o["out"].get(k)
                o["out"][f"ret_{k[3:]}_pct"] = round((v / base - 1.0) * 100.0, 3) if v and base else None
            v = o["out"].get("px_close")
            o["out"]["ret_decide_to_close_pct"] = round((v / dec - 1.0) * 100.0, 3) if v and dec else None
            pd_ = o.get("px_detect"); pdoc = o.get("px_doc")
            o["out"]["ret_detect_to_decide_pct"] = round((dec / pd_ - 1.0) * 100.0, 3) if dec and pd_ else None   # 판단하는 동안 움직인 몫
            o["out"]["ret_detect_to_doc_pct"] = round((pdoc / pd_ - 1.0) * 100.0, 3) if pdoc and pd_ else None
            o["missing"] = [k for k in ("px_detect", "px_doc", "px_decide") if o.get(k) is None] + [k for k in ("px_5m", "px_30m", ek, "px_close") if o["out"].get(k) is None]
            o["written_at"] = kel._iso(now)
            kel._append(kel.OBS_LEDGER, o)
            done.append(obs.pop(rno))
    return done


def _age_sec(iso: str | None, now: datetime) -> float:
    try:
        return (now - datetime.fromisoformat(str(iso))).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def cycle(session_date: str, st: dict, *, dry: bool = False, now: datetime | None = None, phase: str | None = None) -> dict:
    now = now or kel.now_kst()
    _PHASE["phase"] = phase or kel.phase_of(now)
    contract = kel.CONTRACT_NXT if _PHASE["phase"] == "NXT" else kel.CONTRACT
    seen: set = set(st.get("seen", []))
    pending: dict = dict(st.get("pending") or {})  # rcept_no → {item, first_seen, attempts, last_try} (본문 지연 재시도)
    obs: dict = dict(st.get("obs") or {})            # rcept_no → 3시점 관측(탈락 포함), 장 종료에 원장으로
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
            pos = kel.open_phantom({**it, **row}, row["quote"], notify=_notify, now=now, contract=contract)
            if pos:
                open_pos.append(pos); new_today += 1; entered += 1

    # 1) 본문 대기 중인 공시 재시도 (간격 DOC_RETRY_GAP_SEC, 최대 DOC_RETRY_MAX_SEC 뒤 확정)
    for rno, p in sorted(pending.items()):
        if _age_sec(p.get("last_try"), now) < kel.DOC_RETRY_GAP_SEC:
            continue
        final = _age_sec(p.get("first_seen"), now) >= kel.DOC_RETRY_MAX_SEC
        row = kel.process_disclosure(p["item"], session_date=session_date, quote_fn=_quote,
                                     open_n=len(open_pos), new_today=new_today, first_seen=p.get("first_seen"),
                                     doc_attempts=int(p.get("attempts", 0)), final=final, now=now, contract=contract)
        retried += 1
        if row.get("decision") == "PENDING":
            pending[rno] = {**p, "attempts": int(p.get("attempts", 0)) + 1, "last_try": kel._iso(now)}
            continue
        pending.pop(rno, None)
        _obs_decide(obs, rno, row)
        _handle(p["item"], row)
    # 2) 신규 공시
    for it in sorted(fresh, key=lambda x: x["rcept_no"]):
        seen.add(it["rcept_no"])
        row = kel.process_disclosure(it, session_date=session_date, quote_fn=_quote,
                                     open_n=len(open_pos), new_today=new_today, first_seen=kel._iso(now), now=now,
                                     contract=contract)
        _obs_start(obs, it, row, session_date, now)
        if row.get("decision") == "PENDING":
            pending[it["rcept_no"]] = {"item": it, "first_seen": row["ts_detected"], "attempts": 1, "last_try": kel._iso(now)}
            print(f"[KR-EVENT] 본문 대기 {it.get('corp_name')} {it['rcept_no']} — 재시도 예약", flush=True)
            continue
        _obs_decide(obs, it["rcept_no"], row)
        _handle(it, row)
    # 유령 평가 + 관측 결과 칸
    open_pos, closed = kel.evaluate_phantoms(open_pos, _quote, notify=_notify, now=now)
    _obs_fill(obs, _quote, now)
    st["seen"] = sorted(seen)[-3000:]
    st["pending"] = pending
    st["obs"] = obs
    st["open_positions"] = open_pos
    st["session_date"] = session_date
    st["phase"] = _PHASE["phase"]
    st["last_cycle_at"] = kel._iso()
    st["open_n"] = len(open_pos)
    kel.save_state(st)
    _heartbeat({"session_date": session_date, "phase": _PHASE["phase"], "open_n": len(open_pos), "seen_n": len(seen),
                "pending_n": len(pending), "obs_n": len(obs)})
    return {"fresh": len(fresh), "entered": entered, "closed": len(closed), "open_n": len(open_pos),
            "pending": len(pending), "retried": retried}


def end_phase(sd: str, st: dict, now: datetime, phase: str, prev_phase: str) -> bool:
    """단계 종료 처리. KRX→NXT 전환(phase=NXT, prev=KRX)이면 본문 대기 건을 확정하지 않고 NXT로 이월(Codex 3차: 마감 직전 감지분 유실 방지),
    END(20:01)에서만 SKIP 확정. 그 venue의 관측을 마감(이월 건 제외)하고 유령을 강제 청산(이월 없음). 반환 True = 루프 종료."""
    end_venue = "KRX" if phase == "NXT" else prev_phase
    _PHASE["phase"] = end_venue
    pending_now = dict(st.get("pending") or {})
    if phase == "END":
        for rno, p in sorted(pending_now.items()):
            try:
                kel.process_disclosure(p["item"], session_date=sd, quote_fn=_quote, open_n=0, new_today=0,
                                       first_seen=p.get("first_seen"), doc_attempts=int(p.get("attempts", 0)), final=True)
            except Exception as exc:
                print(f"[KR-EVENT] pending finalize error {rno}: {exc}", flush=True)
        st["pending"] = {}
        pending_now = {}
    elif pending_now:
        print(f"[KR-EVENT] 본문 대기 {len(pending_now)}건 NXT 단계로 이월", flush=True)
    written = _obs_fill(st.setdefault("obs", {}), _quote, now, session_end=True, venue=end_venue, keep=set(pending_now))
    if written:
        print(f"[KR-EVENT] {end_venue} 관측 원장 기록 {len(written)}건 (탈락 포함)", flush=True)
    open_pos = load_open_positions(sd, st)
    if open_pos:
        open_pos, closed = kel.evaluate_phantoms(open_pos, _quote, notify=_notify, now=now, force_close=True)
        print(f"[KR-EVENT] {end_venue} 종료 강제 청산 {len(closed)}건 (남음 {len(open_pos)})", flush=True)
    st["open_positions"] = open_pos
    if phase == "END":
        kel.save_state(st)
        print(f"[KR-EVENT] session end {sd} — exit", flush=True)
        return True
    st["phase"] = "NXT"
    _PHASE["phase"] = "NXT"
    kel.save_state(st)
    print(f"[KR-EVENT] {now.strftime('%H:%M')} NXT 시간외 단계 시작 (KIS NX 시세, 진입 마감 {kel.AFTER_HOURS['entry_cutoff_hhmm']} · EOD {kel.AFTER_HOURS['eod_exit_hhmm']})", flush=True)
    return False


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
        phase = kel.phase_of(now)
        prev_phase = st.get("phase") or "KRX"
        if phase == "END" or (phase == "NXT" and prev_phase == "KRX"):
            if end_phase(sd, st, now, phase, prev_phase):
                return 0
            continue
        if hhmm < "08:50":
            time.sleep(min(poll_sec, 30)); continue
        try:
            r = cycle(sd, st, phase=phase)
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
