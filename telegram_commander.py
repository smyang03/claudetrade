"""
telegram_commander.py — 텔레그램 명령어 수신 & 처리

봇에서 메시지를 보내면 명령어를 인식해서 응답.
trading_bot.py의 TradingBot 인스턴스에 접근해 실제 동작 수행.

사용법: TradingBot 생성 후 commander.start(bot) 호출
"""
import os
import threading
import time
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from logger import get_trading_logger
from telegram_reporter import _display_ticker

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = str(os.getenv("TELEGRAM_CHAT_ID", ""))

HELP_TEXT = """━━━━━━━━━━━━━━━━━━━━━━
📋 <b>명령어 목록</b>
━━━━━━━━━━━━━━━━━━━━━━
<b>조회</b>
  ? / /help      — 이 도움말
  /s  /status    — 현재 상태 (모드·포지션·손익)
  /p  /pnl       — 오늘 손익 + 분석가 성과
  /trades        — 전체 매매내역 (기본 20건)
                   예) /trades 30  /trades 005930
  /pos           — 보유 포지션 목록
  /review        — 보유 포지션 즉시 Claude 재판단 (SELL이면 즉시 매도)
  /mode          — 현재 합의 모드
  /brain         — 누적 학습 요약
  /credit        — AI 크레딧 사용량
  /risk          — 리스크 파라미터 현황

<b>설정</b>
  /setorder [금액]     — 최대 주문금액 변경  예) /setorder 300000
  /setloss [숫자]      — 일일 손실 한도 변경 %  예) /setloss -5.0
  /setsl [숫자]        — 종목당 손절 기준 변경 %  예) /setsl -3.0
  /settp [숫자]        — 기본 TP 기준 변경 %  예) /settp 6.0
  /trail on|off        — 트레일링 스탑 ON/OFF
  /trail_pct [숫자]    — 트레일링 폭 변경 %  예) /trail_pct 2
  /trail_analyst on|off — TP 시 분석가 합의 ON/OFF
  /entry               — entry_priority cutoff 상태 조회
  /entry on|off        — cutoff 활성/비활성 토글
  /entry cutoff [값]   — cutoff 임계값 변경  예) /entry cutoff 0.3

<b>액션</b>
  /claude        — Claude 긴급 재판단 트리거
  /close [종목]  — 특정 종목 즉시 청산
                   예) /close 005930
  /closeall      — 전체 포지션 청산 ⚠️

<b>정보</b>
  /judge         — 오늘 아침 판단 요약
━━━━━━━━━━━━━━━━━━━━━━"""


def _send(text: str):
    if not TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.error(f"[commander] 전송 실패: {e}")


