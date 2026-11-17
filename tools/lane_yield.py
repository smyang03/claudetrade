# -*- coding: utf-8 -*-
"""레인 실행 수율 점검 — 신호 → 주문 → 체결이 어디서 새는지(2026-11-17, 운영자 "진행해").

배경(브로커 정본 회고 11-17): 손실의 몸통은 비용이었고, 남은 약점은 실행 누수다.
canary 10월 신호 6건 중 3건 누락(DNS·PC 절전), 패닉 미매도, T1 YSS NYSE 거부(11-13)가 모두
'신호는 났는데 주문·체결로 안 이어졌고 아무도 바로 몰랐다'였다. 이 모듈은 최근 창에서
설명되지 않은 누락만 골라낸다. 계약대로 건너뛴 것(skip 행이 있는 것)은 누락이 아니다.

조회 전용 — 원장 파일만 읽는다. daily_ops_report.py가 매일 08:00 텔레그램 요약에 붙인다.
누수 종류:
  no_record   신호가 있는데 그 세션의 주문·건너뜀 기록이 없다 — 작업이 안 돌았거나 중간에 죽음
  no_fill     주문은 있는데 체결 대사 행이 없다(주문 다음 날 이후)
  not_filled  대사 결과 0주(거부·미체결) — 거부 사유가 있으면 함께
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SH = ROOT / "data" / "shadow"
WINDOW_DAYS = 7
T1_MIN_DVOL = 5e6          # us_earnings_t1_live 기본 min_dvol과 같은 문턱(쉐도우 선정 중 실전 대상)


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _in_window(d: str | None, today: date, days: int = WINDOW_DAYS) -> bool:
    """d가 [today−days, today) 안. 오늘 몫은 아직 진행 중일 수 있어 뺀다."""
    if not d:
        return False
    return (today - timedelta(days=days)).isoformat() <= str(d)[:10] < today.isoformat()


def _fill_checks(lane: str, live: list[dict], today: date) -> list[dict]:
    fills = {r.get("order_no"): r for r in live if r.get("kind") == "fill"}
    out = []
    for o in live:
        if o.get("kind") != "order" or o.get("side") != "buy" or not o.get("success") or not _in_window(o.get("session"), today):
            continue
        f = fills.get(o.get("order_no"))
        if f is None:
            out.append({"lane": lane, "kind": "no_fill", "session": o["session"], "ticker": o.get("ticker"),
                        "detail": "주문은 있는데 체결 대사 기록 없음"})
        elif not int(f.get("filled_qty") or 0):
            pos = [r for r in live if r.get("kind") == "position" and r.get("ticker") == o.get("ticker")
                   and r.get("status") == "CLOSED" and (r.get("entry_session") in (None, o.get("session")))]
            why = (pos[-1].get("reject_reason") or pos[-1].get("close_reason")) if pos else "0주"
            out.append({"lane": lane, "kind": "not_filled", "session": o["session"], "ticker": o.get("ticker"),
                        "detail": f"체결 0주 — {why}"})
    return out


def t1_leaks(shadow: list[dict], live: list[dict], today_et: date) -> list[dict]:
    sel = {}
    for s in shadow:
        if (s.get("kind") == "signal" and s.get("selected_I1") and float(s.get("dvol20") or 0) >= T1_MIN_DVOL
                and _in_window(s.get("session"), today_et)):
            sel.setdefault(s["session"], []).append(str(s.get("ticker")).upper())
    recorded = {r.get("session") for r in live if r.get("kind") in ("order", "skip")}
    out = [{"lane": "T1", "kind": "no_record", "session": d, "ticker": ",".join(sorted(t)),
            "detail": f"선정 {len(t)}건인데 주문·건너뜀 기록 없음"} for d, t in sorted(sel.items()) if d not in recorded]
    return out + _fill_checks("T1", live, today_et)


def canary_leaks(shadow: list[dict], live: list[dict], today: date) -> list[dict]:
    last: dict[str, dict] = {}
    for r in shadow:
        if r.get("key"):
            last[r["key"]] = r
    sigs = [r for r in last.values() if r.get("qualifies") and r.get("rule") in ("Y1", "L1_lowliq")
            and r.get("stock_code") and _in_window(r.get("entry_date"), today)]
    done = {(str(r.get("ticker")), r.get("session")) for r in live if r.get("kind") in ("order", "skip") and r.get("ticker")}
    session_skip = {r.get("session") for r in live if r.get("kind") == "skip" and not r.get("ticker")}
    out = []
    for s in sorted(sigs, key=lambda r: (r["entry_date"], str(r["stock_code"]))):
        k = (str(s["stock_code"]).strip(), s["entry_date"])
        if k not in done and s["entry_date"] not in session_skip:
            out.append({"lane": "canary", "kind": "no_record", "session": s["entry_date"], "ticker": k[0],
                        "detail": f"{s.get('corp_name') or ''} 신호({s.get('rule')})인데 주문·건너뜀 기록 없음".strip()})
    return out + _fill_checks("canary", live, today)


def l7_leaks(rounds: dict[str, dict], entry_of, live: list[dict], today: date) -> list[dict]:
    """rounds = kr_index_inclusion_live.load_rounds(), entry_of = 발표일 → 진입 세션(date)."""
    out = []
    for a, g in sorted(rounds.items()):
        try:
            ent = entry_of(date.fromisoformat(a)).isoformat()
        except Exception:  # noqa: BLE001
            continue
        if not _in_window(ent, today):
            continue
        rows = [r for r in live if r.get("round") == a and r.get("kind") in ("order", "skip")]
        if any(r.get("kind") == "skip" and not r.get("ticker") for r in rows):
            continue                                       # 회차 전체를 계약대로 건너뜀(손실 한도·늦은 실행)
        seen = {r.get("ticker") for r in rows}
        miss = [x["ticker"] for x in g.get("adds", []) if x["ticker"] not in seen]
        if miss:
            out.append({"lane": "L7", "kind": "no_record", "session": ent, "ticker": ",".join(miss),
                        "detail": f"회차 {a} 편입 {len(g.get('adds', []))} 중 {len(miss)}종목 주문·건너뜀 기록 없음"})
    return out + _fill_checks("L7", live, today)


def all_leaks(today_kst: date | None = None, today_et: date | None = None, sh: Path = SH) -> list[dict]:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    today_kst = today_kst or (now + timedelta(hours=9)).date()
    today_et = today_et or (now - timedelta(hours=5)).date()
    out = t1_leaks(_read(sh / "us_earnings_t1_shadow.jsonl"), _read(sh / "us_earnings_t1_live.jsonl"), today_et)
    out += canary_leaks(_read(sh / "kr_buyback_shadow.jsonl"), _read(sh / "kr_buyback_canary_live.jsonl"), today_kst)
    try:
        import sys
        sys.path.insert(0, str(ROOT / "tools"))
        import kr_index_inclusion_live as L
        out += l7_leaks(L.load_rounds(), L.entry_session, _read(sh / "kr_index_inclusion_live.jsonl"), today_kst)
    except Exception as exc:  # noqa: BLE001 — 달력·CSV 실패는 누수 1건으로 드러낸다
        out.append({"lane": "L7", "kind": "check_failed", "session": today_kst.isoformat(), "ticker": "",
                    "detail": f"점검 실패 {type(exc).__name__}"})
    return out


def lines(leaks: list[dict]) -> list[str]:
    if not leaks:
        return [f"실행 수율(최근 {WINDOW_DAYS}일): 누수 없음"]
    return [f"실행 수율(최근 {WINDOW_DAYS}일): 누수 {len(leaks)}건"] + \
        [f"  {x['lane']} {x['session']} {x['ticker']}: {x['detail']}" for x in leaks[:12]]


if __name__ == "__main__":
    print("\n".join(lines(all_leaks())))
