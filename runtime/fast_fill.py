from __future__ import annotations

"""PathB 진입 fast-fill(bounded 재호가) 결정 엔진 (shadow-first).

문제(2026-06-18 093370 실측): 봇이 눌림 limit(17900)을 던진 뒤, 가격이 limit 위로
튀어도 cancel 임계(zone_high×multiplier ≈ 18795) 아래면 체결도 취소도 안 하고 "데드존"에서
방치 → 깨끗한 진입을 놓침. 운영자가 수동으로 bound 없이 추격 → target 위 과지불로 손실.

fast-fill = 그 데드존에서 **bound 안에서만** 재호가해 빠르게 잡고, bound 밖이면 깨끗이 미스 인정.
운영자 수동 추격과의 결정적 차이 = **하드 bound**(target−여유 위로는 절대 안 산다).

안전계약:
- 기본 OFF가 아니라 shadow(관측)지만 enforce 전까지 실주문 무접촉(호출측이 실행 책임).
- 재호가 상한 = min(limit×(1+max_chase%), target×(1−min_reward%)). 둘 중 낮은 값.
- 현재가가 상한 위로 이미 튀었으면 재호가 안 함(MISS) — 추격 손실 차단.
- target/limit 비정상이면 보수적(None).
"""

import os
from typing import Any


def _env(name: str, market: str) -> str | None:
    mk = str(market or "").upper()
    v = os.getenv(f"{mk}_{name}")
    if v is not None:
        return v
    return os.getenv(name)


def mode(market: str) -> str:
    """off | shadow | enforce. 시장별(US_/KR_) 우선, 없으면 글로벌. 기본 shadow."""
    raw = _env("PATHB_FAST_FILL_MODE", market)
    m = str(raw if raw is not None else "shadow").strip().lower()
    return m if m in ("off", "shadow", "enforce") else "shadow"


def is_active(market: str) -> bool:
    return mode(market) in ("shadow", "enforce")


def _float_env(name: str, market: str, default: float) -> float:
    raw = _env(name, market)
    try:
        return float(str(raw).strip()) if raw is not None else default
    except (TypeError, ValueError):
        return default


def _max_chase_pct(market: str) -> float:
    """원래 limit 대비 최대 추격 폭(%). 기본 1.0%."""
    return _float_env("PATHB_FAST_FILL_MAX_CHASE_PCT", market, 1.0)


def _min_reward_pct(market: str) -> float:
    """재호가 후에도 target까지 최소 남겨야 하는 보상(%). 기본 1.5%."""
    return _float_env("PATHB_FAST_FILL_MIN_REWARD_PCT", market, 1.5)


def requote_decision(
    *,
    market: str,
    limit_price: float,
    current: float,
    target: float,
    cancel_threshold: float | None = None,
    max_chase_pct: float | None = None,
    min_reward_pct: float | None = None,
) -> dict[str, Any] | None:
    """데드존(미체결 + 가격이 limit 위)에서 bounded 재호가 결정. 순수(IO 없음).

    action:
      - REQUOTE: 재호가가(=현재가)로 즉시 체결 시도(bound 안). requote_price 포함.
      - MISS: 현재가가 bound 위로 튐 → 추격 안 하고 미스 인정.
    비활성/부적합/정상체결권이면 None(호출측은 기존 로직 유지).
    """
    if not is_active(market):
        return None
    try:
        limit_price = float(limit_price)
        current = float(current)
        target = float(target)
    except (TypeError, ValueError):
        return None
    if limit_price <= 0 or current <= 0 or target <= 0:
        return None
    # 현재가가 limit 이하면 정상 체결권 — fast-fill 무관
    if current <= limit_price:
        return None
    # target 위/근처는 진입 의미 없음
    if current >= target:
        return {"action": "MISS", "reason": "current_at_or_above_target",
                "current": round(current, 4), "limit": round(limit_price, 4),
                "target": round(target, 4)}

    max_chase_pct = _max_chase_pct(market) if max_chase_pct is None else max_chase_pct
    min_reward_pct = _min_reward_pct(market) if min_reward_pct is None else min_reward_pct

    chase_ceiling = limit_price * (1.0 + max_chase_pct / 100.0)
    reward_ceiling = target * (1.0 - min_reward_pct / 100.0)
    requote_cap = min(chase_ceiling, reward_ceiling)
    if cancel_threshold and cancel_threshold > 0:
        # cancel 임계 위는 어차피 취소 영역 — 그 아래로만 재호가
        requote_cap = min(requote_cap, float(cancel_threshold))

    base = {
        "market": str(market or "").upper(),
        "limit": round(limit_price, 4),
        "current": round(current, 4),
        "target": round(target, 4),
        "requote_cap": round(requote_cap, 4),
        "max_chase_pct": max_chase_pct,
        "min_reward_pct": min_reward_pct,
        "mode": mode(market),
    }
    if requote_cap <= limit_price:
        # bound이 limit 이하 = 재호가 여지 없음(보상 너무 얇음)
        return {**base, "action": "MISS", "reason": "no_room_under_reward_floor"}
    if current > requote_cap:
        return {**base, "action": "MISS", "reason": "current_above_bound"}
    # 현재가가 bound 안 → 현재가로 재호가하면 즉시 체결 + 보상 유지
    requote_price = current
    remaining_reward_pct = (target / requote_price - 1.0) * 100.0
    return {**base, "action": "REQUOTE", "reason": "bounded_requote",
            "requote_price": round(requote_price, 4),
            "remaining_reward_pct": round(remaining_reward_pct, 3)}
