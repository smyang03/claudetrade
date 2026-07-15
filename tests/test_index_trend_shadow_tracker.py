from __future__ import annotations

import pandas as pd

from tools.index_trend_shadow_tracker import build_signal_payload


def test_payload_keeps_only_shadow_ready_non_benchmark_arms() -> None:
    index = pd.date_range("2024-01-01", periods=30, freq="MS")
    panel = pd.DataFrame(
        {
            "SPY": range(100, 130),
            "QQQ": range(100, 160, 2),
            "KRW=X": [1400.0] * 30,
            "069500.KS": range(100, 130),
            "229200.KS": range(100, 160, 2),
        },
        index=index,
    )
    report = {
        "results": {
            "US_QQQ_SMA10_CASH": {"verdict": "SHADOW_READY"},
            "US_QQQ_BUY_HOLD": {"verdict": "BENCHMARK"},
        }
    }
    payload = build_signal_payload(
        panel,
        report,
        as_of="2026-07-15",
        report_sha256="r",
        price_sha256="p",
    )
    assert [arm["strategy"] for arm in payload["arms"]] == ["US_QQQ_SMA10_CASH"]
    assert payload["arms"][0]["weights"] == {"QQQ": 1.0}
    assert payload["authority"] == "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT"
