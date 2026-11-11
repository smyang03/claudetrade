#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 공모주 균등배정 청약 알림 (텔레그램 전용, 주문 없음) — 2026-11-11 운영자 "텔레그램으로 알려주는걸로".

KIS Open API에는 공모주 청약 주문이 없다(공식 예제는 ksdinfo/pub-offer 일정 조회뿐). 그래서 청약은 사람이 앱으로 하고,
이 도구는 놓치지 않게 알려만 준다.

알림 종류 (회사×종류마다 1회, 상태 파일로 멱등):
- sub_d1  : 다음 영업일 청약 시작
- sub_d0  : 오늘 청약 시작 (전날 알림을 못 받았을 때 대비 — 같은 회사는 sub_d1을 보냈으면 생략)
- list_d1 : 다음 영업일 상장 (배정받았으면 시초가 매도 판단)
스팩은 제외(공모가 2,000원 고정·균등 수익 구조가 다름). 주관사에 한국투자증권이 있으면 "KIS 계좌로 청약 가능" 표시.

소스: tools/kr_ipo_calendar.py (38커뮤니케이션 공모 일정·신규상장 표). 증거금은 '통상 균등 최소 10주 × 증거금 50%' 추정치이고
종목마다 다르다 — 청약 전 증권사 화면에서 확인한다.
사용: python tools/kr_ipo_alert.py run [--dry-run] [--today YYYY-MM-DD]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE = ROOT / "data" / "shadow" / "kr_ipo_alert_state.json"
KST = timezone(timedelta(hours=9))
KIS_NAME = "한국투자"
MIN_QTY = 10          # 통상 균등배정 최소 청약 수량
MARGIN_RATE = 0.5     # 통상 청약 증거금률


def _is_business_day(d: date) -> bool:
    try:
        from preopen.scheduler import is_trading_day

        return bool(is_trading_day("KR", d.isoformat()))
    except Exception:
        return d.weekday() < 5


def next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not _is_business_day(n):
        n += timedelta(days=1)
    return n


def parse_subscription(text: str) -> tuple[date | None, date | None]:
    """'2026.12.14~12.15' / '2026.12.31~01.02' / '2026.12.14~2026.12.15' → (시작, 끝)."""
    m = re.match(r"\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\s*~\s*(?:(\d{4})\.)?(\d{1,2})\.(\d{1,2})", text or "")
    if not m:
        return None, None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    start = date(y, mo, d)
    ey = int(m.group(4)) if m.group(4) else y
    end = date(ey, int(m.group(5)), int(m.group(6)))
    if end < start and not m.group(4):
        end = date(y + 1, end.month, end.day)
    return start, end


