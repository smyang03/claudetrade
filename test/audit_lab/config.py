"""Configuration for isolated audit-lab modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PRICE_DIR = ROOT / "data" / "price"
RESULT_DIR = ROOT / "data" / "backtest_audit"
MARKET_DATA_DIR = ROOT / "data" / "market_data"
YFINANCE_DATA_DIR = MARKET_DATA_DIR / "yfinance"
MARKET_DATA_DB = MARKET_DATA_DIR / "market_data.sqlite"

STRATEGIES = ("mean_reversion", "gap_pullback", "momentum", "volatility_breakout")
MARKETS = ("KR", "US")
ENTRY_MODELS = ("next_open", "same_close", "gap_filter", "pullback_limit", "confirmation_next_open")

ENTRY_MODEL_DEFAULTS = {
    "gap_filter_max_gap_pct": 1.5,
    "pullback_limit_pct": -0.5,
}

# Phase-1 data trust boundary. Earlier data can still be analyzed, but reports
# must mark it as lower confidence.
TRUSTED_START_BY_MARKET = {
    "KR": "2015-01-01",
    "US": "2006-01-01",
}

DISABLED_COMBOS = {
    ("volatility_breakout", "KR"),
    ("volatility_breakout", "US"),
    ("momentum", "US"),
}

ANALYSIS_WINDOWS = {
    "stable_2018_2019": ("2018-01-01", "2019-12-31"),
    "official_2018": ("2018-01-01", ""),
    "post_covid_2020": ("2020-01-01", ""),
    "stress_2022": ("2022-01-01", ""),
    "max_available": ("", ""),
}


@dataclass(frozen=True)
class AuditConfig:
    market: str = "ALL"
    strategy: str = "ALL"
    start: str = ""
    end: str = ""
    cost_model: str = "realistic"
    entry_timing: str = "next_open"
    entry_model: str = "next_open"
    entry_day_exit_policy: str = "allow"
    regime_timing: str = "previous_close"
    ticker_limit: int = 0
    min_trades: int = 30
    output_dir: Path = RESULT_DIR
