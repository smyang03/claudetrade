"""strategy/continuation.py — 강세 갭 연속 진입 전략 (눌림 미발생 시 대체 신호)

개장 후 강한 갭 종목이 눌림 없이 직선 상승할 때 gap_pullback 조건이 미충족됨.
이 전략은 갭+유동성+연속 상승 확인 후 개장 초반에만 1회 소사이즈로 진입.

사용 조건:
  - session_elapsed_min: [cont_entry_min_min, cont_entry_max_min] (기본 10~45분)
  - gap_pct >= cont_gap_min (기본 4.0%) — gap_pullback보다 강한 갭 요구
  - vol_ratio >= cont_vol_mult (기본 1.5) — 최소 유동성 확인
  - close > open * cont_continuation_min (기본 open + 0.3%) — 실제 상승 중
  - close <= open * cont_overheat_max (기본 open * 1.07) — 과열 방지
  - 1회 진입 제한: trading_bot이 cont_used 플래그 관리
  - 사이즈 승수: 0.5 (정규 대비 절반)
"""
import pandas as pd


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    """
    True 반환 시 continuation entry 조건 충족.
    params에 session_elapsed_min이 주입되어 있어야 함.
    """
    if params.get("disabled"):
        return False
    if i < 5:
        return False

    elapsed_min = float(params.get("session_elapsed_min", 999) or 999)
    min_min     = float(params.get("cont_entry_min_min", 10) or 10)
    max_min     = float(params.get("cont_entry_max_min", 45) or 45)
    if not (min_min <= elapsed_min <= max_min):
        return False

    row     = df.iloc[i]
    gap     = float(row.get("gap_pct", 0) or 0) / 100   # gap_pct는 %단위
    vol_avg = float(row.get("vol_avg20", 1) or 1)
    volume  = float(row.get("volume", 0) or 0)
    vol_ratio = volume / vol_avg if vol_avg else 0.0

    o  = float(row.get("open", 0) or 0)
    c  = float(row.get("close", 0) or 0)
    h  = float(row.get("high", 0) or 0)

    if o <= 0 or c <= 0:
        return False

    gap_min       = float(params.get("cont_gap_min", 0.040) or 0.040)
    vol_mult      = float(params.get("cont_vol_mult", 1.5) or 1.5)
    cont_min      = float(params.get("cont_continuation_min", 0.003) or 0.003)
    overheat_max  = float(params.get("cont_overheat_max", 0.070) or 0.070)

    # 갭 충분한지
    if gap < gap_min:
        return False
    # 최소 유동성
    if vol_ratio < vol_mult:
        return False
    # 실제 상승 중 (close > open * (1 + cont_min)) — pullback 아님
    if c <= o * (1 + cont_min):
        return False
    # 과열 방지: close가 open 대비 overheat_max 이내
    if c > o * (1 + overheat_max):
        return False
    # 단일가봉 제외 (open=high=low — 실제 움직임 없음)
    if h == float(row.get("low", 0) or 0) == o:
        return False

    return True


def params(brain_mode: str, conf: float = 0.6, market: str = "KR") -> dict:
    """
    brain_mode / market별 파라미터.
    MILD_BULL 미만 모드에서는 disabled=True.
    """
    _disabled_modes = {
        "HALT",
        "DEFENSIVE",
        "CAUTIOUS_BEAR",
        "MILD_BEAR",
        "NEUTRAL",
        "CAUTIOUS",
    }

    if brain_mode in _disabled_modes:
        return {"disabled": True, "cont_gap_min": 9.9, "cont_vol_mult": 9.9,
                "size_mult": 0.5, "tp_pct": 0.020, "sl_pct": 0.010,
                "max_hold": 1, "cont_entry_min_min": 10, "cont_entry_max_min": 45}

    # 모드별 갭/거래량 임계값 (강세일수록 완화)
    _kr = {
        "AGGRESSIVE":   (0.030, 1.2),
        "MODERATE_BULL":(0.033, 1.3),
        "MILD_BULL":    (0.035, 1.4),
        "CAUTIOUS":     (0.037, 1.5),
        "NEUTRAL":      (0.040, 1.5),
        "MILD_BEAR":    (0.045, 1.8),
    }
    _us = {
        "AGGRESSIVE":   (0.028, 1.2),
        "MODERATE_BULL":(0.030, 1.3),
        "MILD_BULL":    (0.032, 1.4),
        "CAUTIOUS":     (0.035, 1.5),
        "NEUTRAL":      (0.040, 1.5),
        "MILD_BEAR":    (0.045, 1.8),
    }

    table = _us if market.upper() == "US" else _kr
    gap_min, vol_mult = table.get(brain_mode, table["NEUTRAL"])

    # confidence 보정: conf 낮을수록 조건 엄격하게
    conf_adj = (0.6 - max(0.4, min(0.9, conf))) * 1.0
    vol_mult = round(max(1.0, vol_mult + conf_adj), 2)

    return {
        "tp_pct": 0.020,           # 정규보다 작은 TP (소사이즈 보상)
        "sl_pct": 0.010,
        "max_hold": 1,
        "size_mult": 0.5,          # 정규 사이즈의 50%
        "cont_gap_min": gap_min,
        "cont_vol_mult": vol_mult,
        "cont_continuation_min": 0.003,   # open 대비 +0.3% 이상 상승 중
        "cont_overheat_max": 0.070,       # open 대비 +7% 이상이면 과열
        "cont_entry_min_min": 10,         # 개장 후 최소 10분 후
        "cont_entry_max_min": 45,         # 개장 후 최대 45분 이내
        "disabled": False,
    }


def diagnostics(df: pd.DataFrame, i: int, params: dict) -> dict:
    """신호 미충족 이유 디버깅용."""
    if params.get("disabled"):
        return {"ready": False, "reason": "disabled"}
    if i < 5:
        return {"ready": False, "reason": "data_insufficient"}

    elapsed_min = float(params.get("session_elapsed_min", 999) or 999)
    min_min     = float(params.get("cont_entry_min_min", 10) or 10)
    max_min     = float(params.get("cont_entry_max_min", 45) or 45)
    in_window   = min_min <= elapsed_min <= max_min

    row       = df.iloc[i]
    gap       = float(row.get("gap_pct", 0) or 0) / 100
    vol_avg   = float(row.get("vol_avg20", 1) or 1)
    vol_ratio = float(row.get("volume", 0) or 0) / vol_avg if vol_avg else 0.0
    o = float(row.get("open", 0) or 0)
    c = float(row.get("close", 0) or 0)

    gap_min      = float(params.get("cont_gap_min", 0.040))
    vol_mult     = float(params.get("cont_vol_mult", 1.5))
    cont_min     = float(params.get("cont_continuation_min", 0.003))
    overheat_max = float(params.get("cont_overheat_max", 0.070))

    cont_ratio  = (c / o - 1) if o > 0 else 0.0

    return {
        "ready":        in_window,
        "elapsed_min":  elapsed_min,
        "in_window":    in_window,
        "gap_ok":       gap >= gap_min,
        "gap":          round(gap * 100, 2),
        "gap_min":      round(gap_min * 100, 2),
        "vol_ok":       vol_ratio >= vol_mult,
        "vol_ratio":    round(vol_ratio, 3),
        "vol_mult":     vol_mult,
        "cont_ok":      o > 0 and c > o * (1 + cont_min),
        "cont_ratio":   round(cont_ratio * 100, 2),
        "overheat_ok":  o <= 0 or c <= o * (1 + overheat_max),
    }
