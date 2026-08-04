"""US swing 실행 계약 단일 소스.

shadow 원장(`tools/us_swing_shadow_runner.py`)과 실주문 경로
(`runtime/us_swing_order_bridge.py`)가 **같은** 예산·슬롯·소스 화이트리스트를 읽게 한다.

2026-08-04 실측 사고 기록 — 두 원장이 서로 다른 계약으로 돌고 있었다.
  - shadow: 50,000원(500,000 x micro 0.1) / 슬롯 1
  - 실주문: 300,000원(US_SWING_ORDER_MAX_KRW 절대캡) / 슬롯 3 / 일 1건
결과로 forward 표본이 실거래와 다른 집합이 됐다.
  - 07-24 WEX, 07-27 AXTI(rank1 day_losers, +20.90%)가
    `micro_budget_cannot_buy_one_share`로 배제 — 주당 $35 이상이 원천 제외되어
    **승자 쪽으로 편향된** 표본이 쌓였다.
  - 08-03 FRMI는 실주문이 체결(38@5.5225)됐는데 shadow는
    `slot_occupied_pending:2026-07-29`로 제외 — 실제로 산 건이 표본에서 빠졌다.
이 상태로 30건을 채우면 판정 자체가 무효이므로 계약을 한 곳에서 계산한다.

정책 파일(`config/us_swing_accelerated.json`)은 sealed historical evidence의
`policy_sha256`과 묶여 있어 수정하면 `historical_policy_hash_mismatch`가 난다.
따라서 운영자 오버라이드 계약은 정책 파일이 아니라 이 모듈에서 합성한다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CONTRACT_SCHEMA_VERSION = "us_swing_execution_contract_v1"

# 오버라이드 ACK는 order bridge와 같은 문자열을 쓴다(값이 다르면 계약이 갈라진다).
OPERATOR_MICRO_OVERRIDE_ACK = "I_ACCEPT_MICRO_WITHOUT_FORWARD"

# 오버라이드가 허용하는 blocker 집합(order bridge `_operator_micro_override`와 동일).
OVERRIDABLE_BLOCKERS = frozenset(
    {
        "forward_sessions_insufficient",
        "forward_matured_insufficient",
        "forward_mean_below_hurdle",
        "forward_profit_factor_below_hurdle",
    }
)

# 운영자 결정(2026-08-02): 슬롯 3 / 일 1건.
OPERATOR_TRIAL_MAX_OPEN_SLOTS = 3
OPERATOR_TRIAL_MAX_NEW_PER_DAY = 1


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else float(default)


def parse_allowed_sources(raw: Any) -> tuple[str, ...]:
    """`US_SWING_ALLOWED_SOURCES` 파싱. 빈값은 '전체 허용'(현행 기본)."""

    text = str(raw or "").strip()
    if not text:
        return ()
    parts = [part.strip().lower() for part in text.split(",") if part.strip()]
    return tuple(sorted(set(parts)))


def operator_override_active(*, ack: Any, blockers: Any) -> bool:
    """운영자 micro 오버라이드가 실제로 적용되는 상태인지."""

    if str(ack or "") != OPERATOR_MICRO_OVERRIDE_ACK:
        return False
    items = [str(item) for item in (blockers or [])]
    if not items:
        return False
    return all(item in OVERRIDABLE_BLOCKERS for item in items)


def resolve_execution_contract(
    *,
    policy: Mapping[str, Any],
    effective_mode: str,
    configured_max_order_krw: float,
    base_order_budget_krw: float,
    absolute_order_cap_krw: float = 0.0,
    allowed_sources_raw: Any = "",
    override_active: bool = False,
) -> dict[str, Any]:
    """실주문과 shadow가 공유하는 계약을 계산한다.

    예산 규칙은 `runtime/us_swing_order_bridge._write_execution_status`와 동일하다.
      절대캡이 있으면  budget = min(configured_max, absolute_cap)
      없으면            budget = min(configured_max, base_budget x size_multiplier)
    """

    mode = str(effective_mode or "shadow").lower()
    caps = policy.get("authority_caps") if isinstance(policy.get("authority_caps"), Mapping) else {}
    mode_caps = caps.get(mode) if isinstance(caps.get(mode), Mapping) else {}
    size_multiplier = _number(mode_caps.get("size_multiplier"), 0.0)
    max_open_slots = int(_number(mode_caps.get("max_open_slots"), 0))
    max_new_per_day = int(_number(mode_caps.get("max_new_per_day"), 0))

    if override_active:
        # 오버라이드는 micro 계약을 슬롯 3 / 일 1건으로 확장한다.
        max_open_slots = OPERATOR_TRIAL_MAX_OPEN_SLOTS
        max_new_per_day = OPERATOR_TRIAL_MAX_NEW_PER_DAY

    configured = _number(configured_max_order_krw, 0.0)
    absolute = _number(absolute_order_cap_krw, 0.0)
    if absolute > 0:
        budget_krw = min(configured, absolute) if configured > 0 else absolute
        budget_source = "operator_absolute_cap"
    else:
        derived = _number(base_order_budget_krw, 0.0) * size_multiplier
        budget_krw = min(configured, derived) if configured > 0 else derived
        budget_source = "risk_budget_multiplier"

    contract = policy.get("execution_contract") if isinstance(policy.get("execution_contract"), Mapping) else {}
    payload = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "effective_mode": mode,
        "budget_krw": float(budget_krw),
        "budget_source": budget_source,
        "size_multiplier": float(size_multiplier),
        "max_open_slots": int(max_open_slots),
        "max_new_per_day": int(max_new_per_day),
        "allowed_sources": list(parse_allowed_sources(allowed_sources_raw)),
        "take_profit_pct": _number(contract.get("take_profit_pct"), 0.12),
        "catastrophe_stop_pct": _number(contract.get("catastrophe_stop_pct"), 0.25),
        "max_hold_sessions": int(_number(contract.get("max_hold_sessions"), 5)),
        "operator_override_active": bool(override_active),
    }
    payload["contract_id"] = contract_id(payload)
    return payload


def contract_id(payload: Mapping[str, Any]) -> str:
    """계약 지문. 이 값이 바뀌면 forward 표본을 같은 평균에 섞지 않는다."""

    material = {
        "budget_krw": round(_number(payload.get("budget_krw")), 2),
        "max_open_slots": int(_number(payload.get("max_open_slots"))),
        "max_new_per_day": int(_number(payload.get("max_new_per_day"))),
        "allowed_sources": sorted(str(item).lower() for item in (payload.get("allowed_sources") or [])),
        "take_profit_pct": round(_number(payload.get("take_profit_pct")), 6),
        "catastrophe_stop_pct": round(_number(payload.get("catastrophe_stop_pct")), 6),
        "max_hold_sessions": int(_number(payload.get("max_hold_sessions"))),
    }
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
