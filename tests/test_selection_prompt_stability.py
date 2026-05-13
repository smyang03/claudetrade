from __future__ import annotations

import json
import unittest

from minority_report.analysts import (
    _json_array_object_cap,
    _recover_compact_watch_selection,
)


class SelectionPromptStabilityTests(unittest.TestCase):
    def test_json_array_object_cap_keeps_valid_json_and_whole_objects(self) -> None:
        first = {"ticker": "AAA", "blob": "x" * 30}
        second = {"ticker": "BBB", "blob": "y" * 200}
        first_encoded = json.dumps(first, ensure_ascii=False, separators=(",", ":"))

        text, included, omitted = _json_array_object_cap(
            [first, second],
            max_chars=2 + len(first_encoded),
        )

        self.assertEqual(json.loads(text), [first])
        self.assertEqual(included, [first])
        self.assertEqual(omitted, 1)
        self.assertNotIn("BBB", text)

    def test_compact_watch_recovery_preserves_wl_from_extra_brace_response(self) -> None:
        broken = (
            '{"wl":["319400","356680","012860"],"tr":["319400"],"ca":['
            '{"t":"319400","a":"BUY_READY","pt":{"ref":43400,"lo":42500}},'
            '{"t":"356680","a":"WATCH","s":"gap_pullback","inv":"price_fails_at_high"}},'
            '{"t":"012860","a":"WATCH","s":"momentum","inv":"fade_deepens"}}]}'
        )

        recovered = _recover_compact_watch_selection(broken)

        self.assertEqual(recovered["wl"], ["319400", "356680", "012860"])
        self.assertEqual(recovered["tr"], [])
        self.assertEqual(recovered["ca"], [])
        self.assertTrue(recovered["_parse_recovered"])
        self.assertEqual(recovered["_fallback_mode"], "compact_watch_recovered")
        self.assertEqual(recovered["_recovered_raw_trade_ready"], ["319400"])


if __name__ == "__main__":
    unittest.main()
