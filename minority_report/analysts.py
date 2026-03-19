"""minority_report/analysts.py - Bull/Bear/Neutral 3명 Claude 판단"""
import os, json, time, sys
import anthropic
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_minority_logger

log    = get_minority_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
MODEL  = "claude-sonnet-4-6"

PERSONAS = {
    "bull":    "당신은 낙관적 관점의 주식 분석가입니다. 긍정적 신호와 상승 기회를 우선 포착합니다.",
    "bear":    "당신은 리스크 중심 분석가입니다. 위험 요소와 하락 가능성을 우선 포착합니다.",
    "neutral": "당신은 객관적 중립 분석가입니다. 긍정/부정을 균형 있게 판단하고 불확실성을 인정합니다.",
}
STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"

def call_analyst(analyst_type: str, digest_prompt: str,
                 brain_summary: str, correction: str) -> dict:
    prompt = f"""{PERSONAS[analyst_type]}

{brain_summary}

보정 지침:
{correction}

오늘 시장 데이터:
{digest_prompt}

JSON으로만 응답:
{{"stance":"{STANCES} 중 하나","confidence":0.0~1.0,
  "key_reason":"핵심 근거 한 문장",
  "full_reasoning":"상세 분석 2~3문장",
  "top_risks":["위험1","위험2"],
  "suggested_strategy":"모멘텀|평균회귀|갭+눌림|변동성돌파|관망"}}"""
    try:
        resp = client.messages.create(model=MODEL, max_tokens=512,
            messages=[{"role":"user","content":prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw: raw = raw.split("```")[1].replace("json","").strip()
        result = json.loads(raw)
        log.info(f"[{analyst_type}] {result.get('stance','-')} "
                 f"conf={result.get('confidence',0):.2f} | {result.get('key_reason','')[:60]}")
        return result
    except Exception as e:
        log.error(f"[{analyst_type}] 오류: {e}")
        return {"stance":"NEUTRAL","confidence":0.3,
                "key_reason":f"오류:{str(e)[:40]}",
                "full_reasoning":"","top_risks":[],"suggested_strategy":"관망"}

def get_three_judgments(digest_prompt: str, brain_summary: str,
                        correction: str, delay: float = 1.5) -> dict:
    log.info("3명 판단 요청 시작")
    bull = call_analyst("bull", digest_prompt, brain_summary, correction)
    time.sleep(delay)
    bear = call_analyst("bear", digest_prompt, brain_summary, correction)
    time.sleep(delay)
    neut = call_analyst("neutral", digest_prompt, brain_summary, correction)
    log.info(f"판단 완료 | Bull:{bull['stance']} Bear:{bear['stance']} Neut:{neut['stance']}")
    return {"bull":bull,"bear":bear,"neutral":neut}


def select_tickers(market: str, digest_prompt: str,
                   consensus_mode: str, candidates: list) -> list:
    """
    오늘 집중 모니터링할 종목을 Claude가 선택 (3~5개)

    candidates: screen_market_kr/us 결과
      [{ticker, name, price, change_rate, volume, vol_ratio}]
    반환: [ticker_str, ...]
    """
    if not candidates:
        log.warning(f"[종목선택] 후보 없음 → 기본값 사용")
        defaults = {"KR": ["005930", "000660", "035420"],
                    "US": ["NVDA", "TSLA", "AAPL"]}
        return defaults.get(market, [])

    # Claude에게 줄 후보 텍스트 구성 (간결하게)
    cand_lines = []
    for c in candidates[:40]:  # 최대 40개
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

    valid = {c["ticker"] for c in candidates}
    fallback = [c["ticker"] for c in candidates[:3]]

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=256,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        tickers = [t for t in result.get("tickers", []) if t in valid][:5]
        if not tickers:
            raise ValueError("유효 종목 없음")
        log.info(f"[종목선택] {market} → {tickers}")
        for t, r in result.get("reasons", {}).items():
            log.info(f"  {t}: {r[:60]}")
        return tickers
    except Exception as e:
        log.error(f"[종목선택 오류] {e} → 기본값 사용")
        return fallback
