from __future__ import annotations

import pandas as pd

from tools.us_regime_lead_lag_review import attach_strict_prior, portfolio_metrics


def test_attach_strict_prior_never_uses_same_day_or_business_day_guess() -> None:
    targets = pd.DataFrame({"date": pd.to_datetime(["2026-06-22"])})
    context = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-06-18", "2026-06-22"]),
            "value": [18.0, 22.0],
        }
    )
    joined = attach_strict_prior(targets, context)
    assert joined.iloc[0]["value"] == 18.0


def test_portfolio_metrics_reports_tail_and_concentration() -> None:
    metrics = portfolio_metrics(pd.Series([10.0, 2.0, 1.0, -1.0, -2.0]))
    assert metrics["sessions"] == 5
    assert metrics["profit_factor"] == 13.0 / 3.0
    assert metrics["mean_ex_top3_days_pct"] == -1.5
