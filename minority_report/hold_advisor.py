"""
minority_report/hold_advisor.py — TP 도달 시 분석가 3명 HOLD/SELL 합의

TRAILING_ANALYST_ENABLED=true 일 때만 호출됨.
기본값 false → 트레일링 스탑 즉시 활성화.
"""
import os
import json
import time
import anthropic
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trading_logger
from credit_tracker import record as credit_record

log    = get_trading_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

PERSONAS = {
    "bull": "당신은 15년 경력의 성장주 모멘텀 트레이더입니다. 추세가 살아있으면 보유를 선호합니다.",
    "bear": "당신은 헤지펀드 리스크 매니저입니다. 이익 실현 타이밍을 중시하고 욕심을 경계합니다.",
    "neutral": "당신은 퀀트 통계 분석가입니다. 데이터 기반으로 냉정하게 판단합니다.",
}


def _ask_one(analyst_type: str, pos: dict, market: str, digest_prompt: str) -> dict:
    entry   = pos.get("entry", 0)
    cp      = pos.get("current_price", entry)
    pnl_pct = (cp / entry - 1) * 100 if entry else 0
    ticker  = pos.get("ticker", "-")
    strat   = pos.get("strategy", "-")
    held    = pos.get("held_days", 0)

    prompt = f"""{PERSONAS[analyst_type]}

목표가에 도달한 포지션을 계속 보유할지 판단하세요.

━━━ 포지션 ━━━
  종목: {ticker} ({market})  전략: {strat}
  진입가: {entry:,}  현재가: {cp:,}  수익률: {pnl_pct:+.2f}%
  보유일: {held}일

━━━ 시장 컨텍스트 ━━━
{digest_prompt[:250] if digest_prompt else "  (정보 없음)"}

HOLD(보유) 또는 SELL(청산) 중 하나를 선택하고,
HOLD 시 트레일링 폭(trail_pct: 0.02~0.05)을 제안하세요.

JSON으로만 응답:
{{
  "action": "HOLD" or "SELL",
  "confidence": 0.0~1.0,
  "trail_pct": 0.03,
  "reason": "한 문장"
}}"""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor")
        return {
            "action":     result.get("action", "SELL").upper(),
            "confidence": float(result.get("confidence", 0.5)),
            "trail_pct":  max(0.02, min(0.05, float(result.get("trail_pct", 0.03)))),
            "reason":     result.get("reason", ""),
        }
    except Exception as e:
        log.warning(f"[hold_advisor:{analyst_type}] 오류 → SELL 기본값: {e}")
        return {"action": "SELL", "confidence": 0.5, "trail_pct": 0.03, "reason": "오류"}


def ask(pos: dict, market: str, digest_prompt: str = "", delay: float = 0.5) -> dict:
    """
    분석가 3명 합의 → HOLD/SELL 결정.

    Returns
    -------
    {
        "action": "HOLD" | "SELL",
        "trail_pct": 0.03,
        "votes": {"bull": ..., "bear": ..., "neutral": ...},
    }
    """
    ticker  = pos.get("ticker", "-")
    votes   = {}
    for atype in ("bull", "bear", "neutral"):
        votes[atype] = _ask_one(atype, pos, market, digest_prompt)
        time.sleep(delay)

    hold_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "HOLD"
    )
    sell_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "SELL"
    )
    action = "HOLD" if hold_score > sell_score else "SELL"

    # trail_pct: HOLD 투표한 분석가들의 평균
    hold_voters = [v for v in votes.values() if v["action"] == "HOLD"]
    trail_pct   = (
        sum(v["trail_pct"] for v in hold_voters) / len(hold_voters)
        if hold_voters else 0.03
    )

    log.info(
        f"[hold_advisor] {ticker} → {action} "
        f"(HOLD {hold_score:.2f} vs SELL {sell_score:.2f}) trail={trail_pct:.2f}"
    )
    return {"action": action, "trail_pct": round(trail_pct, 3), "votes": votes}
