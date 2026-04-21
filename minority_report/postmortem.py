"""minority_report/postmortem.py - ??留덇컧 ???ы썑 遺꾩꽍

蹂寃??대젰:
- trade_log ?뚮씪誘명꽣 異붽? ???뱀씪 泥닿껐 ?댁뿭??Claude?먭쾶 ?꾨떖
- ?꾨왂蹂??깃낵 ?먮룞 吏묎퀎 ??BrainDB.update_strategy_performance()
- judgment_log??trade_log + postmortem ?먮낯 蹂댁〈 (?뚯씤?쒕떇 raw ?곗씠??
- best_trade / worst_trade / worst_trade_reason ?꾨뱶 異붽?
- HALT / 嫄곕옒 ?녿뒗 ??postmortem ?ㅽ궢 ?덉쟾?μ튂
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
    "?ㅻ쪟濡??먮룞 ?먯젙",
    "API ?ㅻ쪟濡??먮룞 ?먯젙",
    "postmortem ?묐떟 ?ㅽ뙣",
    "HALT ?몄뀡 ??嫄곕옒 ?놁쓬",
}


def _is_placeholder_lesson(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if text in _POSTMORTEM_PLACEHOLDER_LESSONS:
        return True
    return ("?먮룞 ?먯젙" in text) or ("?묐떟 ?ㅽ뙣" in text)


def _extract_json(text: str) -> dict:
    """Claude ?묐떟?먯꽌 JSON 異붿텧 ???뺤떇 臾닿??섍쾶 寃ш퀬?섍쾶 ?뚯떛"""
    # trailing comma ?쒓굅 (LLM???먯＜ ?앹꽦?섎뒗 ?ㅻ쪟)
    def _fix(s: str) -> str:
        return re.sub(r",(\s*[}\]])", r"\1", s)

    # 1) ```json ... ``` ?먮뒗 ``` ... ``` 釉붾줉 (?먯슃??留ㅼ묶?쇰줈 以묒꺽 {} ?ы븿)
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(_fix(m.group(1)))
    # 2) { ... } 吏곸젒 異붿텧 ??泥?踰덉㎏ { 遺??留덉?留?} 源뚯?
    start = text.find("{")
    end   = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return json.loads(_fix(text[start:end + 1]))
    raise ValueError(f"JSON 異붿텧 ?ㅽ뙣: {text[:200]}")


def _format_trade_log(trade_log: list) -> str:
    """泥닿껐 ?댁뿭 ??Claude ?꾨＼?꾪듃???띿뒪??"""
    if not trade_log:
        return "  (泥닿껐 ?놁쓬)"
    lines = []
    for t in trade_log:
        side  = "留ㅼ닔" if t.get("side") == "buy" else "留ㅻ룄"
        pnl   = t.get("pnl", 0)
        pnl_s = f" PnL {pnl:+,}" if pnl else ""
        lines.append(
            f"  [{side}] {t.get('ticker','-')} {t.get('qty',0)}二?"
            f"@{t.get('price', t.get('entry', 0)):,} "
            f"?꾨왂:{t.get('strategy','-')}{pnl_s}"
        )
    return "\n".join(lines)


def _strategy_pnl(trade_log: list) -> dict:
    """?꾨왂蹂?PnL 吏묎퀎 {strategy: [pnl_pct, ...]}"""
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
_AVOID_STANCES   = {"DEFENSIVE", "HALT"}   # 諛⑺뼢 ?덉륫 ?꾨떂 ???몄텧 ?뚰뵾媛 留욎븯?붽?濡??먯젙

_HIT_THRESHOLD   = 0.5   # 諛⑺뼢???먮떒 HIT 理쒖냼 ?꾧퀎媛?
_FLAT_THRESHOLD  = 0.5   # NEUTRAL HIT: |?쒖옣| <= 0.5%
_FLAT_PARTIAL    = 1.5   # NEUTRAL PARTIAL: 0.5~1.5%
_AVOID_MISS      = 1.0   # DEFENSIVE MISS: ?쒖옣 >= +1.0% (?볦튇 ?곸듅 湲고쉶)


def _code_judge_hit_miss(stance: str, market_change_pct: float) -> str:
    """
    遺꾩꽍媛 ?ㅽ깲??+ ?ㅼ젣 ?쒖옣 ?깅씫瑜좊줈 HIT/MISS/PARTIAL 媛앷? ?먯젙.
    Claude ?먭린?됯? ?명뼢 ?쒓굅??

    BULL/BEAR: 諛⑺뼢 ?덉륫 ?뺥솗??
    - BULL HIT:    ?쒖옣 >= +0.5%
    - BULL PARTIAL: 0% < ?쒖옣 < +0.5%
    - BULL MISS:   ?쒖옣 <= 0%
    - BEAR HIT:    ?쒖옣 <= -0.5%
    - BEAR PARTIAL: -0.5% < ?쒖옣 < 0%
    - BEAR MISS:   ?쒖옣 >= 0%

    NEUTRAL: ?〓낫 ?덉륫 ?뺥솗??
    - HIT: |?쒖옣| <= 0.5%, PARTIAL: <= 1.5%, MISS: > 1.5%

    DEFENSIVE/HALT: ?몄텧 ?뚰뵾 ?곸젅??("??? ?몄텧???좊━?덈뒗媛")
    - HIT:    ?쒖옣 < -0.5%  (由ъ뒪???꾩떎?? ?뚰뵾 ?뺣떦)
    - PARTIAL: -0.5% <= ?쒖옣 < +1.0% (?좊ℓ, ?뚰뵾???섏걯吏 ?딆쓬)
    - MISS:   ?쒖옣 >= +1.0% (媛뺥븳 ?곸듅 ?볦묠, ?뚰뵾媛 ?섎せ???먮떒)
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
        return "  (?섏궗寃곗젙 濡쒓렇 ?놁쓬)"
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
    ??留덇컧 ??Claude ?ы썑 遺꾩꽍.

    Parameters
    ----------
    trade_log : ?뱀씪 泥닿껐 ?댁뿭 (trading_bot??self.risk.trade_log)
                ?놁쑝硫?鍮?由ъ뒪?몃줈 泥섎━
    """
    trade_log = trade_log or []
    decision_event_log = decision_event_log or []
    judgments      = today_judgment.get("judgments", {})
    consensus      = today_judgment.get("consensus", {})
    consensus_mode = consensus.get("mode", "CAUTIOUS")
    trade_log      = trade_log or []
    decision_event_log = decision_event_log or []

    # ?? HALT ?먮뒗 ?먮떒 ?녿뒗 ???ㅽ궢 ??????????????????????????????????????????
    if not judgments or consensus_mode == "HALT":
        log.info(f"[postmortem skip] {date} {market} ??HALT ?먮뒗 ?먮떒 ?놁쓬")
        return {
            "bull_result": "PARTIAL", "bear_result": "PARTIAL", "neutral_result": "PARTIAL",
            "bull_why": "HALT ?ㅽ궢", "bear_why": "HALT ?ㅽ궢", "neutral_why": "HALT ?ㅽ궢",
            "key_lesson": "HALT ?몄뀡 ??嫄곕옒 ?놁쓬",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "HALT", "issue_desc": "", "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    brain_summary = BrainDB.generate_prompt_summary(market)  # ?먮Ⅴ吏 ?딆쓬
    selection_feedback = _recent_selection_feedback_section(market)
    trade_section = _format_trade_log(trade_log)
    decision_section = _format_decision_event_log(decision_event_log)

    sells  = [t for t in trade_log if t.get("side") == "sell" and "pnl" in t]
    wins   = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) <= 0]

    # ?? 嫄곕옒 ?녿뒗 ?? 媛꾩냼 ?꾨＼?꾪듃 (?먮떒 ?곸쨷 + ?댁씪 蹂댁젙 吏移⑤쭔) ??????????
    if not sells:
        prompt = f"""?뱀떊? ?몃젅?대뵫 AI???ы썑 遺꾩꽍媛?낅땲??