def _handle(text: str, bot) -> str:
    """명령어 파싱 후 응답 문자열 반환"""
    cmd = text.strip().lower().split()[0]
    args = text.strip().split()[1:]  # 추가 인자 (종목코드 등)

    # ── 도움말 ────────────────────────────────────────────────────────────────
    if cmd in ("?", "/help", "/h"):
        return HELP_TEXT

    # ── 현재 상태 ─────────────────────────────────────────────────────────────
    if cmd in ("/s", "/status"):
        return _cmd_status(bot)

    # ── 손익 ──────────────────────────────────────────────────────────────────
    if cmd in ("/p", "/pnl"):
        return _cmd_pnl(bot)

    # ── 포지션 ────────────────────────────────────────────────────────────────
    if cmd in ("/pos", "/positions"):
        return _cmd_positions(bot)

    # ── 보유 포지션 즉시 Claude 재판단 ───────────────────────────────────────
    if cmd == "/review":
        # /review [KR|US] [ticker]
        market = bot.current_market or "KR"
        ticker_filter = ""
        if args:
            if args[0].upper() in ("KR", "US"):
                market = args[0].upper()
                ticker_filter = args[1].upper() if len(args) > 1 else ""
            else:
                ticker_filter = args[0].upper()
        try:
            bot._refresh_claude_control()
            bot.claude_control["pending_position_review"] = {
                "market": market,
                "ticker": ticker_filter,
                "source": "telegram_review",
                "requested_at": datetime.now(KST).isoformat(timespec="seconds"),
            }
            bot.claude_control["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
            bot.claude_control["updated_by"] = "telegram"
            bot._save_claude_control()
            target = f"{market} {ticker_filter}" if ticker_filter else market
            return f"✅ {target} 포지션 재판단 큐 등록 — 결과는 텔레그램으로 전송됩니다"
        except Exception as e:
            return f"❌ 재판단 실패: {e}"

    # ── 합의 모드 ─────────────────────────────────────────────────────────────
    if cmd == "/mode":
        return _cmd_mode(bot)

    # ── 학습 요약 ─────────────────────────────────────────────────────────────
    if cmd == "/brain":
        return _cmd_brain(bot)

    # ── 크레딧 ────────────────────────────────────────────────────────────────
    if cmd == "/credit":
        return _cmd_credit()

    # ── 전체 매매 내역 ────────────────────────────────────────────────────────
    if cmd == "/trades":
        return _cmd_trades(bot, args)

    # ── 오늘 판단 요약 ────────────────────────────────────────────────────────
    if cmd == "/judge":
        return _cmd_judge(bot)

    # ── 리스크 파라미터 조회 ──────────────────────────────────────────────────
    if cmd == "/risk":
        return _cmd_risk(bot)

    # ── 최대 주문금액 변경 ────────────────────────────────────────────────────
    if cmd == "/setorder":
        if not args:
            return f"현재 최대주문: <b>{int(bot.risk.max_order_krw):,}원</b>\n변경: /setorder 300000"
        return _cmd_setorder(bot, args[0])

    # ── 일일 손실 한도 변경 ───────────────────────────────────────────────────
    if cmd == "/setloss":
        if not args:
            from risk_manager import HARD_RULES
            return f"현재 일일 손실 한도: <b>{HARD_RULES['max_daily_loss_pct']}%</b>\n변경: /setloss -5.0"
        return _cmd_setloss(bot, args[0])

    # ── 종목당 손절 기준 변경 ─────────────────────────────────────────────────
    if cmd == "/setsl":
        if not args:
            from risk_manager import HARD_RULES
            return f"현재 종목 손절 기준: <b>{HARD_RULES['max_single_loss_pct']}%</b>\n변경: /setsl -3.0"
        return _cmd_setsl(bot, args[0])

    # ── 기본 TP 변경 ──────────────────────────────────────────────────────────
    if cmd == "/settp":
        if not args:
            from risk_manager import HARD_RULES
            return f"현재 기본 TP: <b>{HARD_RULES['take_profit_pct']}%</b>\n변경: /settp 6.0"
        return _cmd_settp(bot, args[0])

    # ── 트레일링 스탑 설정 ────────────────────────────────────────────────────
    if cmd == "/trail":
        if not args:
            st = "ON" if bot.enable_trailing_stop else "OFF"
            an = "ON" if bot.enable_trailing_analyst else "OFF"
            return (f"📈 <b>트레일링 스탑</b>\n"
                    f"  상태: {st}  폭: {bot.trailing_stop_pct*100:.1f}%\n"
                    f"  분석가 합의: {an}\n"
                    f"  변경: /trail on|off")
        return _cmd_trail(bot, args[0])

    if cmd == "/trail_pct":
        if not args:
            return f"현재 트레일링 폭: {bot.trailing_stop_pct*100:.1f}%\n변경: /trail_pct 3"
        return _cmd_trail_pct(bot, args[0])

    if cmd == "/trail_analyst":
        if not args:
            st = "ON" if bot.enable_trailing_analyst else "OFF"
            return f"현재 분석가 합의: {st}\n변경: /trail_analyst on|off"
        return _cmd_trail_analyst(bot, args[0])

    # ── entry_priority cutoff ─────────────────────────────────────────────────
    if cmd == "/entry":
        if not args:
            return _cmd_entry_status(bot)
        if args[0].lower() in ("on", "off"):
            return _cmd_entry_toggle(bot, args[0].lower())
        if args[0].lower() == "cutoff" and len(args) > 1:
            return _cmd_entry_cutoff(bot, args[1])
        return "사용법: /entry | /entry on|off | /entry cutoff 0.3"

    # ── Claude 긴급 재판단 ────────────────────────────────────────────────────
    if cmd == "/claude":
        return _cmd_reinvoke(bot)

    if cmd == "/rescreen":
        return _cmd_rescreen(bot)

    # ── 특정 종목 청산 ────────────────────────────────────────────────────────
    if cmd == "/close":
        if not args:
            return "❌ 종목코드를 입력하세요.\n예) /close 005930"
        return _cmd_close(bot, args[0].upper())

    # ── 전체 청산 ─────────────────────────────────────────────────────────────
    if cmd == "/closeall":
        return _cmd_closeall(bot)

    return f"❓ 알 수 없는 명령어: {cmd}\n? 를 입력하면 명령어 목록을 볼 수 있습니다."


# ── 명령어 핸들러 구현 ────────────────────────────────────────────────────────

def _legacy_cmd_status(bot) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    lines = [f"⏱ <b>[현재 상태 {now}]</b>"]

    for market in ("KR", "US"):
        if not bot.session_active:
            lines.append(f"\n{market}: 세션 없음")
            continue
        j = bot.today_judgment
        if j.get("market") != market:
            continue
        mode = j.get("consensus", {}).get("mode", "-")
        pnl_pct = bot.risk.daily_pnl / max(bot.risk.session_start_equity, 1) * 100
        pnl_krw = int(bot.risk.daily_pnl)
        pnl_icon = "🟢" if pnl_pct > 0 else "🔴" if pnl_pct < 0 else "⚪"
        lines.append(
            f"\n<b>{market}</b>  모드: {mode}\n"
            f"{pnl_icon} 오늘 P&L: {pnl_pct:+.2f}%  {pnl_krw:+,}원"
        )

    # 포지션
    pos = bot.risk.positions
    if pos:
        lines.append("\n📌 <b>보유 포지션</b>")
        for p in pos:
            entry = p.get("entry", 0)
            cur   = p.get("current_price", entry)
            pnl   = (cur / entry - 1) * 100 if entry else 0
            icon  = "🟢" if pnl > 0 else "🔴"
            lines.append(f"  {icon} {_display_symbol(p['ticker'])} {p['qty']}주 | {cur:,}원 | {pnl:+.2f}%")
    else:
        lines.append("\n📌 보유 포지션: 없음")

    # 설정
    lines.append(f"\n⚙️ 최대주문: {int(bot.risk.max_order_krw):,}원  수수료: {int(bot.risk.total_fee):,}원")

    return "\n".join(lines)


def _fmt_price_for_market(value: float, market: str) -> str:
    if market == "US":
        return f"${float(value):.4f}"
    return f"{int(round(float(value))):,}원"


def _display_symbol(ticker: str, market: str = "", name: str = "") -> str:
    raw_ticker = str(ticker or "").strip().upper()
    inferred_market = market or ("US" if raw_ticker.replace(".", "").isalpha() else "KR")
    return _display_ticker(raw_ticker, inferred_market, name or "")


def _status_positions(bot) -> list[str]:
    lines = []
    for p in bot.risk.positions:
        market = "US" if str(p.get("ticker", "")).replace(".", "").isalpha() else "KR"
        ticker_disp = _display_symbol(p.get("ticker", "-"), market, p.get("name", "") or "")
        entry = float(p.get("display_avg_price", p.get("avg_price", p.get("entry", 0))) or 0)
        current = float(p.get("display_current_price", p.get("current_price", entry)) or 0)
        pnl = (current / entry - 1) * 100 if entry > 0 and current > 0 else 0.0
        icon = "🟢" if pnl > 0 else "🔴" if pnl < 0 else "⚪"
        lines.append(
            f"{icon} {ticker_disp} {int(p.get('qty', 0) or 0)}주"
            f" | 매수가 {_fmt_price_for_market(entry, market)}"
            f" | 현재가 {_fmt_price_for_market(current, market)}"
            f" | {pnl:+.2f}%"
        )
    return lines


def _cmd_status(bot) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    total_equity = float(bot._kis_total_equity_krw())
    cash = float(bot.risk.cash)
    stock_value = max(total_equity - cash, 0.0)
    mode = "-"
    market = bot.current_market or (bot.today_judgment.get("market") if bot.today_judgment else "KR")
    if bot.today_judgment:
        mode = bot.today_judgment.get("consensus", {}).get("mode", "-")
    pnl_krw = int(getattr(bot.risk, "daily_pnl", 0) or 0)
    base_equity = max(float(getattr(bot.risk, "session_start_equity", 0) or 0), 1.0)
    pnl_pct = pnl_krw / base_equity * 100.0
    session_label = "진행중" if bot.session_active else "대기"
    pos_lines = _status_positions(bot)
    body = "\n".join(pos_lines) if pos_lines else "보유 포지션 없음"
    return (
        f"📊 <b>[현재 상태 {now}]</b>\n"
        f"시장: <b>{market}</b> | 세션: {session_label}\n"
        f"모드: <b>{mode}</b>\n"
        f"오늘 손익: {pnl_pct:+.2f}%  {pnl_krw:+,}원\n"
        f"현금: {cash:,.0f}원\n"
        f"주식평가: {stock_value:,.0f}원\n"
        f"총자산: {total_equity:,.0f}원\n"
        f"주문한도: {int(bot.risk.max_order_krw):,}원 | 누적 수수료: {int(bot.risk.total_fee):,}원\n\n"
        f"보유 포지션\n{body}"
    )


def _cmd_credit(bot_dummy=None) -> str:
    try:
        from credit_tracker import summary as credit_summary
        cr = credit_summary()
        td = cr["today"]
        tot = cr["total"]
        budget = cr.get("budget", {}) or {}
        remain = []
        if budget.get("daily_remaining_usd") is not None:
            remain.append(f"일간예산 잔여 ${float(budget['daily_remaining_usd']):.3f}")
        if budget.get("monthly_remaining_usd") is not None:
            remain.append(f"월간예산 잔여 ${float(budget['monthly_remaining_usd']):.3f}")
        remain_line = f"\n{' | '.join(remain)}" if remain else ""
        return (
            f"🧾 <b>AI 크레딧 사용량</b>\n"
            f"────────\n"
            f"오늘: ${td['cost_usd']:.4f}  (₩{td['cost_krw']:,})\n"
            f"  입력 {td['input']:,}tok  출력 {td['output']:,}tok  {td['calls']}회\n"
            f"누적: ${tot['cost_usd']:.4f}  (₩{tot['cost_krw']:,})"
            f"{remain_line}\n"
            f"※ 실제 Claude 계정 잔액이 아니라 사용량/예산 기준입니다."
        )
    except Exception as e:
        return f"❌ 크레딧 조회 오류: {e}"


def _cmd_pnl(bot) -> str:
    now       = datetime.now(KST).strftime("%H:%M")
    equity    = bot.risk.equity()
    start_eq  = bot.risk.session_start_equity
    daily_pnl = bot.risk.daily_pnl
    pnl_pct   = daily_pnl / max(start_eq, 1) * 100
    cash      = bot.risk.cash
    total_fee = int(getattr(bot.risk, "total_fee", 0))

    # 실현손익 = 순손익 + 수수료 (역산)
    gross_pnl = daily_pnl + total_fee

    icon = "🟢" if daily_pnl > 0 else "🔴" if daily_pnl < 0 else "⚪"
    trades      = bot.risk.trade_log
    sell_trades = [t for t in trades if t.get("side") == "sell"]
    wins  = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
    total = len(sell_trades)
    wr    = f"{wins}/{total}승" if total else "거래 없음"

    lines = [
        f"💰 <b>[손익 상세 {now}]</b>",
        f"━━━━━━━━━━━━━━━━",
        f"{icon} 오늘 순손익: {pnl_pct:+.2f}%  {daily_pnl:+,.0f}원",
        f"  · 실현손익(수수료 전): {gross_pnl:+,.0f}원",
        f"  · 수수료(누적): -{total_fee:,.0f}원",
        f"📊 청산: {wr}  |  최대주문: {int(bot.risk.max_order_krw):,}원",
        f"💵 현금: {cash:,.0f}원  |  평가액: {equity:,.0f}원",
    ]

    # ── 오늘 청산 내역 (수수료 분리 표시) ────────────────────────────────────
    if sell_trades:
        lines.append("\n<b>📋 오늘 청산 내역</b>")
        for t in sell_trades:
            pnl    = t.get("pnl", 0)
            pct    = t.get("pnl_pct", 0.0)
            reason = t.get("reason", "-")
            ticker = t.get("ticker", "-")
            qty    = t.get("qty", 0)
            price  = t.get("price", 0)
            ic     = "🟢" if pnl > 0 else "🔴"
            lines.append(
                f"  {ic} {ticker}  {qty}주 @{price:,}원\n"
                f"     순손익: {pnl:+,.0f}원 ({pct:+.2f}%)  [{reason}]"
            )
    else:
        lines.append("\n📋 오늘 청산 없음")

    # ── 분석가 성과 ───────────────────────────────────────────────────────────
    lines.append("\n<b>🧠 분석가 성과</b>")
    try:
        from claude_memory import brain as BrainDB
        market = bot.today_judgment.get("market", "KR") if bot.today_judgment else "KR"
        brain  = BrainDB.load()
        perf   = brain["markets"][market]["analyst_performance"]

        trend_icon = {"improving": "↑", "declining": "↓", "stable": "→"}
        for atype, icon_a in [("bull", "🟢"), ("bear", "🔴"), ("neutral", "⚪")]:
            p  = perf[atype]
            r7 = p.get("recent_7d", {}).get("rate", p["rate"]) * 100
            ti = trend_icon.get(p.get("trend", "stable"), "→")
            lines.append(
                f"  {icon_a} {atype.capitalize():<7} "
                f"누적 {p['rate']*100:.1f}% ({p['total']}일)  "
                f"최근7일 {r7:.1f}% {ti}"
            )

        # hold_advisor 성과
        hp = brain.get("hold_advisor_performance")
        if hp and hp.get("total", 0) > 0:
            hold_n   = hp["hold_count"]
            hold_ok  = hp["hold_success"]
            hold_pct = hold_ok / hold_n * 100 if hold_n else 0
            extra    = hp.get("hold_avg_extra_pnl", 0.0)
            lines.append(
                f"\n  📈 TP 후 분석가 합의 (총 {hp['total']}건)\n"
                f"     HOLD {hold_n}건 성공 {hold_ok}건 ({hold_pct:.0f}%)"
                + (f"  평균 추가수익 {extra:+.2f}%" if hold_n > 0 else "")
            )
    except Exception as e:
        lines.append(f"  (분석가 데이터 없음: {e})")

    return "\n".join(lines)


def _cmd_positions(bot) -> str:
    pos = bot.risk.positions
    if not pos:
        return "📌 보유 포지션 없음"
    lines = ["📌 <b>보유 포지션</b>", "━━━━━━━━━━━━━━━━"]
    for p in pos:
        market = "US" if str(p.get("ticker", "")).replace(".", "").isalpha() else "KR"
        ticker_disp = _display_symbol(p.get("ticker", "-"), market, p.get("name", "") or "")
        entry  = float(p.get("display_avg_price", p.get("entry", 0)) or 0)
        cur    = float(p.get("display_current_price", p.get("current_price", entry)) or 0)
        qty    = int(p.get("qty", 0) or 0)
        pnl    = (cur / entry - 1) * 100 if entry else 0
        pnl_icon = "🟢" if pnl > 0 else "🔴"
        is_us  = market == "US"
        px     = lambda v: f"${v:.2f}" if is_us else f"{v:,.0f}원"

        # ── 수익금 + 수수료 차감 실손익 ─────────────────────────────────
        gross_pnl = (cur - entry) * qty
        # 수수료: KR 매수 0.015% + 매도 0.195%(거래세 포함), US 0.015% 양방향
        fee_buy  = entry * qty * (0.00015)
        fee_sell = cur   * qty * (0.00195 if not is_us else 0.00015)
        net_pnl  = gross_pnl - fee_buy - fee_sell
        net_sign = "+" if net_pnl >= 0 else ""
        if is_us:
            pnl_str = f"${net_pnl:+.2f} (수수료 제외)"
        else:
            pnl_str = f"{net_sign}{net_pnl:,.0f}원 (수수료 제외)"

        # ── 매도 기준 (사람 언어로) ──────────────────────────────────────
        is_trailing = p.get("trailing", False)
        trail_sl    = float(p.get("trail_sl", 0) or 0)
        trail_pct   = float(p.get("trail_pct", 0.03) or 0.03) * 100
        sl          = float(p.get("sl", 0) or 0)
        tp          = float(p.get("tp", 0) or 0)
        held        = int(p.get("held_days", 0) or 0)
        max_hold    = int(p.get("max_hold", 0) or 0)

        if is_trailing and trail_sl > 0:
            exit_line = f"  📉 <b>{px(trail_sl)} 이하 시 자동 매도</b> (트레일링 {trail_pct:.1f}%)"
        else:
            parts = []
            if sl > 0: parts.append(f"손절 {px(sl)}")
            if tp > 0: parts.append(f"목표 {px(tp)}")
            exit_line = f"  🎯 {' · '.join(parts)}" if parts else ""

        hold_line = ""
        if max_hold > 0:
            remain = max_hold - held
            hold_line = f"  ⏱ {held}일 보유 중" + (f" (최대 {max_hold}일 · {remain}일 남음)" if remain >= 0 else " (기간 초과)")

        # ── Claude 판단 (hold_advice) ──────────────────────────────────
        adv = p.get("hold_advice")
        claude_line = ""
        if adv:
            action = adv.get("action", "")
            action_ko = {"TRAIL": "트레일링 유지", "SELL": "매도 권고", "HOLD": "홀드"}.get(action, action)
            votes = adv.get("votes", {})
            reason = ""
            for v in votes.values():
                if v.get("action") == action and v.get("reason"):
                    reason = v["reason"][:60]
                    break
            claude_line = f"  🤖 Claude: <b>{action_ko}</b>" + (f"\n     → {reason}" if reason else "")

        # ── 조합 ──────────────────────────────────────────────────────
        strat = p.get("strategy", "")
        if strat in ("broker_balance", "broker_sync", ""): strat = ""
        sub = " · ".join(filter(None, [strat, f"{p['qty']}주", f"{held}일째" if held else ""]))

        block = [f"{pnl_icon} <b>{ticker_disp}</b>  {pnl:+.2f}%  <b>{pnl_str}</b>"]
        if sub:    block.append(f"  {sub}")
        block.append(f"  매수 {px(entry)} → 현재 {px(cur)}")
        if exit_line:  block.append(exit_line)
        if hold_line:  block.append(hold_line)
        if claude_line: block.append(claude_line)
        lines.append("\n".join(block))

    return "\n\n".join(lines)


def _cmd_mode(bot) -> str:
    j = bot.today_judgment
    if not j:
        return "⚪ 오늘 판단 없음 (세션 미시작)"
    consensus = j.get("consensus", {})
    mode  = consensus.get("mode", "-")
    size  = consensus.get("size", "-")
    score = consensus.get("weighted_score")
    score_txt = f"  점수: {score:+.3f}" if score is not None else ""
    return (
        f"⚖️ <b>현재 합의 모드</b>\n"
        f"모드: <b>{mode}</b>  포지션 {size}%{score_txt}"
    )


def _cmd_brain(bot) -> str:
    try:
        from claude_memory import brain as BrainDB
        market = bot.today_judgment.get("market", "KR") if bot.today_judgment else "KR"
        summary = BrainDB.generate_prompt_summary(market)
        return f"🧠 <b>누적 학습 요약 [{market}]</b>\n{summary[:600]}"
    except Exception as e:
        return f"❌ 학습 요약 오류: {e}"


def _legacy_cmd_credit_runtime() -> str:
    try:
        from credit_tracker import summary as credit_summary
        cr  = credit_summary()
        td  = cr["today"]
        tot = cr["total"]
        return (
            f"🤖 <b>AI 크레딧 사용량</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"오늘: ${td['cost_usd']:.4f}  (≈{td['cost_krw']:,}원)\n"
            f"  입력 {td['input']:,}tok  출력 {td['output']:,}tok  {td['calls']}회\n"
            f"누적: ${tot['cost_usd']:.4f}  (≈{tot['cost_krw']:,}원)"
        )
    except Exception as e:
        return f"❌ 크레딧 조회 오류: {e}"


def _cmd_judge(bot) -> str:
    j = bot.today_judgment
    if not j or not j.get("judgments"):
        return "⚪ 오늘 판단 없음 (세션 미시작)"
    judgments = j["judgments"]
    consensus = j.get("consensus", {})
    bull = judgments.get("bull", {}); bear = judgments.get("bear", {}); neut = judgments.get("neutral", {})
    mode = consensus.get("mode", "-")
    return (
        f"🧠 <b>오늘 아침 판단</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"⚖️ 합의: <b>{mode}</b>  {consensus.get('size','-')}%\n\n"
        f"🟢 Bull ({int(bull.get('confidence',0)*100)}%)\n"
        f"  {bull.get('key_reason','')}\n\n"
        f"🔴 Bear ({int(bear.get('confidence',0)*100)}%)\n"
        f"  {bear.get('key_reason','')}\n\n"
        f"⚪ Neutral ({int(neut.get('confidence',0)*100)}%)\n"
        f"  {neut.get('key_reason','')}"
    )


def _cmd_reinvoke(bot) -> str:
    if hasattr(bot, "is_claude_reinvoke_enabled") and not bot.is_claude_reinvoke_enabled():
        return "Claude 재판단 기능이 현재 OFF 상태입니다."
    if not bot.session_active:
        return "❌ 세션이 활성화되어 있지 않습니다."
    market = bot.today_judgment.get("market") if bot.today_judgment else None
    if not market:
        return "❌ 오늘 판단이 없습니다."
    _send("⏳ Claude 긴급 재판단 시작... (1~2분 소요)")
    try:
        bot._reinvoke_analysts(market, "수동 명령: /claude")
        return "✅ 긴급 재판단 완료. 텔레그램 알림을 확인하세요."
    except Exception as e:
        return f"❌ 재판단 실패: {e}"


def _cmd_rescreen(bot) -> str:
    if not bot.session_active:
        return "세션이 비활성 상태입니다."
    market = bot.current_market or (bot.today_judgment.get("market") if bot.today_judgment else None)
    if not market:
        return "시장 정보를 알 수 없습니다."
    _send(f"🔄 {market} 종목 재추천 요청... (10~30초 소요)")
    try:
        selected = bot.manual_rescreen(market)
        selected_disp = ", ".join(_display_symbol(t, market) for t in selected)
        return f"✅ <b>[{market} 종목 재추천 완료]</b>\n{selected_disp}"
    except Exception as e:
        return f"❌ 종목 재추천 실패: {e}"


def _cmd_close(bot, ticker: str) -> str:
    pos = next((p for p in bot.risk.positions if p["ticker"] == ticker), None)
    if not pos:
        return f"❌ {_display_symbol(ticker)} 포지션 없음"
    market = bot._ticker_market(ticker)
    ticker_disp = _display_symbol(ticker, market, pos.get("name", "") or "")
    try:
        from kis_api import place_order, get_price
        price_info = get_price(ticker, bot.token, market=market)
        raw_price = price_info.get("price", 0)
        close_price = bot._price_to_krw(raw_price, market)
        result = place_order(ticker, pos["qty"], raw_price, "sell", bot.token, market=market)
        if result.get("success"):
            ex = bot.risk.close_position(ticker, close_price, "manual_close")
            if not ex:
                return f"❌ 내부 포지션 정리 실패: {ticker_disp}"
            bot._save_positions()
            bot._write_live_status(market)
            bot._maybe_push_dashboard(force=True)
            pnl = ex["pnl"]
            icon = "🟢" if pnl > 0 else "🔴"
            return (
                f"{icon} <b>[수동 청산]</b> {ticker_disp}\n"
                f"  {pos['qty']}주 @{raw_price:,}{'원' if market == 'KR' else '$'}\n"
                f"  P&L: {pnl:+,}원"
            )
        else:
            return f"❌ 청산 실패: {result.get('msg','')}"
    except Exception as e:
        return f"❌ 청산 오류: {e}"


def _cmd_setorder(bot, amount_str: str) -> str:
    try:
        amount = int(amount_str.replace(",", "").replace("원", ""))
        if amount < 10_000:
            return "❌ 최소 10,000원 이상이어야 합니다."
        if amount > 10_000_000:
            return "❌ 1회 최대주문은 1,000만원을 초과할 수 없습니다."
        old = int(bot.risk.max_order_krw)
        bot.risk.max_order_krw = float(amount)
        log.info(f"[commander] max_order_krw 변경: {old:,} → {amount:,}")
        return (
            f"✅ <b>최대주문 금액 변경</b>\n"
            f"  {old:,}원 → <b>{amount:,}원</b>\n"
            f"  (재시작 시 .env 기본값으로 복원됩니다)"
        )
    except ValueError:
        return "❌ 숫자를 입력하세요.\n예) /setorder 300000"


def _cmd_risk(bot) -> str:
    from risk_manager import HARD_RULES
    dl = HARD_RULES["max_daily_loss_pct"]
    sl = HARD_RULES["max_single_loss_pct"]
    tp = HARD_RULES["take_profit_pct"]
    daily_ret = bot.risk.daily_return() if hasattr(bot.risk, "daily_return") else 0.0
    return (
        f"⚙️ <b>리스크 파라미터</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"  일일 손실 한도:  <b>{dl:.1f}%</b>  ← /setloss\n"
        f"  종목 손절 기준:  <b>{sl:.1f}%</b>  ← /setsl\n"
        f"  기본 TP 기준:    <b>{tp:.1f}%</b>  ← /settp\n"
        f"  1회 최대주문:    <b>{int(bot.risk.max_order_krw):,}원</b>  ← /setorder\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"  오늘 수익률: <b>{daily_ret:+.2f}%</b>"
    )


def _cmd_setloss(bot, val: str) -> str:
    from risk_manager import HARD_RULES
    try:
        pct = float(val.replace("%", ""))
        if pct > 0:
            pct = -pct  # 양수로 입력해도 음수로 처리
        if pct < -30:
            return "❌ -30% 이하는 설정할 수 없습니다."
        old = HARD_RULES["max_daily_loss_pct"]
        HARD_RULES["max_daily_loss_pct"] = pct
        log.info(f"[commander] max_daily_loss_pct 변경: {old} → {pct}")
        return (
            f"✅ <b>일일 손실 한도 변경</b>\n"
            f"  {old:.1f}% → <b>{pct:.1f}%</b>\n"
            f"  (재시작 시 .env 값으로 복원)"
        )
    except ValueError:
        return "❌ 숫자를 입력하세요. 예) /setloss -5.0"


def _cmd_setsl(bot, val: str) -> str:
    from risk_manager import HARD_RULES
    try:
        pct = float(val.replace("%", ""))
        if pct > 0:
            pct = -pct
        if pct < -20:
            return "❌ -20% 이하는 설정할 수 없습니다."
        old = HARD_RULES["max_single_loss_pct"]
        HARD_RULES["max_single_loss_pct"] = pct
        log.info(f"[commander] max_single_loss_pct 변경: {old} → {pct}")
        return (
            f"✅ <b>종목 손절 기준 변경</b>\n"
            f"  {old:.1f}% → <b>{pct:.1f}%</b>\n"
            f"  (재시작 시 .env 값으로 복원)"
        )
    except ValueError:
        return "❌ 숫자를 입력하세요. 예) /setsl -3.0"


def _cmd_settp(bot, val: str) -> str:
    from risk_manager import HARD_RULES
    try:
        pct = float(val.replace("%", ""))
        if pct <= 0:
            return "❌ 양수 값을 입력하세요. 예) /settp 6.0"
        if pct > 50:
            return "❌ 50% 이하로 입력하세요."
        old = HARD_RULES["take_profit_pct"]
        HARD_RULES["take_profit_pct"] = pct
        log.info(f"[commander] take_profit_pct 변경: {old} → {pct}")
        return (
            f"✅ <b>기본 TP 기준 변경</b>\n"
            f"  {old:.1f}% → <b>{pct:.1f}%</b>\n"
            f"  (재시작 시 .env 값으로 복원)"
        )
    except ValueError:
        return "❌ 숫자를 입력하세요. 예) /settp 6.0"


def _cmd_trail(bot, val: str) -> str:
    v = val.strip().lower()
    if v in ("on", "1", "true"):
        bot.enable_trailing_stop = True
        return "✅ 트레일링 스탑 <b>ON</b>"
    elif v in ("off", "0", "false"):
        bot.enable_trailing_stop = False
        return "⏹ 트레일링 스탑 <b>OFF</b> (TP 즉시 청산)"
    return "❌ on 또는 off 를 입력하세요."


def _cmd_trail_pct(bot, val: str) -> str:
    try:
        pct = float(val.replace("%", ""))
        if not 1 <= pct <= 10:
            return "❌ 1%~10% 범위로 입력하세요."
        bot.trailing_stop_pct = pct / 100
        return f"✅ 트레일링 폭 → <b>{pct:.1f}%</b>"
    except ValueError:
        return "❌ 숫자를 입력하세요. 예) /trail_pct 3"


def _cmd_trail_analyst(bot, val: str) -> str:
    v = val.strip().lower()
    if v in ("on", "1", "true"):
        bot.enable_trailing_analyst = True
        return "✅ 분석가 합의 <b>ON</b> (TP 시 3명 판단)"
    elif v in ("off", "0", "false"):
        bot.enable_trailing_analyst = False
        return "⏹ 분석가 합의 <b>OFF</b> (트레일링 즉시 활성화)"
    return "❌ on 또는 off 를 입력하세요."


def _cmd_entry_status(bot) -> str:
    enabled = getattr(bot, "entry_priority_cutoff_enabled", False)
    cutoff  = getattr(bot, "entry_priority_cutoff", 0.20)
    st = "ON" if enabled else "OFF"
    return (
        f"📊 <b>entry_priority</b>\n"
        f"  신호 정렬: 항상 활성 (score 높은 순)\n"
        f"  cutoff: <b>{st}</b>  임계값: {cutoff:.2f}\n"
        f"변경: /entry on|off | /entry cutoff 0.3"
    )


def _cmd_entry_toggle(bot, val: str) -> str:
    bot.entry_priority_cutoff_enabled = (val == "on")
    st = "ON" if bot.entry_priority_cutoff_enabled else "OFF"
    cutoff = getattr(bot, "entry_priority_cutoff", 0.20)
    return f"✅ entry_priority cutoff: <b>{st}</b>  임계값: {cutoff:.2f}"


def _cmd_entry_cutoff(bot, val: str) -> str:
    try:
        v = float(val)
        if not 0.0 <= v <= 2.0:
            return "❌ 임계값은 0~2 사이로 입력하세요."
        bot.entry_priority_cutoff = v
        st = "ON" if getattr(bot, "entry_priority_cutoff_enabled", False) else "OFF"
        return f"✅ cutoff 임계값 → <b>{v:.2f}</b>  (cutoff {st})"
    except ValueError:
        return "❌ 숫자를 입력하세요. 예) /entry cutoff 0.3"


def _cmd_trades(bot, args: list) -> str:
    """
    전체 매매 내역 날짜 순 나열.
    args: [] → 기본 20건
          ['30'] → 30건
          ['005930'] → 종목 필터
    """
    limit  = 20
    ticker_filter = None

    for arg in args:
        arg_clean = arg.replace(",", "").upper()
        # 건수 판단: 순수 숫자이고 3자리 이하(1~999) → 건수
        # 4자리 이상 숫자는 KR 종목코드로 처리 (예: 005930, 000660)
        if arg_clean.isdigit() and 1 <= int(arg_clean) <= 999:
            limit = int(arg_clean)
        else:
            ticker_filter = arg_clean

    all_trades = list(getattr(bot.risk, "all_trade_log", []))
    if not all_trades:
        return "📒 매매 내역 없음"

    # 종목 필터
    if ticker_filter:
        all_trades = [t for t in all_trades if t.get("ticker", "").upper() == ticker_filter]
        if not all_trades:
            return f"📒 {_display_symbol(ticker_filter)} 매매 내역 없음"

    # 최근 limit건 (뒤에서 자름)
    all_trades = all_trades[-limit:]

    # 날짜별 그룹화
    from collections import defaultdict
    grouped = defaultdict(list)
    for t in all_trades:
        day = t.get("date", "날짜미상")
        grouped[day].append(t)

    title = f"📒 <b>매매 내역</b>"
    if ticker_filter:
        title += f" [{_display_symbol(ticker_filter)}]"
    title += f" (최근 {len(all_trades)}건)"

    lines = [title, "━━━━━━━━━━━━━━━━"]

    for day in sorted(grouped.keys(), reverse=True):  # 최신 날짜 먼저
        lines.append(f"\n<b>{day}</b>")
        for t in grouped[day]:
            side   = t.get("side", "-")
            ticker = t.get("ticker", "-")
            mkt    = "US" if str(ticker).isalpha() and len(str(ticker)) <= 5 else "KR"
            ticker_disp = _display_symbol(ticker, mkt, t.get("name", "") or "")
            qty    = t.get("qty", 0)
            price  = t.get("price", 0)
            strat  = t.get("strategy", "-")

            if side == "buy":
                fee_r = 0.00015
                krw_price = price if mkt == "KR" else price * getattr(bot, "usd_krw_rate", 1350)
                fee_est = int(krw_price * qty * fee_r)
                unit = "원" if mkt == "KR" else "$"
                lines.append(
                    f"  🟢 매수  {ticker_disp}  {qty}주 @{price:,}{unit}"
                    f"  수수료≈{fee_est:,}원  [{strat}]"
                )
            else:
                pnl    = t.get("pnl", 0)
                pct    = t.get("pnl_pct", 0.0)
                reason = t.get("reason", "-")
                unit   = "원" if mkt == "KR" else "$"
                ic     = "🟢" if pnl > 0 else "🔴"
                lines.append(
                    f"  {ic} 매도  {ticker_disp}  {qty}주 @{price:,}{unit}"
                    f"  순손익 {pnl:+,.0f}원 ({pct:+.2f}%)  [{reason}]"
                )

    if len(getattr(bot.risk, "all_trade_log", [])) > limit:
        total_cnt = len(getattr(bot.risk, "all_trade_log", []))
        lines.append(f"\n📌 전체 {total_cnt}건 중 최근 {limit}건 표시")
        lines.append(f"   더 보기: /trades {limit + 20}")

    return "\n".join(lines)


def _cmd_closeall(bot) -> str:
    pos_list = list(bot.risk.positions)
    if not pos_list:
        return "📌 보유 포지션 없음"
    _send(f"⚠️ 전체 청산 시작: {len(pos_list)}개 포지션")
    results = []
    for pos in pos_list:
        r = _cmd_close(bot, pos["ticker"])
        results.append(r)
    return "\n\n".join(results) + f"\n\n✅ 전체 청산 완료 ({len(pos_list)}건)"


# ── 폴링 루프 ─────────────────────────────────────────────────────────────────

class TelegramCommander:
    def __init__(self):
        self._bot   = None
        self._offset = 0
        self._thread = None
        self._running = False

    def start(self, bot):
        """백그라운드 스레드로 폴링 시작"""
        self._bot = bot
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg-commander")
        self._thread.start()
        log.info("[TelegramCommander] 명령어 수신 시작")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                updates = self._get_updates()
                for upd in updates:
                    self._offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("edited_message")
                    if not msg:
                        continue
                    # 인증된 채팅방만 처리
                    if str(msg.get("chat", {}).get("id")) != CHAT_ID:
                        continue
                    text = msg.get("text", "").strip()
                    if not text:
                        continue
                    # 명령어 처리
                    first = text.split()[0].lower()
                    is_cmd = first.startswith("/") or first == "?"
                    if is_cmd:
                        log.info(f"[commander] 명령 수신: {text}")
                        response = _handle(text, self._bot)
                        _send(response)
            except Exception as e:
                log.warning(f"[commander] 폴링 오류: {e}")
                time.sleep(5)

    def _get_updates(self) -> list:
        if not TOKEN:
            time.sleep(10)
            return []
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params={"offset": self._offset, "timeout": 30, "allowed_updates": ["message"]},
            timeout=35,
        )
        resp.raise_for_status()
        return resp.json().get("result", [])


