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
  /p  /pnl       — 오늘 손익 상세
  /pos           — 보유 포지션 목록
  /mode          — 현재 합의 모드
  /brain         — 누적 학습 요약
  /credit        — AI 크레딧 사용량

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

    # ── 합의 모드 ─────────────────────────────────────────────────────────────
    if cmd == "/mode":
        return _cmd_mode(bot)

    # ── 학습 요약 ─────────────────────────────────────────────────────────────
    if cmd == "/brain":
        return _cmd_brain(bot)

    # ── 크레딧 ────────────────────────────────────────────────────────────────
    if cmd == "/credit":
        return _cmd_credit()

    # ── 오늘 판단 요약 ────────────────────────────────────────────────────────
    if cmd == "/judge":
        return _cmd_judge(bot)

    # ── Claude 긴급 재판단 ────────────────────────────────────────────────────
    if cmd == "/claude":
        return _cmd_reinvoke(bot)

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

def _cmd_status(bot) -> str:
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
            lines.append(f"  {icon} {p['ticker']} {p['qty']}주 | {cur:,}원 | {pnl:+.2f}%")
    else:
        lines.append("\n📌 보유 포지션: 없음")

    return "\n".join(lines)


def _cmd_pnl(bot) -> str:
    now = datetime.now(KST).strftime("%H:%M")
    equity    = bot.risk.equity()
    start_eq  = bot.risk.session_start_equity
    daily_pnl = bot.risk.daily_pnl
    pnl_pct   = daily_pnl / max(start_eq, 1) * 100
    cash      = bot.risk.cash

    icon = "🟢" if daily_pnl > 0 else "🔴" if daily_pnl < 0 else "⚪"
    trades = bot.risk.trade_log
    sell_trades = [t for t in trades if t.get("side") == "sell"]
    wins  = sum(1 for t in sell_trades if t.get("pnl", 0) > 0)
    total = len(sell_trades)
    wr    = f"{wins}/{total} 승" if total else "거래 없음"

    lines = [
        f"💰 <b>[손익 상세 {now}]</b>",
        f"━━━━━━━━━━━━━━━━",
        f"{icon} 오늘 P&L: {pnl_pct:+.2f}%  {daily_pnl:+,}원",
        f"📊 체결: {wr}",
        f"💵 현금: {cash:,.0f}원",
        f"💼 평가액: {equity:,.0f}원",
    ]

    if sell_trades:
        lines.append("\n<b>청산 내역</b>")
        for t in sell_trades[-5:]:  # 최근 5건
            p = t.get("pnl", 0)
            ic = "🟢" if p > 0 else "🔴"
            lines.append(f"  {ic} {t.get('ticker','-')} {p:+,}원 ({t.get('strategy','-')})")

    return "\n".join(lines)


def _cmd_positions(bot) -> str:
    pos = bot.risk.positions
    if not pos:
        return "📌 보유 포지션 없음"
    lines = ["📌 <b>보유 포지션</b>", "━━━━━━━━━━━━━━━━"]
    for p in pos:
        entry = p.get("entry", 0)
        cur   = p.get("current_price", entry)
        pnl   = (cur / entry - 1) * 100 if entry else 0
        tp    = p.get("tp", 0)
        sl    = p.get("sl", 0)
        icon  = "🟢" if pnl > 0 else "🔴"
        lines.append(
            f"{icon} <b>{p['ticker']}</b>  {p['qty']}주\n"
            f"  진입 {entry:,}  현재 {cur:,}  PnL {pnl:+.2f}%\n"
            f"  TP {tp:,}  SL {sl:,}  전략 {p.get('strategy','-')}"
        )
    return "\n".join(lines)


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


def _cmd_credit() -> str:
    try:
        from credit_tracker import summary as credit_summary
        cr  = credit_summary()
        td  = cr["today"]
        tot = cr["total"]
        return (
            f"🤖 <b>AI 크레딧 사용량</b>\n"
            f"━━━━━━━━━━━━━━━━\n"
            f"오늘: ${td['cost_usd']:.4f}  (≈{td['cost_krw']:,}원)\n"
            f"  입력 {td['input_tokens']:,}tok  출력 {td['output_tokens']:,}tok\n"
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


def _cmd_close(bot, ticker: str) -> str:
    pos = next((p for p in bot.risk.positions if p["ticker"] == ticker), None)
    if not pos:
        return f"❌ {ticker} 포지션 없음"
    market = bot._ticker_market(ticker)
    try:
        from kis_api import place_order, get_price
        price_info = get_price(ticker, bot.token, market=market)
        cur_price  = price_info.get("price", 0)
        result = place_order(ticker, pos["qty"], cur_price, "sell", bot.token, market=market)
        if result.get("success"):
            pnl = (cur_price - pos["entry"]) * pos["qty"]
            icon = "🟢" if pnl > 0 else "🔴"
            return (
                f"{icon} <b>[수동 청산]</b> {ticker}\n"
                f"  {pos['qty']}주 @{cur_price:,}원\n"
                f"  P&L: {pnl:+,}원"
            )
        else:
            return f"❌ 청산 실패: {result.get('msg','')}"
    except Exception as e:
        return f"❌ 청산 오류: {e}"


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
