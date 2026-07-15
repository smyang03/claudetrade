from __future__ import annotations

from runtime.us_swing_order_bridge import _operator_micro_override


class Bot:
    def __init__(self, ack: str) -> None:
        self.ack = ack

    def _runtime_value(self, key: str, default=""):
        return self.ack if key == "US_SWING_OPERATOR_MICRO_OVERRIDE_ACK" else default


def test_override_accepts_only_forward_maturity_blockers() -> None:
    authority = {"blockers": ["forward_matured_insufficient", "forward_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result["allowed_to_emit_orders"] is True
    assert result["max_open_slots"] == 1
    assert result["size_multiplier"] == 0.10


def test_override_preserves_non_forward_block() -> None:
    authority = {"blockers": ["historical_mean_below_hurdle"]}
    result = _operator_micro_override(Bot("I_ACCEPT_MICRO_WITHOUT_FORWARD"), authority, "micro")
    assert result is authority
