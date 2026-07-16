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


def test_override_accepts_only_forward_maturity_blockers() -> None:
    authority = {"blockers": ["forward_matured_insufficient", "forward_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result["allowed_to_emit_orders"] is True
    assert result["max_open_slots"] == 1
    assert result["size_multiplier"] == 0.10
    assert result["absolute_order_cap_krw"] == 300_000.0
    assert result["order_cap_source"] == "operator_config_absolute"


def test_override_preserves_non_forward_block() -> None:
    authority = {"blockers": ["historical_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result is authority
