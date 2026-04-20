"""minority_report/analysts.py - Bull/Bear/Neutral 3명 Claude 판단

개선사항:
  1. 페르소나 강화  - 각 분석가 전문 영역·금지 행동 명시
  2. 개별 적중률 피드백 - 자신의 과거 실적만 분리해서 수신
  3. 2라운드 토론  - 1차 판단 후 상대 의견 보고 최종 수정
"""
import os, json, re, time, sys
import anthropic
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_analysis_logger, get_judgment_logger, get_minority_logger
from credit_tracker import record as credit_record
from minority_report.raw_call_logger import save as save_raw_call

log          = get_minority_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
# R1 분석가: 비용 절감을 위해 Haiku 사용 (R2 토론은 Sonnet 유지)
# R1_MODEL 환경변수로 오버라이드 가능 (기본 Haiku 4.5)
R1_MODEL     = os.getenv("R1_MODEL", "claude-haiku-4-5-20251001")
STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"


def _extract_json(text: str) -> dict:
    """Claude 응답에서 JSON 추출 — 형식 무관하게 견고하게 파싱"""
    def _fix(s: str) -> str:
        # trailing comma
        s = re.sub(r",(\s*[}\]])", r"\1", s)
        # JSON 비표준 수치 리터럴 (nan, inf)
        s = re.sub(r'\bNaN\b',       '"NaN"',  s)
        s = re.sub(r'\bInfinity\b',  '999',    s)
        s = re.sub(r'\b-Infinity\b', '-999',   s)
        s = re.sub(r'\bnan\b',       '0',      s)
        s = re.sub(r'\binf\b',       '999',    s)
        s = re.sub(r'\b-inf\b',      '-999',   s)
        # 전각 따옴표/콜론 → ASCII
        s = s.replace('\u201c', '"').replace('\u201d', '"')
        s = s.replace('\u2018', "'").replace('\u2019', "'")
        s = s.replace('\uff1a', ':')
        # string value 내 literal 개행·탭 제거 (키-값 구분자 오파싱 방지)
        s = re.sub(r'(?<=":)(\s*"[^"]*?)\n([^"]*?")', lambda m: m.group(0).replace('\n', ' '), s)
        s = s.replace('\r\n', ' ').replace('\r', ' ')
        # 제어문자 제거 (null바이트 등)
        s = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', s)
        return s

    def _try_parse(s: str) -> dict:
        try:
            return json.loads(_fix(s))
        except json.JSONDecodeError:
            pass
        # tickers 배열 추출
        reasons_start = s.find('"reasons"')
        tickers_section = s[:reasons_start] if reasons_start != -1 else s
        tickers = re.findall(r'"([A-Z0-9]{1,10})"', tickers_section)
        tickers = [t for t in tickers if len(t) >= 2]
        # reasons 개별 key:value 쌍 regex 추출 시도
        reasons = {}
        if reasons_start != -1:
            reasons_section = s[reasons_start:]
            pairs = re.findall(r'"([A-Z0-9]{1,10})"\s*:\s*"([^"]{1,60})"', reasons_section)
            reasons = {k: v for k, v in pairs}
        if tickers:
            log.warning(f"[_extract_json] JSON 파싱 실패 — regex 복구: tickers={tickers[:20]} reasons={len(reasons)}개")
            return {"tickers": tickers[:20], "reasons": reasons}
        raise ValueError("tickers 추출 불가")

    # 1차: 닫힌 ```json ... ``` 블록
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return _try_parse(m.group(1))
    # 2차: { ... } 정상 추출
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return _try_parse(text[start:end + 1])
    # 3차: 응답이 max_tokens로 잘린 경우 — 열린 { 뒤 내용으로 필드 regex 복구
    if start != -1:
        partial = text[start:]
        stance_m = re.search(r'"stance"\s*:\s*"([A-Z_]+)"', partial)
        conf_m   = re.search(r'"confidence"\s*:\s*([0-9.]+)', partial)
        reason_m = re.search(r'"key_reason"\s*:\s*"([^"]{1,200})"', partial)
        if stance_m:
            log.warning(f"[_extract_json] 잘린 응답 regex 복구: stance={stance_m.group(1)}")
            return {
                "stance":     stance_m.group(1),
                "confidence": float(conf_m.group(1)) if conf_m else 0.5,
                "key_reason": reason_m.group(1) if reason_m else "응답 잘림",
            }
    raise ValueError(f"JSON 추출 실패: {text[:200]}")
ALLOWED_STANCES = set(STANCES.split("|"))
ALLOWED_STRATEGIES = {"모멘텀", "평균회귀", "갭풀백", "변동성돌파", "관망"}


