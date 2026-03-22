"""minority_report/analysts.py - Bull/Bear/Neutral 3명 Claude 판단

개선사항:
  1. 페르소나 강화  - 각 분석가 전문 영역·금지 행동 명시
  2. 개별 적중률 피드백 - 자신의 과거 실적만 분리해서 수신
  3. 2라운드 토론  - 1차 판단 후 상대 의견 보고 최종 수정
"""
import os, json, time, sys
import anthropic
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_analysis_logger, get_judgment_logger, get_minority_logger
from credit_tracker import record as credit_record

log          = get_minority_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = "claude-sonnet-4-6"

STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"

# ── 강화된 페르소나 ────────────────────────────────────────────────────────────
PERSONAS = {
    "bull": """당신은 15년 경력의 성장주 모멘텀 트레이더입니다.

[전문 영역 — 이 지표들을 우선 확인]
• RSI 과매도(30 이하) 반등 신호
• MACD 골든크로스 or 히스토그램 상향 전환
• 거래량 평균 대비 1.5배 이상 급증
• 볼린저밴드 하단 터치 후 반등
• 52주 신고가 근접 (5% 이내)

[판단 기준]
• 위 신호 2개 이상 → MODERATE_BULL 이상
• 신호 1개 + 시장 분위기 양호 → MILD_BULL
• 기술적 신호 없음 → NEUTRAL 이하

[절대 하지 말 것]
• 환율·VIX만을 이유로 하락 판단 금지 (매크로는 참고만)
• HALT 판단은 시장 전체 서킷브레이커 상황에서만
• 근거 없이 confidence 0.5 이하 부여 금지""",

    "bear": """당신은 헤지펀드 출신 리스크 매니저입니다.

[전문 영역 — 이 지표들을 우선 확인]
• VIX 20 이상 or 전일 대비 급등
• USD/KRW 1,400원 이상 or 급격한 환율 변동
• 외국인 순매도 지속 (3일 이상)
• 신용잔고 증가 + 지수 하락 (역배열 신호)
• 거래량 급감 + 상승 종목 수 감소

[판단 기준]
• 위험 신호 1개 → CAUTIOUS_BEAR 이하
• VIX 25 이상 or 환율 1,450 이상 → 기본값 DEFENSIVE
• 복수 위험 신호 동시 발생 → HALT 검토
• 위험 신호 없음 → MILD_BEAR 이상 가능

[절대 하지 말 것]
• 기술적 반등 신호만으로 BULL 판단 금지
• 위험 신호가 있는데 NEUTRAL 이상 판단 금지
• 근거 없이 AGGRESSIVE 판단 금지""",

    "neutral": """당신은 퀀트 통계 분석가입니다.

[전문 영역 — 이 관점에서 분석]
• 상승/하락 신호 개수 대비 비교 (몇 대 몇인가)
• 과거 유사 시장 패턴과의 통계적 일치도
• 지표 간 상충 여부 (기술적 긍정 + 매크로 부정 → 불확실)
• 데이터 신뢰도 검증 (데이터 누락시 불확실성 증가)

[판단 기준]
• 상승/하락 신호 균등 → 반드시 NEUTRAL
• 한쪽으로 2:1 이상 기울 때만 MILD_BULL or MILD_BEAR
• confidence는 절대 0.75 초과 금지 (불확실성은 항상 존재)
• 극단 판단(AGGRESSIVE, HALT) 원칙적 금지

[절대 하지 말 것]
• 확신 없이 강한 stance 선택 금지
• 한쪽 분석가 의견에 무조건 동조 금지
• 신호가 명확하지 않은데 confidence 0.7 이상 부여 금지""",
}

