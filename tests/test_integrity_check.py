"""tools/integrity_check.py 순수 평가함수 회귀.

D형(잡 stale)·A형(필드 충진)·커버리지 판정 임계를 고정한다.
"""
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools.integrity_check import (
    OK,
    WARN,
    FAIL,
    check_sync_coverage,
    evaluate_freshness,
    evaluate_population,
    evaluate_ratio,
)

NOW = datetime(2026, 6, 19, 0, 0, tzinfo=timezone.utc)


class FreshnessTests(unittest.TestCase):
    def test_recent_is_ok(self):
        ts = (NOW - timedelta(days=1)).isoformat()
        self.assertEqual(evaluate_freshness("x", ts, NOW, warn_days=3, fail_days=5)["status"], OK)

    def test_warn_band(self):
        ts = (NOW - timedelta(days=4)).isoformat()
        self.assertEqual(evaluate_freshness("x", ts, NOW, warn_days=3, fail_days=5)["status"], WARN)

    def test_stale_fails(self):
        # D형 핵심: 잡이 멈춰 오래 정체되면 FAIL (forward 측정기 3주 정지 시나리오)
        ts = (NOW - timedelta(days=21)).isoformat()
        self.assertEqual(evaluate_freshness("x", ts, NOW, warn_days=3, fail_days=5)["status"], FAIL)

    def test_missing_fails(self):
        self.assertEqual(evaluate_freshness("x", None, NOW, warn_days=3, fail_days=5)["status"], FAIL)

    def test_naive_timestamp_assumed_utc(self):
        ts = (NOW - timedelta(days=1)).replace(tzinfo=None).isoformat()
        self.assertEqual(evaluate_freshness("x", ts, NOW, warn_days=3, fail_days=5)["status"], OK)


class PopulationTests(unittest.TestCase):
    def test_full_is_ok(self):
        self.assertEqual(evaluate_population("f", 59, 59, warn_below=70, fail_below=30)["status"], OK)

    def test_empty_fails(self):
        # A형 핵심: 채워져야 할 필드가 비면 FAIL (mfe/mae/regime 배선 끊김 시나리오)
        self.assertEqual(evaluate_population("f", 1, 59, warn_below=70, fail_below=30)["status"], FAIL)

    def test_partial_warns(self):
        self.assertEqual(evaluate_population("f", 35, 59, warn_below=70, fail_below=30)["status"], WARN)

    def test_small_sample_holds_judgment(self):
        # 표본 부족이면 섣불리 깃발 안 든다(cry-wolf 방지)
        self.assertEqual(evaluate_population("f", 0, 3, warn_below=70, fail_below=30, min_sample=10)["status"], OK)


class RatioTests(unittest.TestCase):
    def test_full_coverage_ok(self):
        self.assertEqual(evaluate_ratio("c", 61, 61, warn_below=90, fail_below=70)["status"], OK)

    def test_low_coverage_fails(self):
        self.assertEqual(evaluate_ratio("c", 5, 61, warn_below=90, fail_below=70)["status"], FAIL)

    def test_zero_target_ok(self):
        self.assertEqual(evaluate_ratio("c", 0, 0, warn_below=90, fail_below=70)["status"], OK)


class SyncCoverageTests(unittest.TestCase):
    """CLOSED→학습 sync 커버리지: 오버나이트 홀드가 창 경계에서 가짜 미스매치를 내면 안 된다."""

    def _make_dbs(self, closed_rows, learn_rows):
        d = tempfile.mkdtemp(prefix="integ_sync_")
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        ev_path = os.path.join(d, "events.db")
        ml_path = os.path.join(d, "ml.db")
        con = sqlite3.connect(ev_path)
        con.execute("CREATE TABLE lifecycle_events (decision_id TEXT, event_type TEXT, session_date TEXT)")
        con.executemany("INSERT INTO lifecycle_events VALUES (?,?,?)", closed_rows)
        con.commit()
        con.close()
        con = sqlite3.connect(ml_path)
        con.execute("CREATE TABLE v2_learning_performance (v2_decision_id TEXT, closed INTEGER, session_date TEXT)")
        con.executemany("INSERT INTO v2_learning_performance VALUES (?,?,?)", learn_rows)
        con.commit()
        con.close()
        return Path(ml_path), Path(ev_path)

    def test_overnight_hold_not_false_missing(self):
        # 진입 6/25 · 청산 6/26. now=7/03·window=7 → cutoff=6/26.
        # CLOSED(청산일 6/26)는 창 안, 학습행(진입일 6/25)은 진입일 필터를 걸면 창 밖 → 과거 버그가 FAIL을 냈다.
        now = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
        ml, ev = self._make_dbs(
            closed_rows=[("dec_AAPL", "CLOSED", "2026-06-26")],
            learn_rows=[("dec_AAPL", 1, "2026-06-25")],
        )
        result = check_sync_coverage(ml, ev, now, window_days=7)[0]
        self.assertEqual(result["status"], OK)  # 1/1 커버 — 정상 반영을 미스match로 오판하지 않음

    def test_real_gap_still_fails(self):
        # 실제 sync 누락(학습행 없음)은 여전히 잡아야 한다 — 수정이 감지력을 죽이지 않았음을 고정.
        now = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
        ml, ev = self._make_dbs(
            closed_rows=[("dec_MISS", "CLOSED", "2026-07-02")],
            learn_rows=[("dec_OTHER", 1, "2026-07-02")],
        )
        result = check_sync_coverage(ml, ev, now, window_days=7)[0]
        self.assertEqual(result["status"], FAIL)  # 0/1

    def test_same_day_close_ok(self):
        now = datetime(2026, 7, 3, 8, 0, tzinfo=timezone.utc)
        ml, ev = self._make_dbs(
            closed_rows=[("dec_IREN", "CLOSED", "2026-07-02")],
            learn_rows=[("dec_IREN", 1, "2026-07-02")],
        )
        result = check_sync_coverage(ml, ev, now, window_days=7)[0]
        self.assertEqual(result["status"], OK)


if __name__ == "__main__":
    unittest.main()
