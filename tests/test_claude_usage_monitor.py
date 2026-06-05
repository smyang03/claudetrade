from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.claude_usage_monitor import (
    category_for_label,
    collect_usage_events,
    date_prefixes_from,
    parse_start_at,
    summarize,
)


KST = timezone(timedelta(hours=9))


class ClaudeUsageMonitorTests(unittest.TestCase):
    def test_category_for_label_groups_runtime_call_sites(self) -> None:
        self.assertEqual(category_for_label("hold_advisor_triage"), "홀드어드바이저")
        self.assertEqual(category_for_label("select_tickers_retry"), "티커 스크리너")
        self.assertEqual(category_for_label("analyst_bull_r1"), "애널리스트")
        self.assertEqual(category_for_label("tune_90min"), "튜너")
        self.assertEqual(category_for_label("postmortem"), "포스트모템")
        self.assertEqual(category_for_label("quick_exit_check"), "빠른청산")

    def test_parse_start_at_uses_current_kst_date_for_time_only_value(self) -> None:
        now = datetime(2026, 6, 4, 21, 30, tzinfo=KST)
        self.assertEqual(parse_start_at("22:00", now=now), datetime(2026, 6, 4, 22, 0, tzinfo=KST))

    def test_date_prefixes_cover_overnight_monitoring_window(self) -> None:
        start_at = datetime(2026, 6, 4, 22, 0, tzinfo=KST)
        self.assertEqual(date_prefixes_from(start_at, end_date=datetime(2026, 6, 5, tzinfo=KST).date()), ["20260604", "20260605"])

    def test_collect_usage_events_prefers_raw_calls_and_summarizes_by_category(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "logs" / "raw_calls"
            raw.mkdir(parents=True)
            (raw / "20260604_KR_hold_advisor_triage_call_a.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-06-04T22:01:00+09:00",
                        "date": "2026-06-04",
                        "market": "KR",
                        "label": "hold_advisor_triage",
                        "call_id": "call_a",
                        "model": "claude-sonnet-4-6",
                        "tokens": {"input": 1000, "output": 200},
                    }
                ),
                encoding="utf-8",
            )
            (raw / "20260604_KR_select_tickers_call_b.json").write_text(
                json.dumps(
                    {
                        "timestamp": "2026-06-04T22:02:00+09:00",
                        "date": "2026-06-04",
                        "market": "KR",
                        "label": "select_tickers",
                        "call_id": "call_b",
                        "model": "claude-sonnet-4-6",
                        "tokens": {"input": 2000, "output": 300},
                    }
                ),
                encoding="utf-8",
            )
            start_at = datetime(2026, 6, 4, 22, 0, tzinfo=KST)

            events = collect_usage_events(root, start_at=start_at, mode="live")
            payload = summarize(events)

        self.assertEqual(payload["total"]["calls"], 2)
        self.assertEqual(payload["total"]["input_tokens"], 3000)
        self.assertEqual(payload["by_category"]["홀드어드바이저"]["calls"], 1)
        self.assertEqual(payload["by_category"]["티커 스크리너"]["calls"], 1)


if __name__ == "__main__":
    unittest.main()
