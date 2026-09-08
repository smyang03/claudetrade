# -*- coding: utf-8 -*-
"""forward 판정 자동화 (2026-09-08) — 신규 전략 arm·원장의 사전등록 반증/후보 조건을 매일 평가하고 **상태가 바뀔 때만** 텔레그램 한 줄.

입력: tools/event_family_report.py --json(가상 북 arm), data/shadow/us_panic_close.jsonl(패닉), data/analysis/discovery_breakdown.json(★4조건 셀).
규칙(사전등록 preregistration_event_family_20260907.md):
  - arm: 완결 코호트 정산 ≥ N 및 진입 세션 ≥30. t<0 → REFUTED(기술적 음수 표지).
    t≥2.5·서로 다른 반기 평균 모두 양수·상위2세션 제외 양수 → REVIEW_REQUIRED.
    일별 t는 중첩/다중검정 보정 검정이 아니며, 이 도구는 실전 승격을 승인하지 않는다.
  - 패닉 마감: forward 패닉 세션 ≥10 & 오버나이트 세션 평균 ≤0 → REFUTED
  - 배제 arm의 기저 대비 증분 검정은 아직 미구현. 절대수익을 필터의 추가효과로 해석하지 않는다.
상태 파일 state/forward_gate_state.json — RESEARCH_ONLY. 구버전 STRONG도 실주문 승인으로 사용 금지.
사용: python tools/forward_gate_watch.py [--no-telegram]
"""
from __future__ import annotations

import json
import math
import statistics as st
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DB = ROOT / "data" / "shadow" / "virtual_books.db"
PANIC = ROOT / "data" / "shadow" / "us_panic_close.jsonl"
BREAKDOWN = ROOT / "data" / "analysis" / "discovery_breakdown.json"
REPORT_JSON = ROOT / "data" / "analysis" / "event_family_report_latest.json"
STATE = ROOT / "state" / "forward_gate_state.json"
MIN_N = {"c_kr_plan_buy": 20, "c_kr_exright": 20}
MIN_SESSIONS = 30
GATE_SCHEMA = "forward_research_gate_v2"
ARMS = ["c_kr_fallen_buyback30", "c_kr_fallen_buyback_active", "c_kr_fallen_nomajor", "c_kr_insider_cluster", "c_kr_plan_buy",
        "c_kr_exright", "c_kr_buyback_start", "c_us_earn_gap", "c_us_insider_cluster", "c_us_volfirst", "c_kr_insider_k1", "c_us_insider_k1",
        "c_kr_fallen_regime", "c_us_fallen_regime", "c_us_slow8", "c_us_slow8_regime", "c_us_panic_all", "c_us_panic_top5", "c_kr_fallen5_nobio"]


def _t(vals):
    if len(vals) < 2:
        return None
    sd = st.stdev(vals)
    return st.mean(vals) / (sd / len(vals) ** 0.5) if sd else None


def forward_arm_stats(con) -> dict:
    out = {}
    for sid in ARMS:
        rows = con.execute("SELECT session_date, net_pct, status FROM trades WHERE strategy_id=? AND backfill=0", (sid,)).fetchall()
        by = defaultdict(list)
        incomplete = set()
        invalid = 0
        for sd, net, status in rows:
            try:
                datetime.strptime(sd, "%Y-%m-%d")
                if status != "CLOSED":
                    incomplete.add(sd)
                    continue
                value = float(net)
                if not math.isfinite(value):
                    raise ValueError("nonfinite return")
            except (ValueError, TypeError):
                incomplete.add(sd)
                invalid += 1
                continue
            by[sd].append(value)
        # Early winners must not represent a cohort whose losers are still OPEN.
        complete = sorted((sd, v) for sd, v in by.items() if sd not in incomplete)
        sm = [st.mean(v) for _, v in complete]
        halves = defaultdict(list)
        for sd, values in complete:
            halves[f"{sd[:4]}H{1 if int(sd[5:7]) <= 6 else 2}"].append(st.mean(values))
        out[sid] = {"n": sum(len(v) for _, v in complete), "sessions": len(sm),
                    "session_mean": st.mean(sm) if sm else None, "session_t": _t(sm),
                    "incomplete_sessions": len(incomplete), "invalid_rows": invalid,
                    "half_year_means": {k: st.mean(v) for k, v in halves.items()},
                    "ex_top2_mean": st.mean(sorted(sm)[:-2]) if len(sm) > 2 else None,
                    "live_eligible": False}
    return out


def panic_forward() -> dict:
    rows = [json.loads(l) for l in PANIC.read_text(encoding="utf-8").splitlines() if l.strip()] if PANIC.exists() else []
    tr = [r for r in rows if r.get("kind") == "trade" and r.get("mode") == "live" and r.get("instrument") == "stock" and r.get("status") == "CLOSED" and r.get("overnight_pct") is not None]
    by = defaultdict(list)
    for r in tr:
        by[r["session_date"]].append(float(r["overnight_pct"]))
    sm = [st.mean(v) for v in by.values()]
    closed = [r for r in tr if r.get("status") == "CLOSED"]
    return {"sessions": len(sm), "overnight_session_mean": round(st.mean(sm), 2) if sm else None, "closed": len(closed)}


