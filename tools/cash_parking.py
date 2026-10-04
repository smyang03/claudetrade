# -*- coding: utf-8 -*-
"""대기 현금 파킹 — 레인 예산 밖 현금을 금리형 ETF에 둔다 (2026-10-05, docs/operating_model.md §7 결정 2).

운영자 위임("내가 정해야 할 건 니가 판단해")에 따른 결정: US 현금 약 $900 유지·나머지 SGOV, KR 현금 약 40만 유지·나머지 423160.
**실주문 도구다.** 켜기 조건(전부 충족해야 주문):
  1. CASH_PARKING_ENABLED=true 가 .env.live 와 config/v2_start_config.json(env_overrides) 두 곳 모두에 있다.
  2. 봇 격리 계약: risk_manager.ISOLATED_STRATEGY_SOURCES 에 cash_parking, 그리고 실행 중인 봇이 그 코드를 싣고 떴다.
  3. 시장 시간: KR 09:05~15:15 KST, US 정규장 중에만 주문(지정가, 현재가 근처).

명령 (운영 파이썬 — kis_api 필요)
  plan              계좌 현금·목표·주문 수량 계산(주문 없음)
  park  --market M  초과 현금만큼 파킹 ETF 지정가 매수 (M = KR | US)
  unpark --market M --krw N   레인 자금이 모자랄 때 N원어치 매도
  reconcile         체결 확인·포지션 갱신
  status            원장 요약
원장: data/shadow/cash_parking_live.jsonl — risk_manager.cash_parking_manifest()가 봇 귀속 복원에 쓴다.
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

LEDGER = ROOT / "data" / "shadow" / "cash_parking_live.jsonl"
START_CFG = ROOT / "config" / "v2_start_config.json"
SOURCE = "cash_parking"
KST = timezone(timedelta(hours=9))
KEYS = ["CASH_PARKING_ENABLED", "CASH_PARKING_US_TICKER", "CASH_PARKING_US_KEEP_USD",
        "CASH_PARKING_KR_TICKER", "CASH_PARKING_KR_KEEP_KRW"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read() -> list[dict]:
    if not LEDGER.exists():
        return []
    return [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _positions(rows: list[dict] | None = None) -> dict[str, dict]:
    cur: dict[str, dict] = {}
    for r in rows if rows is not None else _read():
        if r.get("kind") == "position":
            cur[r["ticker"]] = r
    return cur


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
    enabled = a["CASH_PARKING_ENABLED"].lower() == "true" and not mismatch
    cfg = {"us_ticker": a["CASH_PARKING_US_TICKER"] or "SGOV",
           "us_keep_usd": float(a["CASH_PARKING_US_KEEP_USD"] or 900),
           "kr_ticker": a["CASH_PARKING_KR_TICKER"] or "423160",
           "kr_keep_krw": float(a["CASH_PARKING_KR_KEEP_KRW"] or 400_000),
           "mismatch": mismatch, "enabled_flag": a["CASH_PARKING_ENABLED"]}
    return enabled, cfg


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


def market_open(market: str, now: datetime | None = None) -> bool:
    if market == "KR":
        t = (now or datetime.now(KST)).astimezone(KST)
        return t.weekday() < 5 and (9, 5) <= (t.hour, t.minute) <= (15, 15)
    import zoneinfo
    et = (now or datetime.now(timezone.utc)).astimezone(zoneinfo.ZoneInfo("America/New_York"))
    return et.weekday() < 5 and (9, 35) <= (et.hour, et.minute) <= (15, 50)


def plan_qty(cash: float, keep: float, price: float) -> int:
    """초과 현금으로 살 수 있는 주수(지정가 = price). 순수 함수."""
    if price <= 0:
        return 0
    return max(0, int(math.floor((cash - keep) / price)))


def kr_limit_price(last: float) -> int:
    """KR ETF 호가단위 5원 — 현재가 위 한 틱으로 지정가(즉시 체결 의도)."""
    return int(math.ceil(last / 5.0) * 5 + 5)


def _kis(market: str):
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=True)
    import kis_api
    paper = getattr(kis_api, "IS_PAPER_US" if market == "US" else "IS_PAPER", True)
    if paper:
        raise SystemExit("[parking] 모의투자 모드 — 중단")
    return kis_api, kis_api.get_access_token(market=market)


def _last_price(market: str, ticker: str) -> float:
    if market == "KR":
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                                                           headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as r:
            return float(str(json.loads(r.read().decode())["closePrice"]).replace(",", ""))
    import yfinance as yf
    return float(yf.Ticker(ticker).fast_info["last_price"])


def cmd_plan() -> int:
    enabled, cfg = _env_gate()
    print(f"[parking] enabled={enabled} flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']} 봇격리={_bot_has_isolation()}")
    for m in ("US", "KR"):
        kis, token = _kis(m)
        bal = kis.get_balance(token, market=m, force_refresh=True)
        cash = float(bal.get("orderable_cash") or 0)
        tk = cfg["us_ticker"] if m == "US" else cfg["kr_ticker"]
        keep = cfg["us_keep_usd"] if m == "US" else cfg["kr_keep_krw"]
        px = _last_price(m, tk)
        q = plan_qty(cash, keep, px if m == "US" else kr_limit_price(px))
        print(f"  {m}: 주문가능 {cash:,.2f} · 유지 {keep:,.0f} · {tk} {px:,.2f} → {q}주")
    return 0


def held_from_fills(rows: list[dict], ticker: str) -> int:
    """보유 수량은 체결 합계로만 계산한다(position 행 덮어쓰기로 수량을 잃던 문제 — 리뷰 H1)."""
    q = 0
    for r in rows:
        if r.get("kind") == "fill" and r.get("ticker") == ticker:
            q += int(r.get("filled_qty") or 0) * (1 if r.get("side") == "buy" else -1)
    return max(q, 0)


def pending_orders(rows: list[dict], ticker: str) -> list[dict]:
    final = {r["order_no"] for r in rows if r.get("kind") == "fill" and r.get("final")}
    return [r for r in rows if r.get("kind") == "order" and r.get("ticker") == ticker and r.get("state") != "rejected"
            and r.get("success") is not False and r.get("order_no") not in final]


def cmd_park(market: str, dry: bool) -> int:
    enabled, cfg = _env_gate()
    if not dry and not enabled:
        print(f"[parking] 꺼짐 — {cfg['mismatch'] or cfg['enabled_flag']}"); return 0
    ok, msg = _bot_has_isolation()
    if not dry and not ok:
        print(f"[parking] {msg} — 주문 거부(fail-closed)"); return 2
    if not dry and not market_open(market):
        print(f"[parking] {market} 장 시간 아님 — 주문 안 함"); return 0
    tk = cfg["us_ticker"] if market == "US" else cfg["kr_ticker"]
    rows = _read()
    session = datetime.now(KST).date().isoformat()
    if pending_orders(rows, tk):
        print(f"[parking] {tk} 체결 확인 전 주문 있음 — 새 주문 안 함(reconcile 먼저)"); return 0
    if any(r.get("kind") == "order" and r.get("ticker") == tk and r.get("session") == session for r in rows):
        print(f"[parking] {tk} 오늘 주문 이미 있음 — 멱등 종료"); return 0
    kis, token = _kis(market)
    bal = kis.get_balance(token, market=market, force_refresh=True)
    cash = float(bal.get("orderable_cash") or 0) if market == "US" else safe_cash_krw(bal)   # KR 미결제 반영(리뷰 H2)
    keep = cfg["us_keep_usd"] if market == "US" else cfg["kr_keep_krw"]
    last = _last_price(market, tk)
    limit = round(last * 1.001, 2) if market == "US" else kr_limit_price(last)
    qty = plan_qty(cash, keep, limit)
    print(f"[parking] {'(dry) ' if dry else ''}{market} {tk} 지정가 {limit} x{qty} (주문가능 {cash:,.2f}, 유지 {keep:,.0f})")
    if dry or qty < 1:
        return 0
    res = place_safely(kis, tk, qty, limit, "buy", token, market=market)
    state = order_state(res)
    _append([{"kind": "order", "session": session, "market": market, "ticker": tk, "side": "buy", "qty": qty, "limit": limit,
              "order_no": res.get("order_no", ""), "success": res.get("success"), "state": state, "msg": res.get("msg", ""),
              "at": _now_iso()}]
            + ([{"kind": "position", "market": market, "ticker": tk, "status": "OPEN", "filled_qty": held_from_fills(rows, tk),
                 "source": SOURCE, "order_state": state, "at": _now_iso()}] if state != "rejected" else []))
    if state == "unknown":
        print(f"  ⚠️ 주문 접수 여부 불명 — 브로커 확인 필요: {res.get('msg')}")
    return 0


def _usdkrw() -> float:
    import yfinance as yf
    return float(yf.Ticker("KRW=X").fast_info["last_price"])


def cmd_unpark(market: str, krw: float, dry: bool) -> int:
    if krw <= 0:
        print("[parking] --krw 금액을 지정해야 한다"); return 2       # 기본 0으로 1주를 팔던 문제(리뷰 M7)
    enabled, cfg = _env_gate()
    if not dry and not market_open(market):
        print(f"[parking] {market} 장 시간 아님 — 주문 안 함"); return 0
    tk = cfg["us_ticker"] if market == "US" else cfg["kr_ticker"]
    rows = _read()
    if pending_orders(rows, tk):
        print(f"[parking] {tk} 체결 확인 전 주문 있음 — reconcile 먼저"); return 0
    held = held_from_fills(rows, tk)
    if held < 1:
        print(f"[parking] {tk} 체결 보유 없음"); return 0
    last = _last_price(market, tk)
    unit = last if market == "KR" else last * _usdkrw()
    qty = min(held, max(1, int(math.ceil(krw / unit))))
    limit = round(last * 0.999, 2) if market == "US" else max(5, int(math.floor(last / 5.0) * 5 - 5))
    print(f"[parking] {'(dry) ' if dry else ''}{market} {tk} 지정가 매도 {limit} x{qty} (보유 {held})")
    if dry:
        return 0
    kis, token = _kis(market)
    res = place_safely(kis, tk, qty, limit, "sell", token, market=market)
    _append([{"kind": "order", "session": datetime.now(KST).date().isoformat(), "market": market, "ticker": tk,
              "side": "sell", "qty": qty, "limit": limit, "order_no": res.get("order_no", ""),
              "success": res.get("success"), "state": order_state(res), "msg": res.get("msg", ""), "at": _now_iso()}])
    return 0


def cmd_reconcile() -> int:
    rows = _read()
    final = {r["order_no"] for r in rows if r.get("kind") == "fill" and r.get("final")}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no")
            and r["order_no"] not in final]
    if not todo:
        print("[parking] 확인할 주문 없음"); return 0
    out: list[dict] = []
    seen = {}
    for r in rows:
        if r.get("kind") == "fill":
            seen[r["order_no"]] = seen.get(r["order_no"], 0) + int(r.get("filled_qty") or 0)
    today = datetime.now(KST).date().isoformat()
    for o in todo:
        kis, token = _kis(o["market"])
        f = (kis.get_order_fill_us(token, o["order_no"], o["ticker"], o.get("at", "")) if o["market"] == "US"
             else kis.get_order_fill_kr(token, o["order_no"], o["ticker"], o["session"].replace("-", "")))
        if f is None:
            continue
        filled = int(f.get("filled_qty") or 0)
        delta = filled - seen.get(o["order_no"], 0)           # 같은 주문의 누적 체결 증분만 더한다
        is_final = filled >= int(o.get("qty") or 0) or o["session"] < today
        if delta > 0 or is_final:
            out.append({"kind": "fill", "order_no": o["order_no"], "market": o["market"], "ticker": o["ticker"],
                        "side": o["side"], "filled_qty": max(delta, 0), "fill_price": float(f.get("fill_price") or 0),
                        "final": is_final, "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 누적 {filled}/{o.get('qty')}주 @ {f.get('fill_price')}"
              f"{' (확정)' if is_final else ' (대기)'}")
    after = rows + out                                          # 루프 결과까지 반영해 보유 재계산(리뷰 H1)
    for tk in sorted({o["ticker"] for o in todo}):
        held = held_from_fills(after, tk)
        mk = next(o["market"] for o in todo if o["ticker"] == tk)
        out.append({"kind": "position", "market": mk, "ticker": tk,
                    "status": "OPEN" if (held > 0 or pending_orders(after, tk)) else "CLOSED",
                    "filled_qty": held, "source": SOURCE, "at": _now_iso()})
    _append(out)
    return 0


def cmd_status() -> int:
    enabled, cfg = _env_gate()
    print(f"[parking] enabled={enabled} flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']} 봇격리={_bot_has_isolation()}")
    for tk, p in _positions().items():
        print(f"  {p.get('market')} {tk} {p.get('status')} 보유 {p.get('filled_qty', p.get('qty_ordered'))}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["plan", "park", "unpark", "reconcile", "status"])
    ap.add_argument("--market", choices=["KR", "US"])
    ap.add_argument("--krw", type=float, default=0)
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd in ("park", "unpark") and not a.market:
        ap.error("--market 필요")
    return {"plan": cmd_plan, "park": lambda: cmd_park(a.market, a.dry), "unpark": lambda: cmd_unpark(a.market, a.krw, a.dry),
            "reconcile": cmd_reconcile, "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
