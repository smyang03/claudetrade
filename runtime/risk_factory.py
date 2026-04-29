from __future__ import annotations

from dataclasses import asdict

from runtime.risk_profile import RiskRuntimeProfile, build_risk_profile


def create_risk_manager(profile: RiskRuntimeProfile, *, init_cash_krw: float):
    if init_cash_krw <= 0:
        raise ValueError("init_cash_krw must be positive")
    if profile.fixed_order_krw <= 0:
        raise ValueError("profile.fixed_order_krw must be positive")

    from risk_manager import RiskManager

    manager = RiskManager(
        init_cash=float(init_cash_krw),
        max_order_krw=float(profile.fixed_order_krw),
        market=profile.market,
    )
    manager.v2_risk_profile = asdict(profile)
    return manager


def create_risk_manager_for(
    market: str,
    runtime_mode: str,
    *,
    init_cash_krw: float,
    usd_krw: float | None = None,
):
    profile = build_risk_profile(market, runtime_mode, usd_krw=usd_krw)
    return create_risk_manager(profile, init_cash_krw=init_cash_krw)
