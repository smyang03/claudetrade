"""strategy/gap_pullback.py - 갭 + 눌림 전략"""
import pandas as pd


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if params.get("disabled"):
        return False
    if i < 5:
        return False
    row     = df.iloc[i]
    vol_avg = row.get("vol_avg20", 1)
    gap     = float(row.get("gap_pct", 0)) / 100
    vol_ratio = float(row.get("volume", 0)) / vol_avg if vol_avg else 0
    o = float(row.get("open", 0) or 0)
    h = float(row.get("high", 0) or 0)
    l = float(row.get("low", 0) or 0)
    c = float(row.get("close", 0) or 0)
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return False

    # 장초반 여부 판단 — trading_bot이 session_elapsed_min 주입
    elapsed_min  = params.get("session_elapsed_min", 999)
    opening_min  = params.get("opening_window_min", 30)
    in_opening   = 0 < elapsed_min <= opening_min

    if in_opening:
        gap_min  = params.get("opening_gap_min", 0.030)
        vol_mult = params.get("opening_vol_mult", 0.15)
        pullback_min = params.get("opening_pullback_min_pct", 0.006)
        pullback_max = params.get("opening_pullback_max_pct", 0.050)

        # 장초 단일가봉 진입 억제 — open=high=low이면 실제 눌림이 없는 첫 틱 상태
        # gap_pct 주입 후 pullback=True가 trivially 충족되므로 고점추격 방지
        if h == l == o:
            return False  # 단일가봉 — 눌림 미확인, 진입 보류
    else:
        gap_min  = params.get("gap_min", 0.015)
        vol_mult = params.get("vol_mult", 1.8)
        pullback_min = params.get("pullback_min_pct", 0.010)
        pullback_max = params.get("pullback_max_pct", 0.040)

    pullback_depth = (h - l) / h if h > 0 else 0.0
    open_drawdown = (o - l) / o if o > 0 and l < o else 0.0
    open_drawdown_max = params.get("open_drawdown_max_pct", 0.025)
    recovery_min = params.get("recovery_min_pct", 0.003)
    open_reclaim_buffer = params.get("open_reclaim_buffer_pct", 0.003)
    recovered = c >= l * (1 + recovery_min) and c >= o * (1 - open_reclaim_buffer)
    pullback = (
        pullback_min <= pullback_depth <= pullback_max
        and open_drawdown <= open_drawdown_max
        and recovered
    )

    return gap > gap_min and vol_ratio > vol_mult and pullback


def params(brain_mode: str, conf: float = 0.6, market: str = "KR") -> dict:
    # KR CAUTIOUS_BEAR: 시뮬 결과 WR=18.2%, avg=-0.361% → 진입 차단
    if market.upper() == "KR" and brain_mode == "CAUTIOUS_BEAR":
        return {"tp_pct": 0.025, "sl_pct": 0.010, "max_hold": 1,
                "gap_min": 9.99, "vol_mult": 9.9, "disabled": True}

    # KR: 시뮬레이션 확정값 — NEUTRAL gap_min=0.018, vol_mult=2.0
    _kr = {
        "AGGRESSIVE":   (0.013, 1.5),
        "MODERATE_BULL":(0.015, 1.6),
        "MILD_BULL":    (0.016, 1.7),
        "CAUTIOUS_BULL":(0.017, 1.9),
        "NEUTRAL":      (0.018, 2.0),
        "MILD_BEAR":    (0.020, 2.0),
        "DEFENSIVE":    (0.025, 2.5),
        "HALT":         (9.99,  9.9),
    }
    # US: 시뮬레이션 확정값 — NEUTRAL gap_min=0.018, vol_mult=1.8
    _us = {
        "AGGRESSIVE":   (0.012, 1.3),
        "MODERATE_BULL":(0.014, 1.4),
        "MILD_BULL":    (0.015, 1.5),
        "CAUTIOUS_BULL":(0.016, 1.6),
        "NEUTRAL":      (0.018, 1.8),
        "MILD_BEAR":    (0.020, 2.0),
        "CAUTIOUS_BEAR":(0.022, 2.2),
        "DEFENSIVE":    (0.025, 2.5),
        "HALT":         (9.99,  9.9),
    }

    table = _us if market.upper() == "US" else _kr
    gap_min, vol_mult = table.get(brain_mode, table["NEUTRAL"])
    market_upper = market.upper()

    conf_adj = (0.6 - max(0.4, min(0.9, conf))) * 0.8
    vol_mult = round(max(1.0, vol_mult + conf_adj), 2)

    if market_upper == "US":
        opening_gap_min = 0.030
        opening_vol_mult = 0.15
    else:
        opening_gap_min = 0.030
        opening_vol_mult = 0.20

    return {"tp_pct": 0.025, "sl_pct": 0.010, "max_hold": 1,
            "gap_min": gap_min, "vol_mult": vol_mult,
            # 장초반 완화 파라미터 — trading_bot이 session_elapsed_min 주입하면 활성화
            "opening_gap_min": opening_gap_min,   # 3.0% 이상 갭만 허용 (노이즈 방지)
            "opening_vol_mult": opening_vol_mult, # 장초 vol_ratio 완화
            "opening_window_min": 30,   # 장 시작 후 30분 이내
            "pullback_min_pct": 0.010,
            "pullback_max_pct": 0.040,
            "opening_pullback_min_pct": 0.006,
            "opening_pullback_max_pct": 0.050,
            "open_drawdown_max_pct": 0.025,
            "recovery_min_pct": 0.003,
            "open_reclaim_buffer_pct": 0.003,
            }
