# -*- coding: utf-8 -*-
"""KR 자사주 canary — 소액 실운영 (2026-09-30 운영자 지시 "다하고! 진행해", 권한 승인 후 작성).

규칙은 쉐도우 원장(tools/buyback_shadow.py, data/shadow/kr_buyback_shadow.jsonl)의 채택 행을 그대로 따른다:
자기주식 직접취득결정, 취득 주식 ≥ 발행주식 1%, dvol20 ≥ 5억(Y1 ≥20억 / L1_lowliq 5~20억) → 다음 거래일 시가 진입, 5세션 보유.
근거: docs/reports/kr_event_20260930.md (OOS h5 +2.29% t 3.34, 저유동 L1 +2.12% t 2.66, 2024 밸류업 편중).
목적은 수익보다 **실체결 + 전략 동시 검증**이다. 손실은 작게 묶는다.

**실주문 도구다.** 켜기 조건(전부 충족해야 주문):
  1. KR_BUYBACK_CANARY_ENABLED=true 가 .env.live 와 config/v2_start_config.json(env_overrides) 두 곳 모두에 있다.
  2. 봇 격리 계약: risk_manager.ISOLATED_STRATEGY_SOURCES 에 kr_buyback_canary (봇이 이 종목을 팔지 않음).
  3. 금액 게이트: 건당 KR_BUYBACK_CANARY_ORDER_KRW(기본 10만) · 동시 KR_BUYBACK_CANARY_MAX_OPEN(기본 2)
     · 누적 실현손실 한도 KR_BUYBACK_CANARY_LOSS_LIMIT_KRW(기본 5만) — 한도 도달 시 신규 매수 자동 중단(fail-closed).
  4. 이미 계좌에 있는 종목(코어 ETF 등)은 사지 않는다. 원화 주문가능현금이 모자라면 건너뛴다.

명령 (운영 파이썬 — kis_api 필요)
  buy        장전(KST 08:40, 자사주 KR 스캔 08:30 뒤). 오늘 진입일인 채택 행 중 dvol 큰 순으로 시장가 매수
             → 08:30~09:00 장전 동시호가에 접수돼 09:00 시가에 체결된다.
  sell       장전(KST 08:41·09:10). 진입 후 5번째 세션이 된 보유를 시장가 매도(같은 방식으로 시가 체결).
             예약매도가 있으면 처리결과부터: 전송됨 → 일반 매도 안 냄 / 거부·취소 → 일반 매도 / 미처리(09:05 전) → 09:10 재확인
             / 조회 실패 → 매도 안 냄(rc 3).
  reconcile  장 마감 후(KST 16:10). 오늘 주문 체결 확인, 체결가 − 공식 시가(네이버 일봉) 차이 bp 기록, 손익 기록.
             + 4번째 세션 저녁에 다음 영업일(만기) 시가 시장가 매도 예약(KIS 예약주문, 2026-10-21 A1) + unknown 주문 되짚기
             + 만기 지연 탐지(rc 3).
  reserve_sell  수동 — 다음 영업일이 만기인 보유분을 지금 예약 매도.
  status     원장 요약.
  --dry      게이트를 통과시키되 주문은 내지 않고 출력만.
원장: data/shadow/kr_buyback_canary_live.jsonl — risk_manager.kr_buyback_canary_manifest()가 봇 귀속 복원에 쓴다.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from live_lane_common import (KST as _KST, absorb_transmitted_resv_rows, active_resv, cancel_orphan_resv,  # noqa: E402
                              check_resv, kr_order_window, kr_resv_window, live_sell_order_exists, orphan_resv_candidates,
                              order_state, place_safely, reserve_exit_rows, resolve_unknown_order_rows,
                              resv_exit_decision, resv_rows_to_absorb, resv_target_session, safe_cash_krw,
                              unresolved_unknown_orders)

SHADOW = ROOT / "data" / "shadow" / "kr_buyback_shadow.jsonl"
LEDGER = ROOT / "data" / "shadow" / "kr_buyback_canary_live.jsonl"
START_CFG = ROOT / "config" / "v2_start_config.json"
HOLD = 5
SOURCE = "kr_buyback_canary"
MARKET_BUFFER = 1.30          # 시장가 매수 증거금은 상한가 기준 — 전일 종가 × 1.3까지 현금 확인
KEYS = ["KR_BUYBACK_CANARY_ENABLED", "KR_BUYBACK_CANARY_ORDER_KRW", "KR_BUYBACK_CANARY_MAX_OPEN",
        "KR_BUYBACK_CANARY_LOSS_LIMIT_KRW"]
KST = timezone(timedelta(hours=9))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _today_kst() -> date:
    return datetime.now(KST).date()


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


def _realized_pnl(rows: list[dict] | None = None) -> float:
    """청산 행을 전부 합산한다(같은 종목을 다시 사도 이전 손실이 빠지지 않게 — 리뷰 M1)."""
    rows = rows if rows is not None else _read(LEDGER)
    return sum(float(r.get("pnl_krw") or 0) for r in rows
               if (r.get("kind") == "position" and r.get("status") == "CLOSED" and r.get("pnl_krw") is not None)
               or r.get("kind") == "close")


def signals_for(day: date, shadow_rows: list[dict]) -> list[dict]:
    """쉐도우 원장의 키별 마지막 행 중 오늘 진입 채택분."""
    last: dict[str, dict] = {}
    for r in shadow_rows:
        if r.get("key"):
            last[r["key"]] = r
    return [r for r in last.values() if r.get("qualifies") and r.get("entry_date") == day.isoformat()
            and r.get("rule") in ("Y1", "L1_lowliq") and r.get("stock_code") and r.get("close_D")]


def sessions_since(entry: str, today: date) -> int:
    """entry(진입 세션)부터 today까지 지난 세션 수 — today가 진입+N번째 세션이면 N.
    today가 휴장일이면 today 이전 세션까지만 센다(10-09 한글날에 다음 세션을 미리 세어 만기 매도를 낸 버그)."""
    from buyback_shadow import kr_next_session
    d, n = date.fromisoformat(entry), 0
    while (nxt := kr_next_session(d)) <= today:
        d = nxt
        n += 1
    return n


# ------------------------------------------------------------------ gates
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
    enabled = a["KR_BUYBACK_CANARY_ENABLED"].lower() == "true" and not mismatch
    cfg = {"order_krw": float(a["KR_BUYBACK_CANARY_ORDER_KRW"] or 100_000),
           "max_open": int(a["KR_BUYBACK_CANARY_MAX_OPEN"] or 2),
           "loss_limit": float(a["KR_BUYBACK_CANARY_LOSS_LIMIT_KRW"] or 50_000),
           "mismatch": mismatch, "enabled_flag": a["KR_BUYBACK_CANARY_ENABLED"]}
    return enabled, cfg


def _isolation_gate() -> bool:
    import risk_manager
    return SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES


def _bot_has_isolation() -> tuple[bool, str]:
    """실행 중인 봇이 canary 격리 코드(risk_manager/trading_bot 09-30 수정)를 싣고 떴는가 — 아니면 봇이 canary
    종목을 일반 포지션으로 보고 청산할 수 있으므로 매수를 거부한다(fail-closed)."""
    pid = ROOT / "state" / "live_trading_bot.pid"
    try:
        started = datetime.fromisoformat(json.loads(pid.read_text(encoding="utf-8"))["started_at"])
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
        raise SystemExit("[canary] KR이 모의투자 모드 — 중단")
    return kis_api, kis_api.get_access_token(market="KR")


def plan_buys(sigs: list[dict], cfg: dict, open_pos: dict, held: set, cash: float,
              realized: float) -> tuple[list[dict], list[str]]:
    """순수 함수 — 주문 계획과 건너뛴 사유."""
    notes: list[str] = []
    if realized <= -cfg["loss_limit"]:
        return [], [f"누적 실현손실 {realized:,.0f}원 ≤ −{cfg['loss_limit']:,.0f} — 신규 매수 중단"]
    room = cfg["max_open"] - len(open_pos)
    plans: list[dict] = []
    for s in sorted(sigs, key=lambda r: -float(r.get("dvol20_D") or 0)):
        tk = str(s["stock_code"]).strip()
        if room <= 0:
            notes.append(f"{tk}: 슬롯 가득"); continue
        if tk in held or tk in open_pos:
            notes.append(f"{tk}: 이미 보유"); continue
        px = float(s["close_D"])
        qty = int(math.floor(cfg["order_krw"] / px))
        if qty < 1:
            notes.append(f"{tk}: 1주({px:,.0f}원)가 건당 한도 초과"); continue
        need = qty * px * MARKET_BUFFER
        if need > cash:
            notes.append(f"{tk}: 주문가능현금 부족({cash:,.0f} < {need:,.0f})"); continue
        cash -= need
        room -= 1
        plans.append({"ticker": tk, "name": s.get("corp_name"), "qty": qty, "close_D": px, "rule": s.get("rule"),
                      "rcept_no": s.get("rcept_no"), "ratio": s.get("ratio"), "cost_krw": round(qty * px)})
    return plans, notes


# ------------------------------------------------------------------ buy
def cmd_buy(dry: bool) -> int:
    enabled, cfg = _env_gate()
    if not dry and not enabled:
        print(f"[canary] 꺼짐 — flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']}"); return 0
    if not _isolation_gate():
        print("[canary] 봇 격리 계약(kr_buyback_canary) 없음 — 주문 거부(fail-closed)"); return 2
    bot_ok, bot_msg = _bot_has_isolation()
    if not dry and not bot_ok:
        print(f"[canary] {bot_msg} — 주문 거부(fail-closed)"); return 2
    today = _today_kst()
    rows = _read(LEDGER)
    if any(r.get("kind") == "order" and r.get("side") == "buy" and r.get("session") == today.isoformat() for r in rows):
        print("[canary] 오늘 매수 주문 이미 있음 — 멱등 종료"); return 0
    sigs = signals_for(today, _read(SHADOW))
    if not sigs:
        print(f"[canary] 오늘({today}) 진입 채택 신호 없음"); return 0
    open_pos = {t: p for t, p in _positions(rows).items() if p.get("status") == "OPEN"}
    kis = token = None
    if dry:
        held, cash = set(), 10 ** 9
    else:
        kis, token = _kis()
        bal = kis.get_balance(token, market="KR", force_refresh=True)
        held = {str(s.get("ticker") or "").strip() for s in bal.get("stocks", [])}
        cash = safe_cash_krw(bal)                      # 미결제 매수 반영(리뷰 H2)
    plans, notes = plan_buys(sigs, cfg, open_pos, held, cash, _realized_pnl(rows))
    for n in notes:
        print("  건너뜀", n)
    win_ok, win_msg = kr_order_window("open_auction")
    if plans and not dry and not win_ok:
        print(f"[canary] {win_msg} — 매수 주문 안 냄(시가 진입 계약, 늦은 실행)"); return 0
    for p in plans:
        print(f"[canary] {'(dry) ' if dry else ''}시장가 매수 {p['ticker']} {p['name']} x{p['qty']} "
              f"(전일종가 {p['close_D']:,.0f}, 약 {p['cost_krw']:,}원, {p['rule']}, 비율 {float(p['ratio'] or 0) * 100:.2f}%)")
        if dry:
            continue
        res = place_safely(kis, p["ticker"], p["qty"], 0, "buy", token, market="KR")
        state = order_state(res)
        _append([{"kind": "order", "session": today.isoformat(), "ticker": p["ticker"], "side": "buy", "qty": p["qty"],
                  "order_no": res.get("order_no", ""), "success": res.get("success"), "state": state,
                  "msg": res.get("msg", ""), "at": _now_iso()}]
                + ([{"kind": "position", "ticker": p["ticker"], "name": p["name"], "status": "OPEN",
                     "entry_session": today.isoformat(), "qty": p["qty"], "cost_krw": p["cost_krw"], "rule": p["rule"],
                     "rcept_no": p["rcept_no"], "source": SOURCE, "order_state": state, "at": _now_iso()}]
                   if state != "rejected" else []))   # unknown도 OPEN으로 남겨 봇 청산을 막는다(리뷰 H3)
        if state == "unknown":
            print(f"  ⚠️ {p['ticker']} 주문 접수 여부 불명 — 브로커 확인 필요: {res.get('msg')}")
    return 0


# ------------------------------------------------------------------ sell
def due_exits(positions: dict[str, dict], today: date) -> list[tuple[str, dict, int]]:
    out = []
    for tk, p in positions.items():
        if p.get("status") != "OPEN" or not p.get("filled_qty"):
            continue
        n = sessions_since(p["entry_session"], today)
        if n >= HOLD:
            out.append((tk, p, n))
    return out


def cmd_sell(dry: bool) -> int:
    # 만기 청산은 ENABLED 플래그와 무관하게 한다 — 플래그는 신규 매수만 막는다. 봇은 격리 때문에 팔지 않으므로
    # 여기서 안 팔면 청산 주체가 없어진다(리뷰 M3).
    if not dry:
        cmd_reconcile()                                # 체결 확인이 빠졌어도 만기 매도가 건너뛰어지지 않게(리뷰 M4)
    today = _today_kst()
    rows = _read(LEDGER)
    due = due_exits(_positions(rows), today)
    if not due:
        print("[canary] 오늘 만기 없음"); return 0
    win_ok, win_msg = kr_order_window("exit")
    kis = token = None                                 # KIS는 실제로 조회·주문할 때만 연다(창 밖이면 호출 없음)
    rc = 0
    for tk, p, n in due:
        if live_sell_order_exists(rows, today.isoformat(), tk):   # 거부·미접수 확정 주문은 브로커에 없으니 재시도(10-30 수리)
            print(f"[canary] {tk} 오늘 매도 주문 이미 있음"); continue
        stale = [r for r in resv_rows_to_absorb(rows, today.isoformat())
                 if r.get("ticker") == tk and r.get("target_session") < today.isoformat()]
        if stale and not dry:                          # 지난 예약이 이미 팔았을 수 있다(10-30 수리)
            print(f"[canary] ⚠️ {tk} 이전 예약 seq={stale[-1].get('rsvn_seq') or '-'}({stale[-1].get('target_session')}) "
                  "처리결과 미확인 — 일반 매도 안 냄"); rc = 3; continue
        resv = active_resv(rows, tk, today.isoformat())
        if resv and not dry:                            # A1: 예약매도 처리결과부터 — 이중 매도 방지
            if kis is None:
                kis, token = _kis()
            try:
                status, detail = check_resv(kis, token, resv)
            except Exception as exc:  # noqa: BLE001
                print(f"[canary] ⚠️ {tk} 예약매도 seq={resv.get('rsvn_seq') or '-'} 처리결과 조회 실패({type(exc).__name__}) — "
                      "예약이 살아 있을 수 있어 일반 매도 안 냄"); rc = 3; continue
            act = resv_exit_decision(status)
            print(f"[canary] {tk} 예약매도 seq={resv.get('rsvn_seq') or '-'} 처리결과 {status} {detail} → {act}")
            if act == "skip":
                _append([{"kind": "order", "session": today.isoformat(), "ticker": tk, "side": "sell", "qty": int(p["filled_qty"]),
                          "order_no": detail, "success": True, "state": "ok", "msg": "resv_transmitted", "via": "resv",
                          "rsvn_seq": resv.get("rsvn_seq"), "at": _now_iso()}])
                continue
            if act == "wait":
                print(f"[canary] {tk} 예약주문 전송 대기 — 09:10 재확인"); continue
            print(f"[canary] {tk} 예약매도 미전송 — 일반 시장가 매도로 대체")
        print(f"[canary] {'(dry) ' if dry else ''}시장가 매도 {tk} x{p['filled_qty']} (진입 {p['entry_session']}, {n}번째 세션)")
        if dry:
            continue
        if not win_ok:
            print(f"[canary] {win_msg} — 만기 매도 보류, 다음 정상 실행에서 매도: {tk}"); continue
        if kis is None:
            kis, token = _kis()
        res = place_safely(kis, tk, int(p["filled_qty"]), 0, "sell", token, market="KR")
        _append([{"kind": "order", "session": today.isoformat(), "ticker": tk, "side": "sell", "qty": int(p["filled_qty"]),
                  "order_no": res.get("order_no", ""), "success": res.get("success"), "state": order_state(res),
                  "msg": res.get("msg", ""), "at": _now_iso()}])
    return rc


def due_next_session(positions: dict[str, dict], target: date) -> list[tuple[str, dict]]:
    """target 세션이 만기(진입+HOLD 이상)인 보유 — 예약매도 대상."""
    return [(tk, p) for tk, p in positions.items()
            if p.get("status") == "OPEN" and p.get("filled_qty") and sessions_since(p["entry_session"], target) >= HOLD]


def cmd_reserve_sell(dry: bool = False) -> int:
    """다음 영업일이 만기인 보유분을 지금 예약 매도(A1). reconcile이 16:10에 같은 일을 하고, 이 명령은 수동 1회용."""
    rows = _read(LEDGER)
    now = datetime.now(_KST)
    due = due_next_session(_positions(rows), resv_target_session(now))
    if not due:
        print("[canary] 다음 영업일 만기 보유 없음"); return 0
    if dry:
        for tk, p in due:
            print(f"[canary] {reserve_exit_rows(None, None, rows, tk, int(p['filled_qty']), now=now, dry=True)[1]}")
        return 0
    ok, why = kr_resv_window(now)
    if not ok:
        print(f"[canary] {why} — 예약 안 냄"); return 2
    kis, token = _kis()
    rc = 0
    for tk, p in due:
        new, why = reserve_exit_rows(kis, token, rows, tk, int(p["filled_qty"]), now=now)
        print(f"[canary] {why}")
        if new:
            _append(new); rows = _read(LEDGER)
            if new[0]["state"] != "ok":
                rc = 2
    return rc


def _reserve_and_overdue(rows: list[dict], kis=None, token=None) -> int:
    """reconcile 후반부 — ① 다음 영업일 만기분 예약매도 ② 만기 지연 탐지(15:30 이후에도 만기 보유가 OPEN이면 rc 3)."""
    rc = 0
    now = datetime.now(_KST)
    pos = _positions(rows)
    today_s = _today_kst().isoformat()
    open_qty = {t: int(q.get("filled_qty") or 0) for t, q in pos.items() if q.get("status") == "OPEN"}
    if orphan_resv_candidates(rows, today_s, open_qty):  # 청산·수량 변경 뒤 남은 예약 취소(10-30 수리)
        if kis is None:
            kis, token = _kis()
        rec, notes = cancel_orphan_resv(kis, token, rows, today_s, open_qty)
        for n in notes:
            print(f"[canary] {n}")
            if n.startswith("⚠️"):
                rc = 3
        if rec:
            _append(rec); rows = _read(LEDGER)
    if kr_resv_window(now)[0]:
        for tk, p in due_next_session(pos, resv_target_session(now)):
            if kis is None:
                kis, token = _kis()
            new, why = reserve_exit_rows(kis, token, rows, tk, int(p["filled_qty"]), now=now)
            print(f"[canary] 예약매도: {why}")
            if why.startswith("⚠️"):
                rc = 3
            if new:
                _append(new); rows = _read(LEDGER)
                if new[0]["state"] != "ok":
                    rc = 3
    today = _today_kst()
    if now.time() >= datetime.strptime("15:30", "%H:%M").time():
        for tk, p, n in due_exits(pos, today):
            print(f"[canary] ⚠️ 만기 지연 — {tk} x{p['filled_qty']} 진입 {p['entry_session']} {n}번째 세션인데 장 마감까지 OPEN. "
                  "브로커 보유·예약주문 확인 필요")
            rc = 3
    return rc


# ------------------------------------------------------------------ reconcile
def _official_open(ticker: str, day: str) -> float:
    try:
        from buyback_shadow import naver_daily
        bars = naver_daily(ticker, count=10)
        b = bars.get(day) or bars.get(day.replace("-", ""))
        return float(b[0]) if b else float("nan")
    except Exception:  # noqa: BLE001
        return float("nan")


def cmd_reconcile() -> int:
    rows = _read(LEDGER)
    kis = token = None
    if unresolved_unknown_orders(rows):                  # A1: 접수 응답을 못 받은 주문을 브로커 당일 체결조회로 되짚는다
        kis, token = _kis()
        rec, notes = resolve_unknown_order_rows(kis, token, rows)
        for n in notes:
            print(f"[canary] {n}")
        if rec:
            _append(rec)
            rows = _read(LEDGER)
    if resv_rows_to_absorb(rows, _today_kst().isoformat()):   # A1: 매도 작업이 안 돈 날의 예약매도 전송분을 흡수
        if kis is None:
            kis, token = _kis()
        rec, notes = absorb_transmitted_resv_rows(kis, token, rows, _today_kst().isoformat())
        for n in notes:
            print(f"[canary] {n}")
        if rec:
            _append(rec)
            rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no")
            and r["order_no"] not in done]
    if not todo:
        print("[canary] 확인할 주문 없음")
        return _reserve_and_overdue(rows, kis, token)
    if kis is None:
        kis, token = _kis()
    pos = _positions(rows)
    out = []
    for o in todo:
        f = kis.get_order_fill_kr(token, o["order_no"], o["ticker"], o["session"].replace("-", ""))
        filled = int((f or {}).get("filled_qty") or 0)
        if f is None or (filled == 0 and o.get("session") == _today_kst().isoformat()
                         and datetime.now(KST).hour < 16):
            continue
        px = float(f.get("fill_price") or 0)
        off_open = _official_open(o["ticker"], o["session"])
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
                out.append({**p, "kind": "position", "status": "CLOSED", "close_reason": "unfilled", "pnl_krw": 0,
                            "at": _now_iso()})
        elif filled > 0:
            entry = float(p.get("entry_price") or px)
            gross = (px - entry) * filled
            fee = (px + entry) * filled * 0.00015 + px * filled * 0.0020   # 위탁수수료 근사 + 거래세 — 정확값은 브로커 기간손익으로 대조
            remaining = int(p.get("filled_qty") or filled) - filled
            if remaining > 0:                          # 부분 체결 — 남은 주식은 OPEN으로 유지(리뷰 M2)
                out.append({"kind": "close", "ticker": o["ticker"], "partial": True, "qty": filled,
                            "pnl_krw": round(gross - fee), "at": _now_iso()})
                out.append({**p, "kind": "position", "status": "OPEN", "filled_qty": remaining, "at": _now_iso()})
            else:
                out.append({**p, "kind": "position", "status": "CLOSED", "close_reason": "h5_exit", "exit_price": px,
                            "gross_ret": px / entry - 1, "pnl_krw": round(gross - fee), "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 {filled}주 @ {px:,.0f} (공식 시가 {off_open:,.0f}, 차이 "
              f"{bp if bp is None else round(bp, 1)}bp)")
    _append(out)
    return _reserve_and_overdue(_read(LEDGER), kis, token)


def cmd_status() -> int:
    enabled, cfg = _env_gate()
    rows = _read(LEDGER)
    pos = _positions(rows)
    fills = [r for r in rows if r.get("kind") == "fill" and r.get("slip_bp_vs_open") is not None]
    print(f"[canary] enabled={enabled} flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']} 격리={_isolation_gate()}"
          f" 건당 {cfg['order_krw']:,.0f} · 동시 {cfg['max_open']} · 손실한도 {cfg['loss_limit']:,.0f}"
          f" · 봇격리반영={_bot_has_isolation()}")
    print(f"  보유 {sum(1 for p in pos.values() if p.get('status') == 'OPEN')} · 종료 "
          f"{sum(1 for p in pos.values() if p.get('status') == 'CLOSED')} · 누적 실현 {_realized_pnl(rows):,.0f}원"
          f" · 체결 {len(fills)}건 · 시가 대비 중앙 "
          f"{sorted(f['slip_bp_vs_open'] for f in fills)[len(fills) // 2] if fills else '-'}bp")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["buy", "sell", "reconcile", "reserve_sell", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    return {"buy": lambda: cmd_buy(a.dry), "sell": lambda: cmd_sell(a.dry),
            "reconcile": cmd_reconcile, "reserve_sell": lambda: cmd_reserve_sell(a.dry), "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