def _num(s) -> float | None:
    try:
        return float(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def price_info(rec: dict) -> tuple[float | None, str]:
    """(증거금 계산용 가격, 표시 문자열). 확정공모가가 있으면 그것, 없으면 희망밴드 상단."""
    raw = rec.get("raw") or []
    fixed = rec.get("offer_price")
    if fixed is None and len(raw) > 2:
        fixed = _num(raw[2])
    band = rec.get("band") or (raw[3] if len(raw) > 3 else "")
    if fixed:
        return fixed, f"확정 {fixed:,.0f}원"
    hi = None
    if band and "~" in band:
        hi = _num(band.split("~")[-1])
    return hi, (f"희망 {band}원" if band else "공모가 미정")


def underwriter(rec: dict) -> str:
    raw = rec.get("raw") or []
    return rec.get("underwriter") or (raw[5] if len(raw) > 5 else "")


def recent_regime(ledger_rows: list[dict], today: date, days: int = 90) -> str:
    since = (today - timedelta(days=days)).isoformat()
    sel = [r["ret_open_pct"] for r in ledger_rows
           if r.get("status") == "listed" and not r.get("spac") and r.get("ret_open_pct") is not None
           and r.get("listing_date", "") >= since]
    if not sel:
        return "최근 90일 상장 표본 없음"
    pos = sum(1 for v in sel if v > 0) / len(sel) * 100
    loss = sum(1 for v in sel if v <= -10) / len(sel) * 100
    return (f"최근 90일 비스팩 {len(sel)}건: 시초가/공모가 중앙 {st.median(sel):+.0f}% · 양수 {pos:.0f}% · "
            f"−10% 이하 {loss:.0f}%")


def build_alerts(schedule: list[dict], upcoming: list[dict], today: date, sent: dict) -> list[dict]:
    nxt = next_business_day(today)
    out: list[dict] = []
    for rec in schedule:
        name = str(rec.get("corp_name") or "").strip()
        if not name or rec.get("spac") or ("스팩" in name):
            continue
        start, end = parse_subscription(str(rec.get("subscription") or (rec.get("raw") or ["", ""])[1]))
        if start is None:
            continue
        kind = ""
        if start == nxt:
            kind = "sub_d1"
        elif start == today and f"{name}|sub_d1|{start}" not in sent:
            kind = "sub_d0"
        if not kind:
            continue
        price, ptxt = price_info(rec)
        uw = underwriter(rec)
        out.append({
            "key": f"{name}|{kind}|{start}",
            "kind": kind, "name": name, "start": start.isoformat(), "end": end.isoformat() if end else "",
            "price_text": ptxt, "margin_krw": round(price * MIN_QTY * MARGIN_RATE) if price else None,
            "underwriter": uw, "kis": KIS_NAME in uw,
            "competition": rec.get("competition") or "",
        })
    for r in upcoming:
        name = str(r.get("corp_name") or "").strip()
        if not name or r.get("spac") or ("스팩" in name):
            continue
        if r.get("listing_date") == nxt.isoformat():
            out.append({"key": f"{name}|list_d1|{r['listing_date']}", "kind": "list_d1", "name": name,
                        "listing_date": r["listing_date"], "offer_price": r.get("offer_price")})
    return [a for a in out if a["key"] not in sent]


def format_message(alerts: list[dict], regime: str) -> str:
    lines = ["[공모주 알림]"]
    subs = [a for a in alerts if a["kind"] in ("sub_d1", "sub_d0")]
    lists = [a for a in alerts if a["kind"] == "list_d1"]
    for a in subs:
        when = "내일 청약 시작" if a["kind"] == "sub_d1" else "오늘 청약 시작"
        acct = "✅ KIS 계좌로 청약 가능" if a["kis"] else "⚠️ 타 증권사 계좌 필요"
        margin = f" · 균등 최소 증거금 약 {a['margin_krw']:,}원(10주×50% 추정)" if a.get("margin_krw") else ""
        comp = f" · 경쟁률 {a['competition']}" if a.get("competition") else ""
        lines.append(f"• {when}: {a['name']} ({a['start']}~{a['end']}) {a['price_text']}{margin}{comp}")
        lines.append(f"  주관 {a['underwriter'] or '미상'} — {acct}")
    for a in lists:
        op = f" 공모가 {a['offer_price']:,.0f}원" if a.get("offer_price") else ""
        lines.append(f"• 내일 상장: {a['name']} ({a['listing_date']}){op} — 배정받았으면 시초가 매도 판단")
    lines.append(f"참고: {regime}")
    lines.append("청약은 직접(KIS API에 청약 주문 없음). 증거금·최소수량은 증권사 화면에서 확인.")
    return "\n".join(lines)


def _load_state() -> dict:
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict, today: date) -> None:
    cutoff = (today - timedelta(days=60)).isoformat()
    state = {k: v for k, v in state.items() if str(v) >= cutoff}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _telegram(msg: str) -> bool:
    try:
        from dotenv import load_dotenv

        load_dotenv(str(ROOT / ".env.live"), override=False)
        import telegram_reporter as tg

        return bool(tg.send(msg, parse_mode=None, critical=True))
    except Exception as exc:  # noqa: BLE001 — 알림 실패는 다음 실행에서 재시도
        print(f"[ipo-alert] 텔레그램 실패: {type(exc).__name__}")
        return False


def cmd_run(today: date, dry_run: bool) -> int:
    from tools import kr_ipo_calendar as cal

    calendar = cal.build(2)
    ledger_rows = []
    if cal.LEDGER.exists():
        for line in cal.LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                ledger_rows.append(json.loads(line))
            except ValueError:
                pass
    state = _load_state()
    alerts = build_alerts(calendar.get("schedule") or [], calendar.get("upcoming") or [], today, state)
    print(f"[ipo-alert] {today} 일정 {len(calendar.get('schedule') or [])} · 상장예정 {len(calendar.get('upcoming') or [])} · 새 알림 {len(alerts)}")
    if not alerts:
        return 0
    msg = format_message(alerts, recent_regime(ledger_rows, today))
    print(msg)
    if dry_run:
        return 0
    if _telegram(msg):
        stamp = today.isoformat()
        for a in alerts:
            state[a["key"]] = stamp
        _save_state(state, today)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["run"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default="")
    a = ap.parse_args()
    today = date.fromisoformat(a.today) if a.today else datetime.now(KST).date()
    return cmd_run(today, a.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
