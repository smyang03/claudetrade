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
  sell       KST 08:42 — 전 거래일 매수분을 장전 시장가 매도(시가 체결)
  reconcile  KST 16:12 — 체결 확인·손익 기록
  status     원장 요약
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
from live_lane_common import order_state, place_safely, safe_cash_krw  # noqa: E402

SHADOW = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
LEDGER = ROOT / "data" / "shadow" / "kr_panic_etf_micro_live.jsonl"
START_CFG = ROOT / "config" / "v2_start_config.json"
SOURCE = "kr_panic_etf_micro"
TICKER = "233740"
MARKET_BUFFER = 1.30
TAX_RATE = 0.154          # 파생형 ETF 매매차익 배당소득세(이익에만)
FEE_RATE = 0.00015        # 위탁수수료 편도 근사 — 정확값은 브로커 기간손익으로 대조
KST = timezone(timedelta(hours=9))
KEYS = ["KR_PANIC_MICRO_ENABLED", "KR_PANIC_MICRO_UNIT_KRW", "KR_PANIC_MICRO_LOSS_LIMIT_KRW"]


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


def today_signal(shadow_rows: list[dict], day: str) -> dict | None:
    """쉐도우 원장의 오늘 세션 행이 신호면 {units, price, reason} — 아니면 None."""
    sess = next((r for r in shadow_rows if r.get("kind") == "session" and r.get("session_date") == day), None)
    if not sess or not sess.get("signal_1519") or sess.get("holiday") or sess.get("stale_1519"):
        return None
    trade = next((r for r in shadow_rows if r.get("kind") == "trade" and r.get("session_date") == day
                  and r.get("ticker") == TICKER), None)
    px = float(((sess.get("etf_1519") or {}).get(TICKER)) or (trade or {}).get("entry_1519") or 0)
    if px <= 0:
        return None
    return {"units": int((trade or {}).get("units") or 1), "price": px, "reason": sess.get("reason_1519")}


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
    sig = today_signal(_read(SHADOW), day)
    if not sig:
        print(f"[panic-micro] {day} 신호 없음"); return 0
    qty = plan_qty(cfg["unit_krw"], sig["units"], sig["price"])
    print(f"[panic-micro] {'(dry) ' if dry else ''}시장가 매수 {TICKER} x{qty} (15:19 {sig['price']:,.0f}, units {sig['units']}, {sig['reason']})")
    if dry or qty < 1:
        return 0
    kis, token = _kis()
    cash = safe_cash_krw(kis.get_balance(token, market="KR", force_refresh=True))   # 미결제 매수 반영(리뷰 H2)
    if qty * sig["price"] * MARKET_BUFFER > cash:
        print(f"[panic-micro] 주문가능현금 부족 {cash:,.0f}"); return 0
    res = place_safely(kis, TICKER, qty, 0, "buy", token, market="KR")
    state = order_state(res)
    _append([{"kind": "order", "session": day, "ticker": TICKER, "side": "buy", "qty": qty, "order_no": res.get("order_no", ""),
              "success": res.get("success"), "state": state, "msg": res.get("msg", ""), "units": sig["units"], "at": _now_iso()}]
            + ([{"kind": "position", "ticker": TICKER, "status": "OPEN", "entry_session": day, "qty": qty, "source": SOURCE,
                 "order_state": state, "at": _now_iso()}] if state != "rejected" else []))   # unknown도 OPEN(리뷰 H3)
    if state == "unknown":
        print(f"  ⚠️ 주문 접수 여부 불명 — 브로커 확인 필요: {res.get('msg')}")
    return 0


def cmd_sell(dry: bool) -> int:
    if not dry:
        cmd_reconcile()                                # 전날 체결 확인이 빠져도 1박 계약이 2박이 되지 않게(리뷰 M4)
    day = _today()
    rows = _read(LEDGER)
    p = _positions(rows).get(TICKER)
    if not p or p.get("status") != "OPEN" or not p.get("filled_qty") or p.get("entry_session") >= day:
        print("[panic-micro] 매도 대상 없음"); return 0
    if any(r.get("kind") == "order" and r.get("side") == "sell" and r.get("session") == day for r in rows):
        print("[panic-micro] 오늘 매도 이미 있음"); return 0
    print(f"[panic-micro] {'(dry) ' if dry else ''}장전 시장가 매도 {TICKER} x{p['filled_qty']} (진입 {p['entry_session']})")
    if dry:
        return 0
    kis, token = _kis()
    res = place_safely(kis, TICKER, int(p["filled_qty"]), 0, "sell", token, market="KR")
    _append([{"kind": "order", "session": day, "ticker": TICKER, "side": "sell", "qty": int(p["filled_qty"]),
              "order_no": res.get("order_no", ""), "success": res.get("success"), "state": order_state(res),
              "msg": res.get("msg", ""), "at": _now_iso()}])
    return 0


def close_pnl(entry: float, exit_px: float, qty: int) -> float:
    gross = (exit_px - entry) * qty
    fee = (entry + exit_px) * qty * FEE_RATE
    tax = max(0.0, gross) * TAX_RATE
    return round(gross - fee - tax)


def cmd_reconcile() -> int:
    rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no") and r["order_no"] not in done]
    if not todo:
        print("[panic-micro] 확인할 주문 없음"); return 0
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
            pnl = close_pnl(float(p.get("entry_price") or px), px, filled)
            remaining = int(p.get("filled_qty") or filled) - filled
            out.append({**p, "kind": "position", "status": "OPEN" if remaining > 0 else "CLOSED",   # 부분 체결(리뷰 M2)
                        "filled_qty": max(remaining, 0), "exit_price": px, "at": _now_iso()})
            out.append({"kind": "close", "ticker": o["ticker"], "entry_session": p.get("entry_session"), "exit_session": o["session"],
                        "entry_price": p.get("entry_price"), "exit_price": px, "qty": filled, "pnl_krw": pnl, "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 {filled}주 @ {px:,.0f}")
    _append(out)
    return 0


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
    ap.add_argument("cmd", choices=["buy", "sell", "reconcile", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    return {"buy": lambda: cmd_buy(a.dry), "sell": lambda: cmd_sell(a.dry), "reconcile": cmd_reconcile,
            "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