def _sanitize_analyst_result(result: dict, analyst_type: str) -> dict:
    stance = str(result.get("stance", "NEUTRAL")).strip().upper()
    if stance not in ALLOWED_STANCES:
        log.warning(f"[{analyst_type}] invalid stance={stance} -> NEUTRAL")
        stance = "NEUTRAL"
    try:
        confidence = float(result.get("confidence", 0.3))
    except Exception:
        confidence = 0.3
    confidence = max(0.0, min(1.0, confidence))
    top_risks = result.get("top_risks", [])
    if not isinstance(top_risks, list):
        top_risks = []
    top_risks = [str(x) for x in top_risks[:5]]
    suggested_strategy = str(result.get("suggested_strategy", "관망")).strip()
    if suggested_strategy not in ALLOWED_STRATEGIES:
        suggested_strategy = "관망"
    return {
        "stance": stance,
        "confidence": confidence,
        "key_reason": str(result.get("key_reason", ""))[:500],
        "full_reasoning": str(result.get("full_reasoning", ""))[:2000],
        "top_risks": top_risks,
        "suggested_strategy": suggested_strategy,
    }


def _fallback_result(error: Exception) -> dict:
    return {
        "stance": "NEUTRAL",
        "confidence": 0.3,
        "key_reason": f"오류:{str(error)[:60]}",
        "full_reasoning": "",
        "top_risks": [],
        "suggested_strategy": "관망",
    }


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
• VKOSPI 20 이상 or 전일 대비 급등 (결측이면 중간 불확실성으로 처리)
• USD/KRW 당일 변화 방향: 1d 상승(KRW 약세) = 위험, 1d 하락(KRW 강세) = 위험 완화
  - 절대 수준이 아닌 추세로 판단: 20일고점대비 -5% 이상 하락이면 환율 위험 해소 중
• 외국인 순매도 지속 (3일 이상) — N/A는 판단 유보
• 신용잔고 증가 + 지수 하락 (역배열 신호)
• 거래량 급감 + 상승 종목 수 감소

[판단 기준]
• 위험 신호 1개 → CAUTIOUS_BEAR 이하
• VKOSPI 25 이상 or 환율 당일 +1.5% 이상 급등 → 기본값 DEFENSIVE
• 복수 위험 신호 동시 발생 → HALT 검토
• 위험 신호 없음 → MILD_BEAR 이상 가능
• 월요일이고 금요일 코스피가 하락 마감이었어도, 환율/VKOSPI 안정이면 과도한 하락 판단 금지

[절대 하지 말 것]
• 기술적 반등 신호만으로 BULL 판단 금지
• 위험 신호가 있는데 NEUTRAL 이상 판단 금지
• 근거 없이 AGGRESSIVE 판단 금지
• USD/KRW 절대 수준이 높다는 이유만으로 하락 판단 금지 (추세 방향을 보라)""",

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
                 analyst_feedback: str = "",
                 portfolio_info=None,
                 market: str = "") -> dict:
    """1라운드 독립 판단"""
    feedback_section = f"\n[나의 과거 실적]\n{analyst_feedback}\n" if analyst_feedback else ""

    # 포트폴리오 현황 섹션
    if portfolio_info:
        cash        = portfolio_info.get("cash", 0)
        total       = portfolio_info.get("total_equity", cash)
        max_order   = portfolio_info.get("max_order_krw", 0)
        n_pos       = portfolio_info.get("n_positions", 0)
        max_pos     = portfolio_info.get("max_positions", 3)
        portfolio_section = (
            f"\n[포트폴리오 현황]\n"
            f"• 가용 현금: {cash:,.0f}원\n"
            f"• 총 자산: {total:,.0f}원\n"
            f"• 1회 최대 주문: {max_order:,.0f}원\n"
            f"• 현재 보유 종목: {n_pos}/{max_pos}개\n"
            f"• 잔여 슬롯: {max(0, max_pos - n_pos)}개\n"
        )
    else:
        portfolio_section = ""

    prompt = f"""{PERSONAS[analyst_type]}
{feedback_section}{portfolio_section}
[데이터 해석 가이드 — 반드시 준수]
• 코스피: "1d X% / 5d Y%" 형태 — 1d는 전일 대비, 5d는 주간 추세. 둘 다 확인할 것.
• USD/KRW: "1,465 (1d -0.8%, 5d -3.8%, 20일고점대비 -4.2%)" 형태
  - 1d 음수 = KRW 강세(위험 완화), 양수 = KRW 약세(위험)
  - 20일고점대비 -5% 이상이면 환율 위험은 단기 해소 국면
