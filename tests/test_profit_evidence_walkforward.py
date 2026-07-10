from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from tools.profit_evidence_walkforward import (
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    walk_forward_market,
)


def _synthetic_frame() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    for day_idx in range(9):
        date = (start + timedelta(days=day_idx)).date().isoformat()
        for row_idx in range(70):
            signal = rng.normal()
            row = {
                "market": "US",
                "session_date": date,
                "known_at": f"{date}T14:30:00+00:00",
                "created_at": f"{date}T14:30:00+00:00",
                "ticker": f"T{row_idx:03d}",
                "return_pct": 1.5 * signal + rng.normal(scale=0.5),
                "max_runup_pct": abs(signal) + 1.0,
                "max_drawdown_pct": -abs(rng.normal()),
            }
            for idx, feature in enumerate(NUMERIC_FEATURES):
                row[feature] = signal if idx == 1 else rng.normal()
            for feature in CATEGORICAL_FEATURES:
                row[feature] = "A" if row_idx % 2 else "B"
            rows.append(row)
    return pd.DataFrame(rows)


def test_walk_forward_has_disjoint_chronological_calibration_validation_and_purge() -> None:
    result = walk_forward_market(
        _synthetic_frame(),
        market="US",
        min_train_dates=3,
        calibration_dates=2,
        validation_dates=2,
        purge_dates=1,
        min_selected_validation=1,
        seed=11,
        classifier_kind="logistic",
    )
    assert result["tested_windows"] == 1
    window = result["windows"][0]
    train_end = window["train_dates"][1]
    calibration_start, calibration_end = window["calibration_dates"]
    validation_start, validation_end = window["validation_dates"]
    purge_date = window["purged_dates"][0]
    test_date = window["test_date"]
    assert train_end < calibration_start <= calibration_end < validation_start <= validation_end < purge_date < test_date
    assert window["train_n"] == 210
    assert window["calibration_n"] == 140
    assert window["validation_n"] == 140
    assert window["test_n"] == 70
