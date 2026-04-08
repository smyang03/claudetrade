"""strategy/volatility_breakout.py - 변동성 돌파 전략

현재 KR/US 모두 비활성화 상태 (Sharpe 미달).
재활성화 시: disabled 조건 제거 후 파라미터 테이블 복원 필요.
"""
import pandas as pd


def target_price(row: pd.Series, k: float = 0.45) -> float:
    prev_range = float(row.get("high", 0)) - float(row.get("low", 0))
    return float(row.get("open", 0)) + prev_range * k


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if params.get("disabled"):
        return False
    if i < 5:
        return False
    row      = df.iloc[i]
    vol_mult = params.get("vol_mult", 2.0)
    prev     = df.iloc[i - 1]
    target   = target_price(prev, params.get("k", 0.45))
    close    = float(row.get("close", 0))
    vol_ratio= float(row.get("vol_ratio", 1))
    return close > target and vol_ratio > vol_mult


def params(brain_mode: str, brain_k: float = 0.45, conf: float = 0.6,
           market: str = "KR") -> dict:
    # KR/US 모두 비활성화 — 재활성화 시 이 조건 제거
    return {"tp_pct": 0.025, "sl_pct": 0.015, "max_hold": 2,
            "k": 0.45, "vol_mult": 9.9, "disabled": True}
