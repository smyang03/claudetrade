"""
indicators.py - 기술 지표 계산
RSI, MACD, 볼린저밴드, 이동평균, ATR, 거래량 지표
"""
import pandas as pd
import numpy as np

def calc_all(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d.columns = [c.lower() for c in d.columns]
    # 이동평균
    for n in [5,20,60,120]:
        d[f"ma{n}"] = d["close"].rolling(n).mean()
    d["vol_avg20"] = d["volume"].rolling(20).mean()
    d["vol_ratio"] = d["volume"] / d["vol_avg20"].replace(0,1)
    # RSI
    delta = d["close"].diff()
    gain  = delta.clip(lower=0).rolling(14).mean()
    loss  = (-delta.clip(upper=0)).rolling(14).mean()
    d["rsi"] = 100 - 100/(1 + gain/loss.replace(0,np.nan))
    # MACD
    ema12 = d["close"].ewm(span=12).mean()
    ema26 = d["close"].ewm(span=26).mean()
    d["macd"]        = ema12 - ema26
    d["macd_signal"] = d["macd"].ewm(span=9).mean()
    d["macd_hist"]   = d["macd"] - d["macd_signal"]
    # 볼린저밴드
    std = d["close"].rolling(20).std()
    d["bb_upper"] = d["ma20"] + 2*std
    d["bb_lower"] = d["ma20"] - 2*std
    d["bb_pct"]   = (d["close"]-d["bb_lower"])/(d["bb_upper"]-d["bb_lower"])*100
    # ATR
    hl  = d["high"]-d["low"]
    hpc = (d["high"]-d["close"].shift()).abs()
    lpc = (d["low"]-d["close"].shift()).abs()
    d["atr"] = pd.concat([hl,hpc,lpc],axis=1).max(axis=1).rolling(14).mean()
    # 52주 고저
    d["high52"] = d["high"].rolling(252).max()
    d["low52"]  = d["low"].rolling(252).min()
    denom52 = (d["high52"] - d["low52"]).replace(0, np.nan)   # high==low 구간은 NaN 처리
    d["pos52"]  = (d["close"] - d["low52"]) / denom52 * 100
    # 갭, 수익률
    d["gap_pct"]    = (d["open"]-d["close"].shift())/d["close"].shift()*100
    d["change_pct"] = d["close"].pct_change()*100
    # 신호 생성
    valid_ma = d["ma5"].notna() & d["ma20"].notna() & d["ma60"].notna()
    d["ma_align"] = np.where(
        ~valid_ma, "혼재",
        np.where((d["ma5"]>d["ma20"]) & (d["ma20"]>d["ma60"]), "정배열",
        np.where((d["ma5"]<d["ma20"]) & (d["ma20"]<d["ma60"]), "역배열", "혼재")))
    d["macd_cross"] = np.where(
        (d["macd"]>d["macd_signal"]) & (d["macd"].shift()<=d["macd_signal"].shift()),
        "골든크로스",
        np.where((d["macd"]<d["macd_signal"]) & (d["macd"].shift()>=d["macd_signal"].shift()),
        "데드크로스","없음"))
    d["high20"] = d["high"].rolling(20).max().shift(1)
    d["new_high20"] = d["close"] > d["high20"]
    return d.dropna(subset=["ma60"])

def rsi_signal(rsi: float) -> str:
    if rsi < 25:  return "강한과매도"
    if rsi < 35:  return "과매도"
    if rsi > 75:  return "강한과매수"
    if rsi > 65:  return "과매수"
    return "중립"

def bb_signal(bb_pct: float) -> str:
    if bb_pct < 5:   return "하단이탈"
    if bb_pct < 20:  return "하단"
    if bb_pct > 95:  return "상단이탈"
    if bb_pct > 80:  return "상단"
    return "중간"

def vol_signal(vol_ratio: float) -> str:
    if vol_ratio > 4:   return "폭증"
    if vol_ratio > 2:   return "급증"
    if vol_ratio > 1.5: return "증가"
    if vol_ratio < 0.5: return "급감"
    return "보통"

def get_row_summary(row: pd.Series) -> dict:
    return {
        "rsi":        round(float(row.get("rsi",50)),1),
        "rsi_signal": rsi_signal(float(row.get("rsi",50))),
        "macd":       "골든크로스" if float(row.get("macd",0))>float(row.get("macd_signal",0)) else "데드크로스",
        "bb_pct":     round(float(row.get("bb_pct",50)),1),
        "bb_signal":  bb_signal(float(row.get("bb_pct",50))),
        "vol_ratio":  round(float(row.get("vol_ratio",1)),2),
        "vol_signal": vol_signal(float(row.get("vol_ratio",1))),
        "ma_align":   str(row.get("ma_align","혼재")),
        "pos52":      round(float(row.get("pos52",50)),1),
        "atr":        round(float(row.get("atr",0)),0),
        "new_high20": bool(row.get("new_high20",False)),
    }
