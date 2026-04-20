"""minority_report/postmortem.py - 장 마감 후 사후 분석

변경 이력:
- trade_log 파라미터 추가 → 당일 체결 내역을 Claude에게 전달
- 전략별 성과 자동 집계 → BrainDB.update_strategy_performance()
- judgment_log에 trade_log + postmortem 원본 보존 (파인튜닝 raw 데이터)
- best_trade / worst_trade / worst_trade_reason 필드 추가
- HALT / 거래 없는 날 postmortem 스킵 안전장치
"""
import os, json, re, sys
import anthropic
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_judgment_logger, get_minority_logger
from claude_memory import brain as BrainDB
from credit_tracker import record as credit_record
from minority_report.raw_call_logger import save as save_raw_call

log          = get_minority_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

_POSTMORTEM_PLACEHOLDER_LESSONS = {
    "오류로 자동 판정",
    "API 오류로 자동 판정",
    "postmortem 응답 실패",
    "HALT 세션 — 거래 없음",
}


def _is_placeholder_lesson(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if text in _POSTMORTEM_PLACEHOLDER_LESSONS:
        return True
    return ("자동 판정" in text) or ("응답 실패" in text)


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 추출 — 형식 무관하게 견고하게 파싱"""
    # trailing comma 제거 (LLM이 자주 생성하는 오류)
    def _fix(s: str) -> str:
        return re.sub(r",(\s*[}\]])", r"\1", s)

    # 1) ```json ... ``` 또는 ``` ... ``` 블록 (탐욕적 매칭으로 중첩 {} 포함)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(_fix(m.group(1)))
    # 2) { ... } 직접 추출 — 첫 번째 { 부터 마지막 } 까지
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(_fix(text[start:end + 1]))
    raise ValueError(f"JSON 추출 실패: {text[:200]}")


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
        if s in ("broker_sync", "broker_balance", "", None):
            continue
        result.setdefault(s, []).append(t["pnl_pct"])
    return result


_BULL_STANCES    = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"}
_NEUTRAL_STANCES = {"NEUTRAL"}
_BEAR_STANCES    = {"MILD_BEAR", "CAUTIOUS_BEAR"}
_AVOID_STANCES   = {"DEFENSIVE", "HALT"}   # 방향 예측 아님 — 노출 회피가 맞았는가로 판정

_HIT_THRESHOLD   = 0.5   # 방향성 판단 HIT 최소 임계값
_FLAT_THRESHOLD  = 0.5   # NEUTRAL HIT: |시장| <= 0.5%
_FLAT_PARTIAL    = 1.5   # NEUTRAL PARTIAL: 0.5~1.5%
_AVOID_MISS      = 1.0   # DEFENSIVE MISS: 시장 >= +1.0% (놓친 상승 기회)


def _code_judge_hit_miss(stance: str, market_change_pct: float) -> str:
    """
    분석가 스탠스 + 실제 시장 등락률로 HIT/MISS/PARTIAL 객관 판정.
    Claude 자기평가 편향 제거용.

    BULL/BEAR: 방향 예측 정확도
    - BULL HIT:    시장 >= +0.5%
    - BULL PARTIAL: 0% < 시장 < +0.5%
    - BULL MISS:   시장 <= 0%
    - BEAR HIT:    시장 <= -0.5%
    - BEAR PARTIAL: -0.5% < 시장 < 0%
    - BEAR MISS:   시장 >= 0%

    NEUTRAL: 횡보 예측 정확도
    - HIT: |시장| <= 0.5%, PARTIAL: <= 1.5%, MISS: > 1.5%

    DEFENSIVE/HALT: 노출 회피 적절성 ("낮은 노출이 유리했는가")
    - HIT:    시장 < -0.5%  (리스크 현실화, 회피 정당)
    - PARTIAL: -0.5% <= 시장 < +1.0% (애매, 회피도 나쁘지 않음)
    - MISS:   시장 >= +1.0% (강한 상승 놓침, 회피가 잘못된 판단)
    """
    chg = market_change_pct
    abs_chg = abs(chg)

    if stance in _BULL_STANCES:
        if chg >= _HIT_THRESHOLD:  return "HIT"
        if chg > 0:                 return "PARTIAL"
        return "MISS"
    elif stance in _BEAR_STANCES:
        if chg <= -_HIT_THRESHOLD: return "HIT"
        if chg < 0:                 return "PARTIAL"
        return "MISS"
    elif stance in _AVOID_STANCES:
        if chg < -_HIT_THRESHOLD:  return "HIT"
        if chg < _AVOID_MISS:       return "PARTIAL"
        return "MISS"
    else:  # NEUTRAL
        if abs_chg <= _FLAT_THRESHOLD:  return "HIT"
        if abs_chg <= _FLAT_PARTIAL:    return "PARTIAL"
        return "MISS"


def _format_decision_event_log(decision_event_log: list) -> str:
    if not decision_event_log:
        return "  (의사결정 로그 없음)"
    lines = []
    for e in decision_event_log[-20:]:
        ts = str(e.get("timestamp", ""))[11:19]
        ticker = e.get("ticker", "-")
        action = e.get("action", "-")
        reason = e.get("reason", "")
        detail = e.get("detail", "")
        qty = int(e.get("qty", 0) or 0)
        price_native = float(e.get("price_native", 0) or 0)
        price_krw = float(e.get("price_krw", 0) or 0)
        selected_reason = e.get("selected_reason", "")
        pieces = [
            f"[{ts}] {ticker} {action}",
            f"{qty}주" if qty else "",
            f"원주문가 {price_native:g}" if price_native else "",
            f"원화환산 {price_krw:,.0f}원" if price_krw else "",
            reason,
            detail,
            f"선택사유: {selected_reason}" if selected_reason else "",
        ]
        lines.append("  " + " | ".join([p for p in pieces if p]))
    return "\n".join(lines)


def run(market: str, date: str, today_judgment: dict,
        actual_result: dict, digest_prompt: str,
        trade_log: list = None, decision_event_log: list = None) -> dict:
    """
    장 마감 후 Claude 사후 분석.

    Parameters
    ----------
    trade_log : 당일 체결 내역 (trading_bot의 self.risk.trade_log)
                없으면 빈 리스트로 처리
    """
    trade_log = trade_log or []
    decision_event_log = decision_event_log or []
    judgments      = today_judgment.get("judgments", {})
    consensus      = today_judgment.get("consensus", {})
    consensus_mode = consensus.get("mode", "CAUTIOUS")
    trade_log      = trade_log or []
    decision_event_log = decision_event_log or []

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

    brain_summary = BrainDB.generate_prompt_summary(market)  # 자르지 않음
    trade_section = _format_trade_log(trade_log)
    decision_section = _format_decision_event_log(decision_event_log)

    sells  = [t for t in trade_log if t.get("side") == "sell" and "pnl" in t]
    wins   = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) <= 0]

    # ── 거래 없는 날: 간소 프롬프트 (판단 적중 + 내일 보정 지침만) ──────────
    if not sells:
        prompt = f"""당신은 트레이딩 AI의 사후 분석가입니다.
오늘은 체결된 매도 거래가 없습니다. 판단 적중 여부와 내일 보정 지침만 작성하세요.

━━━ 아침 판단 ━━━
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  합의 모드: {consensus_mode}

━━━ 실제 시장 결과 ━━━
  시장 변동: {actual_result.get('market_change', 0):+.2f}%
  내 손익:   {actual_result.get('pnl_pct', 0):+.2f}%

━━━ 시장 컨텍스트 ━━━
{digest_prompt[:400]}

━━━ 누적 학습 현황 ━━━
{brain_summary}

모든 문자열 값은 20자 이내로 간결하게. JSON으로만 응답:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "짧게",
  "bear_why":      "짧게",
  "neutral_why":   "짧게",
  "best_trade":    null,
  "worst_trade":   null,
  "worst_trade_reason": "",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "한 단어",
  "issue_desc":    "짧게",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "교훈 또는 null",
    "market_regime": "한 단어"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["주의사항"],
    "bear_adjustments":  ["주의사항"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "짧게"
  }}
}}"""
    else:
        # ── 거래 있는 날: 전체 프롬프트 ─────────────────────────────────────
        best  = max(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        worst = min(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        best_s  = (f"{best['ticker']} {best.get('pnl_pct', 0):+.2f}% ({best['pnl']:+,}원) ({best.get('strategy','-')})"
                   if best else "없음")
        worst_s = (f"{worst['ticker']} {worst.get('pnl_pct', 0):+.2f}% ({worst['pnl']:+,}원) ({worst.get('strategy','-')})"
                   if worst else "없음")

        prompt = f"""당신은 트레이딩 AI의 사후 분석가입니다.
