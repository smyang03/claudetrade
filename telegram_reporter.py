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
                strategy: str, tp: int, sl: int, reason: str = "") -> str:
    icon = "🟢" if side=="buy" else "🔴"
    text = f"""━━━━━━━━━━━━━━━━
{icon} <b>[{'매수' if side=='buy' else '매도'} 체결]</b>
종목: {ticker}  {qty}주 @{price:,}원
전략: {strategy}
{'TP: ' + f'{tp:,}원  SL: {sl:,}원' if side=='buy' else '사유: '+reason}
━━━━━━━━━━━━━━━━"""
    send(text)
    return text

def pnl_alert(ticker: str, pnl_pct: float, pnl_krw: int, reason: str) -> str:
    icon = "✅" if pnl_krw>0 else "❌"
    text = f"""{icon} <b>[청산]</b> {ticker}
{fmt_pct(pnl_pct)}  {pnl_krw:+,}원  ({reason})"""
    send(text)
    return text

def emergency_alert(msg: str) -> str:
    text = f"""🚨 <b>[긴급 알림]</b>
{msg}"""
    send(text)
    return text

def status_report(market: str, mode: str, positions: list,
                  budget_avail: float, cash: float, tickers: list = None) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    if positions:
        pos_lines = "\n".join(
            f"  {p['ticker']} {p['qty']}주 | {p['current_price']:,}원 | "
            f"PnL {(p['current_price'] / p['entry'] - 1) * 100:+.2f}%"
            for p in positions
        )
    else:
        pos_lines = "  없음"
    watch = f"\n모니터링: {', '.join(tickers)}" if tickers else ""
    text = f"""⏱ <b>[상태 보고 {now}] {market}</b>
모드: <b>{mode}</b>
보유 포지션:
{pos_lines}
예산 잔여: {budget_avail:,.0f}원 | 현금: {cash:,.0f}원{watch}"""
    send(text)
    return text


def dashboard_push(market: str, mode: str, positions: list,
                   cash: float, pnl_pct: float, pnl_krw: int,
                   judgments: dict, tickers: list) -> str:
    """
    대시보드 상태를 텔레그램으로 전송.
    변동 없으면 호출 전에 걸러야 함 (이 함수 자체는 항상 전송).
    """
    now = datetime.now(KST).strftime("%H:%M")
    icon = "🌅" if market == "KR" else "🌙"

    # 포지션 라인
    if positions:
        pos_lines = "\n".join(
            f"  {p['ticker']} {p['qty']}주 | {p['current_price']:,} | "
            f"{(p['current_price']/p['entry']-1)*100:+.2f}%"
            for p in positions
        )
    else:
        pos_lines = "  없음"

    # 판단 요약
    bull = judgments.get("bull", {}); bear = judgments.get("bear", {}); neut = judgments.get("neutral", {})
    j_lines = (
        f"🟢 Bull {bull.get('stance','-')} {int(bull.get('confidence',0)*100)}%\n"
        f"🔴 Bear {bear.get('stance','-')} {int(bear.get('confidence',0)*100)}%\n"
        f"⚪ Neut {neut.get('stance','-')} {int(neut.get('confidence',0)*100)}%"
    ) if bull else "판단 없음"

    pnl_icon = "🟢" if pnl_pct > 0 else "🔴" if pnl_pct < 0 else "⚪"
    credit_line = _credit_line()

    text = f"""{icon} <b>[{market} 현황 {now}]</b>
━━━━━━━━━━━━━━━━
모드: <b>{mode}</b>
{pnl_icon} 오늘 P&L: {pnl_pct:+.2f}%  {pnl_krw:+,}원
💰 현금: {cash:,.0f}원
━━━━━━━━━━━━━━━━
🧠 판단
{j_lines}
━━━━━━━━━━━━━━━━
📌 모니터링: {', '.join(tickers) if tickers else '-'}
{credit_line}"""
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
