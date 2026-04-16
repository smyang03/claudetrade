"""strategy/entry_priority.py - 진입 우선순위 점수

신호가 발생한 종목들 간의 우선순위를 결정한다.
"신호 통과"와 "진입 품질" 을 분리하는 것이 목적.

점수 설계 원칙:
  - 각 조건이 임계값을 얼마나 강하게 초과하는지 (signal margin)
  - 눌림 깊이 (from_high_pct) — 고점에서 많이 눌릴수록 좋은 진입점
  - 장기 추세 위 여부 (ma60)

Phase 1: 점수 계산 + 로그 기록만 (hard cutoff 없음)
Phase 2: 충분한 데이터 쌓인 후 cutoff 적용
"""
from __future__ import annotations


_INVERSE_TICKERS = {"114800", "SQQQ"}


def compute(
    strategy_name: str,
    sig_row: dict,
    params: dict,
    price_info: dict,
    elapsed_min: float = 999,
    ticker: str = "",
) -> tuple[float, dict]:
    """
    진입 우선순위 점수를 계산한다.

    Returns:
        (score, detail_dict)
        score: 높을수록 좋은 진입 품질
        detail_dict: 로그/ML DB 기록용 세부 내역
    """
    score   = 0.0
    detail: dict = {"strategy": strategy_name}

    # ── 전략별 signal margin ────────────────────────────────────────────────
    if strategy_name == "gap_pullback":
        gap      = abs(float(sig_row.get("gap_pct", 0) or 0)) / 100
        vol_avg  = float(sig_row.get("vol_avg20", 1) or 1)
        volume   = float(sig_row.get("volume", 0) or 0)
        vol_ratio = volume / vol_avg if vol_avg else 0

        opening_min = float(params.get("opening_window_min", 30))
        in_opening  = 0 < elapsed_min <= opening_min
        gap_min  = params.get("opening_gap_min", 0.030) if in_opening else params.get("gap_min", 0.018)
        vol_mult = params.get("opening_vol_mult", 0.20) if in_opening else params.get("vol_mult", 2.0)

        gap_margin = (gap - gap_min) / gap_min if gap_min > 0 else 0
        vol_margin = (vol_ratio - vol_mult) / vol_mult if vol_mult > 0 else 0

        strat_score = gap_margin * 0.6 + vol_margin * 0.4
        detail.update({
            "gap": round(gap * 100, 2),
            "gap_min": round(gap_min * 100, 2),
            "gap_margin": round(gap_margin, 3),
            "vol_ratio": round(vol_ratio, 2),
            "vol_mult": round(vol_mult, 2),
            "vol_margin": round(vol_margin, 3),
        })

    elif strategy_name == "mean_reversion":
        rsi     = float(sig_row.get("rsi", 50) or 50)
        rsi_thr = float(params.get("rsi_thr", 32))
        bb      = float(sig_row.get("bb_pct", 50) or 50)
        bb_thr  = float(params.get("bb_thr", 20))

        rsi_margin = (rsi_thr - rsi) / rsi_thr if rsi_thr > 0 else 0
        bb_margin  = (bb_thr - bb) / bb_thr if bb_thr > 0 else 0

        strat_score = rsi_margin * 0.5 + bb_margin * 0.5
        detail.update({
            "rsi": round(rsi, 1),
            "rsi_thr": rsi_thr,
            "rsi_margin": round(rsi_margin, 3),
            "bb_pct": round(bb, 1),
            "bb_thr": bb_thr,
            "bb_margin": round(bb_margin, 3),
        })

    elif strategy_name == "opening_range_pullback":
        # 신호 조건: 거래량 + 눌림 깊이 (OR_HIGH 대비 현재가 위치)
        vol_avg   = float(sig_row.get("vol_avg20", 1) or 1)
        volume    = float(sig_row.get("volume", 0) or 0)
        vol_ratio = volume / vol_avg if vol_avg else 0
        vol_mult  = float(params.get("vol_mult", 1.3))
        vol_margin = (vol_ratio - vol_mult) / vol_mult if vol_mult > 0 else 0

        # 눌림 깊이: OR_HIGH에서 얼마나 눌렸는지 (0~pullback_max_pct 범위 내 깊을수록 좋음)
        price     = float(price_info.get("price", 0) or 0)
        or_high   = float(params.get("or_high", 0.0) or 0.0)
        pb_max    = float(params.get("pullback_max_pct", 0.010) or 0.010)
        pullback_depth = ((or_high - price) / or_high) if or_high > 0 and price > 0 else 0.0
        pullback_score = min(pullback_depth / pb_max, 1.0) if pb_max > 0 else 0.0

        strat_score = max(0, vol_margin) * 0.7 + pullback_score * 0.3
        detail.update({
            "vol_ratio":      round(vol_ratio, 2),
            "vol_mult":       round(vol_mult, 2),
            "vol_margin":     round(vol_margin, 3),
            "pullback_depth": round(pullback_depth * 100, 3),
            "pullback_score": round(pullback_score, 3),
        })

    elif strategy_name == "momentum":
        vol_avg   = float(sig_row.get("vol_avg20", 1) or 1)
        volume    = float(sig_row.get("volume", 0) or 0)
        vol_ratio = volume / vol_avg if vol_avg else 0
        vol_mult  = float(params.get("vol_mult", 1.5))

        # 실제 신호 조건(MA/MACD/vol/신고가)과 일치하도록 거래량 마진만 사용
        # change_pct 기반 점수는 신호 조건에 없으므로 제거
        vol_margin = (vol_ratio - vol_mult) / vol_mult if vol_mult > 0 else 0

        strat_score = vol_margin
        detail.update({
            "vol_ratio":  round(vol_ratio, 2),
            "vol_margin": round(vol_margin, 3),
        })

    elif strategy_name == "volatility_breakout":
        vol_avg   = float(sig_row.get("vol_avg20", 1) or 1)
        volume    = float(sig_row.get("volume", 0) or 0)
        vol_ratio = volume / vol_avg if vol_avg else 0
        vol_mult  = float(params.get("vol_mult", 2.0))

        vol_margin  = (vol_ratio - vol_mult) / vol_mult if vol_mult > 0 else 0
        strat_score = vol_margin
        detail["vol_margin"] = round(vol_margin, 3)

    else:
        strat_score = 0.0

    # margin이 음수(임계 미달)면 0으로 클램프 — 통과했다는 사실만 활용
    strat_score = max(0.0, strat_score)
    score += strat_score
    detail["strat_score"] = round(strat_score, 3)

    # ── 공통 보정 1: from_high_pct ──────────────────────────────────────────
    # 일반 종목: 고점에서 많이 눌릴수록 +점수 (최대 +0.5) — 눌림 후 재상승 패턴
    # 인버스 ETF: 고점 근처일수록 +점수 — 시장 하락 지속 = 강한 신호
    price    = float(price_info.get("price", 0) or 0)
    day_high = float(price_info.get("high", 0) or 0)
    from_high_bonus = 0.0
    _is_inverse = ticker.upper() in _INVERSE_TICKERS
    if day_high > 0 and price > 0:
        from_high_pct = (price - day_high) / day_high * 100   # 음수(눌림) 또는 0
        if _is_inverse:
            # 인버스: 고점 근처(from_high ≈ 0%)일수록 최대 +0.5
            from_high_bonus = max(0.0, 1.0 - abs(from_high_pct) / 3.0) * 0.5
        else:
            # 일반: 고점에서 많이 눌릴수록 +점수, 최대 +0.5
            from_high_bonus = min(abs(from_high_pct) / 5.0, 1.0) * 0.5
        score += from_high_bonus
        detail["from_high_pct"]   = round(from_high_pct, 2)
        detail["from_high_bonus"] = round(from_high_bonus, 3)
        detail["is_inverse"]      = _is_inverse

    # ── 공통 보정 2: MA60 장기 추세 위 여부 ────────────────────────────────
    close = float(sig_row.get("close", 0) or 0)
    ma60  = float(sig_row.get("ma60",  0) or 0)
    ma60_bonus = 0.0
    if ma60 > 0 and close > ma60:
        ma60_bonus = 0.2
        score += ma60_bonus
    detail["ma60_ok"]    = (ma60 > 0 and close > ma60)
    detail["ma60_bonus"] = round(ma60_bonus, 3)

    detail["total_score"] = round(score, 4)
    return round(score, 4), detail
