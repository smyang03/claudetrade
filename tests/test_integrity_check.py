"""tools/integrity_check.py 순수 평가함수 회귀.

D형(잡 stale)·A형(필드 충진)·커버리지 판정 임계를 고정한다.
"""
import unittest
from datetime import datetime, timedelta, timezone

from tools.integrity_check import (
    OK,
    WARN,
    FAIL,
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


if __name__ == "__main__":
    unittest.main()
