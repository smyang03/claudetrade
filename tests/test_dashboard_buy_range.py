from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from dashboard import dashboard_server


@pytest.fixture(autouse=True)
def _clear_start_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("V2_START_CONFIG_PATH", raising=False)
    monkeypatch.delenv("V2_START_CONFIG_DISABLED", raising=False)


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


def test_summary_order_size_prefers_common_order() -> None:
    def fake_env(_mode: str, key: str):
        return {"MAX_ORDER_KRW": "500000", "US_FIXED_ORDER_KRW": "650000"}.get(key)

    with patch.object(dashboard_server, "_get_env_raw", side_effect=fake_env):
        value = dashboard_server._summary_order_size_setting_krw("US", "live", fallback=300000)

    assert value == 500000.0


def test_summary_gross_cap_setting_prefers_market_manual() -> None:
    def fake_env(_mode: str, key: str):
        return {
            "ANALYST_GROSS_EXPOSURE_CAP_MODE": "auto",
            "ANALYST_GROSS_EXPOSURE_CAP_PCT": "40",
            "US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "manual",
            "US_ANALYST_GROSS_EXPOSURE_CAP_PCT": "65",
        }.get(key)

    with patch.object(dashboard_server, "_get_env_raw", side_effect=fake_env):
        value = dashboard_server._summary_gross_cap_setting("US", "live")

    assert value["mode"] == "manual"
    assert value["pct"] == 65.0
    assert value["keys"] == [
        "US_ANALYST_GROSS_EXPOSURE_CAP_MODE",
        "US_ANALYST_GROSS_EXPOSURE_CAP_PCT",
    ]


def test_order_size_config_keys_are_common_scoped() -> None:
    expected = [
        "MAX_ORDER_KRW",
        "KR_FIXED_ORDER_KRW",
        "US_FIXED_ORDER_KRW",
        "PATHB_FIXED_ORDER_KRW",
    ]
    assert dashboard_server._order_size_config_keys("US") == expected
    assert dashboard_server._order_size_config_keys("KR") == expected


