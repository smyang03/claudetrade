# -*- coding: utf-8 -*-
"""US 어닝 point-in-time 원장 — 처음 본 값·바뀐 값만 append, 원값 보존, 사라진 키 유지 (2026-09-04)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import earnings_pit_ledger as pit  # noqa: E402

T0 = datetime(2026, 9, 3, 13, 0, tzinfo=timezone.utc)


class DiffRowsTest(unittest.TestCase):
    def test_new_then_actual_then_revision(self):
        cal1 = {"fetched_at": "2026-09-03T22:00:00", "by_symbol": {
            "ESP": {"date": "2026-09-17", "hour": "", "eps_estimate": 0.9696, "eps_actual": None}}}
        rows, last = pit.diff_rows(cal1, {}, now_utc=T0)
        self.assertEqual([r["change"] for r in rows], ["new"])
        self.assertEqual(rows[0]["first_seen_at"], T0.isoformat(timespec="seconds"))
        # 같은 값 재조회 → 행 없음
        rows2, last2 = pit.diff_rows(cal1, last, now_utc=T0)
        self.assertEqual(rows2, [])
        # hour·actual이 채워짐 → 각각 첫 관측 행
        cal2 = {"fetched_at": "2026-09-18T07:00:00", "by_symbol": {
            "ESP": {"date": "2026-09-17", "hour": "amc", "eps_estimate": 0.9696, "eps_actual": 1.02}}}
        rows3, last3 = pit.diff_rows(cal2, last2, now_utc=T0)
        self.assertEqual(sorted(r["change"] for r in rows3), ["eps_actual", "hour"])
        # estimate가 바뀜 → revised, prev 보존
        cal3 = {"fetched_at": "2026-09-19T07:00:00", "by_symbol": {
            "ESP": {"date": "2026-09-17", "hour": "amc", "eps_estimate": 0.98, "eps_actual": 1.02}}}
        rows4, last4 = pit.diff_rows(cal3, last3, now_utc=T0)
        self.assertEqual(rows4[0]["change"], "revised")
        self.assertEqual(rows4[0]["changed_field"], "eps_estimate")
        self.assertEqual(rows4[0]["prev"], 0.9696)

    def test_rolled_out_keys_are_kept(self):
        last = {"OLD|2026-08-01": {"hour": "bmo", "eps_estimate": 1.0, "eps_actual": 1.1}}
        cal = {"fetched_at": "x", "by_symbol": {"NEW": {"date": "2026-09-20", "hour": "", "eps_estimate": 2.0, "eps_actual": None}}}
        rows, new_last = pit.diff_rows(cal, last, now_utc=T0)
        self.assertIn("OLD|2026-08-01", new_last)
        self.assertEqual(len(rows), 1)

    def test_run_appends_and_writes_state(self):
        with tempfile.TemporaryDirectory() as d:
            cal = Path(d) / "cal.json"; led = Path(d) / "led.jsonl"; st = Path(d) / "st.json"
            cal.write_text(json.dumps({"fetched_at": "t", "by_symbol": {"A": {"date": "2026-09-10", "hour": "bmo",
                                                                               "eps_estimate": 1.0, "eps_actual": None}}}), encoding="utf-8")
            self.assertEqual(pit.run(cal, led, st), 1)
            self.assertEqual(pit.run(cal, led, st), 0)  # 멱등
            self.assertEqual(len(led.read_text(encoding="utf-8").splitlines()), 1)
            self.assertIn("A|2026-09-10", json.loads(st.read_text(encoding="utf-8")))


if __name__ == "__main__":
    unittest.main()
