from __future__ import annotations

from dataclasses import dataclass
import os


SAFETY_REASON_CODES: tuple[str, ...] = (
    "INSUFFICIENT_CASH",
    "ALREADY_HOLDING",
    "PENDING_ORDER_EXISTS",
    "MAX_POSITIONS",
    "MAX_DAILY_ENTRIES",
    "DAILY_LOSS_LIMIT",
    "BROKER_UNTRUSTED",
    "INVALID_PRICE",
    "MARKET_CLOSED",
    "ENTRY_BLACKOUT",
    "MIN_ORDER_NOT_MET",
    "STALE_MARKET_DATA",
    "SAME_DAY_REENTRY_AFTER_STOP",
    "ORDER_UNKNOWN_UNRESOLVED",
    "PATHB_ORDER_UNKNOWN_SAME_TICKER",
    "PATHB_ORDER_IN_PROGRESS",
    "PATHB_SELL_IN_PROGRESS",
    "SAME_DAY_REENTRY_COOLDOWN",
    "STOP_CLUSTER_FIRST_STOP_COOLDOWN",
    "STOP_CLUSTER_MARKET_BLOCK",
    "STOP_CLUSTER_DISASTER_BLOCK",
    "PATH_DUPLICATE_HOLDING",
    "CLAUDE_PRICE_INVALID",
    "PATHB_DISABLED",
    "PATHB_MANUALLY_DISABLED",
    "PATHB_EMERGENCY_DISABLED",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_CONFIDENCE_TOO_LOW",
    "PATHB_ORDER_UNKNOWN_HALTED",
    "ZONE_EDGE_NO_VALID_LIMIT",
)

