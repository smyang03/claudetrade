"""minority_report/consensus.py - 3명 합의 엔진 + 마이너리티 룰

개선사항:
  3. 합의 가중치 - 분석가별 과거 적중률로 투표 비중 조정
     데이터 부족(< MIN_DATA)이면 기존 1:1:1 투표 사용
"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
from logger import get_minority_logger

load_dotenv()
log = get_minority_logger()

# 가중치 적용 최소 누적 판단 횟수 (이 이하면 균등 가중치)
MIN_DATA = 10

# stance → 수치 점수 (-1.0 ~ +1.0)
STANCE_SCORE = {
    "AGGRESSIVE":    1.00,
    "MODERATE_BULL": 0.70,
    "MILD_BULL":     0.40,
    "CAUTIOUS":      0.15,   # bull 2:1 합의 결과용
    "NEUTRAL":       0.00,
    "MILD_BEAR":    -0.40,
    "CAUTIOUS_BEAR":-0.70,
    "DEFENSIVE":    -0.90,
    "HALT":         -1.00,
}

def _e(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))

# 가중 점수 → (mode, size)
# 임계값 = 인접 STANCE_SCORE 중간값 기준
def _score_to_mode(score: float) -> tuple:
    if   score >=  0.85: return "AGGRESSIVE",    _e("SIZE_AGGRESSIVE",    100)
    elif score >=  0.55: return "MODERATE_BULL", _e("SIZE_MODERATE_BULL",  80)
    elif score >=  0.28: return "MILD_BULL",      _e("SIZE_MILD_BULL",      50)
    elif score >=  0.08: return "CAUTIOUS",       _e("SIZE_CAUTIOUS",       60)
    elif score >= -0.20: return "NEUTRAL",         _e("SIZE_NEUTRAL",        50)
    elif score >= -0.55: return "MILD_BEAR",       _e("SIZE_MILD_BEAR",      30)
    elif score >= -0.80: return "CAUTIOUS_BEAR",   _e("SIZE_CAUTIOUS_BEAR",  20)
    elif score >= -0.95: return "DEFENSIVE",       _e("SIZE_DEFENSIVE",      10)
    else:                return "HALT",             0

# 기존 카테고리 기반 CONSENSUS_MAP (fallback 및 마이너리티 룰용)
CONSENSUS_MAP = {
    ("bull","bull","bull"):           {"mode":"AGGRESSIVE",    "size":_e("SIZE_AGGRESSIVE",   100),"tp_mult":1.2},
    ("bull","bull","neutral"):        {"mode":"MODERATE_BULL", "size":_e("SIZE_MODERATE_BULL", 80), "tp_mult":1.1},
    ("bear","bull","bull"):           {"mode":"CAUTIOUS",      "size":_e("SIZE_CAUTIOUS",      60), "tp_mult":1.0},
    ("bull","neutral","neutral"):     {"mode":"MILD_BULL",     "size":_e("SIZE_MILD_BULL",     50), "tp_mult":1.0},
    ("bear","bull","neutral"):        {"mode":"NEUTRAL",       "size":_e("SIZE_NEUTRAL",       50), "tp_mult":1.0},
    ("neutral","neutral","neutral"):  {"mode":"NEUTRAL",       "size":_e("SIZE_NEUTRAL",       50), "tp_mult":1.0},
    ("bear","neutral","neutral"):     {"mode":"MILD_BEAR",     "size":_e("SIZE_MILD_BEAR",     30), "tp_mult":0.9},
    ("bear","bear","neutral"):        {"mode":"CAUTIOUS_BEAR", "size":_e("SIZE_CAUTIOUS_BEAR", 20), "tp_mult":0.8},
    ("bear","bear","bull"):           {"mode":"DEFENSIVE",     "size":_e("SIZE_DEFENSIVE",     10), "tp_mult":0.8},
    ("bear","bear","bear"):           {"mode":"HALT",          "size":0,                            "tp_mult":0.0},
}

def _cat(stance: str) -> str:
    if stance in ("AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"):
        return "bull"
    if stance in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR"):
        return "bear"
    return "neutral"

TRIGGER_WORDS_KR = ["공시", "급락", "세력", "서킷", "이탈", "장중 -"]
TRIGGER_WORDS_US = ["halt", "circuit", "crash", "sec", "fraud", "bankrupt", "plunge"]

def _get_weights(market: str) -> dict:
    """
    brain.json에서 분석가별 적중률 → 가중치 반환
    데이터 부족 시 균등 가중치(1/3)

    블렌드 공식: rate*0.30 + r30*0.35 + r7*0.35 (r7 충분 시)
    최소 하한: 0.1 (기존 0.2 → 최근 성과 나쁜 분석가 더 강하게 할인)
    """
    try:
        from claude_memory import brain as BrainDB
        brain = BrainDB.load()
        perf  = brain["markets"][market]["analyst_performance"]

        weights = {}
        for atype in ("bull", "bear", "neutral"):
            total = perf[atype]["total"]
            rate  = perf[atype]["rate"]  # 전체 누적 적중률
            r30   = perf[atype]["recent_30d"]
            r7    = perf[atype]["recent_7d"]
            r30_n = r30["total"]
            r7_n  = r7["total"]

            # recent_7d 포함 3-way 블렌드
            # r7 충분(≥5)하면: rate*0.30 + r30*0.35 + r7*0.35
            # r7 부족하면 기존 방식: rate*(1-blend_w) + r30*blend_w
            if r7_n >= 5 and r30_n >= 5:
                blended = rate * 0.30 + r30["rate"] * 0.35 + r7["rate"] * 0.35
            else:
                if r30_n >= 20:
                    blend_w = 0.60
                elif r30_n >= 10:
                    blend_w = 0.45
                elif r30_n >= 5:
                    blend_w = 0.30
                else:
                    blend_w = 0.0
                blended = rate * (1 - blend_w) + r30["rate"] * blend_w if blend_w > 0 else rate

            # 최소 하한 0.1 (기존 0.2) — 최근 성과 나쁜 분석가 더 강하게 할인
            weights[atype] = max(0.1, min(0.8, blended)) if total >= MIN_DATA else None

        # 데이터 부족 분석가가 하나라도 있으면 균등 가중치
        if any(v is None for v in weights.values()):
            log.debug("가중치: 데이터 부족 → 균등 (1:1:1)")
            return {"bull": 1.0, "bear": 1.0, "neutral": 1.0}

        # 정규화: 합이 3이 되도록 (원래 1:1:1 대비 상대적 비중 유지)
        total_w = sum(weights.values())
        for k in weights:
            weights[k] = weights[k] / total_w * 3
        log.debug(f"가중치: Bull={weights['bull']:.2f} "
                  f"Bear={weights['bear']:.2f} Neutral={weights['neutral']:.2f}")
        return weights
    except Exception as e:
        log.warning(f"가중치 로드 실패: {e} → 균등 사용")
        return {"bull": 1.0, "bear": 1.0, "neutral": 1.0}


def build_consensus(judgments: dict, check_minority: bool = True,
                    market: str = "KR") -> dict:
    """
    3명 판단 → 가중 점수 합산 → 최종 모드 결정

    judgments: {"bull": {...}, "bear": {...}, "neutral": {...}}
    """
    bull = judgments["bull"]
    bear = judgments["bear"]
    neut = judgments["neutral"]

    # ── 마이너리티 룰 먼저 체크 (가중치보다 우선) ─────────────────────────────
    minority_triggered = False
    if check_minority:
        bear_reason = bear.get("key_reason", "").lower()
        bear_conf   = bear.get("confidence", 0)
        trigger_words = TRIGGER_WORDS_US if market == "US" else TRIGGER_WORDS_KR
        if any(w in bear_reason for w in trigger_words) and bear_conf > 0.7:
            minority_triggered = True
            log.warning(f"⚠️ 마이너리티 룰 발동 [{market}]: {bear_reason[:60]}")

    # ── 가중 점수 계산 ─────────────────────────────────────────────────────────
    # stance_score만으로 방향 결정 (confidence는 size 보정에만 사용)
    weights = _get_weights(market)

    scores = {
        "bull":    STANCE_SCORE.get(bull["stance"], 0.0),
        "bear":    STANCE_SCORE.get(bear["stance"], 0.0),
        "neutral": STANCE_SCORE.get(neut["stance"], 0.0),
    }

    weighted_score = sum(scores[k] * weights[k] for k in scores) / 3.0
    mode, size = _score_to_mode(weighted_score)

    # confidence 평균으로 size 소폭 보정 (0.7~1.0 범위)
    avg_conf = (bull.get("confidence", 0.5) + bear.get("confidence", 0.5)
                + neut.get("confidence", 0.5)) / 3.0
    conf_mult = 0.7 + avg_conf * 0.3   # conf=0→0.7, conf=1→1.0
    size = max(0, min(100, int(size * conf_mult)))

    # 분석가 suggested_size_pct 반영 (confidence 가중 평균, 있는 경우만)
    analyst_sizes = [
        (j.get("suggested_size_pct"), j.get("confidence", 0.5))
        for j in (bull, bear, neut)
        if j.get("suggested_size_pct") is not None
    ]
    if analyst_sizes:
        w_sum   = sum(w for _, w in analyst_sizes)
        avg_sug = sum(s * w for s, w in analyst_sizes) / w_sum if w_sum else None
        if avg_sug is not None:
            # 분석가 제안(50%)과 기존 size(50%) 혼합
            blended = int(size * 0.5 + avg_sug * 0.5)
            size = max(0, min(100, blended))
            log.info(f"[size 혼합] 기존={int(size*0.5*2)} 분석가제안={avg_sug:.0f} → 최종={size}")

    # ── 만장일치 / 분열에 따른 사이즈 보정 ────────────────────────────────────
    _vote_cats = [_cat(bull["stance"]), _cat(bear["stance"]), _cat(neut["stance"])]
    _n_unique = len(set(_vote_cats))
    if _n_unique == 1:            # 3:0 만장일치 → 확신도 높음 → +30%
        size = max(0, min(100, int(size * 1.3)))
        log.info(f"[size 만장일치 3:0] x1.3 → {size}")
    elif _n_unique == 3:          # 1:1:1 완전분열 → 확신 없음 → -25%
        size = max(0, min(100, int(size * 0.75)))
        log.info(f"[size 완전분열 1:1:1] x0.75 → {size}")
    else:                         # 2:1 분열 → 소폭 축소 → -15%
        size = max(0, min(100, int(size * 0.85)))
        log.info(f"[size 분열 2:1] x0.85 → {size}")

    # tp_mult: CONSENSUS_MAP 기반 카테고리로 보조 참조
    cats = tuple(sorted(_vote_cats))
    tp_mult = CONSENSUS_MAP.get(cats, {}).get("tp_mult", 1.0)

    # ── 마이너리티 룰 적용 ─────────────────────────────────────────────────────
    if minority_triggered:
        # 현재 모드가 DEFENSIVE보다 공격적이면 DEFENSIVE로 강등
        if STANCE_SCORE.get(mode, 0) > STANCE_SCORE["DEFENSIVE"]:
            mode = "DEFENSIVE"
            size = max(10, size // 2)
            tp_mult = 0.8

    result = {
        "mode":               mode,
        "size":               size,
        "tp_mult":            tp_mult,
        "weighted_score":     round(weighted_score, 3),
        "weights":            {k: round(v, 2) for k, v in weights.items()},
        "minority_triggered": minority_triggered,
        "vote":               list(cats),
    }

    log.info(
        f"합의: {mode} size={size}% "
        f"score={weighted_score:+.3f} "
        f"weights=B{weights['bull']:.2f}/Be{weights['bear']:.2f}/N{weights['neutral']:.2f} "
        f"minority={minority_triggered}"
    )
    return result
