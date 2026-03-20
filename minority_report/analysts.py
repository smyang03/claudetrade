"""minority_report/analysts.py - Bull/Bear/Neutral analyst calls."""

import json
import os
import sys
import time
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_analysis_logger, get_judgment_logger, get_minority_logger

log = get_minority_logger()
analysis_log = get_analysis_logger()
judgment_log = get_judgment_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

PERSONAS = {
    "bull": "You are a bullish equity analyst. Prioritize upside opportunities and positive catalysts.",
    "bear": "You are a risk-first bearish equity analyst. Prioritize downside scenarios and threat detection.",
    "neutral": "You are a balanced equity analyst. Assess upside and downside with uncertainty awareness.",
}
STANCES = "AGGRESSIVE|MODERATE_BULL|MILD_BULL|NEUTRAL|MILD_BEAR|CAUTIOUS_BEAR|DEFENSIVE|HALT"
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


def call_analyst(analyst_type: str, digest_prompt: str, brain_summary: str, correction: str) -> dict:
    prompt = f"""{PERSONAS[analyst_type]}

{brain_summary}

Correction guide:
{correction}

Market digest:
{digest_prompt}

Return JSON only:
{{"stance":"{STANCES} one of",
  "confidence":0.0,
  "key_reason":"One-line core reason",
  "full_reasoning":"2-3 lines",
  "top_risks":["risk1","risk2"],
  "suggested_strategy":"모멘텀|평균회귀|갭풀백|변동성돌파|관망"}}"""
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = _sanitize_analyst_result(json.loads(raw), analyst_type)
        log.info(
            f"[{analyst_type}] {result.get('stance', '-')} "
            f"conf={result.get('confidence', 0):.2f} | {result.get('key_reason', '')[:60]}"
        )
        analysis_log.info(
            f"[analyst] {analyst_type} {result.get('stance', '-')}",
            extra={
                "extra": {
                    "event": "analyst_response",
                    "analyst": analyst_type,
                    "stance": result.get("stance"),
                    "confidence": result.get("confidence"),
                    "key_reason": result.get("key_reason"),
                    "top_risks": result.get("top_risks", []),
                    "suggested_strategy": result.get("suggested_strategy"),
                }
            },
        )
        return result
    except Exception as e:
        log.error(f"[{analyst_type}] analyst error: {e}")
        return _fallback_result(e)


def get_three_judgments(
    digest_prompt: str, brain_summary: str, correction: str, delay: float = 1.5
) -> dict:
    log.info("requesting three analyst judgments")
    bull = call_analyst("bull", digest_prompt, brain_summary, correction)
    time.sleep(delay)
    bear = call_analyst("bear", digest_prompt, brain_summary, correction)
    time.sleep(delay)
    neut = call_analyst("neutral", digest_prompt, brain_summary, correction)
    log.info(f"judgments done | Bull:{bull['stance']} Bear:{bear['stance']} Neut:{neut['stance']}")
    judgment_log.info(
        f"[judgments] Bull:{bull['stance']} Bear:{bear['stance']} Neutral:{neut['stance']}",
        extra={
            "extra": {
                "event": "three_judgments",
                "bull": bull,
                "bear": bear,
                "neutral": neut,
            }
        },
    )
    return {"bull": bull, "bear": bear, "neutral": neut}


def select_tickers(market: str, digest_prompt: str, consensus_mode: str, candidates: list) -> list:
    """
    Let Claude choose 3-5 focus tickers from screener candidates.
    """
    if not candidates:
        log.warning("[ticker-selection] no candidates -> default basket")
        defaults = {"KR": ["005930", "000660", "035420"], "US": ["NVDA", "TSLA", "AAPL"]}
        return defaults.get(market, [])

    cand_lines = []
    for c in candidates[:40]:
        rate_str = f"{float(c.get('change_rate', 0.0)):+.2f}%"
        vr = float(c.get("vol_ratio", 0.0))
        vol_str = f"vol_ratio={vr:.1f}x" if vr > 0 else ""
        cand_lines.append(f"{c.get('ticker')} {c.get('name', '')} {rate_str} {vol_str}".strip())
    cand_text = "\n".join(cand_lines)

    prompt = f"""Pick 3-5 tickers for today's {market} session.
Consensus mode: {consensus_mode}
Candidates:
{cand_text}

Context:
{digest_prompt[:400]}

Rules:
- Pick only from candidates.
- Return JSON only.

{{"tickers":["code1","code2","code3"],"reasons":{{"code1":"short reason"}}}}"""

    valid = {c["ticker"] for c in candidates if c.get("ticker")}
    fallback = [c["ticker"] for c in candidates[:3] if c.get("ticker")]

    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        result = json.loads(raw)
        tickers = [t for t in result.get("tickers", []) if t in valid][:5]
        if not tickers:
            raise ValueError("no valid tickers")
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
                    "reasons": result.get("reasons", {}),
                }
            },
        )
        return tickers
    except Exception as e:
        log.error(f"[ticker-selection] error: {e} -> fallback")
        return fallback
