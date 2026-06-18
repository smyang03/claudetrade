from __future__ import annotations

"""통합 꼬리-capture 청산 엔진 (shadow-first).

설계근거 memory §21~22: 시스템=멀티데이 꼬리-수확기. net=상위10% 꼬리(오버나잇). 꼬리의 24%(+67%p)를
샌다. path-aware 시뮬: "증명 후(MFE>=4%) wide-trail"이 +33%p(=오버나잇 +56.7 − 당일더드 −23.7).

엔진 = 메커니즘(어떻게 청산/캐리). 파라미터는 향후 교훈시스템이 forward-검증·국면조건부로 튜닝(훅만).
**기본 OFF. shadow면 결정 산출+로깅, 실주문 무접촉. enforce는 검증 후 토글.**

안전계약:
- 하드스톱은 항상(하방 bound). 엔진은 *이익쪽 trailing + 마감 캐리*만 추가.
- 오버나잇 캐리는 RISK_ON에서만(약세장 베타증폭 차단) + 강도게이트(더드 회피) + 기본 no-carry.
- regime None/미가용/저신뢰 → 보수적(no-carry, close).
"""

import os
from typing import Any


# ---- config (기본 OFF/보수적) ----

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def mode() -> str:
    """off | shadow | enforce. 기본 off."""
    m = str(os.getenv("TAIL_CAPTURE_MODE", "off")).strip().lower()
    return m if m in ("off", "shadow", "enforce") else "off"


def is_active() -> bool:
    return mode() in ("shadow", "enforce")


def _activation_pct() -> float:
    return _env_float("TAIL_CAPTURE_ACTIVATION_PCT", 4.0)


def _give_pct(market: str) -> float:
    """trail giveback. US 꼬리有=wide / KR 꼬리無=tight."""
    if str(market or "").upper() == "KR":
        return _env_float("TAIL_CAPTURE_GIVE_KR", 1.5)
    return _env_float("TAIL_CAPTURE_GIVE_US", 3.0)


def _hard_stop_pct() -> float:
    return _env_float("TAIL_CAPTURE_HARD_STOP_PCT", 2.0)


def _carry_enabled(market: str) -> bool:
    """오버나잇 캐리 *결정* 시장 토글(shadow 로깅용). 기본 US만(꼬리有)."""
    if str(market or "").upper() == "KR":
        return _env_bool("TAIL_CAPTURE_CARRY_KR", False)
    return _env_bool("TAIL_CAPTURE_CARRY_US", True)


def carry_enforce_enabled() -> bool:
    """오버나잇 캐리 *실행* 서브게이트. enforce여도 기본 false(최고위험: cross-day 갭·기존
    session-carry 충돌·broker 재동기화 미검증). shadow는 항상 CARRY 로깅 → forward 재구성으로
    무위험 검증 후 운영자가 켠다. trail EXIT은 이 게이트와 무관(하방 위임이라 안전)."""
    return _env_bool("TAIL_CAPTURE_CARRY_ENFORCE", False)


def _carry_strength_pct() -> float:
    """마감 시 net이 이 이상이면 '강함'=캐리 후보(더드 회피 게이트)."""
    return _env_float("TAIL_CAPTURE_CARRY_STRENGTH_PCT", 3.0)


def _carry_regimes() -> set[str]:
    """오버나잇 캐리 허용 국면(약세장 베타증폭 차단)."""
    raw = os.getenv("TAIL_CAPTURE_CARRY_REGIMES", "risk_on")
    return {x.strip().lower() for x in str(raw).split(",") if x.strip()}


def _preclose_window_min() -> int:
    return _env_int("TAIL_CAPTURE_PRECLOSE_WINDOW_MIN", 15)


# ---- 결정 엔진 (순수) ----

