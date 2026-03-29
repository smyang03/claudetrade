"""
telegram_reporter.py - 텔레그램 리포팅
장 시작 브리핑, 장 중 튜닝, 매매 체결, 긴급 알림, 일일 결산

.env:
  TELEGRAM_TOKEN=...
  TELEGRAM_CHAT_ID=...
"""
import os, requests
from datetime import datetime
from zoneinfo import ZoneInfo
from logger import get_trading_logger
from credit_tracker import summary as credit_summary

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")

TOKEN   = os.getenv("TELEGRAM_TOKEN","")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID","")

def send(text: str, parse_mode: str = "HTML") -> bool:
    if not TOKEN or not CHAT_ID:
        log.debug(f"[텔레그램 비활성] {text[:50]}")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=10
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        log.error(f"텔레그램 전송 실패: {e}")
        return False

def fmt_pct(v: float) -> str:
    return f"{'🟢' if v>0 else '🔴' if v<0 else '⚪'} {v:+.2f}%"

def _credit_line() -> str:
    """오늘 크레딧 사용량 한 줄 요약"""
    try:
        cr = credit_summary()
        td = cr["today"]
        tot = cr["total"]
        return (f"🤖 AI 크레딧 | 오늘 ${td['cost_usd']:.3f}"
                f"(≈{td['cost_krw']:,}원) / 누적 ${tot['cost_usd']:.3f}"
                f"(≈{tot['cost_krw']:,}원)")
    except Exception:
        return ""


