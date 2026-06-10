"""session_close 시 v2 learning sync 자동 실행 훅 검증."""

import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_method():
    import trading_bot

    return trading_bot


class V2LearningSyncSessionCloseTests(unittest.TestCase):
    def setUp(self):
        self.tb = _load_method()
        self.dummy = types.SimpleNamespace(_mode="live")

    def _call(self, market="US", session_date="2026-06-10"):
        return self.tb.TradingBot._run_v2_learning_sync_at_session_close(
            self.dummy, market, session_date
        )

    def test_disabled_env_skips_subprocess(self):
        with mock.patch.dict(os.environ, {"V2_LEARNING_SYNC_AT_SESSION_CLOSE": "false"}):
            with mock.patch.object(self.tb.subprocess, "run") as run_mock:
                self._call()
        run_mock.assert_not_called()

    def test_enabled_runs_sync_tool_with_market_mode_dates(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"V2_LEARNING_SYNC_AT_SESSION_CLOSE": "true"}):
            with mock.patch.object(self.tb.subprocess, "run", return_value=completed) as run_mock:
                self._call(market="US", session_date="2026-06-10")
        run_mock.assert_called_once()
        cmd = run_mock.call_args[0][0]
        self.assertIn("sync_v2_learning_performance.py", cmd[1])
        self.assertIn("--market", cmd)
        self.assertEqual(cmd[cmd.index("--market") + 1], "US")
        self.assertEqual(cmd[cmd.index("--runtime-mode") + 1], "live")
        self.assertEqual(cmd[cmd.index("--start-date") + 1], "2026-05-31")
        self.assertEqual(cmd[cmd.index("--end-date") + 1], "2026-06-10")

    def test_nonzero_returncode_does_not_raise(self):
        completed = mock.Mock(returncode=1, stdout="boom", stderr="")
        with mock.patch.dict(os.environ, {"V2_LEARNING_SYNC_AT_SESSION_CLOSE": "true"}):
            with mock.patch.object(self.tb.subprocess, "run", return_value=completed):
                self._call()  # 예외 없이 경고 로그만 남겨야 한다

    def test_paper_mode_passes_paper_runtime_mode(self):
        self.dummy._mode = "paper"
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.dict(os.environ, {"V2_LEARNING_SYNC_AT_SESSION_CLOSE": "true"}):
            with mock.patch.object(self.tb.subprocess, "run", return_value=completed) as run_mock:
                self._call(market="KR")
        cmd = run_mock.call_args[0][0]
        self.assertEqual(cmd[cmd.index("--runtime-mode") + 1], "paper")
        self.assertEqual(cmd[cmd.index("--market") + 1], "KR")


if __name__ == "__main__":
    unittest.main()
