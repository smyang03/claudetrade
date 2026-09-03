# -*- coding: utf-8 -*-
"""가격 캐시 갱신 마커 대기 + 가상 북 진입 스킵 원장 (2026-09-03 KR 캐시 경합 수리).

09-03 실측: KR CSV 갱신(16:00→16:40)이 끝나기 전 16:22에 가상 북이 돌아 kr_r4x 09-02 통과자
348340·466100 진입이 조용히 건너뛰어졌다. 여기서 고정하는 계약:
- KR 마커는 end_date==today로 판정(08:30 실행 마커는 end_dt=어제라 run_date로는 속는다)
- US 마커는 run_date==today로 판정(22:00 실행 마커는 전날 run_date)
- 타임아웃이면 진행(체인 뒤 단계를 막지 않음)
- 봉 없음 스킵은 원장에 사유가 남고, 다음 세션 대기(정상)와 캐시 미갱신(결함)을 가른다
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import virtual_books as vb  # noqa: E402
from tools import integrity_check as ic  # noqa: E402
from update_data import write_price_update_marker  # noqa: E402


class PriceMarkerPredicateTest(unittest.TestCase):
    def test_kr_uses_end_date_not_run_date(self):
        now = datetime(2026, 9, 3, 16, 20)
        # 08:30 실행 마커(디코이): run_date=오늘, end_date=어제 → 아직 아님
        decoy = {"market": "KR", "run_date": "2026-09-03", "end_date": "2026-09-02", "ok": True}
        self.assertFalse(vb.price_marker_ready("KR", now, decoy))
        # 16:00 실행 마커: end_date=오늘 → 준비됨
        ready = {"market": "KR", "run_date": "2026-09-03", "end_date": "2026-09-03", "ok": True}
        self.assertTrue(vb.price_marker_ready("KR", now, ready))

    def test_us_uses_run_date(self):
        now = datetime(2026, 9, 4, 7, 20)
        # 전날 22:00 실행 마커(디코이): run_date=어제 → 아직 아님
        decoy = {"market": "US", "run_date": "2026-09-03", "end_date": "2026-09-03", "ok": True}
        self.assertFalse(vb.price_marker_ready("US", now, decoy))
        ready = {"market": "US", "run_date": "2026-09-04", "end_date": "2026-09-04", "ok": True}
        self.assertTrue(vb.price_marker_ready("US", now, ready))

    def test_missing_marker_not_ready(self):
        self.assertFalse(vb.price_marker_ready("KR", datetime(2026, 9, 3, 16, 20), {}))

    def test_markers_to_wait_by_clock(self):
        self.assertEqual(vb.markers_to_wait(datetime(2026, 9, 3, 16, 20)), ["KR"])   # 목요일 16:20
        self.assertEqual(vb.markers_to_wait(datetime(2026, 9, 3, 7, 20)), ["US"])    # 목요일 07:20
        self.assertEqual(vb.markers_to_wait(datetime(2026, 9, 3, 13, 0)), [])        # 낮 수동 실행
        self.assertEqual(vb.markers_to_wait(datetime(2026, 9, 5, 16, 20)), [])       # 토요일


class WaitLoopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.orig = dict(vb.PRICE_MARKER)
        for m in ("KR", "US"):
            vb.PRICE_MARKER[m] = Path(self.tmp.name) / f"price_update_marker_{m}.json"

    def tearDown(self):
        vb.PRICE_MARKER.update(self.orig)
        self.tmp.cleanup()

    def test_waits_until_marker_then_proceeds(self):
        now = datetime(2026, 9, 3, 16, 22)
        calls = {"n": 0}

        def fake_sleep(_s):
            calls["n"] += 1
            if calls["n"] == 3:  # 세 번째 폴링 뒤 16:00 작업이 마커를 씀
                write_price_update_marker("KR", "2026-09-03", True, path=vb.PRICE_MARKER["KR"])

        res = vb.wait_for_price_markers(now_fn=lambda: now, sleep_fn=fake_sleep, max_wait_s=600, poll_s=30)
        self.assertEqual(res, {"KR": True})
        self.assertEqual(calls["n"], 3)

    def test_timeout_proceeds_without_marker(self):
        now = datetime(2026, 9, 3, 16, 22)
        slept = []
        res = vb.wait_for_price_markers(now_fn=lambda: now, sleep_fn=slept.append, max_wait_s=90, poll_s=30)
        self.assertEqual(res, {"KR": False})
        self.assertEqual(len(slept), 3)  # 0→30→60→90 에서 중단

    def test_marker_writer_round_trip_and_failure_flag(self):
        p = Path(self.tmp.name) / "m.json"
        write_price_update_marker("kr", "2026-09-03", False, "boom", path=p)
        d = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["market"], "KR")
        self.assertEqual(d["end_date"], "2026-09-03")
        self.assertFalse(d["ok"])
        self.assertEqual(d["error"], "boom")
        self.assertFalse(p.with_suffix(".json.tmp").exists())


class EntrySkipLedgerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Path(self.tmp.name) / "skips.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_session_arm_on_latest_signal_is_awaiting(self):
        s = {"id": "kr_r4x", "universe": "kr"}
        dates = {"2026-09-01": [], "2026-09-02": []}
        self.assertEqual(vb.classify_entry_skip(s, "2026-09-02", dates), "awaiting_session")
        # 옛 신호일인데 봉 없음 = 캐시 미갱신(오늘 09-03 사고의 형태)
        self.assertEqual(vb.classify_entry_skip(s, "2026-09-01", dates), "no_bar_stale")

    def test_same_session_us_arm_is_stale(self):
        s = {"id": "us_wide_dvol", "universe": "wide"}
        dates = {"2026-09-02": []}
        self.assertEqual(vb.classify_entry_skip(s, "2026-09-02", dates), "no_bar_stale")

    def test_record_appends_row(self):
        s = {"id": "us_slow_fallen", "universe": "slowus"}
        row = vb.record_entry_skip(s, "2026-09-02", "IP", "SLOW", {"2026-09-02": []}, path=self.ledger)
        self.assertEqual(row["reason"], "awaiting_session")
        lines = self.ledger.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["ticker"], "IP")


class IntegrityCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data" / "shadow").mkdir(parents=True)
        (self.root / "state").mkdir()
        self.orig_root = ic.ROOT
        ic.ROOT = self.root

    def tearDown(self):
        ic.ROOT = self.orig_root
        self.tmp.cleanup()

    def _write(self, rows):
        p = self.root / "data" / "shadow" / "virtual_books_entry_skips.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    def test_awaiting_only_is_ok(self):
        now = datetime(2026, 9, 3, 18, 0)
        self._write([{"ts": "2026-09-03T07:24:00+00:00", "strategy_id": "b2_leader_pb", "ticker": "ANF",
                      "session_date": "2026-09-02", "reason": "awaiting_session"}])
        (self.root / "state" / "price_update_marker_KR.json").write_text(
            json.dumps({"end_date": "2026-09-03", "ok": True}), encoding="utf-8")
        rows = ic.check_virtual_entry_skips(now)
        self.assertEqual(rows[0]["status"], ic.OK)
        self.assertIn("대기 1", rows[0]["detail"])
        self.assertIn("KR=2026-09-03", rows[0]["detail"])

    def test_stale_recent_warns_old_ignored(self):
        now = datetime(2026, 9, 3, 18, 0)
        self._write([
            {"ts": "2026-09-03T07:22:00+00:00", "strategy_id": "kr_r4x", "ticker": "348340",
             "session_date": "2026-09-02", "reason": "no_bar_stale"},
            {"ts": (datetime(2026, 9, 3, 18, 0) - timedelta(hours=40)).isoformat() + "+00:00",
             "strategy_id": "kr_r4x", "ticker": "OLD", "session_date": "2026-08-30", "reason": "no_bar_stale"},
        ])
        rows = ic.check_virtual_entry_skips(now)
        self.assertEqual(rows[0]["status"], ic.WARN)
        self.assertIn("348340", rows[0]["detail"])
        self.assertNotIn("OLD", rows[0]["detail"])


if __name__ == "__main__":
    unittest.main()
