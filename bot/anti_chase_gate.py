"""극단 급등 추격 배제 게이트 (anti-chase) — 네거티브 스크린.

창의적 발견(2026-07-19, docs/reports/alpha_hunt_and_design_20260719.md,
memory creative-strategy-extreme-spike-exclusion-20260719): spike-chase는 비단조 —
중간 급등(MAX 8~20%)은 정상/우수(8~12구간 avg +0.235%), **극단(MAX≥20%)만 반전·독성**
(20~30 avg −1.078%, 30+ −1.040%, LOSS_CAP 지배). 외부(yfinance 스파이크>12% 5일 −1.62%
vs 무조건부 +0.36%)+우리 net 검증. max_daily_ret_21d는 진입 전 일봉 기준이라 lookahead 없음.

설계:
- off(no-op)/shadow(would_skip 관측만)/enforce(배제). 기본 off.
- fail-open: max 값이 없으면 막지 않는다(데이터 결손으로 후보 죽이지 않음).
- 네거티브 스크린(배제) — 랭킹 재조정 아님(6/27 리랭킹 금지 존중).
"""
from __future__ import annotations

from typing import Any

VALID_MODES = ("off", "shadow", "enforce")
DEFAULT_THRESHOLD = 20.0


def normalize_mode(value: Any) -> str:
    text = str(value or "off").strip().lower()
    return text if text in VALID_MODES else "off"


def evaluate_anti_chase(
    max_daily_ret_21d: Any,
    mode: Any,
    *,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """극단 급등 추격 판정.

    decision: off | allow | allow_no_data | would_skip | skip
    block(bool): 실제 배제해야 하는가(enforce + MAX≥threshold일 때만 True).
    """
    mode = normalize_mode(mode)
    try:
        thr = float(threshold)
    except (TypeError, ValueError):
        thr = DEFAULT_THRESHOLD
    out: dict[str, Any] = {
        "mode": mode,
        "max_daily_ret_21d": None,
        "threshold": thr,
        "block": False,
    }
    if mode == "off":
        out["decision"] = "off"
        out["reason"] = "gate_off"
        return out
    mx = _to_float(max_daily_ret_21d)
    out["max_daily_ret_21d"] = mx
    if mx is None:
        # fail-open: MAX 미상이면 막지 않는다
        out["decision"] = "allow_no_data"
        out["reason"] = "max_unavailable"
        return out
    if mx >= thr:
        out["reason"] = "extreme_spike_chase"
        if mode == "enforce":
            out["decision"] = "skip"
            out["block"] = True
        else:
            out["decision"] = "would_skip"
        return out
    out["decision"] = "allow"
    out["reason"] = "below_threshold"
    return out


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
