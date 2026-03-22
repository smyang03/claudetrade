"""minority_report/postmortem.py - 장 마감 후 사후 분석

변경 이력:
- trade_log 파라미터 추가 → 당일 체결 내역을 Claude에게 전달
- 전략별 성과 자동 집계 → BrainDB.update_strategy_performance()
- judgment_log에 trade_log + postmortem 원본 보존 (파인튜닝 raw 데이터)
- best_trade / worst_trade / worst_trade_reason 필드 추가
- HALT / 거래 없는 날 postmortem 스킵 안전장치
"""
import os, json, sys
import anthropic
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_judgment_logger, get_minority_logger
from claude_memory import brain as BrainDB
from credit_tracker import record as credit_record

log          = get_minority_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")


def _format_trade_log(trade_log: list) -> str:
    """체결 내역 → Claude 프롬프트용 텍스트"""
    if not trade_log:
        return "  (체결 없음)"
    lines = []
    for t in trade_log:
        side  = "매수" if t.get("side") == "buy" else "매도"
        pnl   = t.get("pnl", 0)
        pnl_s = f" PnL {pnl:+,}원" if pnl else ""
        lines.append(
            f"  [{side}] {t.get('ticker','-')} {t.get('qty',0)}주 "
            f"@{t.get('price', t.get('entry', 0)):,} "
            f"전략:{t.get('strategy','-')}{pnl_s}"
        )
    return "\n".join(lines)


def _strategy_pnl(trade_log: list) -> dict:
    """전략별 PnL 집계 {strategy: [pnl_pct, ...]}"""
    result: dict = {}
    sells = [t for t in trade_log if t.get("side") == "sell" and "pnl_pct" in t]
    for t in sells:
        s = t.get("strategy", "unknown")
        result.setdefault(s, []).append(t["pnl_pct"])
    return result


