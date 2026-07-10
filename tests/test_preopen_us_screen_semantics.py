from __future__ import annotations

from unittest.mock import patch

from preopen.scorer import score_us_candidate
from tools.preopen_collector import _collect_us_screen_candidates


def test_regular_us_screen_row_is_not_labeled_as_premarket_quote() -> None:
    rows = [
        {
            "ticker": "RIVN",
            "name": "Rivian",
            "category": "day_gainers",
            "price": 18.12,
            "change_rate": 8.76,
            "volume": 83_000_000,
            "vol_ratio": 1.0,
        }
    ]
    with patch("kis_api.screen_market_us", return_value=rows):
        candidate = _collect_us_screen_candidates(
            "2026-07-10T17:30:00+09:00",
            "2026-07-10",
            top_n=60,
            mode="NEUTRAL",
        )[0]
    assert candidate["change_rate"] == 8.76
    assert candidate["prior_day_traded_value"] == 18.12 * 83_000_000
    assert candidate["gap_pct"] is None
    assert candidate["extended_change_pct"] is None
    assert candidate["extended_dollar_volume"] is None
    assert candidate["volume_ratio"] is None
    assert "premarket_quote_unavailable" in candidate["risk_tags"]

    scored = score_us_candidate(candidate)
    assert "premarket_strength" not in scored["preopen_reason"]
    assert "dollar_volume_quality" not in scored["preopen_reason"]
    assert scored["preopen_score"] == 0.0
