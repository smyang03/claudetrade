# -*- coding: utf-8 -*-
"""KR 지수 정기변경 편입 — 실주문 레인 (2026-10-30, 운영자 지시 "200까지 회차당 50만원 배선 연결하고 진행해").

근거: 사전등록 docs/reports/preregistration_q4_2026_20261022.md §1 통과(docs/reports/kr_index_inclusion_test_20261030.md —
25 클러스터 규모매칭 초과 +4.80% t 4.82, IS +4.69 / OOS +4.91, 22/25 양. 엣지는 KOSDAQ150 쪽, KOSPI200 단독 t 1.79).
계약: KOSPI200·KOSDAQ150 정기변경 **신규 편입 종목**을 발표 다음 거래일 시가(장전 동시호가 시장가)에 사고,
효력일 전 거래일 종가(장 마감 동시호가 시장가)에 판다. 회차당 총액 KR_INDEX_INCL_ROUND_KRW를 편입 종목에 등분하되,
싼 순으로 1주씩 최대한 많은 종목을 담고 남은 돈으로 등가중 목표까지 더 산다(정수 주, allocate 참조).

**실주문 도구다.** 켜기 조건(전부 충족해야 주문):
  1. KR_INDEX_INCL_ENABLED=true 가 .env.live 와 config/v2_start_config.json(env_overrides) 두 곳 모두에 있다.
  2. 봇 격리 계약: risk_manager.ISOLATED_STRATEGY_SOURCES 에 kr_index_inclusion, 실행 중인 봇이 그 코드를 싣고 떴다.
  3. 누적 실현손실 > −KR_INDEX_INCL_LOSS_LIMIT_KRW.

**명단 입력은 사람(또는 Claude 세션)이 한다.** KRX 자동수집은 약관상 금지다. 발표(5월·11월 하순) 당일 저녁에
data/reference/kr_index_changes.csv 에 새 회차 행(index, announce_date, effective_date, ticker, name, action=add/delete,
source_url)을 추가하면 다음 거래일 08:40 매수 작업이 집는다. 발표 시즌인데 새 회차가 없으면 매수 작업이 경고를 남긴다.

명령 (운영 파이썬 — kis_api 필요)
  buy        장전(KST 08:40). 오늘이 진입 세션인 회차를 장전 시장가 매수.
  sell       장 마감 동시호가(KST 15:20:40). 오늘이 청산 세션(효력일 전 거래일)인 보유를 시장가 매도.
             청산 세션이 지났는데 OPEN이면(늦은 실행) 장중 아무 때나 시장가로 판다.
  reconcile  장 마감 후(KST 16:15). 체결 확인·손익 기록. 청산 세션을 넘긴 OPEN은 다음 시가 예약매도(A1).
  status     원장 요약 · 다음 회차 일정.
원장: data/shadow/kr_index_inclusion_live.jsonl — risk_manager.kr_index_inclusion_manifest()가 봇 귀속 복원에 쓴다.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from live_lane_common import (KST as _KST, kr_order_window, live_sell_order_exists, order_state,  # noqa: E402
                              place_safely, reserve_exit_rows, kr_resv_window, resolve_unknown_order_rows,
                              safe_cash_krw, unresolved_unknown_orders, absorb_transmitted_resv_rows,
                              resv_rows_to_absorb)

LEDGER = ROOT / "data" / "shadow" / "kr_index_inclusion_live.jsonl"
CHANGES = ROOT / "data" / "reference" / "kr_index_changes.csv"
START_CFG = ROOT / "config" / "v2_start_config.json"
SOURCE = "kr_index_inclusion"
MARKET_BUFFER = 1.30           # 시장가 매수 증거금(상한가 기준) — 전일 종가 × 1.3까지 현금 확인
FEE_RATE = 0.00015             # 위탁수수료 편도 근사(브로커 실측 ≈ 0.004% — 보수적)
SELL_TAX = 0.0020              # 2026 KR 매도 거래세(코스피 0.05+농특 0.15 / 코스닥 0.20)
KEYS = ["KR_INDEX_INCL_ENABLED", "KR_INDEX_INCL_ROUND_KRW", "KR_INDEX_INCL_LOSS_LIMIT_KRW", "KR_INDEX_INCL_INDEXES"]
SEASONS = ((5, 15, 31), (11, 15, 30))   # 정기변경 발표 시즌(월, 시작일, 끝일)


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


def realized_pnl(rows: list[dict]) -> float:
    return sum(float(r.get("pnl_krw") or 0) for r in rows if r.get("kind") == "close")


# ------------------------------------------------------------------ 달력·회차
def entry_session(announce: date) -> date:
    """발표 다음 거래일(장 마감 뒤 발표 가정 — 연구와 같은 규칙)."""
    from buyback_shadow import kr_next_session
    return kr_next_session(announce)


def exit_session(effective: date) -> date:
    """효력일 전 거래일(그날 종가에 판다)."""
    from buyback_shadow import kr_prev_or_same
    return kr_prev_or_same(effective - timedelta(days=1))


def load_rounds(path: Path | None = None, indexes: set[str] | None = None) -> dict[str, dict]:
    """{announce_date: {"effective": str, "adds": [{index, ticker, name}]}} — action=add만."""
    path = path or CHANGES
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if (r.get("action") or "").strip() != "add":
                continue
            if indexes and (r.get("index") or "").strip() not in indexes:
                continue
            a = (r.get("announce_date") or "").strip()
            tk = (r.get("ticker") or "").strip().zfill(6)
            if not a or not tk.isdigit():
                continue
            g = out.setdefault(a, {"effective": (r.get("effective_date") or "").strip(), "adds": []})
            if tk not in {x["ticker"] for x in g["adds"]}:
                g["adds"].append({"index": r.get("index"), "ticker": tk, "name": r.get("name")})
    return out


def allocate(round_krw: float, prices: dict[str, float]) -> dict[str, int]:
    """회차 총액 배분 — **최대 포함**: 싼 순으로 1주씩 담을 수 있는 만큼 담고, 남은 돈으로 등가중 목표까지 더 산다.
    순수 함수. 10-30 과거 25회차 대조: 연구 등가중(전 종목)과 상관 0.96·평균 13.9종목·+4.16% t 3.72 —
    '몫보다 비싼 종목 제외' 방식(8종목·상관 0.74)보다 계약에 충실하다."""
    cand = {t: p for t, p in prices.items() if p and p > 0}
    inc, spent = [], 0.0
    for t, p in sorted(cand.items(), key=lambda kv: (kv[1], kv[0])):
        if spent + p <= round_krw:
            inc.append(t); spent += p
    if not inc:
        return {}
    q = {t: 1 for t in inc}
    share = round_krw / len(inc)
    while True:                                        # 목표(몫) 대비 가장 덜 찬 종목에 한 주씩 — 등가중 근사
        ok = [t for t in inc if (q[t] + 1) * cand[t] <= share and spent + cand[t] <= round_krw]
        if not ok:
            return q
        t = min(ok, key=lambda x: (q[x] * cand[x] / share, x))
        q[t] += 1; spent += cand[t]


def season_warning(today: date, rounds: dict[str, dict]) -> str | None:
    for m, d0, d1 in SEASONS:
        if today.month == m and d0 <= today.day <= d1:
            recent = [a for a in rounds if a >= (today - timedelta(days=40)).isoformat()]
            if not recent:
                return (f"⚠️ 정기변경 발표 시즌({m}월 {d0}~{d1}일)인데 최근 40일 회차가 CSV에 없다 — "
                        f"발표가 났으면 {CHANGES.name} 에 명단을 넣어야 다음 날 매수한다")
    return None


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
    enabled = a["KR_INDEX_INCL_ENABLED"].lower() == "true" and not mismatch
    cfg = {"round_krw": float(a["KR_INDEX_INCL_ROUND_KRW"] or 500_000),
           "loss_limit": float(a["KR_INDEX_INCL_LOSS_LIMIT_KRW"] or 100_000),
           "indexes": {x.strip() for x in (a["KR_INDEX_INCL_INDEXES"] or "KOSPI200,KOSDAQ150").split(",") if x.strip()},
           "mismatch": mismatch, "enabled_flag": a["KR_INDEX_INCL_ENABLED"]}
    return enabled, cfg


def _isolation_gate() -> bool:
    import risk_manager
    return SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES


def _bot_has_isolation() -> tuple[bool, str]:
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
        raise SystemExit("[idx-incl] KR이 모의투자 모드 — 중단")
    return kis_api, kis_api.get_access_token(market="KR")


def _prev_close(ticker: str, before: date) -> float | None:
    from buyback_shadow import naver_daily
    bars = naver_daily(ticker, count=30)
    ds = [d for d in sorted(bars) if d < before.isoformat()]
    return float(bars[ds[-1]][3]) if ds else None


# ------------------------------------------------------------------ buy
def cmd_buy(dry: bool) -> int:
    enabled, cfg = _env_gate()
    today = _today()
    rounds = load_rounds(indexes=cfg["indexes"])
    w = season_warning(today, rounds)
    if w:
        print(f"[idx-incl] {w}")
    if not dry and not enabled:
        print(f"[idx-incl] 꺼짐 — flag={cfg['enabled_flag']!r} 두소스불일치={cfg['mismatch']}"); return 0
    if not _isolation_gate():
        print("[idx-incl] 봇 격리 계약(kr_index_inclusion) 없음 — 주문 거부(fail-closed)"); return 2
    rows = _read(LEDGER)
    due = [(a, g) for a, g in sorted(rounds.items()) if entry_session(date.fromisoformat(a)) == today]
    if not due:
        print(f"[idx-incl] 오늘({today}) 진입 회차 없음"); return 0
    bot_ok, bot_msg = _bot_has_isolation()
    if not dry and not bot_ok:
        print(f"[idx-incl] {bot_msg} — 주문 거부(fail-closed)"); return 2
    if realized_pnl(rows) <= -cfg["loss_limit"]:
        print(f"[idx-incl] 누적 실현손실 {realized_pnl(rows):,.0f} ≤ −{cfg['loss_limit']:,.0f} — 신규 매수 중단"); return 0
    win_ok, win_msg = kr_order_window("open_auction")
    if not dry and not win_ok:
        print(f"[idx-incl] {win_msg} — 매수 안 냄(시가 진입 계약, 늦은 실행)"); return 0
    kis = token = None
    rc = 0
    for a, g in due:
        if any(r.get("kind") == "order" and r.get("side") == "buy" and r.get("round") == a for r in rows):
            print(f"[idx-incl] 회차 {a} 매수 주문 이미 있음 — 멱등"); continue
        held_open = {t for t, p in _positions(rows).items() if p.get("status") == "OPEN"}
        prices = {}
        for x in g["adds"]:
            if x["ticker"] in held_open:
                print(f"  건너뜀 {x['ticker']} {x['name']}: 이미 보유"); continue
            px = _prev_close(x["ticker"], today)
            if px is None:
                print(f"  건너뜀 {x['ticker']} {x['name']}: 전일 종가 없음"); continue
            prices[x["ticker"]] = px
        plan = allocate(cfg["round_krw"], prices)
        dropped = sorted(set(prices) - set(plan))
        ex = exit_session(date.fromisoformat(g["effective"])).isoformat()
        print(f"[idx-incl] 회차 {a}(효력 {g['effective']}, 청산 {ex}) 편입 {len(g['adds'])} · 매수 {len(plan)} · "
              f"예산상 제외 {len(dropped)}")
        if dry or not plan:
            for t, q in plan.items():
                print(f"  (dry) {t} x{q} @≈{prices[t]:,.0f}")
            continue
        if kis is None:
            kis, token = _kis()
        cash = safe_cash_krw(kis.get_balance(token, market="KR", force_refresh=True))
        names = {x["ticker"]: x for x in g["adds"]}
        for t, q in sorted(plan.items(), key=lambda kv: kv[1] * prices[kv[0]]):
            need = q * prices[t] * MARKET_BUFFER
            if need > cash:
                print(f"  건너뜀 {t}: 주문가능현금 부족({cash:,.0f} < {need:,.0f})"); continue
            cash -= need
            res = place_safely(kis, t, q, 0, "buy", token, market="KR")
            st = order_state(res)
            print(f"  시장가 매수 {t} {names[t]['name']} x{q} (전일종가 {prices[t]:,.0f}) → {st} {res.get('msg', '')}")
            _append([{"kind": "order", "session": today.isoformat(), "round": a, "ticker": t, "side": "buy", "qty": q,
                      "order_no": res.get("order_no", ""), "success": res.get("success"), "state": st,
                      "msg": str(res.get("msg") or "")[:160], "at": _now_iso()}]
                    + ([{"kind": "position", "ticker": t, "name": names[t]["name"], "index": names[t]["index"],
                         "status": "OPEN", "round": a, "entry_session": today.isoformat(), "exit_session": ex,
                         "qty": q, "cost_krw": round(q * prices[t]), "source": SOURCE, "order_state": st,
                         "at": _now_iso()}] if st != "rejected" else []))
            if st == "unknown":
                rc = 3
        rows = _read(LEDGER)
    return rc


# ------------------------------------------------------------------ sell
def due_sells(rows: list[dict], today: date) -> list[dict]:
    """청산 세션이 오늘이거나 지난 OPEN 보유(체결 수량 있는 것)."""
    return [p for p in _positions(rows).values() if p.get("status") == "OPEN" and p.get("filled_qty")
            and str(p.get("exit_session") or "9999") <= today.isoformat()]


def cmd_sell(dry: bool) -> int:
    if not dry:
        cmd_reconcile(reserve=False)
    today = _today()
    rows = _read(LEDGER)
    due = due_sells(rows, today)
    if not due:
        print("[idx-incl] 오늘 청산 없음"); return 0
    kis = token = None
    for p in due:
        tk = p["ticker"]
        if live_sell_order_exists(rows, today.isoformat(), tk):
            print(f"[idx-incl] {tk} 오늘 매도 이미 있음"); continue
        late = p["exit_session"] < today.isoformat()
        ok, msg = kr_order_window("exit" if late else "close_auction")
        print(f"[idx-incl] {'(dry) ' if dry else ''}{'지연 ' if late else ''}시장가 매도 {tk} x{p['filled_qty']} "
              f"(회차 {p['round']}, 청산 세션 {p['exit_session']})")
        if dry:
            continue
        if not ok:
            print(f"  {msg} — 매도 보류(다음 실행 / reconcile 예약매도)"); continue
        if kis is None:
            kis, token = _kis()
        res = place_safely(kis, tk, int(p["filled_qty"]), 0, "sell", token, market="KR")
        _append([{"kind": "order", "session": today.isoformat(), "round": p["round"], "ticker": tk, "side": "sell",
                  "qty": int(p["filled_qty"]), "order_no": res.get("order_no", ""), "success": res.get("success"),
                  "state": order_state(res), "msg": str(res.get("msg") or "")[:160], "at": _now_iso()}])
    return 0


def close_pnl(entry: float, exit_px: float, qty: int) -> float:
    gross = (exit_px - entry) * qty
    return round(gross - (entry + exit_px) * qty * FEE_RATE - exit_px * qty * SELL_TAX)


# ------------------------------------------------------------------ reconcile
def cmd_reconcile(reserve: bool = True) -> int:
    rows = _read(LEDGER)
    kis = token = None
    if unresolved_unknown_orders(rows):
        kis, token = _kis()
        rec, notes = resolve_unknown_order_rows(kis, token, rows)
        for n in notes:
            print(f"[idx-incl] {n}")
        if rec:
            _append(rec); rows = _read(LEDGER)
    today_s = _today().isoformat()
    if resv_rows_to_absorb(rows, today_s):
        if kis is None:
            kis, token = _kis()
        rec, notes = absorb_transmitted_resv_rows(kis, token, rows, today_s)
        for n in notes:
            print(f"[idx-incl] {n}")
        if rec:
            _append(rec); rows = _read(LEDGER)
    done = {r["order_no"] for r in rows if r.get("kind") == "fill"}
    todo = [r for r in rows if r.get("kind") == "order" and r.get("success") and r.get("order_no") and r["order_no"] not in done]
    out = []
    pos = _positions(rows)
    for o in todo:
        if kis is None:
            kis, token = _kis()
        f = kis.get_order_fill_kr(token, o["order_no"], o["ticker"], o["session"].replace("-", ""))
        filled = int((f or {}).get("filled_qty") or 0)
        if f is None or (filled == 0 and o["session"] == today_s and datetime.now(_KST).hour < 16):
            continue
        px = float(f.get("fill_price") or 0)
        out.append({"kind": "fill", "order_no": o["order_no"], "ticker": o["ticker"], "side": o["side"],
                    "filled_qty": filled, "fill_price": px, "at": _now_iso()})
        p = pos.get(o["ticker"], {})
        if o["side"] == "buy":
            out.append({**p, "kind": "position", "status": "OPEN" if filled else "CLOSED", "filled_qty": filled,
                        "entry_price": px, "at": _now_iso()})
        elif filled:
            remaining = int(p.get("filled_qty") or filled) - filled
            out.append({**p, "kind": "position", "status": "OPEN" if remaining > 0 else "CLOSED",
                        "filled_qty": max(remaining, 0), "exit_price": px, "at": _now_iso()})
            out.append({"kind": "close", "ticker": o["ticker"], "round": p.get("round"), "entry_session": p.get("entry_session"),
                        "exit_session": o["session"], "entry_price": p.get("entry_price"), "exit_price": px, "qty": filled,
                        "pnl_krw": close_pnl(float(p.get("entry_price") or px), px, filled), "at": _now_iso()})
        print(f"  {o['side']} {o['ticker']} 체결 {filled}주 @ {px:,.0f}")
    _append(out)
    rows = _read(LEDGER)
    if not todo:
        print("[idx-incl] 확인할 주문 없음")
    rc = 0
    now = datetime.now(_KST)
    if reserve and now.time() >= datetime.strptime("15:40", "%H:%M").time():
        for p in due_sells(rows, now.date()):          # 청산 세션 종가를 놓친 보유 → 다음 시가 예약매도(A1)
            if not kr_resv_window(now)[0]:
                break
            if live_sell_order_exists(rows, now.date().isoformat(), p["ticker"]):
                continue                                  # 오늘 매도가 이미 나갔다(체결 확인 대기) — 이중 매도 방지
            if kis is None:
                kis, token = _kis()
            new, why = reserve_exit_rows(kis, token, rows, p["ticker"], int(p["filled_qty"]), now=now)
            print(f"[idx-incl] ⚠️ 청산 지연 {p['ticker']}(청산 세션 {p['exit_session']}) — 예약매도: {why}")
            if new:
                _append(new); rows = _read(LEDGER)
            rc = 3
    return rc


def cmd_status() -> int:
    enabled, cfg = _env_gate()
    rows = _read(LEDGER)
    rounds = load_rounds(indexes=cfg["indexes"])
    print(f"[idx-incl] enabled={enabled} 두소스불일치={cfg['mismatch']} 봇격리={_bot_has_isolation()} "
          f"회차당 {cfg['round_krw']:,.0f} · 손실한도 {cfg['loss_limit']:,.0f} · 지수 {sorted(cfg['indexes'])}")
    last = max(rounds) if rounds else None
    print(f"  CSV 마지막 회차 {last} · 보유 {sum(1 for p in _positions(rows).values() if p.get('status') == 'OPEN')} · "
          f"누적 실현 {realized_pnl(rows):,.0f}원")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["buy", "sell", "reconcile", "status"])
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd != "status" and not a.dry:
        from net_ready import wait_dns
        wait_dns(["openapi.koreainvestment.com"], tag="idx-incl")
    return {"buy": lambda: cmd_buy(a.dry), "sell": lambda: cmd_sell(a.dry),
            "reconcile": cmd_reconcile, "status": cmd_status}[a.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