def run(market: str, date: str, today_judgment: dict,
        actual_result: dict, digest_prompt: str,
        trade_log: list = None) -> dict:
    """
    장 마감 후 Claude 사후 분석.

    Parameters
    ----------
    trade_log : 당일 체결 내역 (trading_bot의 self.risk.trade_log)
                없으면 빈 리스트로 처리
    """
    trade_log = trade_log or []

    judgments      = today_judgment.get("judgments", {})
    consensus      = today_judgment.get("consensus", {})
    consensus_mode = consensus.get("mode", "CAUTIOUS")

    # ── HALT 또는 판단 없는 날 스킵 ──────────────────────────────────────────
    if not judgments or consensus_mode == "HALT":
        log.info(f"[postmortem skip] {date} {market} — HALT 또는 판단 없음")
        return {
            "bull_result": "PARTIAL", "bear_result": "PARTIAL", "neutral_result": "PARTIAL",
            "bull_why": "HALT 스킵", "bear_why": "HALT 스킵", "neutral_why": "HALT 스킵",
            "key_lesson": "HALT 세션 — 거래 없음",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "HALT", "issue_desc": "", "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    brain_summary = BrainDB.generate_prompt_summary(market)
    trade_section = _format_trade_log(trade_log)

    # 최고/최악 거래 요약
    sells = [t for t in trade_log if t.get("side") == "sell" and "pnl" in t]
    best  = max(sells, key=lambda t: t["pnl"], default=None)
    worst = min(sells, key=lambda t: t["pnl"], default=None)

    best_s  = (f"{best['ticker']} {best['pnl']:+,}원 ({best.get('strategy','-')})"
               if best else "없음")
    worst_s = (f"{worst['ticker']} {worst['pnl']:+,}원 ({worst.get('strategy','-')})"
               if worst else "없음")

    prompt = f"""트레이딩 AI 사후 분석가입니다. 오늘 아침 판단과 실제 매매 결과를 분석하세요.

[아침 판단]
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  합의:    {consensus_mode}

[실제 결과]
  시장 등락: {actual_result.get('market_change', 0):+.2f}%
  봇 손익:   {actual_result.get('pnl_pct', 0):+.2f}%  {'승' if actual_result.get('win') else '패'}
  거래 수:   {len(trade_log)}건 (체결 {actual_result.get('trades', 0)}건)

[오늘 체결 내역]
{trade_section}
  최고 거래: {best_s}
  최악 거래: {worst_s}

[시장 데이터]
{digest_prompt[:350]}

[누적 학습]
{brain_summary[:200]}

JSON으로만 응답 (설명 없이):
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "한 문장",
  "bear_why":      "한 문장",
  "neutral_why":   "한 문장",
  "key_lesson":    "오늘 핵심 교훈 (다음날 반영)",
  "best_trade":    "최고 거래 요인 한 문장 또는 null",
  "worst_trade":   "최악 거래 요인 한 문장 또는 null",
  "worst_trade_reason": "최악 거래의 근본 원인",
  "issue_type":    "이슈 유형",
  "issue_desc":    "한 문장",
  "pattern_id":    "기존ID 또는 null",
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "brain에 추가할 교훈 또는 null",
    "market_regime": "강세장|약세장|횡보|변동성장"
  }},
  "correction_guide": {{
    "bull_adjustments":  [],
    "bear_adjustments":  [],
    "tuning_rules":      [],
    "today_notes":       ""
  }}
}}"""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=700,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        pm = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "postmortem")
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        win = actual_result.get("win", False)
        pm = {
            "bull_result": "HIT" if win else "MISS",
            "bear_result": "MISS" if win else "HIT",
            "neutral_result": "PARTIAL",
            "bull_why": "자동", "bear_why": "자동", "neutral_why": "자동",
            "key_lesson": "오류로 자동 판정",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "미분류", "issue_desc": "", "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    # ── brain 업데이트 ────────────────────────────────────────────────────────
    brain      = BrainDB.load()
    recent     = brain["markets"][market].get("recent_days", [])

    BrainDB.update_analyst(market, "bull",    pm["bull_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "bear",    pm["bear_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "neutral", pm["neutral_result"] == "HIT", recent)
    BrainDB.update_mode_performance(
        market, consensus_mode,
        actual_result.get("pnl_pct", 0), actual_result.get("win", False)
    )

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
    })

    # ── 전략별 성과 자동 업데이트 ─────────────────────────────────────────────
    for strat, pnls in _strategy_pnl(trade_log).items():
        avg_pnl = sum(pnls) / len(pnls)
        BrainDB.update_strategy_performance(market, strat, avg_pnl, avg_pnl > 0)

    # ── 토론 결과 정답 여부 업데이트 ─────────────────────────────────────────
    try:
        BrainDB.update_debate_outcome(market, date, actual_result.get("win", False))
    except Exception as e:
        log.warning(f"토론 결과 업데이트 실패: {e}")

    # ── 내일 Claude 보정 지침 업데이트 ───────────────────────────────────────
    cg = pm.get("correction_guide", {})
    if cg:
        BrainDB.update_correction_guide(market, cg)

    log.info(
        f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
        f"Neut:{pm['neutral_result']} | {pm['key_lesson'][:60]}"
    )

    # ── JSONL 학습 로그 저장 (프롬프트 + 응답 + 거래 원본) ───────────────────
    judgment_log.info(
        f"[postmortem {date} {market}] "
        f"Bull:{pm['bull_result']} Bear:{pm['bear_result']} Neutral:{pm['neutral_result']}",
        extra={"extra": {
            "event":          "postmortem",
            "date":           date,
            "market":         market,
            "consensus_mode": consensus_mode,
            "actual_result":  actual_result,
            "trade_log":      trade_log,          # 당일 체결 원본 보존
            "postmortem":     pm,
            "strategy_pnl":   _strategy_pnl(trade_log),
        }},
    )

    return pm
