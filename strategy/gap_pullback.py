"""strategy/gap_pullback.py - 갭 + 눌림 전략 (국내 단타)"""
import pandas as pd

def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if i < 5: return False
    row  = df.iloc[i]
    prev = df.iloc[i-1]
    gap_min   = params.get("gap_min", 0.010)
    vol_mult  = params.get("vol_mult", 1.5)
    vol_avg   = row.get("vol_avg20", 1)
    gap       = float(row.get("gap_pct", 0)) / 100
    vol_ratio = float(row.get("volume", 0)) / vol_avg if vol_avg else 0
    pullback  = float(row.get("low", 0)) >= float(row.get("open", 0)) * 0.995
    return gap > gap_min and vol_ratio > vol_mult and pullback

def params(brain_mode: str) -> dict:
    return {"tp_pct": 0.025, "sl_pct": 0.010, "max_hold": 1,
            "gap_min": 0.010, "vol_mult": 1.5}
