from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from config.runtime_config import EffectiveRuntimeConfig, redact_config_value


class EffectiveRuntimeConfigTests(unittest.TestCase):
    def test_get_typed_values_and_sources(self) -> None:
        cfg = EffectiveRuntimeConfig.from_env(
            runtime_mode="live",
            environ={
                "ENABLE_LIVE_FUNNEL_JSONL": "true",
                "FUNNEL_SUMMARY_THROTTLE_SEC": "60",
                "PROBE_SIZE_RATIO": "0.30",
            },
            defaults={"PROBE_SIZE_RATIO": "0.10", "MISSING_DEFAULT": "x"},
        )

        self.assertTrue(cfg.get_bool("ENABLE_LIVE_FUNNEL_JSONL"))
        self.assertEqual(cfg.get_int("FUNNEL_SUMMARY_THROTTLE_SEC"), 60)
        self.assertEqual(cfg.get_float("PROBE_SIZE_RATIO"), 0.30)
        self.assertEqual(cfg.get("MISSING_DEFAULT"), "x")
        self.assertEqual(cfg.source_of("PROBE_SIZE_RATIO"), "env")
        self.assertEqual(cfg.source_of("MISSING_DEFAULT"), "code_default")

    def test_redaction_keeps_start_config_path_visible(self) -> None:
        self.assertEqual(redact_config_value("V2_START_CONFIG_PATH", "config/v2_start_config.json"), "config/v2_start_config.json")
        self.assertNotEqual(redact_config_value("TELEGRAM_TOKEN", "abcdef123456"), "abcdef123456")

    def test_redaction_keeps_token_limit_values_visible(self) -> None:
        self.assertEqual(redact_config_value("CLAUDE_ANALYST_R1_MAX_TOKENS", "700"), "700")
        self.assertEqual(redact_config_value("CLAUDE_SELECTION_RETRY_MAX_TOKENS", "3500"), "3500")

    def test_write_redacted_snapshot_is_utf8_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                cfg = EffectiveRuntimeConfig.from_env(
                    runtime_mode="live",
                    environ={"TELEGRAM_TOKEN": "abcdef123456", "V2_START_CONFIG_PATH": "config/v2_start_config.json"},
                )
                path = cfg.write_redacted_snapshot(label="test")

                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["effective"]["V2_START_CONFIG_PATH"], "config/v2_start_config.json")
                self.assertNotEqual(payload["effective"]["TELEGRAM_TOKEN"], "abcdef123456")
                self.assertEqual(payload["source_report"]["source_granularity"], "env_only_v1")


if __name__ == "__main__":
    unittest.main()
