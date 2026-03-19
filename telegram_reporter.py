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

def morning_briefing(market: str, judgments: dict, consensus: dict,
                     balance: dict, digest: dict) -> str:
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    bull = judgments["bull"]; bear = judgments["bear"]; neut = judgments["neutral"]
    mode = consensus["mode"]; size = consensus["size"]
    cash = balance.get("cash", 0)

    text = f"""━━━━━━━━━━━━━━━━━━━━━━
🌅 <b>[장 시작 전 브리핑] {now}</b>
━━━━━━━━━━━━━━━━━━━━━━

🧠 <b>마이너리티 판단</b>
🟢 Bull ({int(bull['confidence']*100)}%): {bull['key_reason']}
🔴 Bear ({int(bear['confidence']*100)}%): {bear['key_reason']}
⚪ Neutral ({int(neut['confidence']*100)}%): {neut['key_reason']}

⚖️ <b>합의: {mode}</b>  포지션 {size}%
{'⚠️ 마이너리티 룰 발동!' if consensus.get('minority_triggered') else ''}

💰 예수금: {cash:,}원
📋 다음 튜닝: 30분 후
━━━━━━━━━━━━━━━━━━━━━━"""
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


def daily_summary(date: str, market: str, pnl_pct: float, pnl_krw: int,
                  trades: int, win_rate: float, cumulative: float,
                  judgments: dict, postmortem: dict) -> str:
    icon = "🌅" if market=="KR" else "🌙"
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
━━━━━━━━━━━━━━━━━━━━━━"""
    send(text)
    return text