# ── 1라운드: 독립 판단 ─────────────────────────────────────────────────────────
def call_analyst(analyst_type: str, digest_prompt: str,
                 brain_summary: str, correction: str,
                 analyst_feedback: str = "") -> dict:
    """1라운드 독립 판단"""
    feedback_section = f"\n[나의 과거 실적]\n{analyst_feedback}\n" if analyst_feedback else ""

    prompt = f"""{PERSONAS[analyst_type]}
{feedback_section}
[시장 전체 메모리]
{brain_summary}

[보정 지침]
{correction}

[오늘 시장 데이터]
{digest_prompt}

위 데이터를 당신의 전문 영역 관점에서 분석하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)",
  "full_reasoning":"상세 분석 2~3문장",
  "top_risks":["위험1","위험2"],
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망"}}"""

    try:
        resp = client.messages.create(model=MODEL, max_tokens=1024,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r1")
        log.info(f"[{analyst_type} R1] {result.get('stance','-')} "
                 f"conf={result.get('confidence',0):.2f} | "
                 f"{result.get('key_reason','')[:60]}")
        analysis_log.info(
            f"[analyst_r1] {analyst_type} {result.get('stance','-')}",
            extra={"extra": {
                "event": "analyst_response_r1",
                "analyst": analyst_type,
                "stance": result.get("stance"),
                "confidence": result.get("confidence"),
                "key_reason": result.get("key_reason"),
                "top_risks": result.get("top_risks", []),
                "suggested_strategy": result.get("suggested_strategy"),
            }},
        )
        return result
    except Exception as e:
        log.error(f"[{analyst_type} R1] 오류: {e}")
        return {"stance": "NEUTRAL", "confidence": 0.3,
                "key_reason": f"오류:{str(e)[:40]}",
                "full_reasoning": "", "top_risks": [],
                "suggested_strategy": "관망"}


# ── 2라운드: 토론 후 최종 판단 ────────────────────────────────────────────────
def call_analyst_debate(analyst_type: str, my_r1: dict,
                        others: dict, digest_prompt: str,
                        debate_history: str = "") -> dict:
    """
    2라운드: 다른 분석가 의견 + 과거 토론 이력 보고 최종 판단 수정
    others: {analyst_type: r1_result, ...} (자신 제외)
    debate_history: brain.get_debate_summary() 결과
    """
    others_txt = "\n".join(
        f"• {atype.upper()} 분석가: {r['stance']} (확신도 {r.get('confidence',0):.0%})\n"
        f"  근거: {r.get('key_reason','')}"
        for atype, r in others.items()
    )

    history_section = f"\n[과거 토론 이력]\n{debate_history}\n" if debate_history else ""

    prompt = f"""{PERSONAS[analyst_type]}
{history_section}
[당신의 1라운드 판단]
• stance: {my_r1['stance']}
• 확신도: {my_r1.get('confidence', 0):.0%}
• 근거: {my_r1.get('key_reason', '')}

[다른 분석가들의 1라운드 판단]
{others_txt}

[오늘 시장 데이터 요약]
{digest_prompt[:500]}

토론 지침:
• 다른 분석가의 논거를 당신의 전문 영역 관점에서 평가하세요.
• 과거 토론 이력이 있다면, 비슷한 상황에서 의견 변경이 도움이 됐는지 참고하세요.
• 설득력 있는 논거라면 stance를 조정하세요. 그렇지 않으면 유지하세요.
• 단순히 다수에 동조하기 위한 변경은 하지 마세요.

JSON으로만 응답:
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"최종 핵심 근거 한 문장 (구체적 지표 포함)",
  "changed":true|false,
  "change_reason":"변경했다면 설득된 논거, 유지했다면 null"}}"""

    try:
        resp = client.messages.create(model=MODEL, max_tokens=512,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r2")

        changed = result.get("changed", False)
        change_mark = f"→ {result['stance']}" if changed else "유지"
        log.info(f"[{analyst_type} R2] {change_mark} "
                 f"conf={result.get('confidence',0):.2f} | "
                 f"{result.get('key_reason','')[:60]}")

        # r1 데이터 병합 (full_reasoning, top_risks, suggested_strategy 보존)
        merged = {**my_r1, **result}
        return merged
    except Exception as e:
        log.error(f"[{analyst_type} R2] 오류: {e}")
        return my_r1  # 오류 시 1라운드 결과 그대로


# ── 3명 판단 통합 (2라운드 토론 포함) ────────────────────────────────────────
def get_three_judgments(digest_prompt: str, brain_summary: str,
                        correction: str, delay: float = 1.5,
                        market: str = "KR") -> dict:
    """
    1라운드 독립 판단 → 2라운드 토론 → 최종 판단
    """
    from claude_memory import brain as BrainDB

    # ── 1라운드: 개별 적중률 피드백 포함 독립 판단 ──────────────────────────
    log.info("━━ Round 1: 독립 판단 ━━")
    r1 = {}
    for atype in ("bull", "bear", "neutral"):
        try:
            feedback = BrainDB.generate_analyst_summary(market, atype)
        except Exception:
            feedback = ""
        r1[atype] = call_analyst(atype, digest_prompt, brain_summary,
                                 correction, feedback)
        time.sleep(delay)

    log.info(f"R1 완료 | Bull:{r1['bull']['stance']} "
             f"Bear:{r1['bear']['stance']} Neut:{r1['neutral']['stance']}")

    # ── 2라운드: 과거 토론 이력 + 상대 의견 보고 최종 수정 ───────────────────
    log.info("━━ Round 2: 토론 ━━")
    try:
        debate_history = BrainDB.get_debate_summary(market, n=5)
    except Exception:
        debate_history = ""

    r2 = {}
    for atype in ("bull", "bear", "neutral"):
        others = {k: v for k, v in r1.items() if k != atype}
        r2[atype] = call_analyst_debate(atype, r1[atype], others,
                                        digest_prompt, debate_history)
        time.sleep(delay)

    log.info(f"R2 완료 | Bull:{r2['bull']['stance']} "
             f"Bear:{r2['bear']['stance']} Neut:{r2['neutral']['stance']}")

    # 변경 여부 로깅
    changes = []
    for atype in ("bull", "bear", "neutral"):
        if r2[atype].get("changed") or r1[atype]["stance"] != r2[atype]["stance"]:
            reason = r2[atype].get("change_reason", "") or ""
            log.info(f"  [{atype}] 의견 변경: {r1[atype]['stance']} → {r2[atype]['stance']} "
                     f"| {reason[:60]}")
            changes.append({
                "analyst":   atype,
                "r1_stance": r1[atype]["stance"],
                "r2_stance": r2[atype]["stance"],
                "reason":    reason[:120],
            })
        else:
            log.info(f"  [{atype}] 의견 유지: {r2[atype]['stance']}")

    # 토론 결과를 brain.json에 저장
    try:
        from datetime import date as _date
        today_str = _date.today().isoformat()
        BrainDB.save_debate_result(market, today_str, r1, r2)
        log.info(f"[토론 기록 저장] {today_str} {market} 변경={len(changes)}건")
    except Exception as e:
        log.warning(f"[토론 기록 저장 실패] {e}")

    judgment_log.info(
        f"[judgments_final] Bull:{r2['bull']['stance']} "
        f"Bear:{r2['bear']['stance']} Neutral:{r2['neutral']['stance']}",
        extra={"extra": {
            "event":   "three_judgments",
            "round1":  r1,
            "round2":  r2,
            "changes": changes,
            "bull":    r2["bull"],
            "bear":    r2["bear"],
            "neutral": r2["neutral"],
        }},
    )
    return {"bull": r2["bull"], "bear": r2["bear"], "neutral": r2["neutral"],
            "_debate": {"r1": r1, "changes": changes}}


def select_tickers(market: str, digest_prompt: str,
                   consensus_mode: str, candidates: list) -> list:
    """
    오늘 집중 모니터링할 종목을 Claude가 선택 (3~5개)
    candidates: screen_market_kr/us 결과
    """
    if not candidates:
        log.warning("[종목선택] 후보 없음 → 기본값 사용")
        defaults = {"KR": ["005930", "000660", "035420"],
                    "US": ["NVDA", "TSLA", "AAPL"]}
        return defaults.get(market, [])

    cand_lines = []
    for c in candidates[:40]:
        rate_str = f"{c['change_rate']:+.2f}%" if c.get("change_rate") else ""
        vol_str  = f"거래량{c['vol_ratio']:.1f}배" if c.get("vol_ratio", 0) > 0 else ""
        cand_lines.append(f"  {c['ticker']} {c['name']} {rate_str} {vol_str}".strip())
    cand_text = "\n".join(cand_lines)

    prompt = f"""주식 트레이딩 AI입니다. 오늘 {market} 장 매매 후보 종목을 선택하세요.

현재 합의 모드: {consensus_mode}

오늘 시장에서 활발한 종목 (스크리너 결과):
{cand_text}

시장 컨텍스트:
{digest_prompt[:400]}

규칙:
- 반드시 3~5개 선택
- {consensus_mode} 모드에 적합한 종목 우선
- HALT/DEFENSIVE: 저변동·방어주 위주, 급등락 종목 제외
- AGGRESSIVE/MODERATE_BULL: 모멘텀·거래량 강한 종목 우선
- 후보 목록에 없는 종목은 선택 불가

JSON으로만:
{{"tickers":["코드1","코드2","코드3"],"reasons":{{"코드1":"이유 한 문장"}}}}"""

    valid    = {c["ticker"] for c in candidates}
    fallback = [c["ticker"] for c in candidates[:3]]

    try:
        resp = client.messages.create(model=MODEL, max_tokens=512,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result  = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "select_tickers")
        tickers = [t for t in result.get("tickers", []) if t in valid][:5]
        if not tickers:
            raise ValueError("유효 종목 없음")
        log.info(f"[종목선택] {market} → {tickers}")
        analysis_log.info(
            f"[selection] {market} {tickers}",
            extra={"extra": {
                "event": "ticker_selection",
                "market": market,
                "consensus_mode": consensus_mode,
                "selected": tickers,
                "candidate_count": len(candidates),
                "reasons": result.get("reasons", {}),
            }},
        )
        for t, r in result.get("reasons", {}).items():
            log.info(f"  {t}: {r[:60]}")
        return tickers
    except Exception as e:
        log.error(f"[종목선택 오류] {e} → 기본값 사용")
        return fallback
