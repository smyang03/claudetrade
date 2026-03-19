"""strategy/mean_reversion.py - 평균 회귀 전략 (국내)"""
import pandas as pd

def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if i < 20: return False
    row = df.iloc[i]
    rsi      = float(row.get("rsi", 50))
    bb_pct   = float(row.get("bb_pct", 50))
    vol_ratio= float(row.get("vol_ratio", 1))
    close    = float(row.get("close", 0))
    ma60     = float(row.get("ma60", 0))
    # RSI 과매도 + BB 하단 + 거래량 과도하지 않음 + MA60 위
    return (rsi < 32 and bb_pct < 20 and
            vol_ratio < 2.5 and close > ma60 * 0.95)

def params(brain_mode: str) -> dict:
    return {"tp_bb_mid": True, "sl_pct": 0.020, "max_hold": 7}
