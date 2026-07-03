from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from minority_report.claude_utils import with_json_schema
import minority_report.quick_exit_check as qec


_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["HOLD", "SELL"]}},
    "required": ["action"],
    "additionalProperties": False,
}


class WithJsonSchemaTests(unittest.TestCase):
    def test_disabled_returns_input_unchanged(self) -> None:
        base = {"thinking": {"type": "disabled"}}
        self.assertIs(with_json_schema(base, _SCHEMA, enabled=False), base)

    def test_enabled_merges_format_and_preserves_thinking(self) -> None:
        base = {"thinking": {"type": "disabled"}}
        out = with_json_schema(base, _SCHEMA, enabled=True)
        self.assertEqual(out["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(out["output_config"]["format"]["schema"], _SCHEMA)
        self.assertEqual(out["thinking"], {"type": "disabled"})
        # 입력 dict를 변형하지 않는다(부작용 없음)
        self.assertNotIn("output_config", base)

    def test_format_coexists_with_effort(self) -> None:
        base = {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}
        out = with_json_schema(base, _SCHEMA, enabled=True)
        self.assertEqual(out["output_config"]["effort"], "medium")
        self.assertIn("format", out["output_config"])

    def test_non_dict_schema_is_noop(self) -> None:
        base = {"thinking": {"type": "disabled"}}
        self.assertIs(with_json_schema(base, None, enabled=True), base)


class QuickExitToggleTests(unittest.TestCase):
    def test_default_off(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("QUICK_EXIT_STRUCTURED_OUTPUT", None)
            self.assertFalse(qec._quick_exit_structured_enabled())

    def test_enabled_via_env(self) -> None:
        with patch.dict(os.environ, {"QUICK_EXIT_STRUCTURED_OUTPUT": "true"}):
            self.assertTrue(qec._quick_exit_structured_enabled())

    def test_schema_uses_only_supported_features(self) -> None:
        # structured-output 미지원 제약(minLength/maximum 등)이 섞이지 않았는지 방어
        sch = qec._QUICK_EXIT_JSON_SCHEMA
        self.assertFalse(sch.get("additionalProperties", True))
        self.assertEqual(set(sch["required"]), set(sch["properties"].keys()))  # 전 필드 required
        banned = {"minLength", "maxLength", "minimum", "maximum", "multipleOf", "pattern"}
        for prop in sch["properties"].values():
            self.assertFalse(banned & set(prop.keys()))


if __name__ == "__main__":
    unittest.main()
