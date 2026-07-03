from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

import kis_api


class CrossProcRateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="kis_rate_test_")
        self._path = os.path.join(self._tmp, "rate_gap.ts")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        for name in (self._path, self._path + ".lock"):
            try:
                if os.path.isdir(name):
                    os.rmdir(name)
                elif os.path.exists(name):
                    os.remove(name)
            except Exception:
                pass
        try:
            os.rmdir(self._tmp)
        except Exception:
            pass

    def test_default_disabled_does_not_invoke_reserve(self) -> None:
        # 기본 OFF: _rate_limit_wait는 cross-proc 예약을 호출하지 않는다(매매 무변경).
        self.assertFalse(kis_api._CROSS_PROC_RATE_ENABLED)
        with patch.object(kis_api, "_cross_process_rate_reserve") as reserve_mock, patch.object(
            kis_api.time, "sleep", lambda *_a, **_k: None
        ):
            kis_api._rate_limit_wait()
        reserve_mock.assert_not_called()

    def test_enabled_reservation_spaces_calls(self) -> None:
        with patch.object(kis_api, "_cross_proc_rate_file", return_value=self._path):
            mg = 0.1
            t0 = time.time()
            self.assertTrue(kis_api._cross_process_rate_reserve(mg))
            self.assertTrue(kis_api._cross_process_rate_reserve(mg))
            elapsed = time.time() - t0
        # 두 번째 예약은 최소 min_gap만큼 뒤로 밀린다.
        self.assertGreaterEqual(elapsed, mg * 0.8)
        self.assertFalse(os.path.exists(self._path + ".lock"))  # 락 정리됨

    def test_reserve_fallback_when_no_path(self) -> None:
        with patch.object(kis_api, "_cross_proc_rate_file", return_value=""):
            self.assertFalse(kis_api._cross_process_rate_reserve(0.1))

    def test_dir_lock_times_out_when_held(self) -> None:
        # 다른 보유자가 잡고 있으면(그리고 stale 아니면) 타임아웃 → False → per-process fallback.
        lock_dir = self._path + ".lock"
        os.mkdir(lock_dir)  # fresh (mtime ~ now, stale 아님)
        try:
            t0 = time.time()
            acquired = kis_api._acquire_dir_lock(lock_dir, timeout_sec=0.2)
            elapsed = time.time() - t0
        finally:
            os.rmdir(lock_dir)
        self.assertFalse(acquired)
        self.assertGreaterEqual(elapsed, 0.15)

    def test_dir_lock_steals_stale(self) -> None:
        lock_dir = self._path + ".lock"
        os.mkdir(lock_dir)
        old = time.time() - 3600
        os.utime(lock_dir, (old, old))  # stale
        acquired = kis_api._acquire_dir_lock(lock_dir, timeout_sec=1.0)
        try:
            self.assertTrue(acquired)  # stale은 회수
        finally:
            try:
                os.rmdir(lock_dir)
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
