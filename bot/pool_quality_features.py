"""후보 풀 품질 관측 피처 — anti-chase MAX·spike·변동성·모멘텀 (shadow 관측 전용).

외부 검증(2026-07-19, docs/reports/alpha_hunt_and_design_20260719.md):
- anti-chase: 큰 단일일 급등(>8~12%) 직후 진입은 단기 반전(외부·우리 net 양쪽 확인).
  스파이크>8% 5일 −0.23%, >12% −1.62% vs 무조건부 +0.36%.
- realized_vol: 알파 아님이나 평균손실 축소(관측 보조).
- ret_1m: 월간 모멘텀 컨텍스트(단기 스파이크와 구분용).

전부 일봉 candles로 산출, 순수 관측(candidate_quality_score/랭킹 무영향), lookahead
없음(과거 캔들만 사용). KR/US 공용.
"""
from __future__ import annotations

import math
from typing import Any

POOL_QUALITY_FEATURE_KEYS: tuple[str, ...] = (
    "max_daily_ret_21d",
    "spike_chase_level",
    "realized_vol_21d",
    "ret_1m_pct",
    "pool_quality_source",
)


def compute_pool_quality_features(candles: Any, *, lookback: int = 21) -> dict[str, Any]:
    """일봉 candles에서 풀 품질 관측 피처를 산출한다.

    Returns dict(부분 가능): max_daily_ret_21d(최근 lookback일 최대 일간수익%),
    spike_chase_level(0/8/12 — 큰 급등 추격 위험 등급), realized_vol_21d(일간수익 표준편차%),
    ret_1m_pct(lookback일 총수익% = 모멘텀 컨텍스트), pool_quality_source.
    """
    closes = _closes(candles)
    out: dict[str, Any] = {"pool_quality_source": "pool_quality:v1"}
    if len(closes) < 3:
        return out
    window = closes[-(lookback + 1):]
    rets = [
        (window[i] / window[i - 1] - 1.0) * 100.0
        for i in range(1, len(window))
        if window[i - 1] > 0
    ]
    if not rets:
        return out

    max_daily = max(rets)
    out["max_daily_ret_21d"] = round(max_daily, 3)
    # dose-response 등급: 외부 검증상 >8%부터 단기 불리, >12% 강한 반전
    out["spike_chase_level"] = 12 if max_daily >= 12.0 else (8 if max_daily >= 8.0 else 0)

    mean = sum(rets) / len(rets)
    out["realized_vol_21d"] = round((sum((x - mean) ** 2 for x in rets) / len(rets)) ** 0.5, 3)

    if len(closes) > lookback and closes[-lookback - 1] > 0:
        out["ret_1m_pct"] = round((closes[-1] / closes[-lookback - 1] - 1.0) * 100.0, 3)
    return out


def _closes(candles: Any) -> list[float]:
    """candles(DataFrame 또는 list-of-dict)에서 종가 시퀀스를 관대하게 추출."""
    if candles is None:
        return []
    # pandas DataFrame (컬럼 대소문자 무관)
    try:
        import pandas as pd

        if isinstance(candles, pd.DataFrame):
            col_map = {str(c).lower(): c for c in candles.columns}
            key = col_map.get("close")
            if key is None:
                return []
            values: list[float] = []
            for raw in candles[key].tolist():
                f = _to_float(raw)
                if f is not None:
                    values.append(f)
            return values
    except Exception:
        pass
    # list / sequence of dict-like
    values = []
    try:
        for row in candles:
            raw = None
            if isinstance(row, dict):
                raw = row.get("close", row.get("Close"))
            if raw is not None:
                f = _to_float(raw)
                if f is not None:
                    values.append(f)
    except TypeError:
        return []
    return values


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        f = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f
