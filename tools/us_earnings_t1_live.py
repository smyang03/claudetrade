# -*- coding: utf-8 -*-
"""T1 US 실적 서프라이즈 — 소액 실운영 (2단계, 2026-09-29 운영자 지시 "백만원만 실운영").

설계서: docs/reports/T1_stage2_design_20260928.md · 신호: tools/us_earnings_t1_shadow.py 쉐도우 원장.
**실주문 도구다.** 켜기 조건(전부 충족해야 주문):
  1. T1_LIVE_ENABLED=true 가 .env.live 와 config/v2_start_config.json(env_overrides) **두 곳 모두**에 있다.
  2. 봇 격리 계약 존재: risk_manager.ISOLATED_STRATEGY_SOURCES 에 us_earnings_t1 (봇이 T1 종목을 팔지 않음).
  3. 금액 게이트: 총 한도 T1_LIVE_BUDGET_KRW(기본 100만) · 1종목 T1_LIVE_SLOT_KRW(기본 14만) ·
     동시 T1_LIVE_MAX_SLOTS(기본 7) · 하루 신규 T1_LIVE_MAX_NEW_PER_DAY(기본 1) · 거래대금 ≥ T1_LIVE_MIN_DVOL_USD(기본 $5M).
  4. 이미 계좌에 있는 종목(코어 SCHG·다른 전략 보유)은 사지 않는다. 달러 주문가능현금이 모자라면 건너뛴다.

명령 (운영 파이썬으로 실행 — kis_api 필요)
  buy        개장 전(KST 22:20, 쉐도우 score 22:15 뒤). 오늘 쉐도우 선택(I1·유동) 중 점수 최고 1종목을
             LOO(ORD_DVSN 32) 매수. 한도가 = 전일 종가 × 1.25(백테스트의 '시가 무조건 체결'에 최대한 맞춤).
  sell       개장 전(KST 22:20). 진입 후 20번째 세션인 보유를 MOO(ORD_DVSN 31) 매도.
  reconcile  장 마감 후(KST 07:40). 오늘 낸 주문의 체결을 확인하고, 체결가 − 공식 시가(yfinance) 차이 bp 기록.
  status     원장 요약.
  --dry      모든 게이트를 통과시키되 주문은 내지 않고 출력만.
원장: data/shadow/us_earnings_t1_live.jsonl — risk_manager.us_earnings_t1_manifest()가 이 파일로 봇 귀속을 복원한다.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from live_lane_common import order_state, place_safely  # noqa: E402

SHADOW = ROOT / "data" / "shadow" / "us_earnings_t1_shadow.jsonl"
LEDGER = ROOT / "data" / "shadow" / "us_earnings_t1_live.jsonl"
START_CFG = ROOT / "config" / "v2_start_config.json"
HOLD = 20
LOO_CAP = 1.25
SOURCE = "us_earnings_t1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_et():
    import zoneinfo
    return datetime.now(zoneinfo.ZoneInfo("America/New_York")).date()


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _append(rows: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def _positions() -> dict[str, dict]:
    """티커별 마지막 position 행."""
    cur: dict[str, dict] = {}
    for r in _read(LEDGER):
        if r.get("kind") == "position":
            cur[r["ticker"]] = r
    return cur


# ------------------------------------------------------------------ gates
def _env_gate() -> tuple[bool, dict]:
    from dotenv import dotenv_values
    envfile = dotenv_values(str(ROOT / ".env.live"))
    try:
        overrides = (json.loads(START_CFG.read_text(encoding="utf-8")).get("env_overrides") or {})
    except (OSError, ValueError):
        overrides = {}
    keys = ["T1_LIVE_ENABLED", "T1_LIVE_BUDGET_KRW", "T1_LIVE_SLOT_KRW", "T1_LIVE_MAX_SLOTS",
            "T1_LIVE_MAX_NEW_PER_DAY", "T1_LIVE_MIN_DVOL_USD"]
    a = {k: str(envfile.get(k, "")).strip() for k in keys}
    b = {k: str(overrides.get(k, "")).strip() for k in keys}
    mismatch = {k: (a[k], b[k]) for k in keys if a[k] != b[k]}
    enabled = a["T1_LIVE_ENABLED"].lower() == "true" and not mismatch
    cfg = {"budget": float(a["T1_LIVE_BUDGET_KRW"] or 1_000_000), "slot": float(a["T1_LIVE_SLOT_KRW"] or 140_000),
           "max_slots": int(a["T1_LIVE_MAX_SLOTS"] or 7), "max_new": int(a["T1_LIVE_MAX_NEW_PER_DAY"] or 1),
           "min_dvol": float(a["T1_LIVE_MIN_DVOL_USD"] or 5e6), "mismatch": mismatch, "enabled_flag": a["T1_LIVE_ENABLED"]}
    return enabled, cfg


def _isolation_gate() -> bool:
    import risk_manager
    return SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES


def _kis():
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=True)
    import kis_api
    if getattr(kis_api, "IS_PAPER_US", True):
        raise SystemExit("[T1 live] US가 모의투자 모드 — LOO/MOO 불가, 중단")
    return kis_api, kis_api.get_access_token(market="US")


def _usdkrw() -> float:
    import yfinance as yf
    fx = yf.download("KRW=X", period="10d", progress=False, auto_adjust=True)
    return float(fx["Close"].dropna().values.ravel()[-1])


def _sessions_between(d0: str, d1) -> int:
    """d0(진입일) 다음 날부터 d1 전날까지 IWM 거래일 수 + 1(= d1이 진입 후 몇 번째 세션인지, d1이 거래일이라는 가정)."""
    import yfinance as yf
    iwm = yf.download("IWM", start=d0, progress=False, auto_adjust=True)
    days = [x.date() for x in iwm.index]
    return sum(1 for x in days if str(x) > d0 and x < d1) + 1


# ------------------------------------------------------------------ buy
def cmd_buy(dry: bool) -> int:
    enabled, cfg = _env_gate()
    if not dry and not enabled:
        print(f"[T1 live] 꺼짐 — flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']}"); return 0
    if not _isolation_gate():
        print("[T1 live] 봇 격리 계약(us_earnings_t1) 없음 — 주문 거부(fail-closed)"); return 2
    today = str(_today_et())
    if any(r.get("kind") == "order" and r.get("side") == "buy" and r.get("session") == today for r in _read(LEDGER)):
        print("[T1 live] 오늘 매수 주문 이미 있음 — 멱등 종료"); return 0
    sigs = [r for r in _read(SHADOW) if r.get("kind") == "signal" and r.get("session") == today
            and r.get("selected_I1") and float(r.get("dvol20") or 0) >= cfg["min_dvol"]]
    if not sigs:
        print(f"[T1 live] 오늘({today}) 쉐도우 선택 종목 없음(유동 ≥ ${cfg['min_dvol']/1e6:.0f}M)"); return 0
    pos = _positions()
    open_pos = {t: p for t, p in pos.items() if p.get("status") == "OPEN"}
    used = sum(float(p.get("cost_krw") or cfg["slot"]) for p in open_pos.values())
    if len(open_pos) >= cfg["max_slots"] or used + cfg["slot"] > cfg["budget"]:
        print(f"[T1 live] 슬롯/한도 가득 — 보유 {len(open_pos)}/{cfg['max_slots']}, 사용 {used:,.0f}/{cfg['budget']:,.0f}")
        if not dry:                            # 용량 진단(T1_live_capacity_20260929) forward 검증용 기록 — 주문과 무관
            top = max(sigs, key=lambda r: float(r["score"]))
            _append([{"kind": "skip", "session": today, "reason": "slots_full", "ticker": str(top["ticker"]).upper(),
                      "score": top["score"], "close_a": top.get("close_a"), "n_candidates": len(sigs),
                      "open_slots": len(open_pos), "at": _now_iso()}])
        return 0
    kis, token = _kis()
    bal = kis.get_balance(token, market="US", force_refresh=True)
    held = {str(s["ticker"]).upper() for s in bal.get("stocks", [])}
    fx = _usdkrw()
    plans = []
    for s in sorted(sigs, key=lambda r: -float(r["score"])):
        tk = str(s["ticker"]).upper()
        if tk in held or tk in open_pos:
            print(f"  건너뜀 {tk}: 이미 보유"); continue
        limit = round(float(s["close_a"]) * LOO_CAP, 2)
        qty = int(math.floor(cfg["slot"] / (float(s["close_a"]) * fx)))
        if qty < 1:
            print(f"  건너뜀 {tk}: 1주 가격이 슬롯 초과"); continue
        if qty * limit > float(bal.get("orderable_cash") or 0):
            print(f"  건너뜀 {tk}: 달러 주문가능현금 부족({bal.get('orderable_cash')})"); continue
        plans.append({"ticker": tk, "qty": qty, "limit": limit, "score": s["score"], "close_a": s["close_a"],
                      "cost_krw": round(qty * float(s["close_a"]) * fx)})
        if len(plans) >= cfg["max_new"]:
            break
    for p in plans:
        print(f"[T1 live] {'(dry) ' if dry else ''}LOO 매수 {p['ticker']} x{p['qty']} 한도 ${p['limit']} "
              f"(전일종가 ${p['close_a']:.2f}, 약 {p['cost_krw']:,}원, score {p['score']:.4f})")
        if dry:
            continue
        res = place_safely(kis, p["ticker"], p["qty"], p["limit"], "buy", token, market="US", ord_dvsn="32")
        state = order_state(res)
        _append([{"kind": "order", "session": today, "ticker": p["ticker"], "side": "buy", "ord_dvsn": "32",
                  "qty": p["qty"], "limit": p["limit"], "order_no": res.get("order_no", ""), "success": res.get("success"),
                  "state": state, "msg": res.get("msg", ""), "at": _now_iso()}]
                + ([{"kind": "position", "ticker": p["ticker"], "status": "OPEN", "entry_session": today,
                     "qty": p["qty"], "cost_krw": p["cost_krw"], "source": SOURCE, "order_state": state, "at": _now_iso()}]
                   if state != "rejected" else []))          # unknown도 OPEN(리뷰 H3)
        if state == "unknown":
            print(f"  ⚠️ {p['ticker']} 주문 접수 여부 불명 — 브로커 확인 필요: {res.get('msg')}")
    return 0


# ------------------------------------------------------------------ sell
def cmd_sell(dry: bool) -> int:
    # 만기 청산은 ENABLED와 무관 — 플래그는 신규 매수만 막는다(봇은 격리로 팔지 않으므로, 리뷰 M3).
    today = _today_et()
    due = []
    for tk, p in _positions().items():
        if p.get("status") != "OPEN" or not p.get("filled_qty"):
            continue
        n = _sessions_between(p["entry_session"], today)
        if n >= HOLD:
            due.append((tk, p, n))
    if not due:
        print("[T1 live] 오늘 만기 없음"); return 0
    kis, token = (None, None) if dry else _kis()
    for tk, p, n in due:
        if any(r.get("kind") == "order" and r.get("side") == "sell" and r.get("ticker") == tk
               and r.get("session") == str(today) for r in _read(LEDGER)):
            continue
        print(f"[T1 live] {'(dry) ' if dry else ''}MOO 매도 {tk} x{p['filled_qty']} (진입 {p['entry_session']}, {n}번째 세션)")
        if dry:
            continue
        res = place_safely(kis, tk, int(p["filled_qty"]), 0, "sell", token, market="US", ord_dvsn="31")
        _append([{"kind": "order", "session": str(today), "ticker": tk, "side": "sell", "ord_dvsn": "31",
                  "qty": int(p["filled_qty"]), "order_no": res.get("order_no", ""), "success": res.get("success"),
                  "state": order_state(res), "msg": res.get("msg", ""), "at": _now_iso()}])
    return 0


# ------------------------------------------------------------------ reconcile
def cmd_reconcile() -> int:
    import yfinance as yf
    rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no")
            and r["order_no"] not in done]
    if not todo:
        print("[T1 live] 확인할 주문 없음"); return 0
    kis, token = _kis()
    pos = _positions()
    out = []
    for o in todo:
        f = kis.get_order_fill_us(token, o["order_no"], o["ticker"], o.get("at", ""))
        filled = int((f or {}).get("filled_qty") or 0)
        if f is None or (filled == 0 and o.get("session") == str(_today_et())):
            continue                           # 아직 결과 없음 — 다음 실행에서
        px = float(f.get("fill_price") or 0)
        try:                                   # 공식 시가 조회 실패가 체결 기록 전체를 막지 않게(리뷰 M4)
            bars = yf.download(o["ticker"], start=o["session"], progress=False, auto_adjust=False)
            off_open = float(bars["Open"].values.ravel()[0]) if len(bars) else float("nan")
        except Exception:  # noqa: BLE001
            off_open = float("nan")
        bp = (px / off_open - 1) * 1e4 if filled and off_open == off_open and off_open > 0 else None
        out.append({"kind": "fill", "order_no": o["order_no"], "ticker": o["ticker"], "side": o["side"],
                    "filled_qty": filled, "fill_price": px, "official_open": off_open, "slip_bp_vs_open": bp,
                    "at": _now_iso()})
        p = pos.get(o["ticker"], {})
        if o["side"] == "buy":
            if filled > 0:
                out.append({**p, "kind": "position", "status": "OPEN", "filled_qty": filled, "entry_price": px,
                            "at": _now_iso()})
            else:
                out.append({**p, "kind": "position", "status": "CLOSED", "close_reason": "loo_unfilled", "at": _now_iso()})
        elif filled > 0:
            ret = (px / float(p.get("entry_price") or px) - 1) if p else None
            remaining = int(p.get("filled_qty") or filled) - filled
            out.append({**p, "kind": "position", "status": "OPEN" if remaining > 0 else "CLOSED",   # 부분 체결(리뷰 M2)
                        "filled_qty": max(remaining, 0), "close_reason": None if remaining > 0 else "moo_exit",
                        "exit_price": px, "gross_ret": ret, "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 {filled}주 @ {px} (공식 시가 {off_open:.2f}, 차이 {bp if bp is None else round(bp, 1)}bp)")
    _append(out)
    return 0


def cmd_status() -> int:
    enabled, cfg = _env_gate()
    pos = _positions()
    fills = [r for r in _read(LEDGER) if r.get("kind") == "fill" and r.get("slip_bp_vs_open") is not None]
    print(f"[T1 live] enabled={enabled} flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']} 격리={_isolation_gate()}")
    print(f"  보유 {sum(1 for p in pos.values() if p.get('status') == 'OPEN')} · 종료 {sum(1 for p in pos.values() if p.get('status') == 'CLOSED')}"
          f" · 체결 {len(fills)}건 · 시가 대비 중앙 차이 "
          f"{sorted(f['slip_bp_vs_open'] for f in fills)[len(fills)//2] if fills else '-'}bp")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["buy", "sell", "reconcile", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    return {"buy": lambda: cmd_buy(a.dry), "sell": lambda: cmd_sell(a.dry),
            "reconcile": cmd_reconcile, "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