def evaluate_exit(
    *,
    market: str,
    entry: float,
    peak: float,
    current: float,
    age_min: float | None = None,
    mins_to_close: float | None = None,
    regime: str | None = None,
    activation_pct: float | None = None,
    give_pct: float | None = None,
    hard_stop_pct: float | None = None,
) -> dict[str, Any]:
    """포지션 1개의 꼬리-capture 결정. 순수(IO 없음).

    action: HOLD / EXIT(reason) / CARRY(오버나잇 보유) / CLOSE(마감 청산)
    """
    if entry <= 0 or current <= 0:
        return {"action": "HOLD", "reason": "invalid_price"}
    activation_pct = _activation_pct() if activation_pct is None else activation_pct
    give_pct = _give_pct(market) if give_pct is None else give_pct
    hard_stop_pct = _hard_stop_pct() if hard_stop_pct is None else hard_stop_pct

    peak = max(peak or entry, entry)
    net_pct = (current / entry - 1) * 100
    mfe_pct = (peak / entry - 1) * 100

    # ① 하드스톱 (하방 bound, 항상)
    hard_lv = entry * (1 - hard_stop_pct / 100)
    if current <= hard_lv:
        return {"action": "EXIT", "reason": "hard_stop", "net_pct": round(net_pct, 3),
                "mfe_pct": round(mfe_pct, 3)}

    # ② trailing (MFE가 activation 넘은 뒤만 = 증명 후)
    active = mfe_pct >= activation_pct
    trail_lv = peak * (1 - give_pct / 100)
    if active and current <= trail_lv:
        return {"action": "EXIT", "reason": "tail_trail", "net_pct": round(net_pct, 3),
                "mfe_pct": round(mfe_pct, 3), "trail_pct": round((trail_lv / entry - 1) * 100, 3)}

    # ③ 마감 결정: 강하면 캐리(오버나잇), 약하면 청산 (더드 회피)
    in_preclose = mins_to_close is not None and 0 <= mins_to_close <= _preclose_window_min()
    if in_preclose:
        regime_ok = (regime or "").strip().lower() in _carry_regimes()
        strong = net_pct >= _carry_strength_pct()
        if _carry_enabled(market) and regime_ok and strong:
            return {"action": "CARRY", "reason": "strong_overnight_carry", "net_pct": round(net_pct, 3),
                    "mfe_pct": round(mfe_pct, 3)}
        return {"action": "CLOSE", "reason": "preclose_not_strong", "net_pct": round(net_pct, 3),
                "mfe_pct": round(mfe_pct, 3)}

    return {"action": "HOLD", "reason": "trailing_active" if active else "pre_activation",
            "net_pct": round(net_pct, 3), "mfe_pct": round(mfe_pct, 3), "active": active}


def should_carry_overnight(pos: dict[str, Any], current: float, market: str,
                           regime: str | None = None, entry_native: float | None = None) -> bool:
    """마감(pre_close)에서 이 포지션을 오버나잇 캐리할지. enforce+서브게이트(CARRY_ENFORCE)일 때만 True.

    기준: 이익중 + MFE(peak)≥activation(증명) + net≥carry_strength(강함) + RISK_ON(베타증폭 차단)
    + 시장 carry 토글. 하나라도 불충족/불확실하면 False(보수 — 기본 청산).

    entry/current는 같은 통화여야 한다. US 포지션 pos["entry"]는 원화 저장이므로 호출측이
    네이티브(달러) entry를 entry_native로 넘긴다. 미제공이면 pos에서 추출(KR 등 동일통화 한정).
    """
    if mode() != "enforce" or not carry_enforce_enabled():
        return False
    if not _carry_enabled(market):
        return False
    if (regime or "").strip().lower() not in _carry_regimes():
        return False
    try:
        entry = float(entry_native) if entry_native and float(entry_native) > 0 \
            else float(pos.get("entry") or pos.get("entry_price") or 0)
        if entry <= 0 or current <= 0:
            return False
        net_pct = (current / entry - 1) * 100
        mfe = pos.get("observed_mfe_pct")
        mfe_pct = float(mfe) if mfe is not None else net_pct
        return net_pct >= _carry_strength_pct() and mfe_pct >= _activation_pct()
    except Exception:
        return False


def shadow_decision(pos: dict[str, Any], current: float, market: str,
                    regime: str | None = None, mins_to_close: float | None = None,
                    entry_native: float | None = None) -> dict[str, Any] | None:
    """exit scan 훅용 — pos에서 entry/peak 추출해 결정 산출(로깅용). 비활성/불가면 None.

    enforce여도 *호출측이 실행 책임* — 이 함수는 결정만 반환(실주문 안 함).

    entry/current는 같은 통화여야 한다. US 포지션 pos["entry"]는 원화 저장이라 그대로 쓰면
    달러 current와 단위가 엇갈려 net이 -99%로 깨진다. 호출측이 _position_entry_native로 변환한
    네이티브 entry를 entry_native로 넘긴다. 미제공이면 pos에서 추출(KR 등 동일통화 한정).
    """
    if not is_active():
        return None
    try:
        entry = float(entry_native) if entry_native and float(entry_native) > 0 \
            else float(pos.get("entry") or pos.get("entry_price") or 0)
        if entry <= 0 or current <= 0:
            return None
        # observed_mfe_pct(Phase 1c)로 peak 복원. 없으면 현재가로 보수적.
        mfe = pos.get("observed_mfe_pct")
        peak = entry * (1 + float(mfe) / 100) if mfe is not None else max(entry, current)
        peak = max(peak, current)
        dec = evaluate_exit(market=market, entry=entry, peak=peak, current=current,
                            regime=regime, mins_to_close=mins_to_close)
        dec["ticker"] = pos.get("ticker")
        dec["mode"] = mode()
        return dec
    except Exception:
        return None
