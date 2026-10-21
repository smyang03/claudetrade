# -*- coding: utf-8 -*-
"""KR 코스닥 패닉 ETF v2 — 소액 실전 (2026-10-05 운영자 권한 B, docs/operating_model.md L3).

신호는 쉐도우 원장(tools/kr_index_etf_panic_shadow.py, data/shadow/kr_index_etf_panic.jsonl)의 15:19 판정을 그대로 쓴다:
코스닥 시가 갭 ≤ −1% 또는 15:19 등락률 ≤ −2.5% → 233740(KODEX 코스닥150레버리지) 종가 매수 → 다음 거래일 시가 매도.
근거: 11년 백필 250건 t 3.40 연도 11/11 · forward 4건 전패(표본 작음) — 소액 실전으로 체결과 성과를 같이 잰다.

**실주문 도구다.** 켜기 조건(전부 충족):
  1. KR_PANIC_MICRO_ENABLED=true 가 .env.live 와 config/v2_start_config.json(env_overrides) 두 곳 모두에 있다.
  2. 봇 격리: risk_manager.ISOLATED_STRATEGY_SOURCES 에 kr_panic_etf_micro, 실행 중인 봇이 그 코드를 싣고 떴다.
  3. 금액: 단위 KR_PANIC_MICRO_UNIT_KRW(기본 10만) × 쉐도우 원장 units(보통 1·강신호 2), 동시 1건,
     누적 실현손실 KR_PANIC_MICRO_LOSS_LIMIT_KRW(기본 5만) 도달 시 신규 매수 자동 중단.

명령 (운영 파이썬)
  buy        KST 15:20:30 — 오늘 15:19 신호가 있으면 시장가 매수(장 마감 동시호가 → 종가 체결)
  reconcile  KST 16:12 — 체결 확인·손익 기록 + **매수 체결 확인 즉시 다음 영업일 시가 시장가 매도 예약**(KIS 예약주문
             CTSC0008U, 접수 15:40~익일 07:30) + unknown 주문 되짚기 + 청산 지연 탐지(rc 3)
  sell       KST 08:42·09:10 — 예약매도 처리결과를 먼저 본다: 전송됨(odno) → 일반 매도 안 냄 / 거부·취소 → 일반 시장가
             매도 / 미처리(09:05 전) → 09:10 재확인 / 조회 실패 → 매도 안 냄(rc 3). 예약이 없으면 종전처럼 장전 시장가 매도.
  reserve_sell  수동 — 지금 보유분의 다음 영업일 시가 매도 예약(운영자 1회 실행용)
  status     원장 요약
2026-10-21 A1: 매도의 PC 의존을 "다음 날 08:42"에서 "매수 당일 16:12"로 옮겼다(10-20 PC 꺼짐 → 매도 보류 → 10-21 DNS 실패 사고).
원장: data/shadow/kr_panic_etf_micro_live.jsonl (risk_manager.kr_panic_etf_micro_manifest)
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from live_lane_common import (KST as _KST, absorb_transmitted_resv_rows, active_resv, check_resv, kr_order_window,  # noqa: E402
                              kr_resv_window, order_state, place_safely, reserve_exit_rows, resolve_unknown_order_rows,
                              resv_exit_decision, resv_rows_to_absorb, safe_cash_krw, unresolved_unknown_orders)

SHADOW = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
LEDGER = ROOT / "data" / "shadow" / "kr_panic_etf_micro_live.jsonl"
START_CFG = ROOT / "config" / "v2_start_config.json"
SOURCE = "kr_panic_etf_micro"
TICKER = "233740"          # 기본값 — 실제 매매 상품은 KR_PANIC_MICRO_TICKER(10-05: 229200 2배 금액, 세후 우위)
MARKET_BUFFER = 1.30
TAX_RATE = 0.154          # 기타 ETF(레버리지·인버스) 매매차익 배당소득세(이익에만, 보유기간과세 상한)
TAXED_TICKERS = {"233740"}   # 229200(KODEX 코스닥150)은 국내주식형 ETF라 매매차익 비과세
FEE_RATE = 0.00015        # 위탁수수료 편도 근사 — 정확값은 브로커 기간손익으로 대조
KST = timezone(timedelta(hours=9))
KEYS = ["KR_PANIC_MICRO_ENABLED", "KR_PANIC_MICRO_UNIT_KRW", "KR_PANIC_MICRO_LOSS_LIMIT_KRW", "KR_PANIC_MICRO_TICKER"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _positions(rows: list[dict] | None = None) -> dict[str, dict]:
    cur: dict[str, dict] = {}
    for r in rows if rows is not None else _read(LEDGER):
        if r.get("kind") == "position":
            cur[r["ticker"]] = r
    return cur


def realized_pnl(rows: list[dict]) -> float:
    return sum(float(r.get("pnl_krw") or 0) for r in rows if r.get("kind") == "close")


def today_signal(shadow_rows: list[dict], day: str, ticker: str = TICKER) -> dict | None:
    """쉐도우 원장의 오늘 세션 행이 신호면 {units, price, reason} — 아니면 None."""
    sess = next((r for r in shadow_rows if r.get("kind") == "session" and r.get("session_date") == day), None)
    if not sess or not sess.get("signal_1519") or sess.get("holiday") or sess.get("stale_1519"):
        return None
    trade = next((r for r in shadow_rows if r.get("kind") == "trade" and r.get("session_date") == day
                  and r.get("ticker") == ticker), None)
    px = float(((sess.get("etf_1519") or {}).get(ticker)) or (trade or {}).get("entry_1519") or 0)
    if px <= 0:
        return None
    return {"units": int((trade or {}).get("units") or 1), "price": px, "reason": sess.get("reason_1519")}


def signal_status(shadow_rows: list[dict], day: str) -> str:
    """오늘 15:19 판정 상태 — signal / none / holiday / stale / missing.
    missing·stale은 '신호 없음'이 아니라 '판정 못 함'이다(10-12·13 PC 꺼짐으로 판정 행이 없어 급락일을 '신호 없음'으로 기록)."""
    sess = next((r for r in shadow_rows if r.get("kind") == "session" and r.get("session_date") == day), None)
    if not sess:
        from buyback_shadow import kr_is_session
        from datetime import date as _date
        return "missing" if kr_is_session(_date.fromisoformat(day)) else "holiday"
    if sess.get("holiday"):
        return "holiday"
    if sess.get("stale_1519"):
        return "stale"
    return "signal" if sess.get("signal_1519") else "none"


def plan_qty(unit_krw: float, units: int, price: float) -> int:
    return max(0, int(math.floor(unit_krw * max(1, min(int(units), 2)) / price))) if price > 0 else 0


def _env_gate() -> tuple[bool, dict]:
    from dotenv import dotenv_values
    envfile = dotenv_values(str(ROOT / ".env.live"))
    try:
        overrides = (json.loads(START_CFG.read_text(encoding="utf-8")).get("env_overrides") or {})
    except (OSError, ValueError):
        overrides = {}
    a = {k: str(envfile.get(k, "")).strip() for k in KEYS}
    b = {k: str(overrides.get(k, "")).strip() for k in KEYS}
    mismatch = {k: (a[k], b[k]) for k in KEYS if a[k] != b[k]}
    enabled = a["KR_PANIC_MICRO_ENABLED"].lower() == "true" and not mismatch
    return enabled, {"unit_krw": float(a["KR_PANIC_MICRO_UNIT_KRW"] or 100_000),
                     "loss_limit": float(a["KR_PANIC_MICRO_LOSS_LIMIT_KRW"] or 50_000),
                     "ticker": a["KR_PANIC_MICRO_TICKER"] or TICKER,
                     "mismatch": mismatch, "enabled_flag": a["KR_PANIC_MICRO_ENABLED"]}


def _bot_has_isolation() -> tuple[bool, str]:
    import risk_manager
    if SOURCE not in risk_manager.ISOLATED_STRATEGY_SOURCES:
        return False, "격리 소스 없음"
    try:
        started = datetime.fromisoformat(json.loads((ROOT / "state" / "live_trading_bot.pid").read_text(encoding="utf-8"))["started_at"])
    except (OSError, ValueError, KeyError) as exc:
        return False, f"봇 PID 파일 읽기 실패: {exc}"
    code_mtime = max((ROOT / f).stat().st_mtime for f in ("risk_manager.py", "trading_bot.py"))
    if started.timestamp() < code_mtime:
        return False, f"봇 시작 {started:%m-%d %H:%M} < 격리 코드 수정 — 봇 재시작 필요"
    return True, f"봇 시작 {started:%m-%d %H:%M}"


def _kis():
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=True)
    import kis_api
    if getattr(kis_api, "IS_PAPER", True):
        raise SystemExit("[panic-micro] 모의투자 모드 — 중단")
    return kis_api, kis_api.get_access_token(market="KR")


def cmd_buy(dry: bool) -> int:
    enabled, cfg = _env_gate()
    if not dry and not enabled:
        print(f"[panic-micro] 꺼짐 — {cfg['mismatch'] or cfg['enabled_flag']}"); return 0
    ok, msg = _bot_has_isolation()
    if not dry and not ok:
        print(f"[panic-micro] {msg} — 주문 거부(fail-closed)"); return 2
    day = _today()
    rows = _read(LEDGER)
    if any(r.get("kind") == "order" and r.get("side") == "buy" and r.get("session") == day for r in rows):
        print("[panic-micro] 오늘 매수 이미 있음 — 멱등 종료"); return 0
    if any(p.get("status") == "OPEN" for p in _positions(rows).values()):
        print("[panic-micro] 보유 중 — 동시 1건"); return 0
    if realized_pnl(rows) <= -cfg["loss_limit"]:
        print(f"[panic-micro] 누적 실현손실 {realized_pnl(rows):,.0f} ≤ −{cfg['loss_limit']:,.0f} — 신규 매수 중단"); return 0
    tk = cfg["ticker"]
    shadow_rows = _read(SHADOW)
    sig = today_signal(shadow_rows, day, tk)
    if not sig:
        status = signal_status(shadow_rows, day)
        if status in ("missing", "stale"):
            why = "15:19 쉐도우 판정 행 없음(PC 꺼짐·절전 또는 claudetrade_kr_etf_panic 미실행)" if status == "missing"                 else "15:19 시세가 stale"
            print(f"[panic-micro] ⚠️ {day} 판정 못 함 — {why}. 신호 여부 모름(신호 없음 아님)")
            return 3                                   # 0이 아니면 일일 운영 리포트가 실패 작업으로 잡는다
        print(f"[panic-micro] {day} {'휴장' if status == 'holiday' else '신호 없음'}"); return 0
    qty = plan_qty(cfg["unit_krw"], sig["units"], sig["price"])
    print(f"[panic-micro] {'(dry) ' if dry else ''}시장가 매수 {tk} x{qty} (15:19 {sig['price']:,.0f}, units {sig['units']}, {sig['reason']})")
    if dry or qty < 1:
        return 0
    win_ok, win_msg = kr_order_window("close_auction")
    if not win_ok:
        print(f"[panic-micro] {win_msg} — 매수 주문 안 냄(종가 진입 계약, 늦은 실행)"); return 0
    kis, token = _kis()
    cash = safe_cash_krw(kis.get_balance(token, market="KR", force_refresh=True))   # 미결제 매수 반영(리뷰 H2)
    if qty * sig["price"] * MARKET_BUFFER > cash:
        print(f"[panic-micro] 주문가능현금 부족 {cash:,.0f}"); return 0
    res = place_safely(kis, tk, qty, 0, "buy", token, market="KR")
    state = order_state(res)
    _append([{"kind": "order", "session": day, "ticker": tk, "side": "buy", "qty": qty, "order_no": res.get("order_no", ""),
              "success": res.get("success"), "state": state, "msg": res.get("msg", ""), "units": sig["units"], "at": _now_iso()}]
            + ([{"kind": "position", "ticker": tk, "status": "OPEN", "entry_session": day, "qty": qty, "source": SOURCE,
                 "order_state": state, "at": _now_iso()}] if state != "rejected" else []))   # unknown도 OPEN(리뷰 H3)
    if state == "unknown":
        print(f"  ⚠️ 주문 접수 여부 불명 — 브로커 확인 필요: {res.get('msg')}")
    return 0


def cmd_sell(dry: bool) -> int:
    if not dry:
        cmd_reconcile()                                # 전날 체결 확인이 빠져도 1박 계약이 2박이 되지 않게(리뷰 M4)
    day = _today()
    rows = _read(LEDGER)
    p = next((q for q in _positions(rows).values() if q.get("status") == "OPEN"), None)   # 상품이 바뀌어도 보유분을 판다
    if not p or p.get("status") != "OPEN" or not p.get("filled_qty") or p.get("entry_session") >= day:
        print("[panic-micro] 매도 대상 없음"); return 0
    if any(r.get("kind") == "order" and r.get("side") == "sell" and r.get("session") == day
           and order_state(r) != "rejected" for r in rows):                 # 거부 주문은 브로커에 없으니 재시도한다
        print("[panic-micro] 오늘 매도 이미 있음"); return 0
    resv = active_resv(rows, p["ticker"], day)
    if dry:
        print(f"[panic-micro] (dry) 매도 {p['ticker']} x{p['filled_qty']} (진입 {p['entry_session']}) 예약={resv and resv.get('rsvn_seq')}")
        return 0
    kis = token = None
    if resv:                                           # A1: 예약매도가 있으면 처리결과부터 — 이중 매도 방지
        kis, token = _kis()
        try:
            status, detail = check_resv(kis, token, resv)
        except Exception as exc:  # noqa: BLE001
            print(f"[panic-micro] ⚠️ 예약매도 seq={resv.get('rsvn_seq') or '-'} 처리결과 조회 실패({type(exc).__name__}) — "
                  "예약이 살아 있을 수 있어 일반 매도 안 냄"); return 3
        act = resv_exit_decision(status)
        print(f"[panic-micro] 예약매도 seq={resv.get('rsvn_seq') or '-'} 처리결과 {status} {detail} → {act}")
        if act == "skip":
            _append([{"kind": "order", "session": day, "ticker": p["ticker"], "side": "sell", "qty": int(p["filled_qty"]),
                      "order_no": detail, "success": True, "state": "ok", "msg": "resv_transmitted", "via": "resv",
                      "rsvn_seq": resv.get("rsvn_seq"), "at": _now_iso()}])
            return 0
        if act == "wait":
            print("[panic-micro] 예약주문 전송 대기 — 09:10 재확인"); return 0
        print("[panic-micro] 예약매도 미전송 — 일반 시장가 매도로 대체")
    print(f"[panic-micro] 장전 시장가 매도 {p['ticker']} x{p['filled_qty']} (진입 {p['entry_session']})")
    win_ok, win_msg = kr_order_window("exit")
    if not win_ok:
        print(f"[panic-micro] {win_msg} — 매도 보류, 다음 정상 실행에서 매도"); return 0
    if kis is None:
        kis, token = _kis()
    res = place_safely(kis, p["ticker"], int(p["filled_qty"]), 0, "sell", token, market="KR")
    _append([{"kind": "order", "session": day, "ticker": p["ticker"], "side": "sell", "qty": int(p["filled_qty"]),
              "order_no": res.get("order_no", ""), "success": res.get("success"), "state": order_state(res),
              "msg": res.get("msg", ""), "at": _now_iso()}])
    return 0


def close_pnl(entry: float, exit_px: float, qty: int, ticker: str = TICKER) -> float:
    gross = (exit_px - entry) * qty
    fee = (entry + exit_px) * qty * FEE_RATE
    tax = max(0.0, gross) * TAX_RATE if ticker in TAXED_TICKERS else 0.0
    return round(gross - fee - tax)


def _open_position(rows: list[dict]) -> dict | None:
    return next((q for q in _positions(rows).values() if q.get("status") == "OPEN" and q.get("filled_qty")), None)


def cmd_reserve_sell(dry: bool = False) -> int:
    """보유분의 다음 영업일 시가 시장가 매도 예약(A1). reconcile이 매일 16:12에 같은 일을 하고, 이 명령은 수동 1회용."""
    rows = _read(LEDGER)
    p = _open_position(rows)
    if not p:
        print("[panic-micro] 예약할 보유 없음"); return 0
    if dry:
        _, why = reserve_exit_rows(None, None, rows, p["ticker"], int(p["filled_qty"]), dry=True)
        print(f"[panic-micro] {why}"); return 0
    ok, why = kr_resv_window()
    if not ok:
        print(f"[panic-micro] {why} — 예약 안 냄"); return 2
    kis, token = _kis()
    new, why = reserve_exit_rows(kis, token, rows, p["ticker"], int(p["filled_qty"]))
    print(f"[panic-micro] {why}")
    if new:
        _append(new)
        return 0 if new[0]["state"] == "ok" else 2
    return 0


def _reserve_and_overdue(rows: list[dict], kis=None, token=None) -> int:
    """reconcile 후반부 — ① 보유 확인분 예약매도 ② 청산 지연 탐지(15:30 이후에도 전날 매수분이 OPEN이면 rc 3)."""
    rc = 0
    p = _open_position(rows)
    if not p:
        return 0
    now = datetime.now(_KST)
    if kr_resv_window(now)[0]:
        if kis is None:
            kis, token = _kis()
        new, why = reserve_exit_rows(kis, token, rows, p["ticker"], int(p["filled_qty"]), now=now)
        print(f"[panic-micro] 예약매도: {why}")
        if new:
            _append(new)
            if new[0]["state"] != "ok":
                rc = 3
    if p.get("entry_session", "") < _today() and now.time() >= datetime.strptime("15:30", "%H:%M").time():
        print(f"[panic-micro] ⚠️ 청산 지연 — {p['ticker']} x{p['filled_qty']} 진입 {p['entry_session']} 이 {_today()} 장 마감까지 OPEN"
              "(계약은 익일 시가 매도). 브로커 보유·예약주문 확인 필요")
        rc = 3
    return rc


def cmd_reconcile() -> int:
    rows = _read(LEDGER)
    kis = token = None
    if unresolved_unknown_orders(rows):                  # A1: 접수 응답을 못 받은 주문을 브로커 당일 체결조회로 되짚는다
        kis, token = _kis()
        rec, notes = resolve_unknown_order_rows(kis, token, rows)
        for n in notes:
            print(f"[panic-micro] {n}")
        if rec:
            _append(rec)
            rows = _read(LEDGER)
    if resv_rows_to_absorb(rows, _today()):              # A1: 매도 작업이 안 돈 날의 예약매도 전송분을 흡수(아니면 매일 재예약)
        if kis is None:
            kis, token = _kis()
        rec, notes = absorb_transmitted_resv_rows(kis, token, rows, _today())
        for n in notes:
            print(f"[panic-micro] {n}")
        if rec:
            _append(rec)
            rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no") and r["order_no"] not in done]
    if not todo:
        print("[panic-micro] 확인할 주문 없음")
        return _reserve_and_overdue(rows, kis, token)
    if kis is None:
        kis, token = _kis()
    pos = _positions(rows)
    out = []
    for o in todo:
        f = kis.get_order_fill_kr(token, o["order_no"], o["ticker"], o["session"].replace("-", ""))
        filled = int((f or {}).get("filled_qty") or 0)
        if f is None or (filled == 0 and o["session"] == _today() and datetime.now(KST).hour < 16):
            continue
        px = float(f.get("fill_price") or 0)
        out.append({"kind": "fill", "order_no": o["order_no"], "ticker": o["ticker"], "side": o["side"], "filled_qty": filled,
                    "fill_price": px, "at": _now_iso()})
        p = pos.get(o["ticker"], {})
        if o["side"] == "buy":
            out.append({**p, "kind": "position", "status": "OPEN" if filled else "CLOSED", "filled_qty": filled,
                        "entry_price": px, "at": _now_iso()})
        elif filled:
            pnl = close_pnl(float(p.get("entry_price") or px), px, filled, o["ticker"])
            remaining = int(p.get("filled_qty") or filled) - filled
            out.append({**p, "kind": "position", "status": "OPEN" if remaining > 0 else "CLOSED",   # 부분 체결(리뷰 M2)
                        "filled_qty": max(remaining, 0), "exit_price": px, "at": _now_iso()})
            out.append({"kind": "close", "ticker": o["ticker"], "entry_session": p.get("entry_session"), "exit_session": o["session"],
                        "entry_price": p.get("entry_price"), "exit_price": px, "qty": filled, "pnl_krw": pnl, "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 {filled}주 @ {px:,.0f}")
    _append(out)
    return _reserve_and_overdue(_read(LEDGER), kis, token)


def cmd_status() -> int:
    enabled, cfg = _env_gate()
    rows = _read(LEDGER)
    closes = [r for r in rows if r.get("kind") == "close"]
    print(f"[panic-micro] enabled={enabled} 두소스불일치={cfg['mismatch']} 봇격리={_bot_has_isolation()} 단위 {cfg['unit_krw']:,.0f}"
          f" · 손실한도 {cfg['loss_limit']:,.0f}")
    print(f"  보유 {sum(1 for p in _positions(rows).values() if p.get('status') == 'OPEN')} · 종료 {len(closes)} · 누적 실현 {realized_pnl(rows):,.0f}원")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["buy", "sell", "reconcile", "reserve_sell", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    return {"buy": lambda: cmd_buy(a.dry), "sell": lambda: cmd_sell(a.dry), "reconcile": cmd_reconcile,
            "reserve_sell": lambda: cmd_reserve_sell(a.dry), "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
