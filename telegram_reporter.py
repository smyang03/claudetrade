"""
telegram_reporter.py
Telegram reporting helpers for trading bot alerts.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from credit_tracker import summary as credit_summary
from logger import get_trading_logger
from bot.log_sanitizer import mask_secrets

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent

TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

_IS_PAPER = str(os.getenv("KIS_IS_PAPER", "true")).strip().lower() != "false"
MODE_LABEL = "모의" if _IS_PAPER else "실전"


def _env_flag(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in ("1", "true", "yes", "on", "y")

_MODE_KO_MAP = {
    "AGGRESSIVE": "공격매수",
    "MODERATE_BULL": "강한상승",
    "Bull_Confirmed": "상승확인",
    "MILD_BULL": "완만상승",
    "CAUTIOUS": "주의",
    "NEUTRAL": "중립",
    "MILD_BEAR": "완만약세",
    "CAUTIOUS_BEAR": "주의약세",
    "DEFENSIVE": "방어",
    "HALT": "거래중지",
    "SIDEWAYS_BEAR": "횡보약세",
    "SIDEWAYS_BULL": "횡보강세",
}

_STRATEGY_KO_MAP = {
    "volatility_breakout": "변동성돌파",
    "gap_pullback": "갭눌림",
    "momentum": "모멘텀",
    "mean_reversion": "평균회귀",
    "broker_sync": "브로커동기화",
}

_EVENT_KO_MAP = {
    "entry_signal": "진입신호",
    "signal_blocked": "신호차단",
    "entry_skip": "진입보류",
    "buy_filled": "매수 체결",
    "buy_order": "매수 주문",
    "buy_failed": "매수 실패",
    "sell_filled": "매도 체결",
    "sell_failed": "매도 실패",
    "signal_check": "신호 없음",
    "waiting": "대기",
    "trailing": "추적중",
    "pending_order": "미체결 주문",
    "live_position": "보유 반영",
}

_REASON_KO_MAP = {
    "already_holding": "이미 보유중",
    "pending_order": "미체결 주문",
    "signal_blocked": "모드 차단",
    "entry_failed": "주문 실패",
    "stop_loss": "손절",
    "take_profit": "익절",
    "trail_stop": "추적손절",
    "max_hold": "보유 기간 만료",
    "session_close": "장마감 청산",
    "live_position": "브로커 잔고 반영",
    "precheck_failed": "사전체크 실패",
    "us_order_blocked": "미국 주문 차단",
    "budget_exhausted": "예산 소진",
    "max_positions": "최대 포지션 도달",
    "HALT": "거래중지 모드",
    "DEFENSIVE": "방어 모드",
}


def _ko_mode(v: str) -> str:
    return _MODE_KO_MAP.get(v or "", v or "-")


def _ko_strategy(v: str) -> str:
    return _STRATEGY_KO_MAP.get(v or "", v or "-")


def _ko_event(v: str) -> str:
    return _EVENT_KO_MAP.get(v or "", v or "-")


def _ko_reason(v: str) -> str:
    return _REASON_KO_MAP.get(v or "", v or "-")


def _latest_name_map(market: str) -> dict[str, str]:
    log_dir = BASE_DIR / "logs" / "daily_judgment"
    name_map: dict[str, str] = {}
    try:
        paths = sorted(log_dir.glob(f"*_{market}.json"))
    except Exception:
        paths = []
    for path in reversed(paths):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        technicals = ((rec.get("digest_raw") or {}).get("technicals") or {})
        for ticker, info in technicals.items():
            t = str(ticker or "").strip().upper()
            name = str((info or {}).get("name", "") or "").strip()
            if t and name and name != t:
                name_map[t] = name
        if name_map:
            break
    return name_map


def _display_ticker(ticker: str, market: str = "KR", name: str = "") -> str:
    raw_ticker = str(ticker or "").strip().upper()
    raw_name = str(name or "").strip()
    mapped = _latest_name_map(market).get(raw_ticker, "") if raw_ticker else ""
    final_name = raw_name or mapped
    if raw_ticker and final_name and final_name != raw_ticker:
        return f"{final_name} ({raw_ticker})"
    return raw_ticker or final_name or "-"


def display_ticker(ticker: str, market: str = "KR", name: str = "") -> str:
    return _display_ticker(ticker, market, name)


def _path_label(value: str = "") -> str:
    key = str(value or "").strip().lower()
    if key in {"path_b", "claude_price", "b"}:
        return "B플랜 | Claude 지정가"
    if key in {"path_a", "timing_adapter", "a"}:
        return "A플랜 | Timing Adapter"
    return ""


def _path_line(value: str = "") -> str:
    label = _path_label(value)
    return f"매수 경로: {label}" if label else ""


def _risk_status_label(value: float) -> str:
    if value <= 0:
        return ""
    if value < 15:
        return "공포"
    if value < 20:
        return "정상"
    if value < 25:
        return "주의"
    if value < 30:
        return "고변동"
    return "위기"


def send(text: str, parse_mode: str = "HTML") -> bool:
    if not TOKEN or not CHAT_ID:
        log.debug(f"[텔레그램 비활성] {text[:80]}")
        return False
    for attempt in range(2):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
                timeout=20,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            if attempt == 0:
                log.debug(f"텔레그램 전송 재시도: {mask_secrets(e)}")
                time.sleep(3)
            else:
                log.error(f"텔레그램 전송 실패: {mask_secrets(e)}")
    return False


def fmt_pct(v: float) -> str:
    icon = "🟢" if v > 0 else "🔴" if v < 0 else "⚪"
    return f"{icon} {v:+.2f}%"


def _credit_line() -> str:
    try:
        cr = credit_summary()
        td = cr["today"]
        tot = cr["total"]
        return (
            f"💸 AI 크레딧 | 오늘 ${td['cost_usd']:.3f} (₩{td['cost_krw']:,}) / "
            f"누적 ${tot['cost_usd']:.3f} (₩{tot['cost_krw']:,})"
        )
    except Exception:
        return ""


def _display_price(value: float, market: str) -> str:
    if market == "US":
        return f"${float(value or 0):.4f}"
    return f"{int(float(value or 0)):,}원"


def _position_line(pos: dict, market: str) -> str:
    entry = float(pos.get("display_avg_price", pos.get("avg_price", pos.get("entry", 0))) or 0)
    current = float(pos.get("display_current_price", pos.get("current_price", pos.get("eval_price", 0))) or 0)
    qty = pos.get("qty", 0)
    pnl_pct = (current / entry - 1) * 100 if entry > 0 and current > 0 else 0.0
    return (
        f"  {_display_ticker(pos.get('ticker', '-'), market, pos.get('name', '') or '')} "
        f"{qty}주 | 매수가 {_display_price(entry, market)} | "
        f"현재가 {_display_price(current, market)} | 수익 {pnl_pct:+.2f}%"
    )


def morning_briefing(
    market: str,
    judgments: dict,
    consensus: dict,
    balance: dict,
    digest: dict,
    round1_judgments: dict = None,
    debate_changes: list = None,
) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    bull = judgments["bull"]
    bear = judgments["bear"]
    neut = judgments["neutral"]
    mode = consensus["mode"]
    size = consensus["size"]
    cash = int(balance.get("cash", 0) or 0)
    credit_line = _credit_line()
    ctx = (digest or {}).get("context", {}) or {}
    risk_label = "VKOSPI" if market == "KR" else "VIX"
    risk_value = float(ctx.get("vix", 0) or 0)
    risk_line = f"\n📣 {risk_label}: {risk_value:.1f} ({_risk_status_label(risk_value)})" if risk_value > 0 else ""

    r1_section = ""
    if round1_judgments:
        def _r1_line(atype: str, icon: str) -> str:
            j = round1_judgments.get(atype, {})
            return f"{icon} {j.get('stance','-')} ({int(j.get('confidence',0)*100)}%): {j.get('key_reason','')[:60]}"

        r1_section = (
            f"\n🧭 <b>1라운드 판단</b>\n"
            f"{_r1_line('bull','🟢')}\n"
            f"{_r1_line('bear','🔴')}\n"
            f"{_r1_line('neutral','⚪')}\n"
        )

    debate_section = ""
    if debate_changes:
        lines = []
        icon_map = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
        for c in debate_changes:
            icon = icon_map.get(c.get("analyst"), "⚪")
            lines.append(
                f"  {icon} {str(c.get('analyst', '')).upper()}: "
                f"{c.get('r1_stance', '-')} → <b>{c.get('r2_stance', '-')}</b>"
            )
        debate_section = f"\n🗣 <b>2라운드 변경</b>\n" + "\n".join(lines) + "\n"

    text = (
        f"━━━━━━━━━━━\n"
        f"🌅 <b>[{MODE_LABEL}][세션 시작 브리핑] {now}</b>\n"
        f"━━━━━━━━━━━\n"
        f"{r1_section}{debate_section}"
        f"🎯 <b>최종 합의: {_ko_mode(mode)}</b>  비중 {size}%\n"
        f"\n🧠 <b>최종 판단</b>\n"
        f"🟢 Bull ({int(bull['confidence']*100)}%): {bull['key_reason']}\n"
        f"🔴 Bear ({int(bear['confidence']*100)}%): {bear['key_reason']}\n"
        f"⚪ Neutral ({int(neut['confidence']*100)}%): {neut['key_reason']}\n"
        f"\n💰 예수금 {cash:,}원{risk_line}\n"
        f"{credit_line}\n"
        f"📣 다음 재판단: 30분 후\n"
        f"━━━━━━━━━━━"
    )
    send(text)
    return text


def tuning_report(elapsed_min: int, tuning_result: dict, prev_mode: str, positions: list) -> str:
    action = tuning_result.get("action", "MAINTAIN")
    new_mode = tuning_result.get("mode", prev_mode)
    changed = action != "MAINTAIN"
    icon = "🔄" if changed else "⏸️"
    prev_mode_disp = _ko_mode(prev_mode)
    new_mode_disp = _ko_mode(new_mode)
    pos_txt = "\n".join(
        [
            f"  {_display_ticker(p.get('ticker', '-'), 'US' if str(p.get('ticker', '')).replace('.', '').isalpha() else 'KR', p.get('name', '') or '')} "
            f"{p.get('qty', 0)}주 현재 {int(float(p.get('current_price', 0) or 0)):,}원"
            for p in positions
        ]
    ) or "  없음"
    line = (
        f"이전: {prev_mode_disp} → 현재: <b>{new_mode_disp}</b>"
        if changed else
        f"유지: <b>{prev_mode_disp}</b>"
    )
    text = (
        f"━━━━━━━━━━━\n"
        f"{icon} <b>[{MODE_LABEL}][{elapsed_min}분 튜닝]</b>\n"
        f"{line}\n"
        f"사유: {tuning_result.get('reason', '')}\n"
        f"보유:\n{pos_txt}\n"
        f"━━━━━━━━━━━"
    )
    send(text)
    return text


def emergency_alert(msg: str) -> str:
    text = f"🚨 <b>[{MODE_LABEL}][긴급 알림]</b>\n{msg}"
    send(text)
    return text


def system_alert(
    title: str,
    lines: Optional[List[str]] = None,
    icon: str = "🤖",
) -> str:
    if not _env_flag("TG_SYSTEM_ALERTS_ENABLED", False):
        return ""
    body = "\n".join([str(x) for x in (lines or []) if str(x).strip()]) or "내용 없음"
    text = (
        f"{icon} <b>[{title}]</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{body}"
    )
    send(text)
    return text


def analyst_reinvoke_alert(
    market: str,
    trigger: str,
    old_mode: str,
    new_mode: str,
    judgments: dict,
    consensus: dict,
    round1_judgments: dict = None,
    debate_changes: list = None,
) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    size = consensus.get("size", 0)
    mode_line = (
        f"{_ko_mode(old_mode)} → <b>{_ko_mode(new_mode)}</b>"
        if old_mode != new_mode else
        f"<b>{_ko_mode(new_mode)}</b> (유지)"
    )
    trigger_ko = {"schedule": "정시", "threshold": "급변"}.get(trigger, trigger)

    r1_section = ""
    if round1_judgments:
        def _r1(atype: str, icon: str) -> str:
            j = round1_judgments.get(atype, {})
            return f"{icon} {j.get('stance','-')} ({int(j.get('confidence',0)*100)}%): {j.get('key_reason','')[:50]}"

        r1_section = (
            f"\n🧭 <b>1라운드</b>\n"
            f"{_r1('bull','🟢')}\n"
            f"{_r1('bear','🔴')}\n"
            f"{_r1('neutral','⚪')}\n"
        )

    debate_section = ""
    if debate_changes:
        lines = []
        icon_map = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
        for c in debate_changes:
            icon = icon_map.get(c.get("analyst"), "⚪")
            lines.append(f"  {icon} {str(c.get('analyst', '')).upper()}: {c.get('r1_stance', '-')} → <b>{c.get('r2_stance', '-')}</b>")
        debate_section = f"\n🗣 <b>2라운드 변경</b>\n" + "\n".join(lines) + "\n"

    text = (
        f"━━━━━━━━━━━\n"
        f"🔁 <b>[{MODE_LABEL}][{market} {trigger_ko} 재판단] {now}</b>\n"
        f"━━━━━━━━━━━\n"
        f"모드: {mode_line}\n"
        f"사이즈: {size}%"
        f"{r1_section}{debate_section}\n"
        f"🟢 Bull ({int(bull.get('confidence', 0) * 100)}%): {bull.get('key_reason', '')}\n"
        f"🔴 Bear ({int(bear.get('confidence', 0) * 100)}%): {bear.get('key_reason', '')}\n"
        f"⚪ Neutral ({int(neut.get('confidence', 0) * 100)}%): {neut.get('key_reason', '')}\n"
        f"━━━━━━━━━━━"
    )
    send(text)
    return text


def signal_alert(
    event: str,
    market: str,
    ticker: str,
    strategy: str = "",
    price: float = 0,
    reason: str = "",
    mode: str = "",
    qty: int = 0,
    order_cost_krw: int = 0,
) -> str:
    now = datetime.now(KST).strftime("%H:%M:%S")
    ticker_disp = _display_ticker(ticker, market)
    mkt_icon = "🇰🇷" if market == "KR" else "🇺🇸"
    price_str = f"{int(price):,}원" if market == "KR" and price > 0 else f"${price:.2f}" if market == "US" and price > 0 else ""
    if event == "entry_signal":
        cost_str = f"  주문금액 {order_cost_krw:,}원" if order_cost_krw else ""
        text = (
            f"🟢 <b>[{MODE_LABEL}][진입신호] {ticker_disp}</b>  {mkt_icon}{market} {now}\n"
            f"전략: {_ko_strategy(strategy)}  가격 {price_str}{cost_str}\n"
            f"모드: {_ko_mode(mode)}"
        )
    elif event == "signal_blocked":
        text = (
            f"⛔ <b>[{MODE_LABEL}][신호차단] {ticker_disp}</b>  {mkt_icon}{market} {now}\n"
            f"전략: {_ko_strategy(strategy)}  가격 {price_str}\n"
            f"모드: <b>{_ko_mode(mode)}</b> | {_ko_reason(reason)}"
        )
    elif event == "entry_skip" and reason == "already_holding":
        text = (
            f"⏭ <b>[{MODE_LABEL}][보유중 스킵] {ticker_disp}</b>  {mkt_icon}{market} {now}\n"
            f"신호: {_ko_strategy(strategy)}  가격 {price_str}\n"
            f"{_ko_reason(reason)} | 중복 진입 차단"
        )
    else:
        return ""
    send(text)
    return text


def signal_state_alert(
    event: str,
    market: str,
    ticker: str,
    strategy: str = "",
    price: float = 0.0,
    reason: str = "",
    mode: str = "",
    summary: str = "",
    order_cost_krw: int = 0,
    name: str = "",
) -> str:
    if not _env_flag("TG_SIGNAL_STATE_ENABLED", False):
        return ""
    ticker_disp = _display_ticker(ticker, market, name)
    text_market = "🇰🇷KR" if market == "KR" else "🇺🇸US"
    now = datetime.now(KST).strftime("%H:%M:%S")
    price_str = _display_price(price, market) if price > 0 else "-"
    ko_strategy = _ko_strategy(strategy)
    ko_mode = _ko_mode(mode)
    ko_reason = _ko_reason(reason)

    if event == "entry_signal":
        detail = [
            f"전략: {ko_strategy} | 가격 {price_str}" + (f" | 주문금액 {order_cost_krw:,}원" if order_cost_krw else ""),
            f"모드: {ko_mode}",
        ]
        return block_alert("진입 신호", [f"{ticker_disp} {text_market} {now}", *detail], market="", icon="🟢")
    if event == "signal_blocked":
        detail = [
            f"전략: {ko_strategy} | 가격 {price_str}",
            f"모드: {ko_mode} | 사유: {ko_reason}",
        ]
        return block_alert("신호 차단", [f"{ticker_disp} {text_market} {now}", *detail], market="", icon="⛔")
    if event == "signal_check":
        detail = [f"{ticker_disp} {text_market} {now}", f"모드: {ko_mode}"]
        if summary:
            detail.append(summary)
        return block_alert("신호 없음", detail, market="", icon="⚪")
    if event == "entry_skip":
        detail = [
            f"{ticker_disp} {text_market} {now}",
            f"사유: {ko_reason} | 가격 {price_str}",
            f"모드: {ko_mode}",
        ]
        if summary:
            detail.append(summary)
        return block_alert("진입 보류", detail, market="", icon="⏭")
    return ""


def block_alert(
    title: str,
    lines: Optional[List[str]] = None,
    market: str = "",
    icon: str = "📌",
    show_time: bool = False,
) -> str:
    now = f" {datetime.now(KST).strftime('%H:%M')}" if show_time else ""
    market_txt = f" {market}" if market else ""
    body = "\n".join([str(x) for x in (lines or []) if str(x).strip()]) or "내용 없음"
    text = (
        f"{icon} <b>[{MODE_LABEL}][{title}{market_txt}]</b>{now}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{body}"
    )
    send(text)
    return text


def buy_order_alert(
    market: str,
    ticker: str,
    qty: int,
    order_no: str = "",
    detail: str = "",
    name: str = "",
    buy_path: str = "",
) -> str:
    ticker_disp = _display_ticker(ticker, market, name)
    lines = [f"{ticker_disp} | {qty}주"]
    path = _path_line(buy_path)
    if path:
        lines.append(path)
    if order_no:
        lines.append(f"주문번호 {order_no}")
    if detail:
        lines.append(detail)
    return block_alert("매수 주문 접수", lines, market=market, icon="🟡")


def fill_confirm_alert(
    market: str,
    ticker: str,
    qty: int,
    order_no: str = "",
    price: float = 0.0,
    source: str = "",
    name: str = "",
    usd_krw: float = 0.0,
    buy_path: str = "",
) -> str:
    ticker_disp = _display_ticker(ticker, market, name)
    price_txt = _display_price(price, market) if price > 0 else "-"
    if market == "US":
        rate = float(usd_krw or 0) or _env_usd_krw_rate()
        if price > 0 and rate > 0:
            price_txt += f" (약 {int(round(price * rate)):,}원)"
    lines = [f"{ticker_disp} | {qty}주", f"체결가 {price_txt}" + (f" ({source})" if source else "")]
    path = _path_line(buy_path)
    if path:
        lines.append(path)
    if order_no:
        lines.insert(1, f"주문번호 {order_no}")
    return block_alert("매수 체결 확인", lines, market=market, icon="🟡")


def trailing_alert(
    market: str,
    ticker: str,
    action: str,
    trail_pct: float = 0.0,
    detail: str = "",
    name: str = "",
) -> str:
    ticker_disp = _display_ticker(ticker, market, name)
    title_map = {
        "sell": "TP→분석가 SELL",
        "hold": "TP→분석가 HOLD",
        "trail": "TP 도달 → 트레일링",
    }
    lines = [ticker_disp]
    if trail_pct > 0:
        lines.append(f"트레일링 스탑 {trail_pct*100:.1f}% 활성화")
    if detail:
        lines.append(detail)
    return block_alert(title_map.get(action, "트레일링"), lines, market=market, icon="📈" if action != "sell" else "🔴")


def watchlist_change_alert(
    market: str,
    removed: List[str],
    added: List[str],
    reason: str = "",
) -> str:
    lines = []
    if removed:
        lines.append("OUT: " + ", ".join(removed))
    if added:
        lines.append("IN: " + ", ".join(added))
    if reason:
        lines.append(f"사유: {reason}")
    return block_alert("종목 교체", lines, market=market, icon="🔁")


def watchlist_alert(
    market: str,
    mode: str,
    tickers: list,
    reasons: dict = None,
    excluded: list = None,
    trigger: str = "session_open",
    mode_order_limit_krw: float = 0.0,
) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🇰🇷" if market == "KR" else "🇺🇸"
    reasons = reasons or {}
    trigger_label = {
        "session_open": "오늘의 모니터링 종목",
        "rescreen": "종목 재선정",
        "reuse": "재판단 후 종목 유지",
    }.get(trigger, "모니터링 종목")
    ticker_lines = []
    for t in tickers:
        r = reasons.get(t, "")
        t_disp = _display_ticker(t, market)
        ticker_lines.append(f"  • <b>{t_disp}</b>  {r}" if r else f"  • <b>{t_disp}</b>")
    excl_line = ""
    if excluded:
        excl_line = f"\n제외: {', '.join(excluded[:10])}" + ("..." if len(excluded) > 10 else "")
    text = (
        f"{icon} <b>[{MODE_LABEL}][{market} {trigger_label}]</b>  {now}\n"
        f"━━━━━━━━\n"
        f"모드: <b>{_ko_mode(mode)}</b>"
        + (f" | 최대매수 {int(mode_order_limit_krw):,}원" if mode_order_limit_krw and mode_order_limit_krw > 0 else "")
        + "\n"
        f"━━━━━━━━\n"
        + "\n".join(ticker_lines)
        + excl_line
    )
    send(text)
    return text


def _clean_postmortem_lesson(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    placeholders = (
        "오류로 자동 판정",
        "API 오류로 자동 판정",
        "postmortem 응답 실패",
        "자동 판정",
        "응답 실패",
    )
    return "" if any(p in text for p in placeholders) else text


def daily_summary(
    date: str,
    market: str,
    pnl_pct: float,
    pnl_krw: int,
    trades: int,
    win_rate: float,
    cumulative: float,
    judgments: dict,
    postmortem: dict,
) -> str:
    icon = "🇰🇷" if market == "KR" else "🇺🇸"
    credit_line = _credit_line()
    lesson = _clean_postmortem_lesson(postmortem.get("key_lesson", ""))
    lesson_block = (
        f"오늘의 교훈:\n"
        f"  {lesson}\n\n"
    ) if lesson else ""
    text = (
        f"━━━━━━━━━━━\n"
        f"{icon} <b>[{MODE_LABEL}][일일 결산] {date} {market}</b>\n"
        f"━━━━━━━━━━━\n"
        f"오늘: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"거래: {trades}건  승률: {win_rate*100:.1f}%\n"
        f"누적: {cumulative:,.0f}원\n\n"
        f"판단 결과\n"
        f"  Bull:    {postmortem.get('bull_result', '?')}\n"
        f"  Bear:    {postmortem.get('bear_result', '?')}\n"
        f"  Neutral: {postmortem.get('neutral_result', '?')}\n\n"
        f"{lesson_block}"
        f"{credit_line}\n"
        f"━━━━━━━━━━━"
    )
    send(text)
    return text


def decision_event_alert(event: dict) -> str:
    raw_event = str(event.get("event") or event.get("action") or "")
    event_name = _ko_event(raw_event)
    market = str(event.get("market", "KR"))
    ticker = _display_ticker(str(event.get("ticker", "-")), market, str(event.get("name", "") or ""))
    strategy = _ko_strategy(str(event.get("strategy", "") or ""))
    mode = _ko_mode(str(event.get("mode", "") or ""))
    detail = str(event.get("detail", "") or "")
    reason = _ko_reason(str(event.get("reason", "") or ""))
    price = float(event.get("price", 0) or 0)
    if price <= 0:
        price_native = float(event.get("price_native", 0) or 0)
        price_krw = float(event.get("price_krw", 0) or 0)
        if market == "US":
            if price_native > 0:
                price = price_native
            elif price_krw > 0:
                rate = _env_usd_krw_rate()
                price = price_krw / rate if rate > 0 else 0.0
        else:
            price = price_krw if price_krw > 0 else price_native
    pnl_pct = float(event.get("pnl_pct", 0) or 0)
    pnl_krw = int(float(event.get("pnl_krw", 0) or 0))
    order_no = str(event.get("order_no", "") or "")
    price_str = _display_price(price, market) if price > 0 else "-"
    extra = []
    if strategy and strategy != "-":
        extra.append(f"전략: {strategy}")
    if mode and mode != "-":
        extra.append(f"모드: {mode}")
    if reason and reason != "-":
        extra.append(f"사유: {reason}")
    if detail:
        extra.append(detail)
    if order_no:
        extra.append(f"주문번호: {order_no}")
    if pnl_pct or pnl_krw:
        extra.append(f"손익: {pnl_pct:+.2f}% / {pnl_krw:+,}원")
    text = (
        f"📌 <b>[{event_name}]</b> {ticker}\n"
        f"시장: {market} | 가격: {price_str}\n"
        + ("\n".join(extra) if extra else "")
    )
    send(text)
    return text


def status_report(
    market: str,
    mode: str,
    positions: list,
    budget_avail: float,
    cash: float,
    tickers: list = None,
    stock_value_krw: float = 0.0,
    total_equity_krw: float = 0.0,
    mode_order_limit_krw: float = 0.0,
) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    pos_lines = "\n".join(_position_line(p, market) for p in positions) if positions else "  없음"
    watch = f"\n감시종목: {', '.join(_display_ticker(t, market) for t in (tickers or []))}" if tickers else ""
    text = (
        f"📣 <b>[{MODE_LABEL}][상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{_ko_mode(mode)}</b>\n"
        f"보유 포지션:\n{pos_lines}\n"
        f"예산 가능: {budget_avail:,.0f}원 | 현금: {cash:,.0f}원 | "
        f"주식평가: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원"
        + (f" | 최대매수 {int(mode_order_limit_krw):,}원" if mode_order_limit_krw and mode_order_limit_krw > 0 else "")
        + watch
    )
    send(text)
    return text


def dashboard_push(
    market: str,
    mode: str,
    positions: list,
    cash: float,
    pnl_pct: float,
    pnl_krw: int,
    judgments: dict,
    tickers: list,
    max_order_krw: int = 0,
    total_fee: int = 0,
    stock_value_krw: float = 0.0,
    total_equity_krw: float = 0.0,
    risk_value: float = 0.0,
    risk_label: str = "",
    mode_order_limit_krw: float = 0.0,
) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🇰🇷" if market == "KR" else "🇺🇸"
    pos_lines = "\n".join(_position_line(p, market) for p in positions) if positions else "  없음"
    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    risk_text = ""
    if risk_value > 0:
        label = risk_label or ("VKOSPI" if market == "KR" else "VIX")
        risk_text = f" | {label} {risk_value:.1f} ({_risk_status_label(risk_value)})"
    tickers_txt = ", ".join(_display_ticker(t, market) for t in tickers) if tickers else "-"
    text = (
        f"{icon} <b>[대시보드 요약 {now}] {market}</b>\n"
        f"모드: <b>{_ko_mode(mode)}</b>{risk_text}"
        + (f" | 최대매수 {int(mode_order_limit_krw):,}원" if mode_order_limit_krw and mode_order_limit_krw > 0 else "")
        + "\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | "
        f"주식평가: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원 | "
        f"수수료누적: {total_fee:,.0f}원\n\n"
        f"보유 포지션:\n{pos_lines}\n\n"
        f"판단 요약\n"
        f"  Bull {_ko_mode(bull.get('stance', '-'))} ({int((bull.get('confidence', 0) or 0) * 100)}%)\n"
        f"  Bear {_ko_mode(bear.get('stance', '-'))} ({int((bear.get('confidence', 0) or 0) * 100)}%)\n"
        f"  Neutral {_ko_mode(neut.get('stance', '-'))} ({int((neut.get('confidence', 0) or 0) * 100)}%)\n\n"
        f"감시종목: {tickers_txt}"
    )
    send(text)
    return text


def _env_usd_krw_rate() -> float:
    try:
        return float(os.getenv("USD_KRW_RATE", "1350") or 1350)
    except Exception:
        return 1350.0


def trade_alert(
    side: str,
    ticker: str,
    qty: int,
    price: float,
    strategy: str,
    tp: int,
    sl: int,
    reason: str = "",
    market: str = "KR",
    name: str = "",
    usd_krw: float = 0.0,
    buy_path: str = "",
) -> str:
    icon = "🟢" if side == "buy" else "🔴"
    ticker_disp = _display_ticker(ticker, market, name)
    rate = float(usd_krw or 0) or _env_usd_krw_rate()
    total = float(price) * int(qty)
    fee_rate = 0.00015 if market == "US" or side == "buy" else 0.00195
    fee = total * fee_rate

    if market == "US":
        price_txt = f"${float(price or 0):.2f}" + (f" (약 {int(round(float(price or 0) * rate)):,}원)" if rate > 0 else "")
        total_txt = f"${total:.2f}" + (f" (약 {int(round(total * rate)):,}원)" if rate > 0 else "")
        fee_txt = f"${fee:.2f}" + (f" (약 {int(round(fee * rate)):,}원)" if rate > 0 else "")
        tp_txt = f"${float(tp or 0):.2f}" + (f" (약 {int(round(float(tp or 0) * rate)):,}원)" if tp else "")
        sl_txt = f"${float(sl or 0):.2f}" + (f" (약 {int(round(float(sl or 0) * rate)):,}원)" if sl else "")
    else:
        price_txt = f"{int(float(price or 0)):,}원"
        total_txt = f"{int(round(total)):,}원"
        fee_txt = f"{int(round(fee)):,}원"
        tp_txt = f"{int(float(tp or 0)):,}원"
        sl_txt = f"{int(float(sl or 0)):,}원"

    extra_line = f"TP: {tp_txt} / SL: {sl_txt}" if side == "buy" else f"사유: {_ko_reason(reason) if reason else reason}"
    path = _path_line(buy_path)
    path_text = f"{path}\n" if path else ""
    text = (
        f"━━━━━━━━━━━━\n"
        f"{icon} <b>[{'매수' if side == 'buy' else '매도'} 체결]</b>\n"
        f"종목: {ticker_disp}  {qty}주 @{price_txt}\n"
        f"총금액: {total_txt}  수수료약 {fee_txt}\n"
        f"{path_text}"
        f"전략: {_ko_strategy(strategy)}\n"
        f"{extra_line}\n"
        f"━━━━━━━━━━━━"
    )
    send(text)
    return text


def pnl_alert(
    ticker: str,
    pnl_pct: float,
    pnl_krw: int,
    reason: str,
    market: Optional[str] = None,
    name: str = "",
    usd_krw: float = 0.0,
    buy_path: str = "",
) -> str:
    icon = "🟢" if pnl_krw > 0 else "🔴"
    market = market or ("US" if str(ticker or "").replace(".", "").isalpha() else "KR")
    ticker_disp = _display_ticker(ticker, market, name)
    rate = float(usd_krw or 0) or _env_usd_krw_rate()
    if market == "US":
        pnl_usd = float(pnl_krw) / rate if rate > 0 else 0.0
        pnl_txt = f"${pnl_usd:+.2f}" + (f" (약 {int(pnl_krw):+,}원)" if pnl_krw else "")
    else:
        pnl_txt = f"{int(pnl_krw):+,}원"
    path = _path_line(buy_path)
    path_text = f"\n{path}" if path else ""
    text = (
        f"{icon} <b>[청산]</b> {ticker_disp}\n"
        f"{fmt_pct(pnl_pct)}  {pnl_txt} (수수료 차감 후)  ({_ko_reason(reason)}){path_text}"
    )
    send(text)
    return text
