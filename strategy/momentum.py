"""strategy/momentum.py - 모멘텀 전략 (국내 스윙)"""
import pandas as pd

def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if i < 60: return False
    row = df.iloc[i]
    ma5  = float(row.get("ma5", 0))
    ma20 = float(row.get("ma20", 0))
    ma60 = float(row.get("ma60", 0))
    macd = float(row.get("macd", 0))
    sig  = float(row.get("macd_signal", 0))
    vol_avg = float(row.get("vol_avg20", 1))
    vol     = float(row.get("volume", 0))
    high20  = float(row.get("high20", 0))
    close   = float(row.get("close", 0))
    ma_ok   = ma5 > ma20 > ma60
    macd_ok = macd > sig
    vol_ok  = vol > vol_avg * 2.0
    high_ok = close > high20 if high20 > 0 else False
    return ma_ok and macd_ok and vol_ok and high_ok

def params(brain_mode: str) -> dict:
    size = {"AGGRESSIVE":1.0,"MODERATE_BULL":0.7,"CAUTIOUS":0.4}.get(brain_mode,0.5)
    return {"tp_pct": 0.060, "sl_pct": 0.030, "max_hold": 5, "size_mult": size}