# 전역 싱글톤
commander = TelegramCommander()


def _legacy_cmd_status_runtime(bot) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    total_equity = float(bot._kis_total_equity_krw())
    cash = float(bot.risk.cash)
    stock_value = max(total_equity - cash, 0.0)
    lines = [f"📋<b>[현재 상태 {now}]</b>"]

    for market in ("KR", "US"):
        if not bot.session_active:
            lines.append(f"\n{market}: 세션 없음")
            continue
        j = bot.today_judgment
        if j.get("market") != market:
            continue
        mode = j.get("consensus", {}).get("mode", "-")
        pnl_pct = bot.risk.daily_pnl / max(bot.risk.session_start_equity, 1) * 100
        pnl_krw = int(bot.risk.daily_pnl)
        pnl_icon = "📈" if pnl_pct > 0 else "📉" if pnl_pct < 0 else "➖"
        lines.append(
            f"\n<b>{market}</b>  모드: {mode}\n"
            f"{pnl_icon} 오늘 P&L: {pnl_pct:+.2f}%  {pnl_krw:+,}원"
        )

    pos = bot.risk.positions
    if pos:
        lines.append("\n💦 <b>보유 포지션</b>")
        for p in pos:
            entry = p.get("entry", 0)
            cur = p.get("current_price", entry)
            pnl = (cur / entry - 1) * 100 if entry else 0
            icon = "📈" if pnl > 0 else "📉"
            lines.append(f"  {icon} {_display_symbol(p['ticker'])} {p['qty']}주 | {cur:,.0f}원 | {pnl:+.2f}%")
    else:
        lines.append("\n💦 보유 포지션 없음")

    lines.append(
        f"\n현금: {cash:,.0f}원 | 주식평가: {stock_value:,.0f}원 | 총자산: {total_equity:,.0f}원"
    )
    lines.append(
        f"주문한도: {int(bot.risk.max_order_krw):,}원 | 수수료: {int(bot.risk.total_fee):,}원"
    )
    return "\n".join(lines)
