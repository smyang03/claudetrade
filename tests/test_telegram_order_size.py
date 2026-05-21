from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import telegram_commander


def _bot(amount: int = 300_000) -> SimpleNamespace:
    return SimpleNamespace(risk=SimpleNamespace(max_order_krw=amount))


def _write_config(path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_setorder_status_shows_common_current_and_next_setting(tmp_path) -> None:
    config_path = tmp_path / "v2_start_config.json"
    _write_config(config_path, {"env_overrides": {"MAX_ORDER_KRW": "500000"}})
    bot = _bot(300_000)

    with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
        message = telegram_commander._handle("/setorder", bot)

    assert "공통 최대주문" in message
    assert "300,000원" in message
    assert "재시작 후 500,000원" in message


def test_setorder_persists_common_max_order_to_start_config(tmp_path) -> None:
    config_path = tmp_path / "v2_start_config.json"
    _write_config(
        config_path,
        {
            "env_overrides": {
                "MAX_ORDER_KRW": "300000",
                "US_FIXED_ORDER_KRW": "500000",
            }
        },
    )
    bot = _bot(300_000)

    with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
        message = telegram_commander._cmd_setorder(bot, "500000")

    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert bot.risk.max_order_krw == 500_000.0
    assert data["env_overrides"]["MAX_ORDER_KRW"] == "500000"
    assert data["env_overrides"]["KR_FIXED_ORDER_KRW"] == "500000"
    assert data["env_overrides"]["US_FIXED_ORDER_KRW"] == "500000"
    assert data["env_overrides"]["PATHB_FIXED_ORDER_KRW"] == "500000"
    assert "공통 최대주문" in message


def test_setorder_rejects_values_outside_live_safe_range_without_writing(tmp_path) -> None:
    for requested in ("10000", "5000001"):
        config_path = tmp_path / f"v2_start_config_{requested}.json"
        original = {"env_overrides": {"MAX_ORDER_KRW": "300000"}}
        _write_config(config_path, original)
        bot = _bot(300_000)

        with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
            message = telegram_commander._cmd_setorder(bot, requested)

        assert bot.risk.max_order_krw == 300_000
        assert json.loads(config_path.read_text(encoding="utf-8")) == original
        assert "❌" in message


def test_setorder_accepts_live_safe_boundaries(tmp_path) -> None:
    for requested in (50_000, 5_000_000):
        config_path = tmp_path / f"v2_start_config_{requested}.json"
        _write_config(config_path, {"env_overrides": {"MAX_ORDER_KRW": "300000"}})
        bot = _bot(300_000)

        with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
            message = telegram_commander._cmd_setorder(bot, str(requested))

        data = json.loads(config_path.read_text(encoding="utf-8"))
        assert bot.risk.max_order_krw == float(requested)
        assert data["env_overrides"]["MAX_ORDER_KRW"] == str(requested)
        assert "변경" in message


def test_setorder_does_not_change_runtime_or_file_when_start_config_invalid(tmp_path) -> None:
    config_path = tmp_path / "v2_start_config.json"
    original = "{invalid"
    config_path.write_text(original, encoding="utf-8")
    bot = _bot(300_000)

    with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
        message = telegram_commander._cmd_setorder(bot, "500000")

    assert config_path.read_text(encoding="utf-8") == original
    assert bot.risk.max_order_krw == 300_000
    assert "변경 실패" in message
    assert "변경하지 않았습니다" in message


def test_setorder_does_not_clobber_non_object_start_config(tmp_path) -> None:
    for original in ("[]", json.dumps({"env_overrides": []}, ensure_ascii=False)):
        config_path = tmp_path / f"v2_start_config_{len(original)}.json"
        config_path.write_text(original, encoding="utf-8")
        bot = _bot(300_000)

        with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
            message = telegram_commander._cmd_setorder(bot, "500000")

        assert config_path.read_text(encoding="utf-8") == original
        assert bot.risk.max_order_krw == 300_000
        assert "변경 실패" in message


def test_setorder_preserves_unrelated_env_overrides(tmp_path) -> None:
    config_path = tmp_path / "v2_start_config.json"
    _write_config(
        config_path,
        {
            "env_overrides": {
                "ENABLED_MARKETS": "KR,US",
                "PATHB_KR_LIVE_ENABLED": "false",
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
                "MAX_ORDER_KRW": "300000",
            }
        },
    )
    bot = _bot(300_000)

    with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}):
        telegram_commander._cmd_setorder(bot, "500000")

    overrides = json.loads(config_path.read_text(encoding="utf-8"))["env_overrides"]
    assert overrides["ENABLED_MARKETS"] == "KR,US"
    assert overrides["PATHB_KR_LIVE_ENABLED"] == "false"
    assert overrides["KR_CONFIRMATION_GATE_MODE"] == "FAST_TRIGGER_WITH_HARD_VETO"
    assert overrides["MAX_ORDER_KRW"] == "500000"


def test_setorder_does_not_change_runtime_when_atomic_write_fails(tmp_path) -> None:
    config_path = tmp_path / "v2_start_config.json"
    _write_config(config_path, {"env_overrides": {"MAX_ORDER_KRW": "300000"}})
    bot = _bot(300_000)

    with patch.dict(os.environ, {"V2_START_CONFIG_PATH": str(config_path)}), patch(
        "telegram_commander._atomic_write_text",
        side_effect=telegram_commander.StartConfigWriteError("boom"),
    ):
        message = telegram_commander._cmd_setorder(bot, "500000")

    assert bot.risk.max_order_krw == 300_000
    assert json.loads(config_path.read_text(encoding="utf-8"))["env_overrides"]["MAX_ORDER_KRW"] == "300000"
    assert "변경 실패" in message