• VKOSPI 결측: 데이터 없음. 중간 불확실성(보통 수준)으로 처리. DEFENSIVE 판단 근거로 쓰지 말 것.
• 오늘 요일: 월요일이면 금요일 종가 기준임을 감안. 주말 사이 갭 가능성 포함.
• 외국인/기관 N/A: 데이터 없음. 0(순매도도 순매수도 없음)과 다름. 판단 유보.
• MACD 골든크로스(확대중): 추세 강화 신호. MACD 골든크로스(축소중): 추세 약화 주의.

[시장 전체 메모리]
{brain_summary}

[보정 지침]
{correction}

[오늘 시장 데이터]
{digest_prompt}

위 데이터를 당신의 전문 영역 관점에서 분석하세요. 반드시 트렌드 수치(1d/5d)를 근거로 언급하세요.
포트폴리오 현황을 참고하여 이 시장 상황에서 1회 최대 주문금액 대비 몇 %를 투자할지 제안하세요.
JSON으로만 응답 (다른 텍스트 없이):
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장 (구체적 지표 수치 포함)",
  "full_reasoning":"상세 분석 2~3문장",
  "top_risks":["위험1","위험2"],
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망",
  "suggested_size_pct":0~100}}"""

    try:
        resp = client.messages.create(model=R1_MODEL, max_tokens=2048,
                                      messages=[{"role": "user", "content": prompt}])
        raw = resp.content[0].text.strip()
        result = _sanitize_analyst_result(_extract_json(raw), analyst_type)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r1")
        save_raw_call(
            label=f"analyst_{analyst_type}_r1",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
        )
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
        return _fallback_result(e)


# ── 2라운드: 토론 후 최종 판단 ────────────────────────────────────────────────
def call_analyst_debate(analyst_type: str, my_r1: dict,
                        others: dict, digest_prompt: str,
                        debate_history: str = "",
                        market: str = "") -> dict:
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
{digest_prompt[:800]}

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
        result = _extract_json(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens,
                      f"analyst_{analyst_type}_r2")
        save_raw_call(
            label=f"analyst_{analyst_type}_r2",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
        )

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
                        market: str = "KR",
                        portfolio_info=None) -> dict:
    """
    1라운드 독립 판단 → 2라운드 토론 → 최종 판단
    portfolio_info: {"cash", "total_equity", "max_order_krw", "n_positions", "max_positions"}
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
                                 correction, feedback, portfolio_info, market=market)
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
                                        digest_prompt, debate_history, market=market)
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


