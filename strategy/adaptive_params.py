# -*- coding: utf-8 -*-
"""strategy/adaptive_params.py — 적응형 파라미터 레이어

레이어 순서:
  1. base   : strategy/*.params() 기본값
  2. regime : mode + VIX + USD/KRW 기반 보정
  3. perf   : decisions.db 최근 성과 반영
  4. guard  : 기본값 대비 이동 범위 제한

사용 예:
  from strategy.adaptive_params import adaptive_params
  p = adaptive_params("gap_pullback", "KR", mode="NEUTRAL", context={"vix": 22.5})
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT = Path(__file__).parent.parent
_DB   = _ROOT / "data" / "ml" / "decisions.db"

import strategy.gap_pullback        as _gap
import strategy.mean_reversion      as _mr
import strategy.momentum            as _mom
import strategy.opening_range_pullback as _orp
import strategy.volatility_breakout as _vb
import strategy.continuation        as _cont

_PARAMS_FN = {
    "opening_range_pullback": _orp.params,
    "gap_pullback":        _gap.params,
    "mean_reversion":      _mr.params,
    "momentum":            _mom.params,
    "volatility_breakout": _vb.params,
    "continuation":        _cont.params,
}

# 가드레일 — base 대비 허용 이동 범위
_GUARD = {
    "rsi_thr":  ("add",  5),          # base ± 5
    "bb_thr":   ("add",  5),          # base ± 5
    "vol_mult": ("mult", 0.8, 1.3),   # base × 0.8 ~ 1.3
    "gap_min":  ("mult", 0.8, 1.3),   # base × 0.8 ~ 1.3
}

# 로그에 출력할 핵심 파라미터 키
_LOG_KEYS = ("rsi_thr", "bb_thr", "vol_mult", "gap_min")


# ── 1. 성과 통계 조회 ──────────────────────────────────────────────────────────

def _query_perf(market: str, like: str, source_filter: str, days: int) -> tuple[int, int]:
    """decisions.db에서 (win_count, total_count) 조회."""
    try:
        with sqlite3.connect(str(_DB), timeout=5) as conn:
            rows = conn.execute(
                f"""
                SELECT forward_1d
                FROM decisions
                WHERE market = ?
                  AND strategy_used LIKE ?
                  AND decision = 'BUY_SIGNAL'
                  AND forward_1d IS NOT NULL
                  AND data_source {source_filter}
                  AND session_date >= date('now', '-{days} days')
                """,
                (market, like),
            ).fetchall()
        wins = sum(1 for (fwd,) in rows if fwd > 0)
        return wins, len(rows)
    except Exception:
        return 0, 0


def get_perf_stats(strategy: str, market: str, days: int = 30) -> dict:
    """최근 N일 전략 성과 조회.

    혼합 규칙:
      live >= 30       → live only
      10 <= live < 30  → live × 2 + backfill × 1 (가중 혼합)
      live < 10        → backfill only
      데이터 없음      → win_rate=None, n=0
    """
    if not _DB.exists():
        return {"win_rate": None, "n": 0, "source": "none"}

    like = f"%{strategy}%"
    live_wins, live_n = _query_perf(market, like, "= 'live'", days)

    if live_n >= 30:
        wr = live_wins / live_n * 100
        return {"win_rate": round(wr, 1), "n": live_n, "source": "live"}

    bf_wins, bf_n = _query_perf(market, like, "= 'backfill'", days * 3)

    if live_n < 10 and bf_n == 0:
        return {"win_rate": None, "n": 0, "source": "none"}

    if live_n < 10:
        if bf_n == 0:
            return {"win_rate": None, "n": 0, "source": "none"}
        wr = bf_wins / bf_n * 100
        return {"win_rate": round(wr, 1), "n": bf_n, "source": "backfill"}

    # live 10~29 → live × 2 + backfill × 1 가중 혼합
    weighted_wins = live_wins * 2 + bf_wins
    weighted_n    = live_n   * 2 + bf_n
    wr = weighted_wins / weighted_n * 100
    return {
        "win_rate": round(wr, 1),
        "n":        live_n + bf_n,
        "source":   "live+backfill",
    }


# ── 2. Regime Overlay ─────────────────────────────────────────────────────────

def _regime_overlay(p: dict, market: str, mode: str, context: dict) -> dict:
    """VIX, USD/KRW 기반 파라미터 보정."""
    p = dict(p)
    vix     = context.get("vix")
    usd_krw = context.get("usd_krw")

    if vix is not None:
        if vix > 35:
            if "vol_mult" in p: p["vol_mult"] = round(p["vol_mult"] * 1.2, 3)
            if "gap_min"  in p: p["gap_min"]  = round(p["gap_min"]  * 1.2, 4)
            if "rsi_thr"  in p: p["rsi_thr"]  = max(15, p["rsi_thr"] - 2)
            if "bb_thr"   in p: p["bb_thr"]   = max(8,  p["bb_thr"]  - 2)
        elif vix > 25:
            if "vol_mult" in p: p["vol_mult"] = round(p["vol_mult"] * 1.1, 3)
            if "gap_min"  in p: p["gap_min"]  = round(p["gap_min"]  * 1.1, 4)
            if "rsi_thr"  in p: p["rsi_thr"]  = max(15, p["rsi_thr"] - 1)

    # USD/KRW 급등은 KR 시장에만 반영
    if market.upper() == "KR" and usd_krw is not None and usd_krw > 1400:
        if "vol_mult" in p: p["vol_mult"] = round(p["vol_mult"] * 1.1, 3)
        if "gap_min"  in p: p["gap_min"]  = round(p["gap_min"]  * 1.1, 4)

    return p


# ── 3. Performance Overlay ────────────────────────────────────────────────────

def _perf_overlay(p: dict, perf: dict, strategy: str) -> dict:
    """최근 성과 기반 보정. WR이 낮으면 진입 필터 강화."""
    wr = perf.get("win_rate")
    n  = perf.get("n", 0)

    if wr is None or n < 5:
        return p

    p = dict(p)

    if wr < 35:
        if "vol_mult" in p: p["vol_mult"] = round(p["vol_mult"] * 1.15, 3)
        if "gap_min"  in p: p["gap_min"]  = round(p["gap_min"]  * 1.15, 4)
        if "rsi_thr"  in p: p["rsi_thr"]  = max(15, p["rsi_thr"] - 3)
        if "bb_thr"   in p: p["bb_thr"]   = max(8,  p["bb_thr"]  - 3)
    elif wr < 43:
        if "vol_mult" in p: p["vol_mult"] = round(p["vol_mult"] * 1.07, 3)
        if "gap_min"  in p: p["gap_min"]  = round(p["gap_min"]  * 1.07, 4)
        if "rsi_thr"  in p: p["rsi_thr"]  = max(15, p["rsi_thr"] - 1)
    elif wr > 60:
        p["size_hint"] = "up"

    return p


# ── 4. Guardrail ──────────────────────────────────────────────────────────────

def _guardrail(p: dict, base_p: dict) -> dict:
    """base 대비 이탈 방지. 각 파라미터를 허용 범위 내로 클리핑."""
    p = dict(p)

    for key, rule in _GUARD.items():
        if key not in p or key not in base_p:
            continue
        base_val = base_p[key]
        if base_val == 0:
            continue

        if rule[0] == "add":
            margin = rule[1]
            p[key] = max(base_val - margin, min(base_val + margin, p[key]))
        elif rule[0] == "mult":
            lo, hi = rule[1], rule[2]
            p[key] = round(max(base_val * lo, min(base_val * hi, p[key])), 4)

    return p


# ── 로그 유틸 ─────────────────────────────────────────────────────────────────

def _fmt_params(p: dict) -> str:
    """핵심 파라미터만 짧게 포맷."""
    parts = []
    for k in _LOG_KEYS:
        if k in p:
            parts.append(f"{k}={p[k]}")
    return " ".join(parts) if parts else str(p)


def _diff_params(base: dict, final: dict) -> str:
    """base → final 변경된 키만 표시."""
    changes = []
    for k in _LOG_KEYS:
        if k in base and k in final and base[k] != final[k]:
            changes.append(f"{k}: {base[k]} → {final[k]}")
    if final.get("size_hint"):
        changes.append(f"size_hint={final['size_hint']}")
    return ", ".join(changes) if changes else "변경 없음"


# ── 메인 진입점 ───────────────────────────────────────────────────────────────

def adaptive_params(
    strategy: str,
    market: str,
    mode: str = "NEUTRAL",
    conf: float = 0.6,
    context: Optional[dict] = None,
    _perf: Optional[dict] = None,
) -> dict:
    """전략 파라미터를 시장 상황에 맞게 동적으로 반환.

    Args:
        strategy : "gap_pullback" | "mean_reversion" | "momentum" | "volatility_breakout"
        market   : "KR" | "US"
        mode     : brain_mode 문자열
        conf     : 분석가 평균 confidence (0.0~1.0)
        context  : {"vix": float, "usd_krw": float}
        _perf    : 외부 주입용 perf_stats (None이면 DB 자동 조회)

    Returns:
        params dict — disabled=True이면 signal에서 즉시 차단됨
    """
    fn = _PARAMS_FN.get(strategy)
    if fn is None:
        return {}

    # 1. base params
    if strategy == "volatility_breakout":
        base_p = fn(mode, conf=conf, market=market)
    else:
        base_p = fn(mode, conf, market=market)

    if base_p.get("disabled"):
        log.debug("[adaptive] %s/%s mode=%s → disabled", strategy, market, mode)
        return base_p

    # 2. regime overlay
    ctx = context or {}
    p = _regime_overlay(dict(base_p), market, mode, ctx)

    # 3. performance overlay
    perf = _perf if _perf is not None else get_perf_stats(strategy, market)
    p = _perf_overlay(p, perf, strategy)

    # 4. guardrail
    p = _guardrail(p, base_p)

    # 로그: base / perf source / 최종 변경사항
    log.info(
        "[adaptive] %s/%s mode=%s conf=%.2f | "
        "base=[%s] | perf=WR%s%%(%s,n=%s) | final=[%s] | diff: %s",
        strategy, market, mode, conf,
        _fmt_params(base_p),
        perf.get("win_rate", "-"), perf.get("source", "-"), perf.get("n", 0),
        _fmt_params(p),
        _diff_params(base_p, p),
    )

    return p