오늘 거래와 아침 판단을 비교해 솔직하게 복기하세요.

━━━ 아침 판단 ━━━
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  합의 모드: {consensus_mode} (size={consensus.get('size','-')}%)

━━━ 오늘 체결 내역 ({len(trade_log)}건) ━━━
{trade_section}
  최고 거래: {best_s}
  최악 거래: {worst_s}

━━━ 오늘 매수/매도 판단 로그 ({len(decision_event_log)}건) ━━━
{decision_section}

━━━ 실제 결과 ━━━
  시장 변동: {actual_result.get('market_change', 0):+.2f}%
  내 손익:   {actual_result.get('pnl_pct', 0):+.2f}%  {'✅ 승' if actual_result.get('win') else '❌ 패'}
  수익 청산: {len(wins)}건 / 손실 청산: {len(losses)}건

━━━ 시장 컨텍스트 ━━━
{digest_prompt[:350]}

━━━ 누적 학습 현황 ━━━
{brain_summary}

━━━ 분석 지침 ━━━
1. 아침 Bull/Bear/Neutral 판단이 실제로 맞았는지 평가하세요.
2. 어떤 거래가 왜 좋았고 왜 나빴는지 구체적으로 설명하세요.
3. 손실 거래가 있다면 반드시 원인을 분석하세요.
4. 같은 상황이 반복된다면 내일 어떻게 다르게 할지 제안하세요.

