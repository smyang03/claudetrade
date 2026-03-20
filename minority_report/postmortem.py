"""minority_report/postmortem.py - 장 마감 후 사후 분석 (거래 논의 포함)"""
import os, json, sys
import anthropic
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_judgment_logger, get_minority_logger
from claude_memory import brain as BrainDB

log          = get_minority_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _format_trades(trade_log: list) -> str:
    """trade_log → 읽기 좋은 텍스트"""
    if not trade_log:
        return "  거래 없음"
    lines = []
    for t in trade_log:
        side   = t.get("side", "?")
        ticker = t.get("ticker", "?")
        qty    = t.get("qty", 0)
        price  = int(t.get("price", 0))
        pnl    = t.get("pnl", None)
        pnl_pct= t.get("pnl_pct", None)
        reason = t.get("reason", t.get("strategy", ""))
        if side == "buy":
            lines.append(f"  매수  {ticker} {qty}주 @ {price:,}  전략={reason}")
        else:
            pnl_str = f" PnL={pnl_pct:+.2f}%" if pnl_pct is not None else ""
            lines.append(f"  매도  {ticker} {qty}주 @ {price:,}{pnl_str}  사유={reason}")
    return "\n".join(lines)


def run(market: str, date: str, today_judgment: dict,
        actual_result: dict, digest_prompt: str,
        trade_log: list | None = None) -> dict:
    """
    today_judgment: trading_bot의 self.today_judgment 전체 dict
    trade_log: self.risk.trade_log (매수/매도 체결 내역 리스트)
    """
    judgments      = today_judgment.get("judgments", {})
    consensus      = today_judgment.get("consensus", {})
    consensus_mode = consensus.get("mode", "CAUTIOUS")
    trade_log      = trade_log or []

    brain_summary  = BrainDB.generate_prompt_summary(market)
    trades_txt     = _format_trades(trade_log)

    # 수익 종목 / 손실 종목 요약
    wins  = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses= [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) <= 0]

    prompt = f"""당신은 트레이딩 AI의 사후 분석가입니다.
오늘 거래와 아침 판단을 비교해 솔직하게 복기하세요.

━━━ 아침 판단 ━━━
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  합의 모드: {consensus_mode} (size={consensus.get('size','-')}%)

━━━ 오늘 체결 내역 ({len(trade_log)}건) ━━━
{trades_txt}

━━━ 실제 결과 ━━━
  시장 변동: {actual_result.get('market_change',0):+.2f}%
  내 손익:   {actual_result.get('pnl_pct',0):+.2f}% ({actual_result.get('pnl_krw',0):+,}원)
  수익 청산: {len(wins)}건 / 손실 청산: {len(losses)}건
  {'✅ 승' if actual_result.get('win') else '❌ 패'}

━━━ 시장 컨텍스트 ━━━
{digest_prompt[:300]}

━━━ 누적 학습 현황 ━━━
{brain_summary[:300]}

━━━ 분석 지침 ━━━
1. 아침 Bull/Bear/Neutral 판단이 실제로 맞았는지 평가하세요.
2. 어떤 거래가 왜 좋았고 왜 나빴는지 구체적으로 설명하세요.
3. 같은 상황이 반복된다면 내일 어떻게 다르게 할지 제안하세요.
4. 손실 거래가 있다면 반드시 원인을 분석하세요.

아래 JSON으로만 응답하세요:
{{
  "bull_result": "HIT|MISS|PARTIAL",
  "bear_result": "HIT|MISS|PARTIAL",
  "neutral_result": "HIT|MISS|PARTIAL",
  "bull_why": "한 문장으로 왜 맞았/틀렸는지",
  "bear_why": "한 문장으로 왜 맞았/틀렸는지",
  "neutral_why": "한 문장으로 왜 맞았/틀렸는지",
  "best_trade": "가장 잘된 거래 ticker 또는 null",
  "worst_trade": "가장 아쉬운 거래 ticker 또는 null",
  "worst_trade_reason": "손실/아쉬운 이유 한 문장",
  "key_lesson": "오늘 핵심 교훈 (내일 행동에 반영할 것)",
  "issue_type": "이슈 유형 (개별기업_호재/지수급락/변동성확대 등)",
  "issue_desc": "이슈 한 문장 요약",
  "pattern_id": "기존 패턴 ID 또는 null",
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson": "교훈 문장 또는 null",
    "market_regime": "현재 장세 한 단어"
  }},
  "correction_guide": {{
    "bull_adjustments": ["내일 Bull 판단 시 주의사항"],
    "bear_adjustments": ["내일 Bear 판단 시 주의사항"],
    "tuning_rules": ["장중 튜닝 규칙"],
    "today_notes": "내일 특별 주의사항"
  }}
}}"""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        pm = json.loads(raw)
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        win = actual_result.get("win", False)
        pm = {
            "bull_result":    "HIT" if win else "MISS",
            "bear_result":    "MISS" if win else "HIT",
            "neutral_result": "PARTIAL",
            "bull_why": "자동 판정", "bear_why": "자동 판정", "neutral_why": "자동 판정",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "key_lesson": "오류로 자동 판정",
            "issue_type": "미분류", "issue_desc": "", "pattern_id": None,
            "brain_updates": {
                "bull_reliability_change": "stable",
                "bear_reliability_change": "stable",
                "new_lesson": None, "market_regime": "unknown"
            },
            "correction_guide": {
                "bull_adjustments": [], "bear_adjustments": [],
                "tuning_rules": [], "today_notes": ""
            }
        }

    # ── brain 업데이트 ────────────────────────────────────────────────────────
    recent = BrainDB.load()["markets"][market].get("recent_days", [])

    BrainDB.update_analyst(market, "bull",    pm["bull_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "bear",    pm["bear_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "neutral", pm["neutral_result"] == "HIT", recent)

    BrainDB.update_mode_performance(
        market, consensus_mode,
        actual_result.get("pnl_pct", 0),
        actual_result.get("win", False)
    )

    # 거래한 전략별 성과도 업데이트
    strategy_pnl: dict[str, list] = {}
    for t in trade_log:
        if t.get("side") == "sell":
            strat = t.get("strategy", "unknown")
            strategy_pnl.setdefault(strat, []).append(t.get("pnl_pct", 0))
    for strat, pnls in strategy_pnl.items():
        avg_pnl = sum(pnls) / len(pnls)
        BrainDB.update_strategy_performance(market, strat, avg_pnl, avg_pnl > 0)

    bu = pm.get("brain_updates", {})
    if bu.get("new_lesson"):
        BrainDB.update_beliefs(market, {"new_lesson": bu["new_lesson"]})
    if bu.get("market_regime"):
        BrainDB.update_beliefs(market, {"market_regime": bu["market_regime"]})

    BrainDB.update_issue_pattern(market, {
        "matched_id":  pm.get("pattern_id"),
        "type":        pm.get("issue_type", "미분류"),
        "description": pm.get("issue_desc", ""),
        "bull_hit":    pm["bull_result"] == "HIT",
        "pnl_pct":     actual_result.get("pnl_pct", 0),
        "insight":     pm.get("key_lesson", ""),
    })

    BrainDB.add_daily_record(market, {
        "date":           date,
        "mode":           consensus_mode,
        "pnl_pct":        actual_result.get("pnl_pct", 0),
        "win":            actual_result.get("win", False),
        "bull_result":    pm["bull_result"],
        "bear_result":    pm["bear_result"],
        "neutral_result": pm["neutral_result"],
        "key_lesson":     pm.get("key_lesson", ""),
        "best_trade":     pm.get("best_trade"),
        "worst_trade":    pm.get("worst_trade"),
        "trades":         len(trade_log),
    })

    cg = pm.get("correction_guide", {})
    if cg:
        BrainDB.update_correction_guide(market, cg)

    log.info(
        f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
        f"Neut:{pm['neutral_result']} | {pm.get('key_lesson','')[:60]}"
    )
    if pm.get("worst_trade"):
        log.warning(
            f"[worst_trade] {pm['worst_trade']} — {pm.get('worst_trade_reason','')}"
        )

    judgment_log.info(
        f"[postmortem {date} {market}] Bull:{pm['bull_result']} "
        f"Bear:{pm['bear_result']} Neutral:{pm['neutral_result']}",
        extra={"extra": {
            "event":          "postmortem",
            "date":           date,
            "market":         market,
            "consensus_mode": consensus_mode,
            "actual_result":  actual_result,
            "trade_log":      trade_log,
            "postmortem":     pm,
        }},
    )
    return pm
