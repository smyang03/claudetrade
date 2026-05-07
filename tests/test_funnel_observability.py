from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.funnel_observability import append_funnel_event, candidate_trace_id


class FunnelObservabilityTests(unittest.TestCase):
    def test_candidate_trace_id_uses_pipe_separator_and_compact_time(self) -> None:
        trace_id = candidate_trace_id(
            session_date="2026-05-06",
            market="KR",
            ticker="001440",
            first_seen_at="2026-05-06T09:05:00+09:00",
            cycle_id="cycle_0007",
        )

        self.assertEqual(trace_id, "20260506|KR|001440|20260506T090500|cycle_0007")
        self.assertEqual(trace_id.count("|"), 4)

    def test_append_funnel_event_writes_utf8_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("runtime_paths._RUNTIME_ROOT", Path(tmpdir)):
                path = append_funnel_event(
                    event_type="candidate_quality_report",
                    session_date="2026-05-06",
                    market="US",
                    payload={"message": "한글 정상", "count": 1},
                )

                rows = path.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(rows), 1)
                payload = json.loads(rows[0])
                self.assertEqual(payload["message"], "한글 정상")
                self.assertEqual(payload["event_type"], "candidate_quality_report")


if __name__ == "__main__":
    unittest.main()
