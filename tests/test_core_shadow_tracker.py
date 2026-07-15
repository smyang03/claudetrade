from __future__ import annotations
import pandas as pd
import pytest

from tools.core_shadow_tracker import build_targets, update_book
from tools.index_trend_strategy_lab import download_panel as _unused  # import contract smoke
from tools.integrated_core_strategy_lab import ALL_SYMBOLS


def _panels() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index = pd.date_range("2023-01-01", periods=42, freq="MS")
    us = pd.DataFrame(
        {"SCHG": range(100, 142), "BIL": [100 + i * 0.1 for i in range(42)], "KRW=X": [1400] * 42},
        index=index,
    )
    integrated = pd.DataFrame(
        {symbol: [100 * (1.01 ** i) for i in range(42)] for symbol in ALL_SYMBOLS},
        index=index,
    )
    bench = pd.DataFrame(
        {
            "SPY": range(100, 142), "QQQ": range(100, 184, 2), "KRW=X": [1400] * 42,
            "069500.KS": range(100, 142), "229200.KS": range(100, 184, 2),
        },
        index=index,
    )
    return us, integrated, bench


def test_targets_have_two_primary_arms_and_benchmarks() -> None:
    us, integrated, bench = _panels()
    payload = build_targets(us_panel=us, integrated_panel=integrated, index_panel=bench, as_of="2026-07-15")

    primary = [arm for arm in payload["arms"] if arm["role"] == "primary"]
    assert {arm["strategy_id"] for arm in primary} == {"US_SCHG_BIL_TREND_V1", "KR_FACTOR_TREND_V1"}
    assert all(sum(arm["weights"].values()) + arm["cash_weight"] == pytest.approx(1.0) for arm in primary)
    assert payload["authority"] == "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT"


def test_book_marks_to_market_and_charges_switch_cost() -> None:
    targets = {
        "effective_month": "2026-07",
        "arms": [{"strategy_id": "US", "market": "US", "role": "primary", "weights": {"SCHG": 1.0}, "cash_weight": 0.0}],
    }
    book, first = update_book({}, targets, {"SCHG": 10.0}, usd_krw=1400.0, price_date="2026-07-14")
    book, second = update_book(book, targets, {"SCHG": 11.0}, usd_krw=1400.0, price_date="2026-07-15")

    assert first[0]["cost_return"] == pytest.approx(0.0025)
    assert second[0]["gross_return"] == pytest.approx(0.10)
    assert second[0]["turnover"] == pytest.approx(0.0)
    assert book["arms"]["US"]["nav"] > 1.09
