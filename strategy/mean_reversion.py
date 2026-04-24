"""strategy/mean_reversion.py - 평균 회귀 전략"""
import pandas as pd


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if params.get("disabled"):
        return False
    if i < 20:
        return False
    row       = df.iloc[i]
    rsi       = float(row.get("rsi", 50))
    bb_pct    = float(row.get("bb_pct", 50))
    vol_ratio = float(row.get("vol_ratio", 1))
    close     = float(row.get("close", 0))
    ma60      = float(row.get("ma60", 0))
    rsi_thr   = float(params.get("rsi_thr", 32))
    bb_thr    = float(params.get("bb_thr", 20))
    ma60_thr  = float(params.get("ma60_thr", 0.85))
    vol_limit = float(params.get("vol_limit", 2.5))
    return (rsi < rsi_thr and bb_pct < bb_thr
            and vol_ratio < vol_limit and close > ma60 * ma60_thr)


def params(brain_mode: str, conf: float = 0.6, market: str = "KR") -> dict:
    if market.upper() == "KR" and brain_mode == "MODERATE_BULL":
        return {"tp_bb_mid": True, "sl_pct": 0.020, "max_hold": 7,
                "rsi_thr": 0, "bb_thr": 0, "ma60_thr": 0.95,
                "vol_limit": 2.5, "disabled": True}
    # KR: 시뮬레이션 확정값 — NEUTRAL rsi=25, bb=15, ma60_thr=0.95
    _kr = {
        "AGGRESSIVE":   (31, 23),
        "MILD_BULL":    (27, 17),
        "CAUTIOUS":     (25, 15),
        "NEUTRAL":      (25, 15),
        "MILD_BEAR":    (23, 13),
        "CAUTIOUS_BEAR":(23, 12),
        "DEFENSIVE":    (18,  7),
        "HALT":         (0,   0),
    }
    # US: 시뮬레이션 확정값 — NEUTRAL rsi=25, bb=17, ma60_thr=0.95
    _us = {
        "AGGRESSIVE":   (31, 25),
        "MODERATE_BULL":(29, 22),
        "MILD_BULL":    (27, 19),
        "CAUTIOUS":     (25, 17),
        "NEUTRAL":      (25, 17),
        "MILD_BEAR":    (23, 15),
        "CAUTIOUS_BEAR":(23, 14),
        "DEFENSIVE":    (20,  9),
        "HALT":         (0,   0),
    }
    table = _us if market.upper() == "US" else _kr
    rsi_thr, bb_thr = table.get(brain_mode, table["NEUTRAL"])

    if conf >= 0.75:
        rsi_thr += 2
        bb_thr  += 3
    elif conf < 0.5:
        rsi_thr -= 2
        bb_thr  -= 3

    ma60_thr = 0.95

    return {"tp_bb_mid": True, "sl_pct": 0.020, "max_hold": 7,
            "rsi_thr": rsi_thr, "bb_thr": bb_thr,
            "ma60_thr": ma60_thr, "vol_limit": 2.5}
