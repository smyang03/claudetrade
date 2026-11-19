# -*- coding: utf-8 -*-
"""공모주 균등배정 상장일 시초가 자동 매도 (2026-11-19, 운영자 "자동매도 진행해").

근거: 공모주 원장(tools/kr_ipo_calendar.py, 스팩 제외 140건 2024-11~2026-11) 공모가 대비 시초가 중앙 +48~+206%(반기별),
시초가 → 첫날 종가는 중앙 −18.9%(2026 −26.7%) → **상장일 장전 시장가(시초가) 매도**가 계약이다.
청약은 사람이 한다(KIS API에 청약 기능 없음). 이 도구는 KIS 계좌에 입고된 공모주를 상장일 시초가에 판다.

식별(오매도 방지 — 셋 다 맞아야 판다):
  ① 오늘이 공모주 원장의 상장일(스팩 제외)이고 ② KIS 잔고 종목명이 그 기업명과 정규화 일치이며
  ③ 다른 레인 원장 보유·코어(SCHG·275280·275300)가 아니다. 원장에 공모가가 있으면 평균단가가 공모가 ±1% 안이어야 한다.
명령:
  sell       상장일 08:43(개장 지연일은 밀린 창까지 대기). 대상 OPEN 행을 먼저 남기고 시장가 매도(봇 격리 귀속용).
  reconcile  16:10. 체결 확인 → CLOSED·손익. 못 판 보유는 다음 세션 장중(exit 창) 재매도 대상, 텔레그램.
  status     원장 요약.
원장: data/shadow/kr_ipo_autosell_live.jsonl (형식은 다른 레인과 같다: order/position/fill/close).
⚠️ 봇 격리 배선(risk_manager.ISOLATED_STRATEGY_SOURCES + trading_bot 귀속 복원)이 들어가고 봇이 재시작된 뒤에만 주문한다(fail-closed).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from live_lane_common import (KST as _KST, kr_order_window, kr_wait_for_delayed_open, order_state,  # noqa: E402
                              place_safely)

LEDGER = ROOT / "data" / "shadow" / "kr_ipo_autosell_live.jsonl"
IPO_LEDGER = ROOT / "data" / "shadow" / "kr_ipo_ledger.jsonl"
SOURCE = "kr_ipo_autosell"
CORE = {"275280", "275300", "SCHG"}
OTHER_LANES = ("kr_index_inclusion_live.jsonl", "kr_buyback_canary_live.jsonl", "kr_panic_etf_micro_live.jsonl",
               "cash_parking_live.jsonl")
FEE_RATE = 0.00015
SELL_TAX = 0.0020


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> date:
    return datetime.now(_KST).date()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(rows: list[dict]) -> None:
    if not rows:
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _positions(rows: list[dict]) -> dict[str, dict]:
    cur: dict[str, dict] = {}
    for r in rows:
        if r.get("kind") == "position":
            cur[r["ticker"]] = r
    return cur


def norm_name(s: str) -> str:
    """'디티에스(구.동양티에스)'·'에스에프씨 (유가)'·'(주)xx' → 괄호·공백·법인 표기 제거."""
    s = re.sub(r"\([^)]*\)", "", str(s or ""))
    s = s.replace("주식회사", "").replace("㈜", "")
    return re.sub(r"\s+", "", s).strip().upper()


def listings_on(day: date, ipo_rows: list[dict]) -> list[dict]:
    return [r for r in ipo_rows if r.get("listing_date") == day.isoformat() and not r.get("spac")]


def other_lane_open(root: Path | None = None) -> set[str]:
    out: set[str] = set()
    for fn in OTHER_LANES:
        for t, p in _positions(_read((root or ROOT / "data" / "shadow") / fn)).items():
            if str(p.get("status")).upper() == "OPEN":
                out.add(str(t).upper())
    return out


def match_targets(stocks: list[dict], listings: list[dict], excluded: set[str]) -> list[dict]:
    """KIS 잔고 × 오늘 상장 공모주 → 매도 대상 [{ticker, name, qty, avg_price, corp_name, offer_price}]."""
    by = {norm_name(r["corp_name"]): r for r in listings}
    out = []
    for s in stocks:
        tk = str(s.get("ticker") or "").strip().upper()
        qty = int(s.get("qty") or 0)
        if qty < 1 or tk in CORE or tk in excluded:
            continue
        ipo = by.get(norm_name(s.get("name")))
        if not ipo:
            continue
        offer = ipo.get("offer_price")
        avg = float(s.get("avg_price") or 0)
        if offer and avg and abs(avg / float(offer) - 1) > 0.01:
            continue                                   # 평균단가가 공모가와 다르면 장내 매수분일 수 있다 — 팔지 않는다
        out.append({"ticker": tk, "name": s.get("name"), "qty": qty, "avg_price": avg,
                    "corp_name": ipo["corp_name"], "offer_price": offer})
    return out


def _alert(msg: str) -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(str(ROOT / ".env.live"), override=False)
        import telegram_reporter as tg
        tg.send(msg, parse_mode=None, critical=True)
    except Exception as exc:  # noqa: BLE001
        print(f"[ipo-sell] 텔레그램 실패: {type(exc).__name__}")


def _isolation_ready() -> tuple[bool, str]:
    import risk_manager
    if SOURCE not in risk_manager.ISOLATED_STRATEGY_SOURCES:
        return False, "봇 격리 계약(kr_ipo_autosell) 없음"
    try:
        started = datetime.fromisoformat(json.loads((ROOT / "state" / "live_trading_bot.pid").read_text(encoding="utf-8"))["started_at"])
    except (OSError, ValueError, KeyError) as exc:
        return False, f"봇 PID 파일 읽기 실패: {exc}"
    code_mtime = max((ROOT / f).stat().st_mtime for f in ("risk_manager.py", "trading_bot.py"))
    if started.timestamp() < code_mtime:
        return False, f"봇 시작 {started:%m-%d %H:%M} < 격리 코드 수정 — 봇 재시작 필요"
    return True, ""


def _kis():
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=True)
    import kis_api
    if getattr(kis_api, "IS_PAPER", True):
        raise SystemExit("[ipo-sell] KR이 모의투자 모드 — 중단")
    return kis_api, kis_api.get_access_token(market="KR")


def due_retries(rows: list[dict], today: date) -> list[dict]:
    """상장일에 못 판 OPEN 보유(상장일 < 오늘) — 장중 시장가로 재매도."""
    return [p for p in _positions(rows).values() if str(p.get("status")).upper() == "OPEN"
            and str(p.get("listing_date") or "") < today.isoformat()]


def cmd_sell(dry: bool) -> int:
    today = _today()
    ipo = _read(IPO_LEDGER)
    rows = _read(LEDGER)
    lst = listings_on(today, ipo)
    retry = due_retries(rows, today)
    if not lst and not retry:
        print(f"[ipo-sell] 오늘({today}) 상장 공모주·재매도 대상 없음"); return 0
    kis, token = _kis()                                 # 잔고 조회(읽기)는 dry에서도 한다 — 대상 식별 확인용
    stocks = (kis.get_balance(token, market="KR", force_refresh=True) or {}).get("stocks", [])
    targets = match_targets(stocks, lst, other_lane_open())
    held = {s["ticker"]: s for s in stocks}
    for p in retry:                                     # 어제 못 판 것 — 아직 보유 중일 때만
        if p["ticker"] in held and p["ticker"] not in {t["ticker"] for t in targets}:
            targets.append({**p, "qty": int(held[p["ticker"]].get("qty") or 0), "retry": True})
    print(f"[ipo-sell] 오늘 상장 {len(lst)}건({', '.join(r['corp_name'] for r in lst) or '-'}) · 매도 대상 {len(targets)}")
    if dry or not targets:
        for t in targets:
            print(f"  (dry) {t['ticker']} {t.get('name')} x{t['qty']}")
        return 0
    ok, why = _isolation_ready()
    if not ok:
        print(f"[ipo-sell] {why} — 주문 거부(fail-closed)")
        _alert(f"⚠️ 공모주 자동매도 못 함 {today} — {why}. 보유 {', '.join(t['ticker'] for t in targets)} 수동 매도 필요")
        return 2
    w = kr_wait_for_delayed_open("open_auction")
    if w:
        print(f"[ipo-sell] {w}")
    kind = "exit" if any(t.get("retry") for t in targets) and not lst else "open_auction"
    win_ok, win_msg = kr_order_window(kind)
    if not win_ok:
        print(f"[ipo-sell] {win_msg} — 매도 안 냄")
        _alert(f"⚠️ 공모주 자동매도 창 밖 {today} — {win_msg}. 보유 {', '.join(t['ticker'] for t in targets)} 수동 확인")
        return 0
    bad = []
    for t in targets:
        if not t.get("retry"):
            _append([{"kind": "position", "ticker": t["ticker"], "name": t.get("name"), "corp_name": t["corp_name"],
                      "status": "OPEN", "listing_date": today.isoformat(), "qty": t["qty"],
                      "entry_price": t.get("offer_price") or t.get("avg_price"), "source": SOURCE, "at": _now_iso()}])
        res = place_safely(kis, t["ticker"], t["qty"], 0, "sell", token, market="KR")
        st = order_state(res)
        _append([{"kind": "order", "session": today.isoformat(), "ticker": t["ticker"], "side": "sell", "qty": t["qty"],
                  "order_no": res.get("order_no", ""), "success": res.get("success"), "state": st,
                  "msg": str(res.get("msg") or "")[:160], "retry": bool(t.get("retry")), "at": _now_iso()}])
        print(f"  시장가 매도 {t['ticker']} {t.get('name')} x{t['qty']} → {st} {res.get('msg', '')}")
        if st != "ok":
            bad.append(f"{t['ticker']} {st} {str(res.get('msg') or '')[:40]}")
    _alert(f"공모주 시초가 매도 {today}: " + ", ".join(f"{t.get('name')} x{t['qty']}" for t in targets)
           + (f" — ⚠️ 실패 {'; '.join(bad)}" if bad else ""))
    return 3 if bad else 0


def close_pnl(entry: float, exit_px: float, qty: int) -> float:
    return round((exit_px - entry) * qty - (entry + exit_px) * qty * FEE_RATE - exit_px * qty * SELL_TAX)


def cmd_reconcile() -> int:
    rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no") and r["order_no"] not in done]
    if not todo:
        print("[ipo-sell] 확인할 주문 없음"); return 0
    kis, token = _kis()
    pos = _positions(rows)
    out = []
    for o in todo:
        f = kis.get_order_fill_kr(token, o["order_no"], o["ticker"], o["session"].replace("-", ""))
        filled = int((f or {}).get("filled_qty") or 0)
        if f is None or (filled == 0 and o["session"] == _today().isoformat() and datetime.now(_KST).hour < 16):
            continue
        px = float(f.get("fill_price") or 0)
        p = pos.get(o["ticker"], {})
        out.append({"kind": "fill", "order_no": o["order_no"], "ticker": o["ticker"], "side": "sell",
                    "filled_qty": filled, "fill_price": px, "at": _now_iso()})
        if filled:
            left = int(p.get("qty") or filled) - filled
            out.append({**p, "kind": "position", "status": "OPEN" if left > 0 else "CLOSED", "qty": max(left, 0),
                        "exit_price": px, "at": _now_iso()})
            ent = float(p.get("entry_price") or px)
            out.append({"kind": "close", "ticker": o["ticker"], "corp_name": p.get("corp_name"), "qty": filled,
                        "entry_price": ent, "exit_price": px, "pnl_krw": close_pnl(ent, px, filled), "at": _now_iso()})
            print(f"  {o['ticker']} {p.get('corp_name')} 매도 {filled}주 @ {px:,.0f} (공모가 {ent:,.0f})")
    _append(out)
    left = [p for p in _positions(_read(LEDGER)).values() if str(p.get("status")).upper() == "OPEN"]
    if left:
        _alert(f"⚠️ 공모주 미매도 보유 {', '.join(p['ticker'] for p in left)} — 다음 세션 장중 재매도 예정")
    return 0


def cmd_status() -> int:
    rows = _read(LEDGER)
    closes = [r for r in rows if r.get("kind") == "close"]
    print(f"[ipo-sell] 격리 준비={_isolation_ready()} · 매도 {len(closes)}건 · 실현 {sum(r['pnl_krw'] for r in closes):,.0f}원 · "
          f"OPEN {[t for t, p in _positions(rows).items() if str(p.get('status')).upper() == 'OPEN']}")
    up = [r for r in _read(IPO_LEDGER) if r.get("status") == "upcoming" and not r.get("spac")]
    print("  예정 상장: " + ", ".join(f"{r['corp_name']}({r['listing_date']})" for r in up[:8]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sell", "reconcile", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd != "status" and not a.dry:
        from net_ready import wait_dns
        wait_dns(["openapi.koreainvestment.com"], tag="ipo-sell")
    return {"sell": lambda: cmd_sell(a.dry), "reconcile": cmd_reconcile, "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
