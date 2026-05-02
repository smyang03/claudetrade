"""minority_report/postmortem.py - 장 마감 후 사후 분석

변경 이력:
- trade_log 파라미터 추가 → 당일 체결 내역을 Claude에게 전달
- 전략별 성과 자동 집계 → BrainDB.update_strategy_performance()
- judgment_log에 trade_log + postmortem 원본 보존 (파인튜닝 raw 데이터)
- best_trade / worst_trade / worst_trade_reason 필드 추가
- HALT / 거래 없는 날 postmortem 스킵 안전장치
"""
import os, json, re, sys, time, uuid
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
    "오류로 자동 보정",
    "API 오류로 자동 보정",
    "postmortem 응답 실패",
    "HALT 세션 또는 거래 없음",
}


def _is_placeholder_lesson(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if text in _POSTMORTEM_PLACEHOLDER_LESSONS:
        return True
    return ("자동 보정" in text) or ("응답 실패" in text)


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 추출 시 방어적으로 파싱한다."""
    # trailing comma 제거 (LLM이 자주 만드는 오류)
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
    """체결 내역을 Claude 프롬프트용 텍스트로 변환한다."""
    if not trade_log:
        return "  (체결 없음)"
    lines = []
    for t in trade_log:
        side  = "매수" if t.get("side") == "buy" else "매도"
        pnl   = t.get("pnl", 0)
        pnl_s = f" PnL {pnl:+,}" if pnl else ""
        lines.append(
            f"  [{side}] {t.get('ticker','-')} {t.get('qty',0)}주"
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
_AVOID_MISS      = 1.0   # DEFENSIVE MISS: 시장 >= +1.0% (상승 기회 놓침)


def _code_judge_hit_miss(stance: str, market_change_pct: float) -> str:
    """
    분석가 스탠스 + 실제 시장 등락으로 HIT/MISS/PARTIAL 결과를 계산한다.
    Claude 자기평가 영향 제거용.

    BULL/BEAR: 방향 예측 정확도
    - BULL HIT:    시장 >= +0.5%
    - BULL PARTIAL: 0% < 시장 < +0.5%
    - BULL MISS:   시장 <= 0%
    - BEAR HIT:    시장 <= -0.5%
    - BEAR PARTIAL: -0.5% < 시장 < 0%
    - BEAR MISS:   시장 >= 0%

    NEUTRAL: 횡보 예측 정확도
    - HIT: |시장| <= 0.5%, PARTIAL: <= 1.5%, MISS: > 1.5%

    DEFENSIVE/HALT: 노출 회피 적절성("왜 노출을 줄이는가")
    - HIT:    시장 < -0.5%  (리스크 회피 판단 정당)
    - PARTIAL: -0.5% <= 시장 < +1.0% (중립, 회피가 과하지는 않음)
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
            f"native {price_native:g}" if price_native else "",
            f"krw {price_krw:,.0f}" if price_krw else "",
            reason,
            detail,
            f"selected_reason: {selected_reason}" if selected_reason else "",
        ]
        lines.append("  " + " | ".join([p for p in pieces if p]))
    return "\n".join(lines)