?ㅻ뒛? 泥닿껐??留ㅻ룄 嫄곕옒媛 ?놁뒿?덈떎. ?먮떒 ?곸쨷 ?щ?? ?댁씪 蹂댁젙 吏移⑤쭔 ?묒꽦?섏꽭??

?곣봺???꾩묠 ?먮떒 ?곣봺??
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  ?⑹쓽 紐⑤뱶: {consensus_mode}

?곣봺???ㅼ젣 ?쒖옣 寃곌낵 ?곣봺??
  ?쒖옣 蹂?? {actual_result.get('market_change', 0):+.2f}%
  ???먯씡:   {actual_result.get('pnl_pct', 0):+.2f}%

?곣봺???쒖옣 而⑦뀓?ㅽ듃 ?곣봺??
{digest_prompt[:400]}
{selection_feedback}

?곣봺???꾩쟻 ?숈뒿 ?꾪솴 ?곣봺??
{brain_summary}

紐⑤뱺 臾몄옄??媛믪? 20???대궡濡?媛꾧껐?섍쾶. JSON?쇰줈留??묐떟:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "吏㏐쾶",
  "bear_why":      "吏㏐쾶",
  "neutral_why":   "吏㏐쾶",
  "best_trade":    null,
  "worst_trade":   null,
  "worst_trade_reason": "",
  "key_lesson":    "?듭떖 援먰썕",
  "issue_type":    "???⑥뼱",
  "issue_desc":    "吏㏐쾶",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "援먰썕 ?먮뒗 null",
    "market_regime": "???⑥뼱"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["二쇱쓽?ы빆"],
    "bear_adjustments":  ["二쇱쓽?ы빆"],
    "tuning_rules":      ["洹쒖튃"],
    "today_notes":       "吏㏐쾶"
  }}
}}"""
    else:
        # ?? 嫄곕옒 ?덈뒗 ?? ?꾩껜 ?꾨＼?꾪듃 ?????????????????????????????????????
        best  = max(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        worst = min(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        best_s  = (f"{best['ticker']} {best.get('pnl_pct', 0):+.2f}% ({best['pnl']:+,}?? ({best.get('strategy','-')})"
                   if best else "?놁쓬")
        worst_s = (f"{worst['ticker']} {worst.get('pnl_pct', 0):+.2f}% ({worst['pnl']:+,}?? ({worst.get('strategy','-')})"
                   if worst else "?놁쓬")

        prompt = f"""?뱀떊? ?몃젅?대뵫 AI???ы썑 遺꾩꽍媛?낅땲??
