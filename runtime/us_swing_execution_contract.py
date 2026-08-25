"""US swing 실행 계약 단일 소스.

shadow 원장(`tools/us_swing_shadow_runner.py`)과 실주문 경로
(`runtime/us_swing_order_bridge.py`)가 **같은** 예산·슬롯·소스 화이트리스트를 읽게 한다.

2026-08-04 실측 사고 기록 — 두 원장이 서로 다른 계약으로 돌고 있었다.
  - shadow: 50,000원(500,000 x micro 0.1) / 슬롯 1
  - 실주문: **1,000,000원**(US_SWING_ORDER_MAX_KRW 절대캡) / 슬롯 3 / 일 1건
    금액 이력: 30만(~08-13) -> 50만(08-14) -> 100만(08-17, 운영자 결정).
    최악 동시 SL(-25%) = 상한 x 슬롯3 x 0.25 = **75만원**. 한도 정본은
    docs/reports/preregistered_falsification_criteria_20260804.md.
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
import os
from typing import Any, Mapping

# 보유기간 기본값 (2026-08-25 운영자 결정 D5→D7).
# ⚠️ 이 값은 **실주문 브리지와 같은 env 키**를 읽어야 한다. 08-21 이전에 슬롯·일한도가
# 실주문과 shadow 두 군데에 따로 하드코딩돼 계약이 갈라졌던 것과 같은 계열이다.
# max_hold_sessions는 contract_id 재료라, 갈라지면 지문이 서로 다른 코호트가 된다.
_DEFAULT_MAX_HOLD_SESSIONS = 5


def default_max_hold_sessions() -> int:
    """US swing 보유 세션 수 — env 단일 소스."""
    try:
        return max(1, int(float(os.getenv("US_SWING_MAX_HOLD_SESSIONS", _DEFAULT_MAX_HOLD_SESSIONS))))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_HOLD_SESSIONS


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
# 2026-08-20 개정: 슬롯 3 -> 5 (주문상한 100만 -> 76만 동반).
#   근거: D5 보유 x 일1건이면 정상상태 동시보유가 5개다. 슬롯 3은 진입률을
#   3/5 = 0.6건/일로 강제로 깎는다(실측 08-03~08-19: 13거래일 7건 = 0.54건/일로 일치).
#   자본 383만 고정에서 3x100만(투입 300만)보다 5x76만(투입 380만)이
#   투입 +27%, 표본 +85%로 우월하다. TP/SL이 퍼센트 계약이라 크기 축소의 대가가 없다.
#   검증 국면(8/30) 목표는 수익 극대화가 아니라 게이트 도달이다.
OPERATOR_TRIAL_MAX_OPEN_SLOTS = 5
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
    min_probability: float = 0.55,
    min_predicted_net_pct: float = 0.25,
    hurdles_enforced: bool = False,
    max_open_slots_override: int = OPERATOR_TRIAL_MAX_OPEN_SLOTS,
    max_new_per_day_override: int = OPERATOR_TRIAL_MAX_NEW_PER_DAY,
    selection_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """실주문과 shadow가 공유하는 계약을 계산한다.

    예산 규칙은 `runtime/us_swing_order_bridge._write_execution_status`와 동일하다.
      절대캡이 있으면  budget = min(configured_max, absolute_cap)
      없으면            budget = min(configured_max, base_budget x size_multiplier)

    2026-08-07 확장(정산 0건 상태의 사전 수정): 절대 허들(min_probability /
    min_predicted_net_pct)도 계약의 일부다. 실주문 핸드오프는 이 값으로 차단하는데
    shadow가 무시하면 판정 코호트가 실주문과 갈라진다(ULS·LCID 실측 사고 —
    live 차단·shadow 편입). 기본값은 order bridge의 env 기본값과 동일해야 한다.

    2026-08-23 확장 (Codex 리뷰 P1-3): **후보 선별 정책(거래대금 밴드·MAX 하한)도
    계약의 일부다.** 08-20부터 실주문은 밴드/MAX로 재선별한 종목을 사는데 shadow는
    여전히 원 rank1을 평가해, 같은 날 다른 종목을 재는 상태였다(08-20 shadow=VOYG /
    live=MXL). 선별 정책이 바뀌면 거래 집합이 바뀌므로 지문도 바뀌어야 한다 —
    선별이 다른 구간을 한 평균에 섞지 않기 위해서다.
    `selection_policy`를 넘기지 않으면 payload·지문 모두 이전과 동일하다(하위호환).
    """

    mode = str(effective_mode or "shadow").lower()
    caps = policy.get("authority_caps") if isinstance(policy.get("authority_caps"), Mapping) else {}
    mode_caps = caps.get(mode) if isinstance(caps.get(mode), Mapping) else {}
    size_multiplier = _number(mode_caps.get("size_multiplier"), 0.0)
    max_open_slots = int(_number(mode_caps.get("max_open_slots"), 0))
    max_new_per_day = int(_number(mode_caps.get("max_new_per_day"), 0))

    if override_active:
        # 오버라이드는 micro 계약을 운영자 결정 슬롯/일한도로 확장한다.
        # 2026-08-21: 호출부가 env 값을 넘길 수 있게 파라미터로 승격했다. 기본값은
        # 기존 상수 그대로라 인자를 안 넘기면 동작이 동일하다.
        # os.getenv를 여기서 읽지 않는 이유: 이 함수는 실주문·shadow·오프라인 시뮬이
        # 공유하는 **순수 함수**다. 내부에서 env를 읽으면 경로마다 다른 값을 볼 수 있고
        # (ULS·LCID 코호트 분기 사고 유형) 테스트가 env에 오염된다.
        max_open_slots = int(max_open_slots_override)
        max_new_per_day = int(max_new_per_day_override)

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
        "max_hold_sessions": int(_number(contract.get("max_hold_sessions"), default_max_hold_sessions())),
        "min_probability": _number(min_probability, 0.55),
        "min_predicted_net_pct": _number(min_predicted_net_pct, 0.25),
        "hurdles_enforced": bool(hurdles_enforced),
        "operator_override_active": bool(override_active),
    }
    if selection_policy:
        payload["selection_policy"] = dict(selection_policy)
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
        # 2026-08-07: 허들도 거래 집합을 정의하므로 지문에 포함한다.
        # 값이 바뀌면 코호트를 섞지 않는다 — id가 바뀌는 것이 의도다.
        "min_probability": round(_number(payload.get("min_probability"), 0.55), 6),
        "min_predicted_net_pct": round(_number(payload.get("min_predicted_net_pct"), 0.25), 6),
        "hurdles_enforced": bool(payload.get("hurdles_enforced")),
    }
    # 2026-08-23: 선별 정책(밴드·MAX)이 있으면 지문에 포함한다. 없으면 키 자체를 넣지
    # 않는다 — 기존 지문(afc07db8·feb33565 등)이 그대로 재현돼야 이력이 안 끊긴다.
    selection = payload.get("selection_policy")
    if selection:
        material["selection_policy"] = json.loads(
            json.dumps(selection, sort_keys=True, ensure_ascii=False, default=str)
        )
    blob = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