CLOSE_REASON_CODES: tuple[str, ...] = (
    "CLOSED_HARD_STOP",
    "CLOSED_TRAILING_STOP",
    "CLOSED_TIME_STOP",
    "CLOSED_CLAUDE_SELL",
    "CLOSED_USER_MANUAL",
    "CLOSED_SESSION_FORCE",
    "CLOSED_MARKET_HALT",
    "CLOSED_PANIC",
    "CLOSED_BROKER_SYNC",
    "CLOSED_CLAUDE_PRICE_TARGET",
    "CLOSED_CLAUDE_PRICE_STOP",
    "CLOSED_CLAUDE_PRICE_TIME",
    "CLOSED_CLAUDE_PRICE_PRE_CLOSE",
)


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return int(raw)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return float(raw)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class V2Config:
    kr_fixed_order_krw: int = 100_000
    us_fixed_order_usd: int = 50
    us_fixed_order_krw: int = 0
    kr_min_order_krw: int = 50_000
    us_min_order_usd: int = 30
    us_min_order_krw: int = 0
    kr_max_positions: int = 3
    us_max_positions: int = 3
    daily_loss_limit_pct: float = -2.0
    stale_market_data_minutes: int = 5
    kr_partial_fill_ttl_sec: int = 120
    us_partial_fill_ttl_sec: int = 180
    momentum_timing_ttl_minutes: int = 45
    pullback_timing_ttl_minutes: int = 120
    new_entry_cutoff_minutes_before_close: int = 10
    kr_time_stop_minutes: int = 90
    us_time_stop_minutes: int = 120
    time_stop_min_progress_pct: float = 0.3
    time_stop_min_mfe_pct: float = 0.5
    hold_review_interval_minutes: int = 60
    midsession_index_move_trigger_pct: float = 1.0
    midsession_candidate_change_ratio: float = 0.5
    pathb_mode: str = "min_size_live"
    pathb_enabled: bool = True
    pathb_telegram_control_enabled: bool = True
    pathb_fixed_order_krw: int = 100_000
    pathb_max_positions: int = 1
    pathb_max_daily_entries: int = 1
    pathb_min_confidence: float = 0.5
    pathb_intraday_only: bool = True
    pathb_allow_stop_loss_lowering: bool = False
    pathb_allow_same_ticker_with_patha: bool = False
    pathb_order_unknown_halts_entry: bool = True
    pathb_kr_slippage_cap: float = 1.003
    pathb_us_slippage_cap: float = 1.002
    pathb_sell_partial_wait_sec: int = 10
    pathb_sell_pending_ttl_minutes: int = 15
    pathb_sell_partial_ttl_minutes: int = 30
    pathb_pre_close_market_fallback: bool = True
    pathb_pre_close_timeout_minutes: int = 5
    pathb_emergency_disable: bool = False
    kr_reentry_cooldown_minutes: int = 120
    us_reentry_cooldown_minutes: int = 90
    kr_profit_reentry_cooldown_minutes: int = 60
    us_profit_reentry_cooldown_minutes: int = 45
    prompt_version: str = "v2"

    @classmethod
    def from_env(cls) -> "V2Config":
        return cls(
            kr_fixed_order_krw=_int_env("KR_FIXED_ORDER_KRW", cls.kr_fixed_order_krw),
            us_fixed_order_usd=_int_env("US_FIXED_ORDER_USD", cls.us_fixed_order_usd),
            us_fixed_order_krw=_int_env("US_FIXED_ORDER_KRW", cls.us_fixed_order_krw),
            kr_min_order_krw=_int_env("KR_MIN_ORDER_KRW", cls.kr_min_order_krw),
            us_min_order_usd=_int_env("US_MIN_ORDER_USD", cls.us_min_order_usd),
            us_min_order_krw=_int_env("US_MIN_ORDER_KRW", cls.us_min_order_krw),
            kr_max_positions=_int_env("KR_MAX_POSITIONS", cls.kr_max_positions),
            us_max_positions=_int_env("US_MAX_POSITIONS", cls.us_max_positions),
            daily_loss_limit_pct=_float_env("DAILY_LOSS_LIMIT_PCT", cls.daily_loss_limit_pct),
            stale_market_data_minutes=_int_env("STALE_MARKET_DATA_MINUTES", cls.stale_market_data_minutes),
            kr_partial_fill_ttl_sec=_int_env("KR_PARTIAL_FILL_TTL_SEC", cls.kr_partial_fill_ttl_sec),
            us_partial_fill_ttl_sec=_int_env("US_PARTIAL_FILL_TTL_SEC", cls.us_partial_fill_ttl_sec),
            momentum_timing_ttl_minutes=_int_env(
                "MOMENTUM_TIMING_TTL_MINUTES", cls.momentum_timing_ttl_minutes
            ),
            pullback_timing_ttl_minutes=_int_env(
                "PULLBACK_TIMING_TTL_MINUTES", cls.pullback_timing_ttl_minutes
            ),
            new_entry_cutoff_minutes_before_close=_int_env(
                "NEW_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE",
                cls.new_entry_cutoff_minutes_before_close,
            ),
            kr_time_stop_minutes=_int_env("KR_TIME_STOP_MINUTES", cls.kr_time_stop_minutes),
            us_time_stop_minutes=_int_env("US_TIME_STOP_MINUTES", cls.us_time_stop_minutes),
            time_stop_min_progress_pct=_float_env(
                "TIME_STOP_MIN_PROGRESS_PCT", cls.time_stop_min_progress_pct
            ),
            time_stop_min_mfe_pct=_float_env("TIME_STOP_MIN_MFE_PCT", cls.time_stop_min_mfe_pct),
            hold_review_interval_minutes=_int_env(
                "HOLD_REVIEW_INTERVAL_MINUTES", cls.hold_review_interval_minutes
            ),
            midsession_index_move_trigger_pct=_float_env(
                "MIDSESSION_INDEX_MOVE_TRIGGER_PCT", cls.midsession_index_move_trigger_pct
            ),
            midsession_candidate_change_ratio=_float_env(
                "MIDSESSION_CANDIDATE_CHANGE_RATIO", cls.midsession_candidate_change_ratio
            ),
            pathb_mode=os.getenv("PATHB_MODE", cls.pathb_mode),
            pathb_enabled=_bool_env("PATHB_ENABLED", cls.pathb_enabled),
            pathb_telegram_control_enabled=_bool_env(
                "PATHB_TELEGRAM_CONTROL_ENABLED", cls.pathb_telegram_control_enabled
            ),
            pathb_fixed_order_krw=_int_env("PATHB_FIXED_ORDER_KRW", cls.pathb_fixed_order_krw),
            pathb_max_positions=_int_env("PATHB_MAX_POSITIONS", cls.pathb_max_positions),
            pathb_max_daily_entries=_int_env("PATHB_MAX_DAILY_ENTRIES", cls.pathb_max_daily_entries),
            pathb_min_confidence=_float_env("PATHB_MIN_CONFIDENCE", cls.pathb_min_confidence),
            pathb_intraday_only=_bool_env("PATHB_INTRADAY_ONLY", cls.pathb_intraday_only),
            pathb_allow_stop_loss_lowering=_bool_env(
                "PATHB_ALLOW_STOP_LOSS_LOWERING", cls.pathb_allow_stop_loss_lowering
            ),
            pathb_allow_same_ticker_with_patha=_bool_env(
                "PATHB_ALLOW_SAME_TICKER_WITH_PATHA", cls.pathb_allow_same_ticker_with_patha
            ),
            pathb_order_unknown_halts_entry=_bool_env(
                "PATHB_ORDER_UNKNOWN_HALTS_ENTRY", cls.pathb_order_unknown_halts_entry
            ),
            pathb_kr_slippage_cap=_float_env("PATHB_KR_SLIPPAGE_CAP", cls.pathb_kr_slippage_cap),
            pathb_us_slippage_cap=_float_env("PATHB_US_SLIPPAGE_CAP", cls.pathb_us_slippage_cap),
            pathb_sell_partial_wait_sec=_int_env(
                "PATHB_SELL_PARTIAL_WAIT_SEC", cls.pathb_sell_partial_wait_sec
            ),
            pathb_sell_pending_ttl_minutes=_int_env(
                "PATHB_SELL_PENDING_TTL_MINUTES", cls.pathb_sell_pending_ttl_minutes
            ),
            pathb_sell_partial_ttl_minutes=_int_env(
                "PATHB_SELL_PARTIAL_TTL_MINUTES", cls.pathb_sell_partial_ttl_minutes
            ),
            pathb_pre_close_market_fallback=_bool_env(
                "PATHB_PRE_CLOSE_MARKET_FALLBACK", cls.pathb_pre_close_market_fallback
            ),
            pathb_pre_close_timeout_minutes=_int_env(
                "PATHB_PRE_CLOSE_TIMEOUT_MINUTES", cls.pathb_pre_close_timeout_minutes
            ),
            pathb_emergency_disable=_bool_env("PATHB_EMERGENCY_DISABLE", cls.pathb_emergency_disable),
            kr_reentry_cooldown_minutes=_int_env(
                "KR_REENTRY_COOLDOWN_MINUTES", cls.kr_reentry_cooldown_minutes
            ),
            us_reentry_cooldown_minutes=_int_env(
                "US_REENTRY_COOLDOWN_MINUTES", cls.us_reentry_cooldown_minutes
            ),
            kr_profit_reentry_cooldown_minutes=_int_env(
                "KR_PROFIT_REENTRY_COOLDOWN_MINUTES", cls.kr_profit_reentry_cooldown_minutes
            ),
            us_profit_reentry_cooldown_minutes=_int_env(
                "US_PROFIT_REENTRY_COOLDOWN_MINUTES", cls.us_profit_reentry_cooldown_minutes
            ),
            prompt_version=os.getenv("PROMPT_VERSION", cls.prompt_version),
        )


DEFAULT_V2_CONFIG = V2Config.from_env()