def morning_briefing(market: str, judgments: dict, consensus: dict,
                     balance: dict, digest: dict,
                     round1_judgments: dict = None,
                     debate_changes: list = None) -> str:
    now  = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    bull = judgments["bull"]; bear = judgments["bear"]; neut = judgments["neutral"]
    mode = consensus["mode"]; size = consensus["size"]
    cash = balance.get("cash", 0)
    credit_line = _credit_line()

    # ── 1라운드 섹션 ─────────────────────────────────────────────────────────
    r1 = round1_judgments or {}
    if r1:
        def _r1_line(atype, icon):
            j = r1.get(atype, {})
            return f"{icon} {j.get('stance','-')} ({int(j.get('confidence',0)*100)}%): {j.get('key_reason','')[:60]}"
        r1_section = (
            f"\n🔍 <b>1라운드 독립 판단</b>\n"
            f"{_r1_line('bull','🟢')}\n"
            f"{_r1_line('bear','🔴')}\n"
            f"{_r1_line('neutral','⚪')}\n"
        )
    else:
        r1_section = ""

    # ── 2라운드 토론 섹션 ─────────────────────────────────────────────────────
    changes = debate_changes or []
    if r1:  # r1이 있어야 토론 섹션 의미있음
        if changes:
            change_lines = []
            for c in changes:
                icon_map = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
                icon = icon_map.get(c["analyst"], "•")
                reason = c.get("reason", "") or ""
                change_lines.append(
                    f"  {icon} {c['analyst'].upper()}: "
                    f"{c['r1_stance']} → <b>{c['r2_stance']}</b>\n"
                    f"     └ {reason[:70]}"
                )
            # 유지한 분석가도 표시
            changed_types = {c["analyst"] for c in changes}
            for atype, icon in [("bull","🟢"),("bear","🔴"),("neutral","⚪")]:
                if atype not in changed_types:
                    change_lines.append(f"  {icon} {atype.upper()}: {judgments[atype]['stance']} 유지")
            debate_section = "💬 <b>2라운드 토론</b>\n" + "\n".join(change_lines) + "\n"
        else:
            stances = " / ".join(
                f"{judgments[a]['stance']}"
                for a in ("bull","bear","neutral")
            )
            debate_section = f"💬 <b>2라운드 토론</b>  전원 의견 유지\n  └ {stances}\n"
    else:
        debate_section = ""

    # ── 최종 합의 ─────────────────────────────────────────────────────────────
    minority_line = "⚠️ 마이너리티 룰 발동!\n" if consensus.get("minority_triggered") else ""
    score_line = ""
    if "weighted_score" in consensus:
        score_line = f"  점수: {consensus['weighted_score']:+.3f}  가중치 B{consensus['weights'].get('bull',1):.2f}/Be{consensus['weights'].get('bear',1):.2f}/N{consensus['weights'].get('neutral',1):.2f}\n"

    text = (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🌅 <b>[장 시작 전 브리핑] {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{r1_section}"
        f"{debate_section}"
        f"⚖️ <b>최종 합의: {mode}</b>  포지션 {size}%\n"
        f"{score_line}"
        f"{minority_line}"
        f"\n🧠 <b>최종 판단</b>\n"
        f"🟢 Bull ({int(bull['confidence']*100)}%): {bull['key_reason']}\n"
        f"🔴 Bear ({int(bear['confidence']*100)}%): {bear['key_reason']}\n"
        f"⚪ Neutral ({int(neut['confidence']*100)}%): {neut['key_reason']}\n"
        f"\n💰 예수금: {cash:,}원\n"
        f"{credit_line}\n"
        f"📋 다음 튜닝: 30분 후\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    send(text)
    return text

def tuning_report(elapsed_min: int, tuning_result: dict,
                  prev_mode: str, positions: list) -> str:
    action = tuning_result.get("action","MAINTAIN")
    new_mode = tuning_result.get("mode", prev_mode)
    changed = action != "MAINTAIN"
    icon = "🔄" if changed else "✅"
    pos_txt = "\n".join([
        f"  {p['ticker']} {p['qty']}주 현재 {p['current_price']:,}원"
        for p in positions
    ]) or "  없음"
    text = f"""━━━━━━━━━━━━━━━━━━━━━━
{icon} <b>[{elapsed_min}분 튜닝]</b>
{f'이전: {prev_mode} → 현재: <b>{new_mode}</b>' if changed else f'유지: <b>{prev_mode}</b>'}
사유: {tuning_result.get('reason','')}
보유:
{pos_txt}
━━━━━━━━━━━━━━━━━━━━━━"""
    send(text)
    return text

def trade_alert(side: str, ticker: str, qty: int, price: int,
                strategy: str, tp: int, sl: int, reason: str = "",
                market: str = "KR") -> str:
    icon = "🟢" if side=="buy" else "🔴"
    total = price * qty
    # KR: 매수 0.015%, 매도 0.015%+증권거래세 0.18%=0.195%
    # US: 매수/매도 0.015% (해외주식 수수료)
    if market == "KR":
        fee_rate = 0.00015 if side == "buy" else 0.00195
    else:
        fee_rate = 0.00015
    fee = int(total * fee_rate)
    text = f"""━━━━━━━━━━━━━━━━
{icon} <b>[{'매수' if side=='buy' else '매도'} 체결]</b>
종목: {ticker}  {qty}주 @{price:,}원
총금액: {total:,}원  수수료≈{fee:,}원
전략: {strategy}
{'TP: ' + f'{tp:,}원  SL: {sl:,}원' if side=='buy' else '사유: '+reason}
━━━━━━━━━━━━━━━━"""
    send(text)
    return text


def _entry_price(pos: dict) -> float:
    return float(pos.get("entry", pos.get("avg_price", 0)) or 0)


def _display_price(pos: dict, kind: str) -> float:
    if kind == "entry":
        return float(pos.get("display_avg_price", pos.get("avg_price", pos.get("entry", 0))) or 0)
    return float(pos.get("display_current_price", pos.get("current_price", pos.get("eval_price", 0))) or 0)


def _display_currency(pos: dict, market: str) -> str:
    return pos.get("display_currency", pos.get("currency", "USD" if market == "US" else "KRW"))


def _format_display_price(value: float, currency: str) -> str:
    if currency == "USD":
        return f"${value:.4f}"
    return f"{value:,.0f}원"


def _position_line(pos: dict, market: str) -> str:
    entry = _display_price(pos, "entry")
    current = _display_price(pos, "current")
    currency = _display_currency(pos, market)
    pnl_pct = (current / entry - 1) * 100 if entry > 0 and current > 0 else 0.0
    return (
        f"  {pos['ticker']} {pos['qty']}주 | 매수가 {_format_display_price(entry, currency)} | "
        f"현재가 {_format_display_price(current, currency)} | "
        f"PnL {pnl_pct:+.2f}%"
    )

def pnl_alert(ticker: str, pnl_pct: float, pnl_krw: int, reason: str) -> str:
    icon = "✅" if pnl_krw > 0 else "❌"
    text = (
        f"{icon} <b>[청산]</b> {ticker}\n"
        f"{fmt_pct(pnl_pct)}  {pnl_krw:+,}원 (수수료 차감 후)  ({reason})"
    )
    send(text)
    return text

def emergency_alert(msg: str) -> str:
    text = f"""🚨 <b>[긴급 알림]</b>
{msg}"""
    send(text)
    return text

def analyst_reinvoke_alert(market: str, trigger: str, old_mode: str, new_mode: str,
                           judgments: dict, consensus: dict,
                           round1_judgments: dict = None,
                           debate_changes: list = None) -> str:
    """긴급 재판단 결과 텔레그램 전송"""
    now = datetime.now(KST).strftime("%H:%M")
    bull = judgments.get("bull", {}); bear = judgments.get("bear", {}); neut = judgments.get("neutral", {})
    size = consensus.get("size", 0)
    mode_changed = old_mode != new_mode
    mode_line = f"{old_mode} → <b>{new_mode}</b>" if mode_changed else f"<b>{new_mode}</b> (유지)"

    # 1라운드 섹션
    r1 = round1_judgments or {}
    if r1:
        def _r1(atype, icon):
            j = r1.get(atype, {})
            return f"{icon} {j.get('stance','-')} ({int(j.get('confidence',0)*100)}%): {j.get('key_reason','')[:50]}"
        r1_section = (
            f"\n🔍 <b>1라운드</b>\n"
            f"{_r1('bull','🟢')}\n"
            f"{_r1('bear','🔴')}\n"
            f"{_r1('neutral','⚪')}\n"
        )
    else:
        r1_section = ""

    # 2라운드 토론 섹션
    changes = debate_changes or []
    if changes:
        lines = []
        for c in changes:
            icon_map = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}
            icon = icon_map.get(c["analyst"], "•")
            lines.append(f"  {icon} {c['analyst'].upper()}: {c['r1_stance']} → <b>{c['r2_stance']}</b>")
        debate_section = "💬 <b>토론 변경</b>\n" + "\n".join(lines) + "\n"
    else:
        debate_section = ""

    text = (
        f"🚨 <b>[긴급 재판단] {market} {now}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ 트리거: {trigger}\n"
        f"⚖️ 모드: {mode_line}  포지션 {size}%\n"
        f"{r1_section}"
        f"{debate_section}"
        f"\n🧠 <b>최종 재판단</b>\n"
        f"🟢 Bull ({int(bull.get('confidence',0)*100)}%): {bull.get('key_reason','')}\n"
        f"🔴 Bear ({int(bear.get('confidence',0)*100)}%): {bear.get('key_reason','')}\n"
        f"⚪ Neutral ({int(neut.get('confidence',0)*100)}%): {neut.get('key_reason','')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    send(text)
    return text


def signal_alert(event: str, market: str, ticker: str,
                 strategy: str = "", price: float = 0,
                 reason: str = "", mode: str = "",
                 qty: int = 0, order_cost_krw: int = 0) -> str:
    """
    신호 발생/차단 시 텔레그램 전송.
    event: entry_signal | signal_blocked | entry_skip(already_holding)
    """
    now = datetime.now(KST).strftime("%H:%M:%S")
    mkt_icon = "🌅" if market == "KR" else "🌙"
    price_str = (
        f"{int(price):,}원" if market == "KR" and price > 0
        else f"${price:.2f}" if market == "US" and price > 0
        else ""
    )

    if event == "entry_signal":
        cost_str = f"  주문금액≈{order_cost_krw:,}원" if order_cost_krw else ""
        text = (
            f"🟢 <b>[진입신호] {ticker}</b>  {mkt_icon}{market} {now}\n"
            f"전략: {strategy}  가격: {price_str}{cost_str}\n"
            f"모드: {mode}"
        )
    elif event == "signal_blocked":
        text = (
            f"🚫 <b>[신호차단] {ticker}</b>  {mkt_icon}{market} {now}\n"
            f"전략: {strategy}  가격: {price_str}\n"
            f"모드: <b>{mode}</b> → 진입 억제"
        )
    elif event == "entry_skip" and reason == "already_holding":
        text = (
            f"🔵 <b>[보유중 스킵] {ticker}</b>  {mkt_icon}{market} {now}\n"
            f"신호: {strategy}  가격: {price_str}\n"
            f"이미 보유 중 → 중복 진입 차단"
        )
    else:
        return ""  # 기타 skip은 알림 불필요

    send(text)
    return text


def watchlist_alert(market: str, mode: str, tickers: list,
                    reasons: dict = None, excluded: list = None,
                    trigger: str = "session_open") -> str:
    """
    세션 시작 / 종목 재선택 시 모니터링 리스트 텔레그램 전송.
    trigger: session_open | rescreen | reuse
    """
    now  = datetime.now(KST).strftime("%H:%M")
    icon = "🌅" if market == "KR" else "🌙"
    reasons = reasons or {}

    trigger_label = {
        "session_open": "📋 오늘의 모니터링 종목",
        "rescreen":     "🔄 종목 재선택",
        "reuse":        "♻️ 판단 재사용 — 종목 재스크리닝",
    }.get(trigger, "📋 모니터링 종목")

    ticker_lines = []
    for t in tickers:
        r = reasons.get(t, "")
        ticker_lines.append(f"  • <b>{t}</b>  {r}" if r else f"  • <b>{t}</b>")

    excl_line = ""
    if excluded:
        excl_line = f"\n제외: {', '.join(excluded[:10])}" + ("..." if len(excluded) > 10 else "")

    text = (
        f"{icon} <b>[{market} {trigger_label}]</b>  {now}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"모드: <b>{mode}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        + "\n".join(ticker_lines)
        + excl_line
    )
    send(text)
    return text


def daily_summary(date: str, market: str, pnl_pct: float, pnl_krw: int,
                  trades: int, win_rate: float, cumulative: float,
                  judgments: dict, postmortem: dict) -> str:
    icon = "🌅" if market=="KR" else "🌙"
    credit_line = _credit_line()
    text = f"""━━━━━━━━━━━━━━━━━━━━━━
{icon} <b>[일일 결산] {date} {market}</b>
━━━━━━━━━━━━━━━━━━━━━━
💰 오늘: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원
📊 거래: {trades}건  승률: {win_rate*100:.1f}%
💼 누적: {cumulative:,.0f}원

🧠 판단 적중
  Bull:    {postmortem.get('bull_result','?')}
  Bear:    {postmortem.get('bear_result','?')}
  Neutral: {postmortem.get('neutral_result','?')}

💡 오늘의 교훈:
  {postmortem.get('key_lesson','')}

{credit_line}
━━━━━━━━━━━━━━━━━━━━━━"""
    send(text)
    return text


def _legacy_status_report_bootstrap(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    if positions:
        pos_lines = "\n".join(
            f"  {p['ticker']} {p['qty']}주 | 매수가 {_entry_price(p):,.0f} | "
            f"현재가 {p['current_price']:,} | "
            f"PnL {(p['current_price'] / max(_entry_price(p), 1) - 1) * 100:+.2f}%"
            for p in positions
        )
    else:
        pos_lines = "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    text = f"""📊<b>[상태 보고 {now}] {market}</b>
모드: <b>{mode}</b>
보유 포지션
{pos_lines}
예산 여유: {budget_avail:,.0f}원 | 현금: {cash:,.0f}원{watch}"""
    send(text)
    return text


def _legacy_dashboard_push_1(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🇰🇷" if market == "KR" else "🇺🇸"
    if positions:
        pos_lines = "\n".join(
            f"  {p['ticker']} {p['qty']}주 | 매수가 {_entry_price(p):,.0f} | "
            f"현재가 {p['current_price']:,} | "
            f"{(p['current_price'] / max(_entry_price(p), 1) - 1) * 100:+.2f}%"
            for p in positions
        )
    else:
        pos_lines = "  없음"

    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    text = f"""{icon} <b>[대시보드 {now}] {market}</b>
모드: <b>{mode}</b>
손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원
현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | 수수료누적: {total_fee:,.0f}원

보유 포지션
{pos_lines}

판단 요약
{j_lines}

모니터링: {tickers_txt}"""
    send(text)
    return text


def _legacy_status_report_1(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None,
                  stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {budget_avail:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원"
    )
    text = (
        f"📋<b>[상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"보유 포지션\n"
        f"{pos_lines}\n"
        f"{asset_line}"
        f"{watch}"
    )
    send(text)
    return text


def _legacy_dashboard_push_2(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0,
                   stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🟤" if market == "KR" else "🔉"
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"

    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원 | "
        f"수수료누적: {total_fee:,.0f}원"
    )
    text = (
        f"{icon} <b>[대시보드 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"{asset_line}\n\n"
        f"보유 포지션\n"
        f"{pos_lines}\n\n"
        f"판단 요약\n"
        f"{j_lines}\n\n"
        f"모니터링: {tickers_txt}"
    )
    send(text)
    return text


# Final override: keep the latest runtime-compatible signatures here.
def _legacy_status_report_2(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None,
                  stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {budget_avail:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원"
    )
    text = (
        f"📋<b>[상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"보유 포지션\n"
        f"{pos_lines}\n"
        f"{asset_line}"
        f"{watch}"
    )
    send(text)
    return text


def _legacy_dashboard_push_3(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0,
                   stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🟤" if market == "KR" else "🔉"
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"

    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원 | "
        f"수수료누적: {total_fee:,.0f}원"
    )
    text = (
        f"{icon} <b>[대시보드 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"{asset_line}\n\n"
        f"보유 포지션\n"
        f"{pos_lines}\n\n"
        f"판단 요약\n"
        f"{j_lines}\n\n"
        f"모니터링: {tickers_txt}"
    )
    send(text)
    return text


def _legacy_status_report_3(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None,
                  stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    pos_lines = "\n".join(_position_line_v2(p, market) for p in positions) if positions else "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    asset_line = (
        f"\n주식평가: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원"
        if total_equity_krw > 0 else ""
    )
    text = (
        f"📋<b>[상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"보유 포지션\n"
        f"{pos_lines}\n"
        f"예산 여유: {budget_avail:,.0f}원 | 현금: {cash:,.0f}원"
        f"{asset_line}"
        f"{watch}"
    )
    send(text)
    return text


def _legacy_dashboard_push_4(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0,
                   stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "📤" if market == "KR" else "🌉"
    pos_lines = "\n".join(_position_line_v2(p, market) for p in positions) if positions else "  없음"
    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    asset_line = (
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원\n"
        if total_equity_krw > 0 else ""
    )
    text = (
        f"{icon} <b>[대시보드 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | 수수료누적: {total_fee:,.0f}원\n"
        f"{asset_line}\n"
        f"보유 포지션\n"
        f"{pos_lines}\n\n"
        f"판단 요약\n"
        f"{j_lines}\n\n"
        f"모니터링: {tickers_txt}"
    )
    send(text)
    return text


def decision_event_alert(event: dict) -> str:
    action = event.get("action", "")
    ticker = event.get("ticker", "-")
    qty = int(event.get("qty", 0) or 0)
    reason = event.get("reason", "") or "-"
    detail = event.get("detail", "") or ""
    strategy = event.get("strategy", "") or "-"
    selected_reason = event.get("selected_reason", "") or ""
    market = event.get("market", "KR")
    native = float(event.get("price_native", 0) or 0)
    pnl_krw = int(event.get("pnl_krw", 0) or 0)
    pnl_pct = float(event.get("pnl_pct", 0) or 0)
    price_txt = f"${native:.4f}" if market == "US" and native > 0 else (f"{int(native):,}원" if native > 0 else "-")
    icon_map = {
        "buy_order": "🟢",
        "buy_failed": "🟠",
        "sell_filled": "🔴",
        "sell_failed": "⚠️",
    }
    title_map = {
        "buy_order": "매수 판단",
        "buy_failed": "매수 실패",
        "sell_filled": "매도 판단",
        "sell_failed": "매도 실패",
    }
    if action not in title_map:
        return ""
    lines = [
        f"{icon_map[action]} <b>[{title_map[action]}]</b> {ticker}",
        f"수량: {qty}주 | 가격: {price_txt} | 전략: {strategy}",
        f"이유: {reason}",
    ]
    if selected_reason:
        lines.append(f"선택사유: {selected_reason}")
    if detail:
        lines.append(f"상세: {detail}")
    if action == "sell_filled":
        lines.append(f"결과: {pnl_krw:+,}원 ({pnl_pct:+.2f}%)")
    text = "\n".join(lines)
    send(text)
    return text


def _position_line_v2(pos: dict, market: str) -> str:
    entry = _display_price(pos, "entry")
    current = _display_price(pos, "current")
    currency = _display_currency(pos, market)
    pnl_pct = (current / entry - 1) * 100 if entry > 0 and current > 0 else 0.0
    return (
        f"  {pos.get('ticker', '-')} {pos.get('qty', 0)}주 | "
        f"매수가 {_format_display_price(entry, currency)} | "
        f"현재가 {_format_display_price(current, currency)} | "
        f"PnL {pnl_pct:+.2f}%"
    )


def _legacy_status_report_4(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    text = (
        f"📤<b>[상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"보유 포지션\n"
        f"{pos_lines}\n"
        f"예산 여유: {budget_avail:,.0f}원 | 현금: {cash:,.0f}원"
        f"{watch}"
    )
    send(text)
    return text


def _legacy_dashboard_push_5(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "📊" if market == "KR" else "🌎"
    if positions:
        pos_lines = "\n".join(_position_line_v2(p, market) for p in positions)
    else:
        pos_lines = "  없음"

    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    text = (
        f"{icon} <b>[대시보드 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | 수수료누적: {total_fee:,.0f}원\n\n"
        f"보유 포지션\n"
        f"{pos_lines}\n\n"
        f"판단 요약\n"
        f"{j_lines}\n\n"
        f"모니터링: {tickers_txt}"
    )
    send(text)
    return text


def _legacy_status_report_0(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None,
                  stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    pos_lines = "\n".join(_position_line_v2(p, market) for p in positions) if positions else "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {budget_avail:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원"
    )
    text = (
        f"📋<b>[상태 보고 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"보유 포지션\n"
        f"{pos_lines}\n"
        f"{asset_line}"
        f"{watch}"
    )
    send(text)
    return text


def status_report(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None,
                  stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    return _legacy_status_report_0(
        market,
        mode,
        positions,
        budget_avail,
        cash,
        tickers=tickers,
        stock_value_krw=stock_value_krw,
        total_equity_krw=total_equity_krw,
    )


def dashboard_push(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list,
                   max_order_krw: int = 0, total_fee: int = 0,
                   stock_value_krw: float = 0.0, total_equity_krw: float = 0.0) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    icon = "📈" if market == "KR" else "🌎"
    pos_lines = "\n".join(_position_line_v2(p, market) for p in positions) if positions else "  없음"
    bull = judgments.get("bull", {})
    bear = judgments.get("bear", {})
    neut = judgments.get("neutral", {})
    j_lines = (
        f"  Bull {bull.get('stance', '-'):<14} ({int((bull.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Bear {bear.get('stance', '-'):<14} ({int((bear.get('confidence', 0) or 0) * 100):2d}%)\n"
        f"  Neu  {neut.get('stance', '-'):<14} ({int((neut.get('confidence', 0) or 0) * 100):2d}%)"
    )
    tickers_txt = ", ".join(tickers) if tickers else "-"
    asset_line = (
        f"현금: {cash:,.0f}원 | 주문한도: {max_order_krw:,.0f}원 | "
        f"보유주식: {stock_value_krw:,.0f}원 | 총자산: {total_equity_krw:,.0f}원 | "
        f"수수료누적: {total_fee:,.0f}원"
    )
    text = (
        f"{icon} <b>[대시보드 {now}] {market}</b>\n"
        f"모드: <b>{mode}</b>\n"
        f"손익: {fmt_pct(pnl_pct)}  {pnl_krw:+,}원\n"
        f"{asset_line}\n\n"
        f"보유 포지션\n"
        f"{pos_lines}\n\n"
        f"판단 요약\n"
        f"{j_lines}\n\n"
        f"모니터링: {tickers_txt}"
    )
    send(text)
    return text
