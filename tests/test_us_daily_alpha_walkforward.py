from __future__ import annotations

import pandas as pd

from tools.us_daily_alpha_walkforward import expanding_month_splits


def test_expanding_month_split_purges_before_test() -> None:
    dates = pd.bdate_range("2025-01-01", periods=170).strftime("%Y-%m-%d")
    frame = pd.DataFrame({"session_date": dates})
    frame["month"] = frame["session_date"].str[:7]
    splits = expanding_month_splits(frame, min_train_sessions=120, purge_sessions=5)
    assert splits
    train, purge, test = splits[0]
    assert len(purge) == 5
    assert max(train) < min(purge) < min(test)
    assert not set(train) & set(test)
