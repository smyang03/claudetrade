"""minority_report/tuner.py - 장 중 30분 튜닝"""
import os, json, time, sys
import anthropic
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_minority_logger
from claude_memory import brain as BrainDB
from credit_tracker import record as credit_record

log    = get_minority_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY",""))
MODEL  = "claude-sonnet-4-6"

def tune(market: str, elapsed_min: int, current_state: dict,
         morning_judgment: dict, brain_summary: str) -> dict:
    """
    장 중 튜닝 - 현재 상황 vs 아침 예측 비교
    반환: {action: MAINTAIN|TIGHTEN|REVERSE, mode, size, sl_adj, reason}
    """
    prev_mode = morning_judgment.get("consensus",{}).get("mode","CAUTIOUS")
    prompt = f"""장 중 튜닝 분석가입니다. 아침 판단과 현재 상황을 비교하세요.

아침 판단: {prev_mode} / Bull: {morning_judgment.get('judgments',{}).get('bull',{}).get('key_reason','')}
{brain_summary[:300]}

현재 상황 ({elapsed_min}분 경과):
  지수 변동: {current_state.get('index_change',0):+.2f}%
  거래량 추이: {current_state.get('volume_trend','보통')}
  보유 포지션: {json.dumps(current_state.get('positions',[]),ensure_ascii=False)}
  이상 신호: {current_state.get('alerts',[])}

JSON으로만:
{{"action":"MAINTAIN|TIGHTEN|REVERSE","mode":"조정된 모드",
  "size_adj":0,"sl_adj":0.0,"reason":"조정 이유 한 문장",
  "warning":"주의사항 또는 null"}}"""
    try:
        resp = client.messages.create(model=MODEL, max_tokens=256,
            messages=[{"role":"user","content":prompt}])
        raw = resp.content[0].text.strip()
        if "```" in raw: raw = raw.split("```")[1].replace("json","").strip()
        result = json.loads(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, f"tune_{elapsed_min}min")
        log.info(f"[튜닝 {elapsed_min}분] {result.get('action','-')} "
                 f"→ {result.get('mode','-')} | {result.get('reason','')[:60]}")
        # 튜닝 패턴 기록
        key = f"{(elapsed_min//30)*30}min_tune"
        BrainDB.update_tuning_pattern(market, key,
            correct=(result.get("action")!="MAINTAIN"),
            new_insight=result.get("reason",""))
        return result
    except Exception as e:
        log.error(f"튜닝 오류: {e}")
        return {"action":"MAINTAIN","mode":prev_mode,"size_adj":0,"sl_adj":0.0,
                "reason":f"오류:{e}","warning":None}