모든 문자열 값은 30자 이내로 간결하게. JSON으로만 응답:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "짧게",
  "bear_why":      "짧게",
  "neutral_why":   "짧게",
  "best_trade":    "ticker 또는 null",
  "worst_trade":   "ticker 또는 null",
  "worst_trade_reason": "짧게",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "한 단어",
  "issue_desc":    "짧게",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "교훈 또는 null",
    "market_regime": "한 단어"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["주의사항"],
    "bear_adjustments":  ["주의사항"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "짧게"
  }}
}}"""

    # ── 코드 기반 HIT/MISS 사전 계산 (Claude 편향 제거) ───────────────────────
    market_chg = actual_result.get("market_change", 0)
    code_bull    = _code_judge_hit_miss(judgments.get("bull",    {}).get("stance", "NEUTRAL"), market_chg)
    code_bear    = _code_judge_hit_miss(judgments.get("bear",    {}).get("stance", "NEUTRAL"), market_chg)
    code_neutral = _code_judge_hit_miss(judgments.get("neutral", {}).get("stance", "NEUTRAL"), market_chg)
    log.info(f"[postmortem 코드판정] 시장변동 {market_chg:+.2f}% | "
             f"bull={code_bull} bear={code_bear} neutral={code_neutral}")

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        pm = _extract_json(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "postmortem")
        save_raw_call(
            label="postmortem",
            prompt=prompt, raw_response=raw, parsed=pm,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market, call_date=date,
        )
        # Claude HIT/MISS를 코드 판정으로 덮어씌움 (편향 제거)
        pm["bull_result"]    = code_bull
        pm["bear_result"]    = code_bear
        pm["neutral_result"] = code_neutral
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        pm = {
            "bull_result": code_bull,
            "bear_result": code_bear,
            "neutral_result": code_neutral,
            "bull_why": "응답 실패, 코드 판정",
            "bear_why": "응답 실패, 코드 판정",
            "neutral_why": "응답 실패, 코드 판정",
            "key_lesson": "postmortem 응답 실패",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "postmortem_error", "issue_desc": str(e)[:160], "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": "postmortem 응답 실패"},
        }

    # ── brain 업데이트 ────────────────────────────────────────────────────────
    recent = BrainDB.load()["markets"][market].get("recent_days", [])

    BrainDB.update_analyst(market, "bull",    pm["bull_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "bear",    pm["bear_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "neutral", pm["neutral_result"] == "HIT", recent)
    BrainDB.update_mode_performance(
        market, consensus_mode,
        actual_result.get("pnl_pct", 0), actual_result.get("win", False)
    )


    bu = pm.get("brain_updates", {})
    # new_lesson 없으면 key_lesson을 fallback으로 사용
    lesson_to_save = bu.get("new_lesson") or pm.get("key_lesson")
    if lesson_to_save and not _is_placeholder_lesson(lesson_to_save):
        BrainDB.update_beliefs(market, {"new_lesson": lesson_to_save})
    if bu.get("market_regime") and bu["market_regime"] != "unknown":
        BrainDB.update_beliefs(market, {"market_regime": bu["market_regime"]})

    BrainDB.update_issue_pattern(market, {
        "matched_id":  pm.get("pattern_id"),
        "type":        pm.get("issue_type", "미분류"),
        "description": pm.get("issue_desc", ""),
        "bull_hit":    pm["bull_result"] == "HIT",
        "pnl_pct":     actual_result.get("pnl_pct", 0),
        "insight":     "" if _is_placeholder_lesson(pm.get("key_lesson", "")) else pm.get("key_lesson", ""),
    })

    BrainDB.add_daily_record(market, {
        "date":              date,
        "mode":              consensus_mode,
        "pnl_pct":           actual_result.get("pnl_pct", 0),
        "market_change":     actual_result.get("market_change", 0),
        "win":               actual_result.get("win", False),
        "bull_result":       pm["bull_result"],
        "bear_result":       pm["bear_result"],
        "neutral_result":    pm["neutral_result"],
        "bull_stance":       judgments.get("bull", {}).get("stance", ""),
        "bear_stance":       judgments.get("bear", {}).get("stance", ""),
        "neutral_stance":    judgments.get("neutral", {}).get("stance", ""),
        "bull_reason":       judgments.get("bull", {}).get("key_reason", ""),
        "bear_reason":       judgments.get("bear", {}).get("key_reason", ""),
        "neutral_reason":    judgments.get("neutral", {}).get("key_reason", ""),
        "key_lesson":        pm.get("key_lesson", ""),
        "issue_type":        pm.get("issue_type", ""),
        "best_trade":        pm.get("best_trade"),
        "worst_trade":       pm.get("worst_trade"),
        "worst_trade_reason": pm.get("worst_trade_reason", ""),
        "trades":            len(trade_log),
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
        f"Neut:{pm['neutral_result']} | {pm.get('key_lesson','')[:60]}"
    )
    if pm.get("worst_trade"):
        log.warning(
            f"[worst_trade] {pm['worst_trade']} — {pm.get('worst_trade_reason','')}"
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