def select_tickers(market: str, digest_prompt: str, consensus_mode: str, candidates: list,
                   intraday_context: str = "") -> list:
    """
    오늘 집중 모니터링할 종목을 Claude가 선택 (최소 8개, 최대 10개)
    candidates: screen_market_kr/us 결과
    """
    if not candidates:
        log.warning("[종목선택] 후보 없음 → 기본값 사용")
        defaults = {
            "KR": ["005930", "000660", "035420", "005380", "051910", "068270", "207940", "012450"],
            "US": ["NVDA", "TSLA", "AAPL", "GOOGL", "NFLX", "AMD", "INTC", "PLTR"],
        }
        return defaults.get(market, []), {}

    # KR 장전(08:30~09:05 KST) vol_ratio 마스킹 — 개장 전 거래량회전율은 신뢰 불가
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    _now_kr = _dt.now(_ZI("Asia/Seoul"))
    _kr_premarket = (
        market == "KR"
        and (
            (_now_kr.hour == 8 and _now_kr.minute >= 30)
            or (_now_kr.hour == 9 and _now_kr.minute <= 5)
        )
    )
    if _kr_premarket:
        log.debug("[종목선택] KR 장전 — vol_ratio Claude 입력 마스킹 적용")

    cand_lines = []
    for c in candidates[:50]:
        rate_str = f"{float(c.get('change_rate', 0.0)):+.2f}%"
        vr = float(c.get("vol_ratio", 0.0))
        if _kr_premarket:
            vol_str = ""   # 장전 KR: vol_ratio 의미없음 → Claude 입력에서 제거
        else:
            vol_str = f"거래량{vr:.1f}배" if vr > 0 else ""
        cand_lines.append(f"  {c.get('ticker')} {c.get('name','')} {rate_str} {vol_str}".strip())
    cand_text = "\n".join(cand_lines)

    n_cands  = len([c for c in candidates if c.get("ticker")])
    req_min  = min(16, n_cands)
    req_max  = min(20, n_cands)

    intraday_section = (
        f"\n장중 현재 상황:\n{intraday_context}\n"
        if intraday_context else ""
    )

    prompt = f"""오늘 {market} 세션에서 집중 모니터링할 종목을 최소 {req_min}개, 최대 {req_max}개 선택하세요.
합의 모드: {consensus_mode}
후보 종목:
{cand_text}

시장 컨텍스트 (장전 분석):
{digest_prompt[:400]}{intraday_section}
규칙:
- 후보 종목 중에서만 선택. 중복 없이 선택할 것.
- 최소 {req_min}개 이상 선택할 것. 후보가 충분하면 {req_max}개까지 선택 가능.
- reasons는 반드시 한국어로 작성 (30자 이내).
- JSON만 반환.

{{"tickers":["code1","code2","code3"],"reasons":{{"code1":"선택 이유 한국어"}}}}"""

    valid    = {c["ticker"] for c in candidates if c.get("ticker")}
    fallback = [c["ticker"] for c in candidates[:req_min] if c.get("ticker")]

    # US DEFENSIVE/HALT 모드 시 인버스 ETF만 남지 않도록 안정 종목 보호 목록
    US_INVERSE_ETFS = {"TZA", "SPDN", "NVD", "SQQQ", "SDOW", "SPXU", "SH", "PSQ", "MYY"}
    US_STABLE_ANCHORS = ["T", "VZ", "XLU", "KO", "JNJ", "PG", "O", "VYM", "SCHD"]

    import time as _time
    last_err = None
    resp = None
    for _attempt in range(3):
        try:
            resp = client.messages.create(model=MODEL, max_tokens=1024,
                                          messages=[{"role": "user", "content": prompt}])
            last_err = None
            break
        except Exception as _e:
            last_err = _e
            _emsg = str(_e)
            if ("529" in _emsg or "overloaded" in _emsg.lower()) and _attempt < 2:
                _wait = 2 ** (_attempt + 1)   # 2s, 4s
                log.warning(f"[ticker-selection] Claude 과부하(529) — {_wait}s 후 재시도 ({_attempt+1}/3)")
                _time.sleep(_wait)
            else:
                break

    try:
        if last_err is not None:
            raise last_err
        raw = resp.content[0].text.strip()
        result  = _extract_json(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "select_tickers")
        save_raw_call(
            label="select_tickers",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
        )
        tickers = list(dict.fromkeys(t for t in result.get("tickers", []) if t in valid))[:20]
        if not tickers:
            raise ValueError("no valid tickers")

        reasons = result.get("reasons", {})

        # 16개 미만이면 상위 후보로 보충
        if len(tickers) < 16:
            if _kr_premarket:
                top_fill = sorted(
                    candidates,
                    key=lambda c: (
                        abs(float(c.get("change_rate", 0) or 0)),
                        float(c.get("price", 0) or 0),
                    ),
                    reverse=True,
                )
            else:
                top_fill = sorted(candidates, key=lambda c: float(c.get("vol_ratio", 0) or 0), reverse=True)
            for _c in top_fill:
                _t = _c.get("ticker")
                if _t and _t in valid and _t not in tickers:
                    tickers.append(_t)
                    reasons[_t] = "장전후보 보충" if _kr_premarket else "거래량 상위 보충"
                if len(tickers) >= 16:
                    break

        # US DEFENSIVE/HALT 모드: 인버스 ETF만 선택된 경우 안정 종목 보완
        if market == "US" and consensus_mode in ("DEFENSIVE", "HALT"):
            non_inverse = [t for t in tickers if t not in US_INVERSE_ETFS]
            if not non_inverse:
                # 후보에서 안정 종목 찾기
                stable_in_candidates = [t for t in US_STABLE_ANCHORS if t in valid]
                if stable_in_candidates:
                    # 인버스 1개 유지, 나머지를 안정 종목으로 교체 (최소 16개 맞춤)
                    tickers = tickers[:1] + stable_in_candidates[:15]
                    log.info(f"[ticker-selection] DEFENSIVE/HALT — 안정 종목 보완: {tickers}")
        log.info(f"[ticker-selection] {market} -> {tickers}")
        analysis_log.info(
            f"[selection] {market} {tickers}",
            extra={
                "extra": {
                    "event": "ticker_selection",
                    "market": market,
                    "consensus_mode": consensus_mode,
                    "selected": tickers,
                    "candidate_count": len(candidates),
                    "reasons": reasons,
                }
            },
        )
        return tickers, reasons
    except Exception as e:
        log.error(f"[ticker-selection] error: {e} -> fallback")
        return fallback, {}
