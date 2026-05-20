from __future__ import annotations

import json
from unittest.mock import patch

from dashboard import dashboard_server


def test_summary_min_order_uses_mode_specific_us_krw_override() -> None:
    def fake_env(_mode: str, key: str):
        return {"US_MIN_ORDER_KRW": "50000", "US_MIN_ORDER_USD": "30"}.get(key)

    with patch.object(dashboard_server, "_get_env_raw", side_effect=fake_env):
        value = dashboard_server._summary_min_order_krw("US", {}, 1506.38, mode="live")

    assert value == 50000.0


def test_summary_min_order_falls_back_to_usd_when_krw_override_missing() -> None:
    def fake_env(_mode: str, key: str):
        return {"US_MIN_ORDER_USD": "30"}.get(key)

    with patch.object(dashboard_server, "_get_env_raw", side_effect=fake_env):
        value = dashboard_server._summary_min_order_krw("US", {}, 1506.38, mode="live")

    assert round(value, 2) == 45191.4


def test_summary_min_order_prefers_live_runtime_value() -> None:
    with patch.object(dashboard_server, "_get_env_raw", return_value="50000"):
        value = dashboard_server._summary_min_order_krw(
            "US",
            {"min_effective_order_krw": 70000},
            1506.38,
            mode="live",
        )

    assert value == 70000.0


def test_update_start_config_order_size_writes_us_order_keys(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    config_path.write_text(
        json.dumps(
            {
                "US_FIXED_ORDER_KRW": 300000,
                "env_overrides": {
                    "US_FIXED_ORDER_KRW": "300000",
                    "PATHB_FIXED_ORDER_KRW": "300000",
                },
            }
        ),
        encoding="utf-8",
    )

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_order_size("US", 650000)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["restart_required"] is True
    assert result["updated_keys"] == ["MAX_ORDER_KRW", "US_FIXED_ORDER_KRW", "PATHB_FIXED_ORDER_KRW"]
    assert data["US_FIXED_ORDER_KRW"] == 650000
    assert data["env_overrides"]["MAX_ORDER_KRW"] == "650000"
    assert data["env_overrides"]["US_FIXED_ORDER_KRW"] == "650000"
    assert data["env_overrides"]["PATHB_FIXED_ORDER_KRW"] == "650000"
