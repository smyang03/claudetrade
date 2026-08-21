from __future__ import annotations

from runtime.us_swing_order_bridge import _operator_micro_override


class Bot:
    def __init__(self, ack: str, max_order_krw: float = 300_000.0) -> None:
        self.ack = ack
        self.max_order_krw = max_order_krw

    def _runtime_value(self, key: str, default=""):
        return self.ack if key == "US_SWING_OPERATOR_MICRO_OVERRIDE_ACK" else default

    def _runtime_float(self, key: str, default=0.0):
        return self.max_order_krw if key == "US_SWING_ORDER_MAX_KRW" else default

    def _runtime_int(self, key: str, default=0):
        # 2026-08-21 슬롯·일한도 env 승격. 스텁은 기본값(5슬롯/일1건)을 그대로 돌려줘
        # 이 테스트가 검사하는 override 동작이 승격 전과 동일함을 보장한다.
        return default


def test_override_accepts_only_forward_maturity_blockers() -> None:
    authority = {"blockers": ["forward_matured_insufficient", "forward_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result["allowed_to_emit_orders"] is True
    # 2026-08-02 운영자 결정: 슬롯 3/일1건 (일일 신규 리스크 불변, D5 보유 중첩만 최대 3)
    # 2026-08-20 개정(B안): 슬롯 3 -> 5. D5 보유 x 일1건의 정상상태 동시보유가 5개라
    # 슬롯 3이 진입률을 0.6건/일로 깎고 있었다(실측 0.54). 일1건은 불변.
    assert result["max_open_slots"] == 5
    assert result["max_new_per_day"] == 1
    assert result["size_multiplier"] == 0.10
    assert result["absolute_order_cap_krw"] == 300_000.0
    assert result["order_cap_source"] == "operator_config_absolute"


def test_override_preserves_non_forward_block() -> None:
    authority = {"blockers": ["historical_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result is authority
