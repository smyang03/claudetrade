"""minority_report/consensus.py - 3명 합의 엔진 + 마이너리티 룰"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent.parent))
from logger import get_minority_logger
log = get_minority_logger()

# 키는 sorted(cats) 결과와 일치해야 함 (알파벳 정렬: bear < bull < neutral)
CONSENSUS_MAP = {
    ("bull","bull","bull"):           {"mode":"AGGRESSIVE",    "size":100,"tp_mult":1.2},
    ("bull","bull","neutral"):        {"mode":"MODERATE_BULL", "size":80, "tp_mult":1.1},
    ("bear","bull","bull"):           {"mode":"CAUTIOUS",      "size":60, "tp_mult":1.0},  # bull×2 + bear
    ("bull","neutral","neutral"):     {"mode":"MILD_BULL",     "size":50, "tp_mult":1.0},
    ("neutral","neutral","neutral"):  {"mode":"NEUTRAL",       "size":40, "tp_mult":1.0},
    ("bear","neutral","neutral"):     {"mode":"MILD_BEAR",     "size":30, "tp_mult":0.9},
    ("bear","bear","neutral"):        {"mode":"CAUTIOUS_BEAR", "size":20, "tp_mult":0.8},
    ("bear","bear","bull"):           {"mode":"DEFENSIVE",     "size":10, "tp_mult":0.8},
    ("bear","bear","bear"):           {"mode":"HALT",          "size":0,  "tp_mult":0.0},
}

def _cat(stance: str) -> str:
    if stance in ("AGGRESSIVE","MODERATE_BULL","MILD_BULL"): return "bull"
    if stance in ("HALT","DEFENSIVE","CAUTIOUS_BEAR","MILD_BEAR"): return "bear"
    return "neutral"

def build_consensus(judgments: dict, check_minority: bool = True) -> dict:
    bull = judgments["bull"]; bear = judgments["bear"]; neut = judgments["neutral"]
    cats = [_cat(bull["stance"]), _cat(bear["stance"]), _cat(neut["stance"])]
    key  = tuple(sorted(cats))
    base = dict(CONSENSUS_MAP.get(key, {"mode":"CAUTIOUS","size":40,"tp_mult":1.0}))

    # 마이너리티 룰: Bear가 공시/세력 관련 극단 경고 시 우선 채택
    minority_triggered = False
    if check_minority:
        bear_reason = bear.get("key_reason","").lower()
        bear_conf   = bear.get("confidence",0)
        trigger_words = ["공시","급락","세력","서킷","이탈","장중 -"]
        if any(w in bear_reason for w in trigger_words) and bear_conf > 0.7:
            base["mode"] = "DEFENSIVE"
            base["size"] = max(10, base["size"]//2)
            minority_triggered = True
            log.warning(f"⚠️ 마이너리티 룰 발동: {bear_reason[:60]}")

    base["minority_triggered"] = minority_triggered
    base["vote"] = cats
    log.info(f"합의: {base['mode']} size={base['size']}% minority={minority_triggered}")
    return base
