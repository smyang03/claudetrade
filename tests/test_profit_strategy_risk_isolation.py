from __future__ import annotations

from risk_manager import RiskManager


def _position(source: str, current: float, *, tp_pct: float = 0.0) -> dict:
    return {
        "ticker": "TEST",
        "source_strategy": source,
        "entry": 100.0,
        "current_price": current,
        "qty": 1,
        "sl_pct": 0.25,
        "tp_pct": tp_pct,
    }


def test_core_is_not_closed_by_generic_exit_owner() -> None:
    risk = RiskManager()
    risk.positions = [_position("us_schg_bil_trend_v1", 50.0)]
    assert risk.get_exit_candidates() == []


def test_fixed_horizon_ignores_ordinary_drawdown_but_keeps_catastrophe_stop() -> None:
    risk = RiskManager()
    risk.positions = [_position("us_consensus_3d", 90.0)]
    assert risk.get_exit_candidates() == []
    risk.positions[0]["current_price"] = 74.0
    result = risk.get_exit_candidates()
    assert result[0]["reason"] == "strategy_catastrophe_stop"


def test_us_swing_keeps_predeclared_take_profit() -> None:
    risk = RiskManager()
    risk.positions = [_position("us_swing_5d", 113.0, tp_pct=0.12)]
    result = risk.get_exit_candidates()
    assert result[0]["reason"] == "strategy_fixed_take_profit"
