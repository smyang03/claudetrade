from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from interface.v2_telegram import _pathb_status


def test_pathb_status_exposes_us_entry_and_strategy_policy() -> None:
    status = {
        "enabled": True,
        "operator_enabled": True,
        "emergency_disabled": False,
        "mode": "min_size_live",
        "runtime_mode": "live",
        "fixed_order_krw": 300_000,
        "fixed_order_krw_by_market": {"KR": 300_000, "US": 300_000},
        "max_positions": 15,
        "max_daily_entries": 40,
        "min_confidence": 0.5,
        "entry_quality_policy": {
            "US": {
                "zone_fill_mode": "enforce_wait",
                "top_threshold": 0.67,
                "reward_threshold_pct": 5.0,
            }
        },
        "us_live_strategy_policy": {
            "momentum_enabled": True,
            "gap_pullback_enabled": False,
        },
    }
    bot = SimpleNamespace(
        current_market="US",
        _mode="live",
        pathb=SimpleNamespace(status=lambda: status),
    )

    with patch(
        "interface.v2_telegram.build_v2_ops_summary",
        return_value={"path_b_live": {}, "broker_truth": {}},
    ):
        text = _pathb_status(bot)

    assert "US 진입가: enforce_wait" in text
    assert "momentum 켜짐" in text
    assert "gap-pullback 꺼짐" in text
