from __future__ import annotations

from dataclasses import dataclass
import os

from config.v2 import DEFAULT_V2_CONFIG, V2Config


VALID_MARKETS = {"KR", "US"}
VALID_RUNTIME_MODES = {"live", "paper"}


@dataclass(frozen=True)
class RiskRuntimeProfile:
    market: str
    runtime_mode: str
    currency: str
    fixed_order_native: float
    fixed_order_krw: float
    min_order_native: float
    min_order_krw: float
    max_positions: int
    daily_loss_limit_pct: float
    stale_market_data_minutes: int
    partial_fill_ttl_sec: int
    new_entry_cutoff_minutes_before_close: int
    time_stop_minutes: int
    time_stop_min_progress_pct: float
    time_stop_min_mfe_pct: float
    usd_krw: float = 0.0
    session_force_close_enabled: bool = False


def build_risk_profile(
    market: str,
    runtime_mode: str,
    *,
    config: V2Config = DEFAULT_V2_CONFIG,
    usd_krw: float | None = None,
) -> RiskRuntimeProfile:
    market = str(market or "").upper()
    runtime_mode = str(runtime_mode or "").lower()
    if market not in VALID_MARKETS:
        raise ValueError(f"unsupported market: {market}")
    if runtime_mode not in VALID_RUNTIME_MODES:
        raise ValueError(f"unsupported runtime_mode: {runtime_mode}")

    if market == "KR":
        return RiskRuntimeProfile(
            market=market,
            runtime_mode=runtime_mode,
            currency="KRW",
            fixed_order_native=float(config.kr_fixed_order_krw),
            fixed_order_krw=float(config.kr_fixed_order_krw),
            min_order_native=float(config.kr_min_order_krw),
            min_order_krw=float(config.kr_min_order_krw),
            max_positions=int(config.kr_max_positions),
            daily_loss_limit_pct=float(config.daily_loss_limit_pct),
            stale_market_data_minutes=int(config.stale_market_data_minutes),
            partial_fill_ttl_sec=int(config.kr_partial_fill_ttl_sec),
            new_entry_cutoff_minutes_before_close=int(config.new_entry_cutoff_minutes_before_close),
            time_stop_minutes=int(config.kr_time_stop_minutes),
            time_stop_min_progress_pct=float(config.time_stop_min_progress_pct),
            time_stop_min_mfe_pct=float(config.time_stop_min_mfe_pct),
        )

    rate = _resolve_usd_krw(usd_krw)
    configured_fixed_order_krw = float(getattr(config, "us_fixed_order_krw", 0) or 0)
    configured_min_order_krw = float(getattr(config, "us_min_order_krw", 0) or 0)
    fixed_order_krw = (
        configured_fixed_order_krw
        if configured_fixed_order_krw > 0
        else float(config.us_fixed_order_usd) * rate
    )
    min_order_krw = (
        configured_min_order_krw
        if configured_min_order_krw > 0
        else float(config.us_min_order_usd) * rate
    )
    return RiskRuntimeProfile(
        market=market,
        runtime_mode=runtime_mode,
        currency="USD",
        fixed_order_native=fixed_order_krw / rate,
        fixed_order_krw=fixed_order_krw,
        min_order_native=min_order_krw / rate,
        min_order_krw=min_order_krw,
        max_positions=int(config.us_max_positions),
        daily_loss_limit_pct=float(config.daily_loss_limit_pct),
        stale_market_data_minutes=int(config.stale_market_data_minutes),
        partial_fill_ttl_sec=int(config.us_partial_fill_ttl_sec),
        new_entry_cutoff_minutes_before_close=int(config.new_entry_cutoff_minutes_before_close),
        time_stop_minutes=int(config.us_time_stop_minutes),
        time_stop_min_progress_pct=float(config.time_stop_min_progress_pct),
        time_stop_min_mfe_pct=float(config.time_stop_min_mfe_pct),
        usd_krw=rate,
    )


def _resolve_usd_krw(usd_krw: float | None) -> float:
    if usd_krw is not None and float(usd_krw) > 0:
        return float(usd_krw)
    raw = os.getenv("USD_KRW_RATE", "1400")
    try:
        rate = float(raw)
    except (TypeError, ValueError):
        rate = 1400.0
    if rate <= 0:
        raise ValueError("USD/KRW rate must be positive for US risk profile")
    return rate
