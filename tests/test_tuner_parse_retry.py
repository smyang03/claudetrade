from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import patch

from minority_report import tuner


def _response(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


class TunerParseRetryTests(unittest.TestCase):
    def test_malformed_response_retries_once_then_uses_valid_json(self) -> None:
        valid = (
            '{"action":"MAINTAIN","mode":"CAUTIOUS","size_adj":0,"sl_adj":0.0,'
            '"momentum_wait_adjust_min":0,"entry_priority_cutoff_adjust":0.0,'
            '"kr_momentum_atr_cap_adjust":0.0,"kr_momentum_atr_cap_high_adjust":0.0,'
            '"reason":"retry succeeded","warning":null}'
        )
        with (
            patch.object(tuner.client.messages, "create", side_effect=[_response('{"action":"MAINTAIN"'), _response(valid)]) as create,
            patch.object(tuner, "credit_record") as credit,
            patch.object(tuner, "save_raw_call") as save,
            patch.object(tuner.BrainDB, "update_tuning_pattern"),
            patch.dict("os.environ", {"TUNER_JSON_RETRY_MAX": "1", "TUNER_JSON_RETRY_BACKOFF_SEC": "0"}),
        ):
            result = tuner.tune("KR", 30, {"positions": []}, {"consensus": {"mode": "CAUTIOUS"}}, "")

        self.assertEqual(result["action"], "MAINTAIN")
        self.assertEqual(result["reason"], "retry succeeded")
        self.assertEqual(create.call_count, 2)
        self.assertEqual(credit.call_count, 2)
        self.assertEqual(save.call_count, 2)
        self.assertEqual(save.call_args_list[0].kwargs["parsed"]["warning"], "JSON_PARSE_FAILED")


if __name__ == "__main__":
    unittest.main()
