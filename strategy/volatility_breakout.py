"""strategy/volatility_breakout.py - 변동성 돌파 전략 (미국장)"""
import pandas as pd

def target_price(row: pd.Series, k: float = 0.45) -> float:
    prev_range = float(row.get("high", 0)) - float(row.get("low", 0))
    return float(row.get("open", 0)) + prev_range * k

def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if i < 5: return False
    row      = df.iloc[i]
    k        = params.get("k", 0.45)
    vol_mult = params.get("vol_mult", 2.0)
    prev     = df.iloc[i-1]
    prev_range = float(prev.get("high",0)) - float(prev.get("low",0))
    target   = float(row.get("open",0)) + prev_range * k
    close    = float(row.get("close",0))
    vol_ratio= float(row.get("vol_ratio",1))
    return close > target and vol_ratio > vol_mult

def params(brain_mode: str, brain_k: float = 0.45) -> dict:
    return {"tp_pct": 0.025, "sl_pct": 0.015, "max_hold": 1,
            "k": brain_k, "vol_mult": 2.0}
