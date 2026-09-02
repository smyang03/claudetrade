"""유령 포지션(phantom position) 북 — 쉐도우를 실매매처럼 (2026-09-03, 설계 정본
`docs/reports/phantom_execution_design_20260902.md` §2).

브리지가 REHEARSAL_READY를 찍는 순간(실제 호가·수량, 제출만 차단) 실주문 포지션과 같은 dict를
만들어 **별도 파일**(`state/phantom_positions.json`)에 둔다. 매 사이클 실제 봇의 sleeve 출구 평가
(`risk._isolated_strategy_exit_candidate` — TP12/SL25/BE락)와 만기 판정(`_fixed_horizon_strategy_exit_candidates`)을
**같은 함수로** 태우고, 매도 판정이 나면 실주문 대신 가상 청산을 원장(`data/shadow/phantom_ledger.jsonl`)에 적는다.

격리(설계 원칙 2): 이 리스트는 `bot.risk.positions`에 절대 들어가지 않는다 → 슬롯·리스크·브로커 truth·
실주문 원장에 영향 0. integrity_check가 매일 격리를 검사한다.
fail-silent: 이 모듈의 예외가 실주문 경로를 막으면 안 된다 — 호출부는 WARNING 한 줄로 삼킨다.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from bot.session_date import KST
from logger import get_trading_logger
from runtime_paths import get_runtime_path

log = get_trading_logger()

PHANTOM_SOURCES = {"us_swing_5d"}


def _state_path() -> Path:
    return get_runtime_path("state", "phantom_positions.json")


def _ledger_path() -> Path:
    return get_runtime_path("data", "shadow", "phantom_ledger.jsonl")


def load_positions() -> list[dict]:
    p = _state_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "[]")
        return [x for x in data if isinstance(x, dict) and x.get("virtual")]
    except Exception as exc:
        log.warning(f"[phantom] 상태 파일 읽기 실패(무시): {exc}")
        return []


def save_positions(positions: list[dict]) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _append_ledger(row: dict) -> None:
    try:
        p = _ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning(f"[phantom] 원장 기록 실패(무시): {exc}")


def _now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def build_position(*, ticker: str, qty: int, quote_usd: float, usd_krw: float,
                   session_date: str, source: str = "us_swing_5d",
                   reason: str = "", retro: bool = False,
                   tp_pct: float = 0.12, sl_pct: float = 0.25, max_hold: int = 7) -> dict:
    """실주문 us_swing 포지션(APLD 09-02 실측 템플릿)과 같은 필드 집합 + virtual 표식.
    risk._isolated_strategy_exit_candidate가 읽는 필드: current_price(KRW)·display_currency·
    display_avg_price·display_current_price·sl_pct·tp_pct·peak_pnl_pct/position_mfe_pct·source_strategy."""
    quote_usd = float(quote_usd or 0.0)
    rate = float(usd_krw or 0.0) or 1.0
    entry_krw = quote_usd * rate
    now = _now_iso()
    return {
        "ticker": ticker.upper(), "name": ticker.upper(), "market": "US",
        "entry": entry_krw, "qty": int(qty or 0), "current_price": entry_krw,
        "display_avg_price": quote_usd, "display_current_price": quote_usd, "display_currency": "USD",
        "price_source": "rehearsal_quote", "order_no": "", "strategy": "MICRO_PROBE",
        "source_strategy": source, "strategy_used": "MICRO_PROBE", "source_type": "signal_entry",
        "micro_probe": True, "micro_probe_reason": "phantom_rehearsal",
        "original_order_cost_krw": entry_krw * int(qty or 0),
        "tp": entry_krw * (1.0 + tp_pct), "sl": entry_krw * (1.0 - sl_pct),
        "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold, "held_days": 0,
        "entry_date": session_date, "session_date": session_date, "entry_session_date": session_date,
        "entry_time": now, "trailing": False, "trail_sl": 0.0, "trail_pct": 0.03, "tp_triggered": False,
        "position_id": f"phantom_US_{ticker.upper()}_{session_date}",
        "entry_route": "plan_a", "route_source": "signal_entry", "path_type": "", "pathb_path_run_id": "",
        "position_origin": "phantom", "position_integrity": "virtual", "management_protected": False,
        "position_mfe_pct": 0.0, "peak_pnl_pct": 0.0, "position_mae_pct": 0.0, "trough_pnl_pct": 0.0,
        "peak_price_native": quote_usd, "position_peak_price": quote_usd,
        "exit_owner": source, "exit_policy": "isolated_strategy", "exit_contract": "TP12_SL25_D7_BE4",
        "virtual": True, "phantom_reason": reason, "retro": bool(retro), "opened_at": now,
        "last_price_at": now,
    }


def open_from_rehearsal(bot: Any, *, ticker: str, qty: int, quote_usd: float, session_date: str,
                        reason: str = "", retro: bool = False) -> dict | None:
    """(세션, 종목) 멱등. 이미 있으면 None."""
    positions = load_positions()
    key = (str(session_date), str(ticker).upper())
    if any((str(p.get("entry_session_date")), str(p.get("ticker")).upper()) == key for p in positions):
        return None
    pos = build_position(ticker=ticker, qty=qty, quote_usd=quote_usd,
                         usd_krw=float(getattr(bot, "usd_krw_rate", 0.0) or 0.0),
                         session_date=session_date, reason=reason, retro=retro)
    positions.append(pos)
    save_positions(positions)
    _append_ledger({"event": "OPEN", "ts": pos["opened_at"], "session_date": session_date,
                    "ticker": pos["ticker"], "qty": pos["qty"], "quote_usd": quote_usd,
                    "usd_krw": float(getattr(bot, "usd_krw_rate", 0.0) or 0.0),
                    "source": pos["source_strategy"], "reason": reason, "retro": bool(retro),
                    "note": "retro: 장중 봉우리 이력 없음(peak=entry)" if retro else ""})
    log.info(f"[VIRTUAL][phantom OPEN] {pos['ticker']} {pos['qty']}주 @ ${quote_usd:.2f} "
             f"session={session_date}{' (retro)' if retro else ''} — 실주문 아님")
    return pos


def ensure_from_handoff_ledger(bot: Any, *, lookback_days: int = 10) -> int:
    """브리지 원장(signals.handoff_status=REHEARSAL_READY)에 있는데 유령이 없는 픽을 소급 생성.
    재시작·창 밖 발생 등으로 놓친 픽을 복원한다(첫 표본 09-02 SN)."""
    db = str(bot._runtime_value("US_SWING_SHADOW_DB", "") or "")
    if not db or not Path(db).exists():
        return 0
    created = 0
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        try:
            rows = con.execute(
                """SELECT signal_date, ticker, handoff_quote_price, handoff_qty FROM signals
                   WHERE handoff_status='REHEARSAL_READY' AND signal_date >= date('now', ?)
                   ORDER BY signal_date""", (f"-{int(lookback_days)} days",)).fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning(f"[phantom] 핸드오프 원장 조회 실패(무시): {exc}")
        return 0
    closed = _closed_keys()
    for sd, tk, px, qty in rows:
        if not px or not qty:
            continue
        if (str(sd), str(tk).upper()) in closed:
            continue
        if open_from_rehearsal(bot, ticker=str(tk), qty=int(qty), quote_usd=float(px),
                               session_date=str(sd), reason="retro_from_handoff_ledger", retro=True):
            created += 1
    return created


def _closed_keys() -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    p = _ledger_path()
    if not p.exists():
        return keys
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "CLOSE":
            keys.add((str(r.get("session_date")), str(r.get("ticker")).upper()))
    return keys


def _refresh_price(bot: Any, pos: dict, price_fn: Callable | None) -> bool:
    ticker = str(pos.get("ticker") or "")
    try:
        if price_fn is not None:
            quote = price_fn(ticker)
        else:
            from kis_api import get_price
            quote = get_price(ticker, bot._token_for_market("US"), market="US", allow_fallback=False)
    except Exception as exc:
        log.debug(f"[phantom] {ticker} 시세 실패(무시): {exc}")
        return False
    px = float((quote or {}).get("price") or 0.0)
    if px <= 0:
        return False
    rate = float(getattr(bot, "usd_krw_rate", 0.0) or 0.0) or 1.0
    entry_usd = float(pos.get("display_avg_price") or 0.0)
    pos["display_current_price"] = px
    pos["current_price"] = px * rate
    pos["last_price_at"] = _now_iso()
    if entry_usd > 0:
        pnl = (px / entry_usd - 1.0) * 100.0
        if pnl > float(pos.get("peak_pnl_pct") or 0.0):
            pos["peak_pnl_pct"] = pnl
            pos["position_mfe_pct"] = pnl
            pos["peak_price_native"] = px
            pos["position_peak_price"] = px
        if pnl < float(pos.get("trough_pnl_pct") or 0.0):
            pos["trough_pnl_pct"] = pnl
            pos["position_mae_pct"] = pnl
    return True


def _close(pos: dict, *, reason: str, exit_usd: float, usd_krw: float) -> dict:
    entry_usd = float(pos.get("display_avg_price") or 0.0)
    net = (exit_usd / entry_usd - 1.0) * 100.0 if entry_usd > 0 else 0.0
    row = {"event": "CLOSE", "ts": _now_iso(), "session_date": pos.get("entry_session_date"),
           "ticker": pos.get("ticker"), "qty": pos.get("qty"), "entry_usd": entry_usd, "exit_usd": exit_usd,
           "gross_pct": round(net, 4), "reason": reason, "held_days": int(pos.get("held_days", 0) or 0),
           "peak_pnl_pct": round(float(pos.get("peak_pnl_pct") or 0.0), 4),
           "trough_pnl_pct": round(float(pos.get("trough_pnl_pct") or 0.0), 4),
           "usd_krw": usd_krw, "source": pos.get("source_strategy"), "retro": bool(pos.get("retro"))}
    _append_ledger(row)
    log.info(f"[VIRTUAL][phantom CLOSED] {row['ticker']} {reason} {net:+.2f}% "
             f"(${entry_usd:.2f}→${exit_usd:.2f}, {row['held_days']}일) — 실주문 아님")
    try:
        from telegram_reporter import send as _tg_send
        _tg_send(f"🧪 [VIRTUAL] 유령 청산 US — 실주문 없음\n{row['ticker']} {row['qty']}주 "
                 f"${entry_usd:.2f}→${exit_usd:.2f} ({net:+.2f}%) 사유 {reason} 보유 {row['held_days']}일",
                 parse_mode=None, critical=True)
    except Exception:
        pass
    return row


def evaluate(bot: Any, *, price_fn: Callable | None = None) -> dict:
    """매 사이클(US 세션 중) 호출. 실제 출구 함수로 판정 → 가상 청산. 반환: 요약 카운트."""
    positions = load_positions()
    summary = {"open": len(positions), "closed": 0, "priced": 0}
    if not positions:
        return summary
    market = str(getattr(bot, "current_market", "") or "").upper()
    current_session = ""
    try:
        current_session = str(bot._current_session_date_str("US") or "")
    except Exception:
        pass
    rate = float(getattr(bot, "usd_krw_rate", 0.0) or 0.0)
    keep: list[dict] = []
    for pos in positions:
        if current_session and pos.get("entry_session_date"):
            try:
                pos["held_days"] = int(bot._count_session_holding_days(
                    "US", str(pos["entry_session_date"]), current_session))
            except Exception:
                pass
        if market == "US" and _refresh_price(bot, pos, price_fn):
            summary["priced"] += 1
        exit_row = None
        try:
            isolated, cand = bot.risk._isolated_strategy_exit_candidate(pos)
            if isolated and isinstance(cand, dict):
                exit_row = cand
        except Exception as exc:
            log.warning(f"[phantom] 출구 평가 실패(무시) {pos.get('ticker')}: {exc}")
        if exit_row is None:
            try:
                horizon = bot._fixed_horizon_strategy_exit_candidates(positions=[pos])
                if horizon:
                    exit_row = horizon[0]
            except TypeError:
                pass  # 구버전 시그니처 — 만기 판정 생략
            except Exception as exc:
                log.warning(f"[phantom] 만기 평가 실패(무시) {pos.get('ticker')}: {exc}")
        if exit_row is not None:
            _close(pos, reason=str(exit_row.get("reason") or "unknown"),
                   exit_usd=float(pos.get("display_current_price") or 0.0), usd_krw=rate)
            summary["closed"] += 1
            continue
        keep.append(pos)
    save_positions(keep)
    summary["open"] = len(keep)
    return summary