def test_update_start_config_order_size_writes_common_keys_from_us_control(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    config_path.write_text(
        json.dumps(
            {
                "US_FIXED_ORDER_KRW": 300000,
                "env_overrides": {
                    "MAX_ORDER_KRW": "400000",
                    "KR_FIXED_ORDER_KRW": "250000",
                    "US_FIXED_ORDER_KRW": "300000",
                    "PATHB_FIXED_ORDER_KRW": "300000",
                },
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env.live"
    env_path.write_text(
        "MAX_ORDER_KRW=400000\n"
        "KR_FIXED_ORDER_KRW=250000\n"
        "US_FIXED_ORDER_KRW=300000\n"
        "PATHB_FIXED_ORDER_KRW=300000\n",
        encoding="utf-8",
    )

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_order_size("US", 650000)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")
    assert result["restart_required"] is True
    assert result["env_path"] == str(env_path)
    assert result["updated_keys"] == [
        "MAX_ORDER_KRW",
        "KR_FIXED_ORDER_KRW",
        "US_FIXED_ORDER_KRW",
        "PATHB_FIXED_ORDER_KRW",
    ]
    assert data["US_FIXED_ORDER_KRW"] == 650000
    assert data["env_overrides"]["MAX_ORDER_KRW"] == "650000"
    assert data["env_overrides"]["KR_FIXED_ORDER_KRW"] == "650000"
    assert data["env_overrides"]["US_FIXED_ORDER_KRW"] == "650000"
    assert data["env_overrides"]["PATHB_FIXED_ORDER_KRW"] == "650000"
    assert "MAX_ORDER_KRW=650000" in env_text
    assert "KR_FIXED_ORDER_KRW=650000" in env_text
    assert "US_FIXED_ORDER_KRW=650000" in env_text
    assert "PATHB_FIXED_ORDER_KRW=650000" in env_text


def test_update_start_config_order_size_writes_common_keys_from_kr_control(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    config_path.write_text(
        json.dumps(
            {
                "KR_FIXED_ORDER_KRW": 200000,
                "US_FIXED_ORDER_KRW": 300000,
                "env_overrides": {
                    "MAX_ORDER_KRW": "400000",
                    "KR_FIXED_ORDER_KRW": "200000",
                    "US_FIXED_ORDER_KRW": "300000",
                    "PATHB_FIXED_ORDER_KRW": "300000",
                },
            }
        ),
        encoding="utf-8",
    )

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_order_size("KR", 550000)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["updated_keys"] == [
        "MAX_ORDER_KRW",
        "KR_FIXED_ORDER_KRW",
        "US_FIXED_ORDER_KRW",
        "PATHB_FIXED_ORDER_KRW",
    ]
    assert data["KR_FIXED_ORDER_KRW"] == 550000
    assert data["US_FIXED_ORDER_KRW"] == 550000
    assert data["env_overrides"]["MAX_ORDER_KRW"] == "550000"
    assert data["env_overrides"]["KR_FIXED_ORDER_KRW"] == "550000"
    assert data["env_overrides"]["US_FIXED_ORDER_KRW"] == "550000"
    assert data["env_overrides"]["PATHB_FIXED_ORDER_KRW"] == "550000"


def test_update_start_config_gross_cap_writes_market_specific_keys(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    config_path.write_text(
        json.dumps(
            {
                "env_overrides": {
                    "ANALYST_GROSS_EXPOSURE_CAP_MODE": "auto",
                    "KR_ANALYST_GROSS_EXPOSURE_CAP_MODE": "auto",
                },
            }
        ),
        encoding="utf-8",
    )
    env_path = tmp_path / ".env.live"
    env_path.write_text("US_ANALYST_GROSS_EXPOSURE_CAP_MODE=auto\n", encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_gross_cap("US", "manual", 65)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    env_text = env_path.read_text(encoding="utf-8")
    assert result["restart_required"] is True
    assert result["env_path"] == str(env_path)
    assert result["updated_keys"] == [
        "US_ANALYST_GROSS_EXPOSURE_CAP_MODE",
        "US_ANALYST_GROSS_EXPOSURE_CAP_PCT",
    ]
    assert data["env_overrides"]["ANALYST_GROSS_EXPOSURE_CAP_MODE"] == "auto"
    assert data["env_overrides"]["KR_ANALYST_GROSS_EXPOSURE_CAP_MODE"] == "auto"
    assert data["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_MODE"] == "manual"
    assert data["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_PCT"] == "65"
    assert "US_ANALYST_GROSS_EXPOSURE_CAP_MODE=manual" in env_text
    assert "US_ANALYST_GROSS_EXPOSURE_CAP_PCT=65" in env_text


def test_update_start_config_gross_cap_auto_clears_market_pct(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    config_path.write_text(
        json.dumps(
            {
                "env_overrides": {
                    "US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "manual",
                    "US_ANALYST_GROSS_EXPOSURE_CAP_PCT": "65",
                },
            }
        ),
        encoding="utf-8",
    )

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_gross_cap("US", "auto", None)

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert result["cap_mode"] == "auto"
    assert result["cap_pct"] == 0.0
    assert data["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_MODE"] == "auto"
    assert data["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_PCT"] == ""


def test_update_start_config_order_size_honors_v2_start_config_path(tmp_path) -> None:
    default_path = tmp_path / "config" / "v2_start_config.json"
    custom_path = tmp_path / "custom_start_config.json"
    default_path.parent.mkdir()
    default_path.write_text(json.dumps({"env_overrides": {"MAX_ORDER_KRW": "300000"}}), encoding="utf-8")
    custom_path.write_text(json.dumps({"env_overrides": {"MAX_ORDER_KRW": "400000"}}), encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path), patch.dict(
        os.environ,
        {"V2_START_CONFIG_PATH": str(custom_path), "V2_START_CONFIG_DISABLED": ""},
    ):
        result = dashboard_server._update_start_config_order_size("US", 650000, mode="live")

    default_data = json.loads(default_path.read_text(encoding="utf-8"))
    custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
    assert result["path"] == str(custom_path)
    assert default_data["env_overrides"]["MAX_ORDER_KRW"] == "300000"
    assert custom_data["env_overrides"]["MAX_ORDER_KRW"] == "650000"
    assert custom_data["env_overrides"]["KR_FIXED_ORDER_KRW"] == "650000"


def test_update_start_config_order_size_uses_mode_env_start_config_path(tmp_path) -> None:
    default_path = tmp_path / "config" / "v2_start_config.json"
    custom_path = tmp_path / "runtime" / "live_start.json"
    default_path.parent.mkdir()
    custom_path.parent.mkdir()
    default_path.write_text(json.dumps({"env_overrides": {"MAX_ORDER_KRW": "300000"}}), encoding="utf-8")
    custom_path.write_text(json.dumps({"env_overrides": {"MAX_ORDER_KRW": "400000"}}), encoding="utf-8")
    (tmp_path / ".env.live").write_text("V2_START_CONFIG_PATH=runtime/live_start.json\n", encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        result = dashboard_server._update_start_config_order_size("KR", 550000, mode="live")
        summary_value = dashboard_server._summary_order_size_setting_krw("KR", "live", fallback=300000)

    default_data = json.loads(default_path.read_text(encoding="utf-8"))
    custom_data = json.loads(custom_path.read_text(encoding="utf-8"))
    assert result["path"] == str(custom_path)
    assert summary_value == 550000.0
    assert default_data["env_overrides"]["MAX_ORDER_KRW"] == "300000"
    assert custom_data["env_overrides"]["MAX_ORDER_KRW"] == "550000"


def test_order_size_endpoint_rejects_when_start_config_disabled(tmp_path) -> None:
    config_path = tmp_path / "config" / "v2_start_config.json"
    config_path.parent.mkdir()
    original = {"env_overrides": {"MAX_ORDER_KRW": "300000"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")
    (tmp_path / ".env.live").write_text("V2_START_CONFIG_DISABLED=true\n", encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        response = dashboard_server.app.test_client().post(
            "/api/control/order-size",
            json={"mode": "live", "market": "US", "amount_krw": 650000},
        )

    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_gross_cap_endpoint_rejects_invalid_manual_pct_without_rewrite(tmp_path) -> None:
    config_path = tmp_path / "config" / "v2_start_config.json"
    config_path.parent.mkdir()
    original = {"env_overrides": {"US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "auto"}}
    config_path.write_text(json.dumps(original), encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        response = dashboard_server.app.test_client().post(
            "/api/control/gross-cap",
            json={"mode": "live", "market": "US", "cap_mode": "manual", "cap_pct": 0},
        )

    assert response.status_code == 400
    assert response.get_json()["ok"] is False
    assert json.loads(config_path.read_text(encoding="utf-8")) == original


def test_gross_cap_endpoint_writes_auto_mode(tmp_path) -> None:
    config_path = tmp_path / "config" / "v2_start_config.json"
    config_path.parent.mkdir()
    config_path.write_text(
        json.dumps(
            {
                "env_overrides": {
                    "US_ANALYST_GROSS_EXPOSURE_CAP_MODE": "manual",
                    "US_ANALYST_GROSS_EXPOSURE_CAP_PCT": "65",
                }
            }
        ),
        encoding="utf-8",
    )

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        response = dashboard_server.app.test_client().post(
            "/api/control/gross-cap",
            json={"mode": "live", "market": "US", "cap_mode": "auto"},
        )

    data = response.get_json()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert data["ok"] is True
    assert data["restart_required"] is True
    assert config["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_MODE"] == "auto"
    assert config["env_overrides"]["US_ANALYST_GROSS_EXPOSURE_CAP_PCT"] == ""


def test_update_start_config_order_size_rejects_invalid_json_without_rewrite(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    original = "{invalid"
    config_path.write_text(original, encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        with pytest.raises(dashboard_server.StartConfigValidationError):
            dashboard_server._update_start_config_order_size("US", 650000)

    assert config_path.read_text(encoding="utf-8") == original


def test_update_start_config_order_size_rejects_non_object_config_without_rewrite(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    original = json.dumps(["not", "object"])
    config_path.write_text(original, encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        with pytest.raises(dashboard_server.StartConfigValidationError):
            dashboard_server._update_start_config_order_size("US", 650000)

    assert config_path.read_text(encoding="utf-8") == original


def test_update_start_config_order_size_rejects_invalid_env_overrides_without_rewrite(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "v2_start_config.json"
    original = json.dumps({"env_overrides": ["not", "object"]})
    config_path.write_text(original, encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        with pytest.raises(dashboard_server.StartConfigValidationError):
            dashboard_server._update_start_config_order_size("US", 650000)

    assert config_path.read_text(encoding="utf-8") == original


def test_order_size_endpoint_returns_400_for_invalid_start_config(tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "v2_start_config.json").write_text("{invalid", encoding="utf-8")

    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        response = dashboard_server.app.test_client().post(
            "/api/control/order-size",
            json={"mode": "live", "market": "US", "amount_krw": 650000},
        )

    assert response.status_code == 400
    assert response.get_json()["ok"] is False


def test_order_size_endpoint_returns_500_for_missing_start_config(tmp_path) -> None:
    with patch.object(dashboard_server, "BASE_DIR", tmp_path):
        response = dashboard_server.app.test_client().post(
            "/api/control/order-size",
            json={"mode": "live", "market": "US", "amount_krw": 650000},
        )

    assert response.status_code == 500
    assert response.get_json()["ok"] is False
