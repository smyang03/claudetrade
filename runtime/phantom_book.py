"""유령 포지션(phantom position) 북 — 쉐도우를 실매매처럼 (2026-09-03, 설계 정본
`docs/reports/phantom_execution_design_20260902.md` §2·§3).

② 라이브 미러: 브리지가 REHEARSAL_READY를 찍는 순간(실제 호가·수량, 제출만 차단) 실주문 포지션과 같은 dict를
   만들어 **별도 파일**(`state/phantom_positions.json`)에 둔다.
③ 전 arm: 22:36 관측기 원장(`data/shadow/arm_picks_realtime.jsonl`)의 픽을 창 안에서 KIS 호가로 유령 진입한다.
   (세션, arm, 종목) 멱등. arm 계약(tp_pct·order_krw·slots)은 원장 행에 박제돼 있어 봇이 virtual_books를 import하지 않는다.

출구: 매 틱 실제 봇의 sleeve 출구 평가(`risk._isolated_strategy_exit_candidate` — TP/SL/BE락)와 만기 판정
(`_fixed_horizon_strategy_exit_candidates(positions=)`)을 **같은 함수로** 태운다. 판정이 나면 실주문 대신 가상 청산을
원장(`data/shadow/phantom_ledger.jsonl`)에 적는다. source_strategy는 항상 "us_swing_5d"(출구 평가가 이 값을 본다),
arm은 별도 필드.

격리(원칙 2): 이 리스트는 `bot.risk.positions`에 절대 들어가지 않는다. integrity_check가 매일 격리를 검사한다.
스레드: 브리지(run_cycle)와 장중 루프가 다른 스레드일 수 있어 load→modify→save를 모듈 락으로 감싼다.
fail-silent: 이 모듈의 예외가 실주문 경로를 막으면 안 된다.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from bot.session_date import KST
from logger import get_trading_logger
from runtime_paths import get_runtime_path

log = get_trading_logger()

PHANTOM_SOURCE = "us_swing_5d"          # 출구 평가(isolated_strategy_source)가 인식하는 값
LIVE_MIRROR_ARM = "us_live_dvol"        # 브리지 REHEARSAL 픽이 속하는 arm
ENTRY_WINDOW_MIN = (5, 45)              # 개장 후 유령 진입 창(분) — 관측기 22:36 이후
_LOCK = threading.Lock()


def _state_path() -> Path:
    return get_runtime_path("state", "phantom_positions.json")


def _ledger_path() -> Path:
    return get_runtime_path("data", "shadow", "phantom_ledger.jsonl")


def _picks_ledger_path() -> Path:
    return get_runtime_path("data", "shadow", "arm_picks_realtime.jsonl")


def _entry_mark_path() -> Path:
    return get_runtime_path("state", "phantom_arm_entry_mark.json")


def load_positions() -> list[dict]:
    p = _state_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "[]")
        out = []
        for x in data:
            if isinstance(x, dict) and x.get("virtual"):
                x.setdefault("arm", LIVE_MIRROR_ARM)
                out.append(x)
        return out
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


def _tg(text: str) -> None:
    try:
        from telegram_reporter import send as _tg_send
        _tg_send(text[:3900], parse_mode=None, critical=True)
    except Exception:
        pass


def build_position(*, ticker: str, qty: int, quote_usd: float, usd_krw: float, session_date: str,
                   arm: str = LIVE_MIRROR_ARM, reason: str = "", retro: bool = False,
                   tp_pct: float = 0.12, sl_pct: float = 0.25, max_hold: int = 7,
                   book_session_date: str | None = None, decision_quote: float | None = None,
                   decision_quote_source: str | None = None) -> dict:
    """실주문 us_swing 포지션(APLD 09-02 실측 템플릿)과 같은 필드 집합 + virtual/arm 표식."""
    quote_usd = float(quote_usd or 0.0)
    rate = float(usd_krw or 0.0) or 1.0
    entry_krw = quote_usd * rate
    now = _now_iso()
    return {
        "ticker": ticker.upper(), "name": ticker.upper(), "market": "US",
        "entry": entry_krw, "qty": int(qty or 0), "current_price": entry_krw,
        "display_avg_price": quote_usd, "display_current_price": quote_usd, "display_currency": "USD",
        "price_source": "phantom_quote", "order_no": "", "strategy": "MICRO_PROBE",
        "source_strategy": PHANTOM_SOURCE, "strategy_used": "MICRO_PROBE", "source_type": "signal_entry",
        "micro_probe": True, "micro_probe_reason": "phantom",
        "original_order_cost_krw": entry_krw * int(qty or 0),
        "tp": entry_krw * (1.0 + tp_pct), "sl": entry_krw * (1.0 - sl_pct),
        "tp_pct": float(tp_pct), "sl_pct": float(sl_pct), "max_hold": int(max_hold), "held_days": 0,
        "entry_date": session_date, "session_date": session_date, "entry_session_date": session_date,
        "book_session_date": book_session_date or session_date,
        "entry_time": now, "trailing": False, "trail_sl": 0.0, "trail_pct": 0.03, "tp_triggered": False,
        "position_id": f"phantom_US_{arm}_{ticker.upper()}_{session_date}",
        "entry_route": "plan_a", "route_source": "signal_entry", "path_type": "", "pathb_path_run_id": "",
        "position_origin": "phantom", "position_integrity": "virtual", "management_protected": False,
        "position_mfe_pct": 0.0, "peak_pnl_pct": 0.0, "position_mae_pct": 0.0, "trough_pnl_pct": 0.0,
        "peak_price_native": quote_usd, "position_peak_price": quote_usd,
        "exit_owner": PHANTOM_SOURCE, "exit_policy": "isolated_strategy",
        "exit_contract": f"TP{int(round(tp_pct*100))}_SL{int(round(sl_pct*100))}_D{int(max_hold)}_BE4",
        "virtual": True, "arm": arm, "phantom_reason": reason, "retro": bool(retro), "opened_at": now,
        "last_price_at": now, "decision_quote": decision_quote, "decision_quote_source": decision_quote_source,
    }


def _open(bot: Any, pos: dict, *, reason: str, retro: bool) -> dict | None:
    key = (str(pos["entry_session_date"]), str(pos["arm"]), str(pos["ticker"]).upper())
    with _LOCK:
        positions = load_positions()
        if any((str(p.get("entry_session_date")), str(p.get("arm")), str(p.get("ticker")).upper()) == key
               for p in positions):
            return None
        positions.append(pos)
        save_positions(positions)
    _append_ledger({"event": "OPEN", "ts": pos["opened_at"], "session_date": pos["entry_session_date"],
                    "book_session_date": pos.get("book_session_date"), "arm": pos["arm"],
                    "ticker": pos["ticker"], "qty": pos["qty"], "quote_usd": pos["display_avg_price"],
                    "decision_quote": pos.get("decision_quote"), "usd_krw": float(getattr(bot, "usd_krw_rate", 0.0) or 0.0),
                    "tp_pct": pos["tp_pct"], "sl_pct": pos["sl_pct"], "reason": reason, "retro": bool(retro),
                    "note": "retro: 장중 봉우리 이력 없음(peak=entry)" if retro else ""})
    log.info(f"[VIRTUAL][phantom OPEN] {pos['arm']} {pos['ticker']} {pos['qty']}주 @ ${pos['display_avg_price']:.2f} "
             f"session={pos['entry_session_date']}{' (retro)' if retro else ''} — 실주문 아님")
    return pos


def open_from_rehearsal(bot: Any, *, ticker: str, qty: int, quote_usd: float, session_date: str,
                        reason: str = "", retro: bool = False) -> dict | None:
    """② 브리지 REHEARSAL → 라이브 미러 유령. (세션, arm, 종목) 멱등."""
    pos = build_position(ticker=ticker, qty=qty, quote_usd=quote_usd,
                         usd_krw=float(getattr(bot, "usd_krw_rate", 0.0) or 0.0),
                         session_date=session_date, arm=LIVE_MIRROR_ARM, reason=reason, retro=retro,
                         decision_quote=quote_usd, decision_quote_source="kis_rehearsal")
    return _open(bot, pos, reason=reason, retro=retro)


# ── ③ 전 arm 진입 (관측기 원장 → KIS 호가) ─────────────────────────────────────
def _read_picks_for_session(session_date: str) -> list[dict]:
    p = _picks_ledger_path()
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if str(r.get("session_date")) == str(session_date) and r.get("arm") and r.get("ticker"):
            rows.append(r)
    return rows


def _entry_mark(session_date: str) -> bool:
    p = _entry_mark_path()
    try:
        return p.exists() and json.loads(p.read_text(encoding="utf-8")).get("session_date") == session_date
    except Exception:
        return False


def _set_entry_mark(session_date: str, n: int) -> None:
    p = _entry_mark_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"session_date": session_date, "opened": n, "ts": _now_iso()}), encoding="utf-8")


def _quotes(bot: Any, tickers: list[str], price_fn: Callable | None) -> tuple[dict[str, float], str]:
    """유니크 종목당 1회. KIS 실패 시 yfinance 배치 폴백. 반환 (quotes, source)."""
    want = sorted({str(t).upper() for t in tickers if t})
    out: dict[str, float] = {}
    src = "kis"
    for t in want:
        try:
            if price_fn is not None:
                q = price_fn(t)
            else:
                from kis_api import get_price
                q = get_price(t, bot._token_for_market("US"), market="US", allow_fallback=False)
            px = float((q or {}).get("price") or 0.0)
            if px > 0:
                out[t] = px
        except Exception as exc:
            log.debug(f"[phantom] {t} KIS 시세 실패: {exc}")
    missing = [t for t in want if t not in out]
    if missing and price_fn is None:
        try:
            import yfinance as yf
            data = yf.download(missing, period="1d", interval="1m", progress=False, group_by="ticker", threads=True)
            for t in missing:
                try:
                    df = data[t] if len(missing) > 1 else data
                    close = df["Close"].dropna()
                    if len(close):
                        out[t] = float(close.iloc[-1])
                        src = "kis+yfinance"
                except Exception:
                    continue
        except Exception:
            pass
    return out, src


def open_arm_picks_from_ledger(bot: Any, *, session_date: str, price_fn: Callable | None = None,
                               minutes_since_open: float | None = None) -> dict:
    """22:36 관측기 원장의 픽을 유령 진입. 세션당 1회(마커). 라이브 미러 arm은 브리지가 만든 행과 중복 방지."""
    summary = {"session_date": session_date, "candidates": 0, "opened": 0, "skipped": {}, "quote_source": None}
    if _entry_mark(session_date):
        return summary
    if minutes_since_open is not None and not (ENTRY_WINDOW_MIN[0] <= minutes_since_open <= ENTRY_WINDOW_MIN[1]):
        if minutes_since_open > ENTRY_WINDOW_MIN[1] and not getattr(bot, "_phantom_ledger_warned", False):
            try:
                setattr(bot, "_phantom_ledger_warned", True)
            except Exception:
                pass
            if not _read_picks_for_session(session_date):
                log.warning(f"[VIRTUAL][phantom] {session_date} 관측기 원장(arm_picks) 행 없음 — 22:36 스케줄 작업 미실행 의심")
        return summary
    rows = _read_picks_for_session(session_date)
    if not rows:
        return summary
    try:
        from runtime.virtual_overrides import load_overrides, arm_state
        overrides = load_overrides()
    except Exception:
        overrides, arm_state = {}, (lambda a, o=None: "active")  # type: ignore
    summary["candidates"] = len(rows)
    quotes, src = _quotes(bot, [r["ticker"] for r in rows], price_fn)
    summary["quote_source"] = src
    rate = float(getattr(bot, "usd_krw_rate", 0.0) or 0.0) or 1.0
    open_by_arm: dict[str, int] = {}
    for p in load_positions():
        open_by_arm[str(p.get("arm"))] = open_by_arm.get(str(p.get("arm")), 0) + 1
    opened: list[str] = []
    for r in sorted(rows, key=lambda x: (x["arm"], int(x.get("pick_pos") or 0))):
        arm, t = str(r["arm"]), str(r["ticker"]).upper()
        if arm == LIVE_MIRROR_ARM:
            # 라이브 미러는 실제 봇의 REHEARSAL(브리지)만이 진입 근거다. 관측기 원장으로 만들면
            # 브리지가 BLOCKED(시가 이탈·슬롯·창)한 날에도 유령이 생겨 "실제 봇 결정"이 아니게 된다.
            summary["skipped"][f"{arm}:{t}"] = "live_mirror_bridge_only"
            continue
        if arm_state(arm, overrides) != "active":
            summary["skipped"][arm] = "override:" + arm_state(arm, overrides)
            continue
        slots = int(r.get("slots") or 7)
        if open_by_arm.get(arm, 0) >= slots:
            summary["skipped"][f"{arm}:{t}"] = "slots_full"
            continue
        px = quotes.get(t)
        if not px:
            summary["skipped"][f"{arm}:{t}"] = "no_quote"
            continue
        order_krw = float(r.get("order_krw") or 540_000.0)
        qty = int(math.floor(order_krw / (px * rate))) if px * rate > 0 else 0
        if qty <= 0:
            summary["skipped"][f"{arm}:{t}"] = "qty_zero"
            continue
        pos = build_position(ticker=t, qty=qty, quote_usd=px, usd_krw=rate, session_date=session_date, arm=arm,
                             reason="arm_picks_ledger", tp_pct=float(r.get("tp_pct") or 0.12),
                             sl_pct=float(r.get("sl_pct") or 0.25), book_session_date=r.get("book_session_date"),
                             decision_quote=r.get("quote"), decision_quote_source=r.get("quote_source"))
        if _open(bot, pos, reason="arm_picks_ledger", retro=False):
            opened.append(f"{arm}:{t}")
            open_by_arm[arm] = open_by_arm.get(arm, 0) + 1
    summary["opened"] = len(opened)
    transient = any(v == "no_quote" for v in summary["skipped"].values())
    if opened or not transient:
        _set_entry_mark(session_date, len(opened))   # 시세 결손(no_quote)만 남았으면 다음 틱 재시도
    else:
        log.warning(f"[VIRTUAL][phantom ARM ENTRY] {session_date} 시세 결손으로 진입 0 — 다음 틱 재시도")
    log.info(f"[VIRTUAL][phantom ARM ENTRY] {session_date} 후보 {len(rows)} → 진입 {len(opened)} "
             f"(시세 {src}, skip {len(summary['skipped'])})")
    if opened:
        _tg(f"🧪 [VIRTUAL] 유령 진입 다이제스트 {session_date} — 실주문 없음\n"
            f"{len(opened)}건 (시세 {src}): " + ", ".join(opened[:40]))
    return summary


def ensure_from_handoff_ledger(bot: Any, *, lookback_days: int = 10) -> int:
    """브리지 원장(signals.handoff_status=REHEARSAL_READY)에 있는데 유령이 없는 픽을 소급 생성."""
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
        if (str(sd), LIVE_MIRROR_ARM, str(tk).upper()) in closed:
            continue
        if open_from_rehearsal(bot, ticker=str(tk), qty=int(qty), quote_usd=float(px),
                               session_date=str(sd), reason="retro_from_handoff_ledger", retro=True):
            created += 1
    return created


def _closed_keys() -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    p = _ledger_path()
    if not p.exists():
        return keys
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("event") == "CLOSE":
            keys.add((str(r.get("session_date")), str(r.get("arm") or LIVE_MIRROR_ARM), str(r.get("ticker")).upper()))
    return keys


def _apply_price(pos: dict, px: float, rate: float) -> None:
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


def _close_row(pos: dict, *, reason: str, exit_usd: float, usd_krw: float) -> dict:
    entry_usd = float(pos.get("display_avg_price") or 0.0)
    net = (exit_usd / entry_usd - 1.0) * 100.0 if entry_usd > 0 else 0.0
    row = {"event": "CLOSE", "ts": _now_iso(), "session_date": pos.get("entry_session_date"),
           "book_session_date": pos.get("book_session_date"), "arm": pos.get("arm", LIVE_MIRROR_ARM),
           "ticker": pos.get("ticker"), "qty": pos.get("qty"), "entry_usd": entry_usd, "exit_usd": exit_usd,
           "gross_pct": round(net, 4), "reason": reason, "held_days": int(pos.get("held_days", 0) or 0),
           "peak_pnl_pct": round(float(pos.get("peak_pnl_pct") or 0.0), 4),
           "trough_pnl_pct": round(float(pos.get("trough_pnl_pct") or 0.0), 4),
           "tp_pct": pos.get("tp_pct"), "sl_pct": pos.get("sl_pct"),
           "usd_krw": usd_krw, "source": pos.get("source_strategy"), "retro": bool(pos.get("retro"))}
    _append_ledger(row)
    log.info(f"[VIRTUAL][phantom CLOSED] {row['arm']} {row['ticker']} {reason} {net:+.2f}% "
             f"(${entry_usd:.2f}→${exit_usd:.2f}, {row['held_days']}일) — 실주문 아님")
    return row


def evaluate(bot: Any, *, price_fn: Callable | None = None) -> dict:
    """매 틱(US 세션 중) 호출. 유니크 종목당 시세 1회 → 실제 출구 함수로 판정 → 가상 청산 다이제스트."""
    with _LOCK:
        positions = load_positions()
    summary = {"open": len(positions), "closed": 0, "priced": 0, "quote_ms": 0}
    if not positions:
        return summary
    market = str(getattr(bot, "current_market", "") or "").upper()
    current_session = ""
    try:
        current_session = str(bot._current_session_date_str("US") or "")
    except Exception:
        pass
    rate = float(getattr(bot, "usd_krw_rate", 0.0) or 0.0) or 1.0
    quotes: dict[str, float] = {}
    if market == "US":
        t0 = datetime.now()
        quotes, _src = _quotes(bot, [p.get("ticker") for p in positions], price_fn)
        summary["quote_ms"] = int((datetime.now() - t0).total_seconds() * 1000)
        summary["priced"] = len(quotes)
    keep: list[dict] = []
    closed_rows: list[dict] = []
    for pos in positions:
        if current_session and pos.get("entry_session_date"):
            try:
                pos["held_days"] = int(bot._count_session_holding_days(
                    "US", str(pos["entry_session_date"]), current_session))
            except Exception:
                pass
        px = quotes.get(str(pos.get("ticker")).upper())
        if px:
            _apply_price(pos, px, rate)
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
                pass
            except Exception as exc:
                log.warning(f"[phantom] 만기 평가 실패(무시) {pos.get('ticker')}: {exc}")
        if exit_row is not None:
            closed_rows.append(_close_row(pos, reason=str(exit_row.get("reason") or "unknown"),
                                          exit_usd=float(pos.get("display_current_price") or 0.0), usd_krw=rate))
            continue
        keep.append(pos)
    with _LOCK:
        # 락 밖에서 평가하는 동안 새로 열린 포지션(브리지)이 있으면 보존
        latest = load_positions()
        known = {(str(p.get("entry_session_date")), str(p.get("arm")), str(p.get("ticker")).upper()) for p in positions}
        extra = [p for p in latest if (str(p.get("entry_session_date")), str(p.get("arm")), str(p.get("ticker")).upper()) not in known]
        save_positions(keep + extra)
    summary["closed"] = len(closed_rows)
    summary["open"] = len(keep)
    if closed_rows:
        _tg(f"🧪 [VIRTUAL] 유령 청산 다이제스트 — 실주문 없음\n" + "\n".join(
            f"{r['arm']} {r['ticker']} {r['reason']} {r['gross_pct']:+.2f}% (${r['entry_usd']:.2f}→${r['exit_usd']:.2f}, {r['held_days']}일)"
            for r in closed_rows[:40]))
    if market == "US":
        log.info(f"[VIRTUAL][phantom tick] open={summary['open']} priced={summary['priced']} "
                 f"closed={summary['closed']} quote_ms={summary['quote_ms']}")
    return summary
