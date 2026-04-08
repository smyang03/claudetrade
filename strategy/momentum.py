"""strategy/momentum.py - 국내 추세 돌파형 모멘텀 전략"""
import pandas as pd


def diagnostics(df: pd.DataFrame, i: int, params: dict) -> dict:
    if i < 0 or i >= len(df):
        return {"ready": False, "reason": "index_out_of_range"}

    row = df.iloc[i]
    ma5         = float(row.get("ma5", 0))
    ma20        = float(row.get("ma20", 0))
    ma60        = float(row.get("ma60", 0))
    macd        = float(row.get("macd", 0))
    macd_signal = float(row.get("macd_signal", 0))
    vol_avg20   = float(row.get("vol_avg20", 0))
    volume      = float(row.get("volume", 0))
    high20      = float(row.get("high20", 0))
    close       = float(row.get("close", 0))
    vol_mult    = float(params.get("vol_mult", 1.5))

    ready   = all(v > 0 for v in (ma5, ma20, ma60, vol_avg20, high20, close))
    ma_ok   = ma5 > ma20 > ma60 if ready else False
    macd_ok = macd > macd_signal
    vol_ok  = volume > vol_avg20 * vol_mult if vol_avg20 > 0 else False
    high_ok = close > high20 if high20 > 0 else False

    return {
        "ready": ready, "ma_ok": ma_ok, "macd_ok": macd_ok,
        "vol_ok": vol_ok, "high_ok": high_ok,
        "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "macd": macd, "macd_signal": macd_signal,
        "volume": volume, "vol_avg20": vol_avg20,
        "vol_mult": vol_mult, "high20": high20, "close": close,
    }


def signal(df: pd.DataFrame, i: int, params: dict) -> bool:
    if params.get("disabled"):
        return False
    diag = diagnostics(df, i, params)
    if not diag.get("ready"):
        return False
    return bool(diag["ma_ok"] and diag["macd_ok"] and diag["vol_ok"] and diag["high_ok"])


def params(brain_mode: str, conf: float = 0.6, market: str = "KR") -> dict:
    if market.upper() == "US":
        return {
            "tp_pct": 0.060,
            "sl_pct": 0.030,
            "max_hold": 5,
            "size_mult": 0.0,
            "vol_mult": 9.9,
            "disabled": True,
        }
    if brain_mode in {"MILD_BEAR", "CAUTIOUS_BEAR", "DEFENSIVE", "HALT"}:
        return {
            "tp_pct": 0.060,
            "sl_pct": 0.030,
            "max_hold": 5,
            "size_mult": 0.0,
            "vol_mult": 9.9,
            "disabled": True,
        }
    _table = {
        "AGGRESSIVE":   (1.0, 1.2),
        "MODERATE_BULL":(0.8, 1.3),
        "MILD_BULL":    (0.7, 1.4),
        "CAUTIOUS_BULL":(0.55,1.5),
        "NEUTRAL":      (0.5, 1.5),
    }
    size, vol_mult = _table.get(brain_mode, _table["NEUTRAL"])

    conf_adj = (0.6 - max(0.4, min(0.9, conf))) * 1.0
    vol_mult = round(max(1.0, vol_mult + conf_adj), 2)

    return {
        "tp_pct":    0.060,
        "sl_pct":    0.030,
        "max_hold":  5,
        "size_mult": size,
        "vol_mult":  vol_mult,
    }