?ㅻ뒛 嫄곕옒? ?꾩묠 ?먮떒??鍮꾧탳???붿쭅?섍쾶 蹂듦린?섏꽭??

?곣봺???꾩묠 ?먮떒 ?곣봺??
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  ?⑹쓽 紐⑤뱶: {consensus_mode} (size={consensus.get('size','-')}%)

?곣봺???ㅻ뒛 泥닿껐 ?댁뿭 ({len(trade_log)}嫄? ?곣봺??
{trade_section}
  理쒓퀬 嫄곕옒: {best_s}
  理쒖븙 嫄곕옒: {worst_s}

?곣봺???ㅻ뒛 留ㅼ닔/留ㅻ룄 ?먮떒 濡쒓렇 ({len(decision_event_log)}嫄? ?곣봺??
{decision_section}

?곣봺???ㅼ젣 寃곌낵 ?곣봺??
  ?쒖옣 蹂?? {actual_result.get('market_change', 0):+.2f}%
  ???먯씡:   {actual_result.get('pnl_pct', 0):+.2f}%  {'WIN' if actual_result.get('win') else 'LOSS'}
  ?섏씡 泥?궛: {len(wins)}嫄?/ ?먯떎 泥?궛: {len(losses)}嫄?

?곣봺???쒖옣 而⑦뀓?ㅽ듃 ?곣봺??
{digest_prompt[:350]}
{selection_feedback}

?곣봺???꾩쟻 ?숈뒿 ?꾪솴 ?곣봺??
{brain_summary}

?곣봺??遺꾩꽍 吏移??곣봺??
1. ?꾩묠 Bull/Bear/Neutral ?먮떒???ㅼ젣濡?留욎븯?붿? ?됯??섏꽭??
2. ?대뼡 嫄곕옒媛 ??醫뗭븯怨????섎뭅?붿? 援ъ껜?곸쑝濡??ㅻ챸?섏꽭??
3. ?먯떎 嫄곕옒媛 ?덈떎硫?諛섎뱶???먯씤??遺꾩꽍?섏꽭??
4. 媛숈? ?곹솴??諛섎났?쒕떎硫??댁씪 ?대뼸寃??ㅻⅤ寃??좎? ?쒖븞?섏꽭??

紐⑤뱺 臾몄옄??媛믪? 30???대궡濡?媛꾧껐?섍쾶. JSON?쇰줈留??묐떟:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "吏㏐쾶",
  "bear_why":      "吏㏐쾶",
  "neutral_why":   "吏㏐쾶",
  "best_trade":    "ticker ?먮뒗 null",
  "worst_trade":   "ticker ?먮뒗 null",
  "worst_trade_reason": "吏㏐쾶",
  "key_lesson":    "?듭떖 援먰썕",
  "issue_type":    "???⑥뼱",
  "issue_desc":    "吏㏐쾶",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "援먰썕 ?먮뒗 null",
    "market_regime": "???⑥뼱"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["二쇱쓽?ы빆"],
    "bear_adjustments":  ["二쇱쓽?ы빆"],
    "tuning_rules":      ["洹쒖튃"],
    "today_notes":       "吏㏐쾶"
  }}
}}"""

    # ?? 肄붾뱶 湲곕컲 HIT/MISS ?ъ쟾 怨꾩궛 (Claude ?명뼢 ?쒓굅) ???????????????????????
    market_chg = actual_result.get("market_change", 0)
    code_bull    = _code_judge_hit_miss(judgments.get("bull",    {}).get("stance", "NEUTRAL"), market_chg)
    code_bear    = _code_judge_hit_miss(judgments.get("bear",    {}).get("stance", "NEUTRAL"), market_chg)
    code_neutral = _code_judge_hit_miss(judgments.get("neutral", {}).get("stance", "NEUTRAL"), market_chg)
    log.info(f"[postmortem 肄붾뱶?먯젙] ?쒖옣蹂??{market_chg:+.2f}% | "
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
        # Claude HIT/MISS瑜?肄붾뱶 ?먯젙?쇰줈 ??뼱?뚯? (?명뼢 ?쒓굅)
        pm["bull_result"]    = code_bull
        pm["bear_result"]    = code_bear
        pm["neutral_result"] = code_neutral
    except Exception as e:
        log.error(f"postmortem ?ㅻ쪟: {e}")
        pm = {
            "bull_result": code_bull,
            "bear_result": code_bear,
            "neutral_result": code_neutral,
            "bull_why": "?묐떟 ?ㅽ뙣, 肄붾뱶 ?먯젙",
            "bear_why": "?묐떟 ?ㅽ뙣, 肄붾뱶 ?먯젙",
            "neutral_why": "?묐떟 ?ㅽ뙣, 肄붾뱶 ?먯젙",
            "key_lesson": "postmortem ?묐떟 ?ㅽ뙣",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "postmortem_error", "issue_desc": str(e)[:160], "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": "postmortem ?묐떟 ?ㅽ뙣"},
        }

    # ?? brain ?낅뜲?댄듃 ????????????????????????????????????????????????????????
    recent = BrainDB.load()["markets"][market].get("recent_days", [])

    BrainDB.update_analyst(market, "bull",    pm["bull_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "bear",    pm["bear_result"]    == "HIT", recent)
    BrainDB.update_analyst(market, "neutral", pm["neutral_result"] == "HIT", recent)
    BrainDB.update_mode_performance(
        market, consensus_mode,
        actual_result.get("pnl_pct", 0), actual_result.get("win", False)
    )


    bu = pm.get("brain_updates", {})
    # new_lesson ?놁쑝硫?key_lesson??fallback?쇰줈 ?ъ슜
    lesson_to_save = bu.get("new_lesson") or pm.get("key_lesson")
    if lesson_to_save and not _is_placeholder_lesson(lesson_to_save):
        BrainDB.update_beliefs(market, {"new_lesson": lesson_to_save})
    if bu.get("market_regime") and bu["market_regime"] != "unknown":
        BrainDB.update_beliefs(market, {"market_regime": bu["market_regime"]})

    BrainDB.update_issue_pattern(market, {
        "matched_id":  pm.get("pattern_id"),
        "type":        pm.get("issue_type", "unknown"),
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
        "selection_feedback": BrainDB.get_recent_selection_feedback_text(market, days=20, max_chars=400),
    })

    # ?? ?꾨왂蹂??깃낵 ?먮룞 ?낅뜲?댄듃 ?????????????????????????????????????????????
    for strat, pnls in _strategy_pnl(trade_log).items():
        avg_pnl = sum(pnls) / len(pnls)
        BrainDB.update_strategy_performance(market, strat, avg_pnl, avg_pnl > 0)

    # ?? ?좊줎 寃곌낵 ?뺣떟 ?щ? ?낅뜲?댄듃 ?????????????????????????????????????????
    try:
        BrainDB.update_debate_outcome(market, date, actual_result.get("win", False))
    except Exception as e:
        log.warning(f"?좊줎 寃곌낵 ?낅뜲?댄듃 ?ㅽ뙣: {e}")

    # ?? ?댁씪 Claude 蹂댁젙 吏移??낅뜲?댄듃 ???????????????????????????????????????
    cg = pm.get("correction_guide", {})
    if cg:
        BrainDB.update_correction_guide(market, cg)

    log.info(
        f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
        f"Neut:{pm['neutral_result']} | {pm.get('key_lesson','')[:60]}"
    )
    if pm.get("worst_trade"):
        log.warning(
            f"[worst_trade] {pm['worst_trade']} ??{pm.get('worst_trade_reason','')}"
        )

    # ?? JSONL ?숈뒿 濡쒓렇 ???(?꾨＼?꾪듃 + ?묐떟 + 嫄곕옒 ?먮낯) ???????????????????
    judgment_log.info(
        f"[postmortem {date} {market}] "
        f"Bull:{pm['bull_result']} Bear:{pm['bear_result']} Neutral:{pm['neutral_result']}",
        extra={"extra": {
            "event":          "postmortem",
            "date":           date,
            "market":         market,
            "consensus_mode": consensus_mode,
            "actual_result":  actual_result,
            "trade_log":      trade_log,          # ?뱀씪 泥닿껐 ?먮낯 蹂댁〈
            "postmortem":     pm,
            "strategy_pnl":   _strategy_pnl(trade_log),
        }},
    )

    return pm

