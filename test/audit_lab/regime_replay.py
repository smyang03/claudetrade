"""Point-in-time replay regime classifier.

Backtests must not classify an old date using future information. The default
``previous_close`` timing maps each trading date to the regime that was known
after the previous completed candle.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import pandas as pd


def classify_regime(close: float, ma60: float) -> str:
    if close <= 0 or ma60 <= 0:
        return "NEUTRAL"
    ratio = close / ma60
    if ratio < 0.92:
        return "CAUTIOUS_BEAR"
    if ratio < 0.97:
        return "MILD_BEAR"
    if ratio > 1.10:
        return "AGGRESSIVE"
    if ratio > 1.05:
        return "MODERATE_BULL"
    if ratio > 1.02:
        return "MILD_BULL"
    return "NEUTRAL"


@dataclass(frozen=True)
class ReplayRegimeClassifier:
    mode_by_date: dict[pd.Timestamp, str]
    timing: str = "previous_close"

    @classmethod
    def from_price_frame(cls, df: pd.DataFrame, timing: str = "previous_close") -> "ReplayRegimeClassifier":
        if df is None or df.empty or "date" not in df.columns:
            return cls({}, timing=timing)
        frame = df.sort_values("date").drop_duplicates("date").reset_index(drop=True)
        modes: dict[pd.Timestamp, str] = {}
        prior_mode = "NEUTRAL"
        for _, row in frame.iterrows():
            date = pd.to_datetime(row["date"]).normalize()
            current_mode = classify_regime(float(row.get("close", 0) or 0), float(row.get("ma60", 0) or 0))
            if timing == "current_close":
                modes[date] = current_mode
            else:
                modes[date] = prior_mode
                prior_mode = current_mode
        return cls(modes, timing=timing)

    def mode_for(self, date: object) -> str:
        if not self.mode_by_date:
            return "NEUTRAL"
        target = pd.to_datetime(date).normalize()
        dates = sorted(self.mode_by_date.keys())
        idx = bisect_right(dates, target) - 1
        if idx < 0:
            return "NEUTRAL"
        return self.mode_by_date[dates[idx]]

    def to_rows(self) -> list[dict]:
        return [{"date": date.strftime("%Y-%m-%d"), "mode": mode, "timing": self.timing} for date, mode in sorted(self.mode_by_date.items())]