def verdict(sid: str, s: dict) -> str:
    if s.get("invalid_rows", 0):
        return "DATA_INVALID"
    n_min = MIN_N.get(sid, 30)
    if s["n"] < n_min:
        return f"ACCUMULATING({s['n']}/{n_min})"
    if s.get("sessions", 0) < MIN_SESSIONS:
        return f"ACCUMULATING_SESSIONS({s.get('sessions', 0)}/{MIN_SESSIONS})"
    t = s.get("session_t")
    if t is not None and t < 0:
        return "REFUTED"
    halves = s.get("half_year_means", {})
    if (t is not None and t >= 2.5 and len(halves) >= 2
            and all(v > 0 for v in halves.values()) and (s.get("ex_top2_mean") or 0) > 0):
        # Descriptive t is not an overlap/multiple-testing adjusted promotion test.
        # Portfolio replay, contract parity and an independent review are still required.
        return "REVIEW_REQUIRED"
    return "WATCH"


def main() -> int:
    args = sys.argv[1:]
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.execute("PRAGMA busy_timeout=5000")
    arms = forward_arm_stats(con); con.close()
    verdicts = {sid: verdict(sid, s) for sid, s in arms.items()}
    pf = panic_forward()
    verdicts["panic_close_overnight"] = ("REFUTED" if pf["sessions"] >= 10 and (pf["overnight_session_mean"] or 0) <= 0
                                        else f"ACCUMULATING({pf['sessions']}/10)" if pf["sessions"] < 10 else "WATCH")
    stars = []
    try:
        bd = json.loads(BREAKDOWN.read_text(encoding="utf-8"))
        for pid, p in (bd.get("pools") or {}).items():
            for c in (p.get("needle_candidates_buy") or []):
                if c.get("passes_4"):
                    stars.append(f"{pid}:{c.get('ladder')}={c.get('cell')}")
    except (OSError, ValueError):
        pass
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur = {"schema_version": GATE_SCHEMA, "authority": "RESEARCH_ONLY",
           "live_eligible": False, "arms": arms,
           "verdicts": verdicts, "stars": sorted(stars), "panic": pf, "generated_at": now}
    try:
        prev = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prev = {}
    changes = [(k, prev.get("verdicts", {}).get(k), v) for k, v in verdicts.items() if prev.get("verdicts", {}).get(k) != v]
    new_stars = sorted(set(stars) - set(prev.get("stars") or []))
    STATE.parent.mkdir(parents=True, exist_ok=True); STATE.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    REPORT_JSON.write_text(json.dumps(cur, ensure_ascii=False, indent=1), encoding="utf-8")
    for sid, v in verdicts.items():
        s = arms.get(sid, {})
        print(f"{sid:28s} {v:22s} n={s.get('n', pf.get('closed') if sid.startswith('panic') else 0)} sess_mean={s.get('session_mean')} t={s.get('session_t')}")
    if stars:
        print("★4조건 통과 셀:", stars)
    # 텔레그램: 판정 변화(축적→판정, 판정 간 이동)·★ 신규 출현만. 축적 카운트 변화는 침묵.
    notable = [(k, a, b) for k, a, b in changes if not (str(a or "").startswith("ACCUMULATING") and str(b).startswith("ACCUMULATING"))]
    if (notable or new_stars) and "--no-telegram" not in args:
        lines = ["🧪 [VIRTUAL] forward 판정 변화 (신규 전략, 실매수 아님)"]
        lines += [f"- {k}: {a or '-'} → {b}" for k, a, b in notable]
        lines += [f"- ★4조건 신규 셀: {x}" for x in new_stars]
        try:
            import telegram_reporter as tg
            tg.send("\n".join(lines), parse_mode=None, critical=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[GATE] telegram 실패 {exc}")
    print(f"[GATE] 변화 {len(notable)}건 · ★신규 {len(new_stars)}")
    if "--weekly" in args:   # 월요일 08:00 주간 다이제스트 — 변화 유무와 무관하게 한 통(운영자 "매주 보고")
        def _cnt(p):
            try:
                return sum(1 for _ in p.open(encoding="utf-8"))
            except OSError:
                return 0
        sh = ROOT / "data" / "shadow"
        lines = ["📊 [VIRTUAL] 주간 forward 현황 (실매수 아님)"]
        for sid, s in arms.items():
            lines.append(f"- {sid}: {s['n']}건/{MIN_N.get(sid, 30)} t={s.get('session_t')} 평균={s.get('session_mean')}")
        lines.append(f"- 패닉 마감: 세션 {pf['sessions']}/10 오버나이트={pf['overnight_session_mean']}")
        lines.append(f"- 원장: 공시fast {_cnt(sh / 'kr_event_fast.jsonl')} · 개장충격 {_cnt(sh / 'us_open_impact.jsonl')} · 매도흡수 {_cnt(sh / 'kr_absorption.jsonl')} · 종가동시호가 {_cnt(sh / 'kr_close_auction.jsonl')} · 유예 {_cnt(sh / 'kr_event_preopen_fills.jsonl')}")
        lines.append("- 판정: " + (", ".join(f"{k}={v}" for k, v in verdicts.items() if not str(v).startswith("ACCUMULATING")) or "전부 축적 중"))
        if "--no-telegram" not in args:
            try:
                import telegram_reporter as tg
                tg.send(chr(10).join(lines), parse_mode=None, critical=False)
            except Exception as exc:  # noqa: BLE001
                print(f"[GATE] weekly telegram 실패 {exc}")
        print(chr(10).join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
