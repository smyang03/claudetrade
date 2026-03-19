"""minority_report/postmortem.py - 장 마감 후 사후 분석"""
import os, json, sys
import anthropic
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_minority_logger
import brain as BrainDB

log    = get_minority_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
MODEL  = "claude-sonnet-4-6"

def run(market: str, date: str, judgment: dict,
        actual_result: dict, digest_prompt: str) -> dict:
    brain_summary = BrainDB.generate_prompt_summary(market)
    prompt = f"""트레이딩 AI 사후 분석가입니다.

판단:
  Bull:    {judgment['bull']['stance']} / {judgment['bull']['key_reason']}
  Bear:    {judgment['bear']['stance']} / {judgment['bear']['key_reason']}
  Neutral: {judgment['neutral']['stance']} / {judgment['neutral']['key_reason']}
  합의:    {judgment['consensus']['mode']}

실제 결과:
  시장: {actual_result.get('market_change',0):+.2f}%
  손익: {actual_result.get('pnl_pct',0):+.2f}%
  {'승' if actual_result.get('win') else '패'}

{digest_prompt[:400]}
{brain_summary[:200]}

JSON으로만:
{{"bull_result":"HIT|MISS|PARTIAL","bear_result":"HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":"한 문장","bear_why":"한 문장","neutral_why":"한 문장",
  "key_lesson":"오늘 핵심 교훈",
  "issue_type":"이슈 유형","issue_desc":"한 문장",
  "pattern_id":"기존ID 또는 null",
  "brain_updates":{{"bull_reliability_change":"up|down|stable",
    "bear_reliability_change":"up|down|stable",
    "new_lesson":"교훈 또는 null","market_regime":"장세"}}}}"""
    try:
        resp = client.messages.create(model=MODEL,max_tokens=512,
            messages=[{"role":"user","content":prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw: raw=raw.split("```")[1].replace("json","").strip()
        pm = json.loads(raw)
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        win = actual_result.get("win",False)
        pm = {"bull_result":"HIT" if win else "MISS",
              "bear_result":"MISS" if win else "HIT",
              "neutral_result":"PARTIAL","bull_why":"자동","bear_why":"자동",
              "neutral_why":"자동","key_lesson":"오류로 자동 판정",
              "issue_type":"미분류","issue_desc":"","pattern_id":None,
              "brain_updates":{"bull_reliability_change":"stable",
                "bear_reliability_change":"stable","new_lesson":None,"market_regime":"unknown"}}
    # brain 업데이트
    recent = BrainDB.load()["markets"][market].get("recent_days",[])
    BrainDB.update_analyst(market,"bull",  pm["bull_result"]=="HIT",  recent)
    BrainDB.update_analyst(market,"bear",  pm["bear_result"]=="HIT",  recent)
    BrainDB.update_analyst(market,"neutral",pm["neutral_result"]=="HIT",recent)
    BrainDB.update_mode_performance(market,judgment["consensus"]["mode"],
        actual_result.get("pnl_pct",0), actual_result.get("win",False))
    bu = pm.get("brain_updates",{})
    if bu.get("new_lesson"):
        BrainDB.update_beliefs(market,{"new_lesson":bu["new_lesson"]})
    if bu.get("market_regime"):
        BrainDB.update_beliefs(market,{"market_regime":bu["market_regime"]})
    BrainDB.update_issue_pattern(market,{
        "matched_id":pm.get("pattern_id"),
        "type":pm.get("issue_type","미분류"),
        "description":pm.get("issue_desc",""),
        "bull_hit":pm["bull_result"]=="HIT",
        "pnl_pct":actual_result.get("pnl_pct",0),
        "insight":pm.get("key_lesson",""),
    })
    BrainDB.add_daily_record(market,{
        "date":date,"mode":judgment["consensus"]["mode"],
        "pnl_pct":actual_result.get("pnl_pct",0),
        "win":actual_result.get("win",False),
        "bull_result":pm["bull_result"],"bear_result":pm["bear_result"],
        "neutral_result":pm["neutral_result"],
    })
    log.info(f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
             f"Neut:{pm['neutral_result']} | {pm['key_lesson'][:60]}")
    return pm
