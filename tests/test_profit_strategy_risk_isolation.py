from __future__ import annotations

from minority_report.postmortem import _effective_strategy_label, _format_trade_log, _strategy_pnl
from risk_manager import ISOLATED_STRATEGY_SOURCES, RiskManager
from trading_bot import TradingBot


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


def test_trading_bot_generic_advisor_recognizes_all_isolated_exit_owners() -> None:
    for source in ISOLATED_STRATEGY_SOURCES:
        assert TradingBot._isolated_strategy_exit_owner(_position(source, 100.0)) == source
    assert TradingBot._isolated_strategy_exit_owner(_position("momentum", 100.0)) == ""


def test_generic_exit_flags_are_cleared_only_for_isolated_sleeves() -> None:
    bot = object.__new__(TradingBot)
    bot.risk = RiskManager()
    isolated = {
        **_position("us_schg_bil_trend_v1", 100.0),
        "market": "US",
        "pending_next_open_sell": True,
        "pending_next_open_reason": "generic_advisor",
        "pending_intraday_recheck": True,
    }
    ordinary = {
        **_position("momentum", 100.0),
        "ticker": "OTHER",
        "market": "US",
        "pending_next_open_sell": True,
    }
    bot.risk.positions = [isolated, ordinary]
    bot._ticker_market = lambda ticker: "US"
    bot._save_positions = lambda: None

    assert bot._clear_isolated_strategy_generic_exit_flags("US") == 1
    assert isolated["exit_owner"] == "us_schg_bil_trend_v1"
    assert isolated["exit_policy"] == "isolated_strategy"
    assert "pending_next_open_sell" not in isolated
    assert "pending_intraday_recheck" not in isolated
    assert ordinary["pending_next_open_sell"] is True


def test_postmortem_preserves_real_isolated_strategy_and_exit_contract() -> None:
    trade = {
        "side": "sell",
        "ticker": "SCHG",
        "qty": 1,
        "price": 51762.0,
        "strategy": "MICRO_PROBE",
        "source_strategy": "us_schg_bil_trend_v1",
        "reason": "intraday_review_sell",
        "pnl_pct": -0.45,
        "pnl": -233.0,
    }
    assert _effective_strategy_label(trade) == "us_schg_bil_trend_v1"
    assert _strategy_pnl([trade]) == {"us_schg_bil_trend_v1": [-0.45]}
    prompt = _format_trade_log([trade], "US")
    assert "source_strategy:us_schg_bil_trend_v1" in prompt
    assert "exit_reason:intraday_review_sell" in prompt
    assert "contract:isolated_exit_owner_only" in prompt


def test_post_session_review_does_not_report_isolated_carry_as_no_position(monkeypatch) -> None:
    bot = object.__new__(TradingBot)
    alerts: list[tuple] = []
    monkeypatch.setattr("trading_bot.block_alert", lambda *args, **kwargs: alerts.append((args, kwargs)))

    bot._post_session_position_review(
        "US",
        [{**_position("us_schg_bil_trend_v1", 100.0), "market": "US"}],
    )

    assert alerts == []