def _recent_selection_feedback_section(market: str) -> str:
    try:
        text = BrainDB.get_recent_selection_feedback_text(market, days=20, max_chars=900)
        if text:
            return f"\n[Recent selection feedback]\n{text}\n"
    except Exception as exc:
        log.debug(f"[postmortem] selection feedback skipped: {exc}")
    return ""


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

    # HALT 이거나 판단 자체가 없는 날은 postmortem 스킵
    if not judgments or consensus_mode == "HALT":
        log.info(f"[postmortem skip] {date} {market} | HALT 또는 판단 없음")
        return {
            "bull_result": "PARTIAL", "bear_result": "PARTIAL", "neutral_result": "PARTIAL",
            "bull_why": "HALT 스킵", "bear_why": "HALT 스킵", "neutral_why": "HALT 스킵",
            "key_lesson": "HALT 세션 또는 거래 없음",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "HALT", "issue_desc": "", "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    brain_summary = BrainDB.generate_prompt_summary(market)  # 실패해도 무시하지 않음
    selection_feedback = _recent_selection_feedback_section(market)
    trade_section = _format_trade_log(trade_log)
    decision_section = _format_decision_event_log(decision_event_log)

    sells  = [t for t in trade_log if t.get("side") == "sell" and "pnl" in t]
    wins   = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) <= 0]

    # 거래가 없는 날에는 판단 평가와 보정 지침만 작성한다.
    if not sells:
        prompt = f"""당신은 한국 주식 자동매매 시스템의 장마감 사후분석 AI입니다.
오늘은 체결된 매도 거래가 없습니다. 판단 적중 여부와 내일 보정 지침만 작성하세요.

[오늘 판단 요약]
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  최종 합의: {consensus_mode}

[실제 시장 결과]
  시장 변화: {actual_result.get('market_change', 0):+.2f}%
  세션 손익: {actual_result.get('pnl_pct', 0):+.2f}%

[시장 컨텍스트]
{digest_prompt[:400]}
{selection_feedback}

[누적 학습 요약]
{brain_summary}

아래 형식의 JSON만 반환하세요:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "설명",
  "bear_why":      "설명",
  "neutral_why":   "설명",
  "best_trade":    null,
  "worst_trade":   null,
  "worst_trade_reason": "",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "분류",
  "issue_desc":    "설명",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "새 교훈 또는 null",
    "market_regime": "시장 레짐"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["조정안"],
    "bear_adjustments":  ["조정안"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "메모"
  }}
}}"""
    else:
        # 거래가 있는 날에는 체결 결과와 판단 근거를 함께 평가한다.
        best = max(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        worst = min(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        best_s = (
            f"{best['ticker']} {best.get('pnl_pct', 0):+.2f}% ({best['pnl']:+,}원) ({best.get('strategy','-')})"
            if best else "없음"
        )
        worst_s = (
            f"{worst['ticker']} {worst.get('pnl_pct', 0):+.2f}% ({worst['pnl']:+,}원) ({worst.get('strategy','-')})"
            if worst else "없음"
        )

        prompt = f"""당신은 한국 주식 자동매매 시스템의 장마감 사후분석 AI입니다.
오늘 체결된 거래와 실제 시장 결과를 보고, 판단 정확도와 실행 품질을 같이 평가하세요.

[오늘 판단 요약]
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  최종 합의: {consensus_mode} (size={consensus.get('size','-')}%)

[오늘 체결 내역 ({len(trade_log)}건)]
{trade_section}
  최고 거래: {best_s}
  최악 거래: {worst_s}

[오늘 판단 후보/차단 로그 ({len(decision_event_log)}건)]
{decision_section}

[실제 시장 결과]
  시장 변화: {actual_result.get('market_change', 0):+.2f}%
  세션 손익: {actual_result.get('pnl_pct', 0):+.2f}%  {'WIN' if actual_result.get('win') else 'LOSS'}
  수익 청산: {len(wins)}건 / 손실 청산: {len(losses)}건

[시장 컨텍스트]
{digest_prompt[:350]}
{selection_feedback}

[누적 학습 요약]
{brain_summary}

[분석 요청]
1. 오늘 Bull/Bear/Neutral 판단의 적중 여부를 실제 시장 기준으로 평가하세요.
2. 최고 거래는 왜 잘됐는지, 최악 거래는 왜 실패했는지 구체적으로 설명하세요.
3. 손실 거래가 있다면 공통 원인을 분석하세요.
4. 같은 상황이 반복되면 내일 무엇을 다르게 할지 제안하세요.

아래 형식의 JSON만 반환하세요:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "설명",
  "bear_why":      "설명",
  "neutral_why":   "설명",
  "best_trade":    "티커 또는 null",
  "worst_trade":   "티커 또는 null",
  "worst_trade_reason": "설명",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "분류",
  "issue_desc":    "설명",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "새 교훈 또는 null",
    "market_regime": "시장 레짐"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["조정안"],
    "bear_adjustments":  ["조정안"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "메모"
  }}
}}"""

    # 코드 기반 HIT/MISS 사전 계산 (Claude 응답 오염 제거)
    market_chg = actual_result.get("market_change", 0)
    code_bull    = _code_judge_hit_miss(judgments.get("bull",    {}).get("stance", "NEUTRAL"), market_chg)
    code_bear    = _code_judge_hit_miss(judgments.get("bear",    {}).get("stance", "NEUTRAL"), market_chg)
    code_neutral = _code_judge_hit_miss(judgments.get("neutral", {}).get("stance", "NEUTRAL"), market_chg)
    log.info(f"[postmortem 코드보정] 시장변화 {market_chg:+.2f}% | "
             f"bull={code_bull} bear={code_bear} neutral={code_neutral}")

    try:
        started = time.monotonic()
        resp = client.messages.create(
            model=MODEL, max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        raw = resp.content[0].text.strip()
        call_id = f"postmortem_{market}_{date}_{uuid.uuid4().hex[:10]}"
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "postmortem", model=MODEL)
        save_raw_call(
            label="postmortem",
            prompt=prompt, raw_response=raw, parsed={},
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market, call_date=date,
            model=MODEL,
            call_id=call_id,
            parse_stage="raw",
            duration_ms=duration_ms,
            extra={"_raw_only": True},
        )
        try:
            pm = _extract_json(raw)
        except Exception:
            save_raw_call(
                label="postmortem",
                prompt=prompt, raw_response=raw, parsed={},
                input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                market=market, call_date=date,
                model=MODEL,
                call_id=call_id,
                parse_error=True,
                parse_stage="parse_failed",
                duration_ms=duration_ms,
                extra={"_raw_only": True},
            )
            raise
        save_raw_call(
            label="postmortem",
            prompt=prompt, raw_response=raw, parsed=pm,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market, call_date=date,
            model=MODEL,
            call_id=call_id,
            parse_error=False,
            parse_stage="parsed",
            duration_ms=duration_ms,
        )
        # Claude HIT/MISS를 코드 보정값으로 덮어쓴다 (응답 오염 제거)
        pm["bull_result"]    = code_bull
        pm["bear_result"]    = code_bear
        pm["neutral_result"] = code_neutral
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        pm = {
            "bull_result": code_bull,
            "bear_result": code_bear,
            "neutral_result": code_neutral,
            "bull_why": "응답 실패, 코드 보정",
            "bear_why": "응답 실패, 코드 보정",
            "neutral_why": "응답 실패, 코드 보정",
            "key_lesson": "",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "", "issue_desc": "", "pattern_id": None,
            "_system_error": True,
            "_skip_issue_pattern": True,
            "_system_error_detail": str(e)[:160],
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    # ── brain 업데이트 ───────────────────────────────────────────────
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

    if not pm.get("_skip_issue_pattern"):
        BrainDB.update_issue_pattern(market, {
            "matched_id":  pm.get("pattern_id"),
            "type":        pm.get("issue_type", "unknown"),
            "description": pm.get("issue_desc", ""),
            "bull_hit":    pm["bull_result"] == "HIT",
            "pnl_pct":     actual_result.get("pnl_pct", 0),
            "insight":     "" if _is_placeholder_lesson(pm.get("key_lesson", "")) else pm.get("key_lesson", ""),
        })

    policy_key_lesson = "" if pm.get("_system_error") else pm.get("key_lesson", "")
    policy_issue_type = "" if pm.get("_system_error") else pm.get("issue_type", "")

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
        "key_lesson":        policy_key_lesson,
        "issue_type":        policy_issue_type,
        "best_trade":        pm.get("best_trade"),
        "worst_trade":       pm.get("worst_trade"),
        "worst_trade_reason": pm.get("worst_trade_reason", ""),
        "trades":            len(trade_log),
        "selection_feedback": BrainDB.get_recent_selection_feedback_text(market, days=20, max_chars=400),
    })

    # 전략별 성과 자동 업데이트
    for strat, pnls in _strategy_pnl(trade_log).items():
        avg_pnl = sum(pnls) / len(pnls)
        BrainDB.update_strategy_performance(market, strat, avg_pnl, avg_pnl > 0)

    # ── 토론 결과 정답 여부 업데이트 ─────────────────────────────────
    try:
        BrainDB.update_debate_outcome(market, date, actual_result.get("win", False))
    except Exception as e:
        log.warning(f"토론 결과 업데이트 실패: {e}")

    # 당일 Claude 보정 지침 업데이트
    cg = pm.get("correction_guide", {})
    if cg and not pm.get("_system_error"):
        BrainDB.update_correction_guide(market, cg)

    log.info(
        f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
        f"Neut:{pm['neutral_result']} | {pm.get('key_lesson','')[:60]}"
    )
    if pm.get("worst_trade"):
        log.warning(
            f"[worst_trade] {pm['worst_trade']} | {pm.get('worst_trade_reason','')}"
        )

    # JSONL 학습 로그 저장 (프롬프트 + 응답 + 거래 원본)
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

