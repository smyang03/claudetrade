"""strategy/opening_range_pullback.py - KR 장초 OR 눌림 전략"""

import pandas as pd


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if params.get("disabled"):
        return False
    if i < 5:
        return False

    elapsed_min = float(params.get("session_elapsed_min", 999) or 999)
    or_minutes = float(params.get("or_minutes", 10) or 10)
    entry_window_min = float(params.get("entry_window_min", 60) or 60)
    or_formed = bool(params.get("or_formed", False))
    or_high = float(params.get("or_high", 0.0) or 0.0)
    or_low = float(params.get("or_low", 0.0) or 0.0)

    if not or_formed:
        return False
    if 0 < elapsed_min <= or_minutes:
        return False
    if elapsed_min > (or_minutes + entry_window_min):
        return False
    if or_high <= 0 or or_low <= 0 or or_high <= or_low:
        return False

    or_range_pct = (or_high - or_low) / or_low if or_low > 0 else 0.0
    or_min_range_pct = float(params.get("or_min_range_pct", 0.003) or 0.003)
    or_max_range_pct = float(params.get("or_max_range_pct", 0.030) or 0.030)
    if not (or_min_range_pct <= or_range_pct <= or_max_range_pct):
        return False

    row = df.iloc[i]
    close_px = float(row.get("close", 0) or 0)
    vol_avg = float(row.get("vol_avg20", 0) or 0)
    vol_ratio = float(row.get("volume", 0) or 0) / vol_avg if vol_avg else 0.0
    vol_mult = float(params.get("vol_mult", 1.3) or 1.3)

    pullback_min_pct = float(params.get("pullback_min_pct", 0.002) or 0.002)
    pullback_max_pct = float(params.get("pullback_max_pct", 0.010) or 0.010)
    upper_bound = or_high * (1.0 - pullback_min_pct)
    lower_bound = or_high * (1.0 - pullback_max_pct)
    in_pullback_zone = lower_bound <= close_px <= upper_bound

    return in_pullback_zone and vol_ratio > vol_mult


def params(brain_mode: str, conf: float = 0.6, market: str = "KR") -> dict:
    market = market.upper()
    if market == "US":
        return {
            "disabled": False,
            "or_minutes": 15,
            "or_min_range_pct": 0.004,
            "or_max_range_pct": 0.035,
            "pullback_min_pct": 0.003,
            "pullback_max_pct": 0.012,
            "vol_mult": 1.2,
            "entry_window_min": 60,
            "tp_pct": 0.030,
            "sl_pct": 0.012,
            "max_hold": 1,
        }
    if market != "KR":
        return {"disabled": True}

    if brain_mode in ("DEFENSIVE", "HALT", "CAUTIOUS_BEAR"):
        return {"disabled": True}

    conf_adj = (0.6 - max(0.4, min(0.9, conf))) * 0.5
    vol_mult = round(max(1.1, 1.3 + conf_adj), 2)

    return {
        "disabled": False,
        "or_minutes": 10,
        "or_min_range_pct": 0.003,
        "or_max_range_pct": 0.030,
        "pullback_min_pct": 0.002,
        "pullback_max_pct": 0.010,
        "vol_mult": vol_mult,
        "entry_window_min": 60,
        "tp_pct": 0.030,
        "sl_pct": 0.012,
        "max_hold": 1,
    }
