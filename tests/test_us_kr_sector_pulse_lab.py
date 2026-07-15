from __future__ import annotations

import pandas as pd

from tools.us_kr_sector_pulse_lab import next_after, top_sector_signals


def frame(values: list[float]) -> pd.DataFrame:
    index = pd.to_datetime(["2026-07-01", "2026-07-02", "2026-07-03"])
    return pd.DataFrame({"Open": values, "Close": values}, index=index)


def test_next_after_is_strict() -> None:
    index = pd.to_datetime(["2026-07-01", "2026-07-02"])
    assert next_after(index, pd.Timestamp("2026-07-01")) == pd.Timestamp("2026-07-02")


def test_top_sector_signal_selects_largest_known_close_return(monkeypatch) -> None:
    from tools import us_kr_sector_pulse_lab as lab

    monkeypatch.setattr(lab, "PAIRS", {"A": "TA", "B": "TB"})
    data = {
        "A": frame([100.0, 102.0, 102.0]),
        "B": frame([100.0, 103.0, 103.0]),
    }
    signals = top_sector_signals(data, 1.0)
    assert len(signals) == 1
    assert signals[0][1:3] == ("B", "TB")
