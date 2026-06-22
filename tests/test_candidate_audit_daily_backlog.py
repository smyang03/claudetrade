"""후보 일 단위 forward 라벨 백로그 갱신 검증."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.update_candidate_audit_outcomes import update_candidate_audit_daily_backlog
from trading_bot import TradingBot


class DailyBacklogHelperTests(unittest.TestCase):
    def test_missing_db_returns_reason_without_error(self):
        result = update_candidate_audit_daily_backlog(db_path=Path("nonexistent") / "no.db")
        self.assertEqual(result["reason"], "missing_db")
        self.assertEqual(result["dates"], [])

    def test_backlog_collects_pending_dates_and_runs_daily_horizons(self):
        import sqlite3, tempfile
        from datetime import datetime, timedelta

        # 최근 세션은 lookback(10일) 내여야 한다 — 고정 날짜는 시간이 지나며 lookback을
        # 이탈하므로 오늘 기준 상대 날짜로 생성한다.
        recent = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "candidate_audit.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE audit_candidate_rows (candidate_key TEXT, session_date TEXT, market TEXT)")
            con.execute("CREATE TABLE audit_candidate_outcomes (candidate_key TEXT, status TEXT)")
            # 오래된 pending 세션 (lookback 밖) + 최근 세션
            con.execute("INSERT INTO audit_candidate_rows VALUES ('k1','2026-05-19','US')")
            con.execute("INSERT INTO audit_candidate_rows VALUES ('k2',?,'US')", (recent,))
            con.execute("INSERT INTO audit_candidate_outcomes VALUES ('k1','daily_pending')")
            con.commit()
            con.close()

            calls: list[dict] = []

            def fake_update(**kwargs):
                calls.append(kwargs)
                return {"updated": 1}

            with patch("tools.update_candidate_audit_outcomes.update_candidate_audit_outcomes", side_effect=fake_update):
                result = update_candidate_audit_daily_backlog(db_path=db, lookback_days=10)

        # 오래된 pending(5/19)도 lookback 밖이지만 포함돼야 한다
        self.assertIn("2026-05-19", result["dates"])
        self.assertIn(recent, result["dates"])
        for call in calls:
            self.assertEqual(tuple(sorted(call["horizons"])), (1440, 2880, 4320))


class SessionCloseWiringTests(unittest.TestCase):
    def _bot(self):
        import types
        dummy = types.SimpleNamespace(_mode="live")
        dummy._runtime_bool = lambda key, default: TradingBot._runtime_bool(dummy, key, default)
        dummy._run_candidate_audit_daily_backlog_at_session_close = (
            lambda market: TradingBot._run_candidate_audit_daily_backlog_at_session_close(dummy, market)
        )
        return dummy

    def test_session_close_runs_backlog_when_enabled(self):
        bot = self._bot()
        with patch(
            "tools.update_candidate_audit_outcomes.update_candidate_audit_daily_backlog",
            return_value={"dates": ["2026-06-10"], "summaries": [], "reason": ""},
        ) as helper, patch.dict("os.environ", {"CANDIDATE_AUDIT_DAILY_OUTCOME_UPDATE_ENABLED": "true"}):
            bot._run_candidate_audit_daily_backlog_at_session_close("US")
        helper.assert_called_once()
        self.assertEqual(helper.call_args.kwargs["market"], "US")

    def test_session_close_skips_when_disabled(self):
        bot = self._bot()
        with patch(
            "tools.update_candidate_audit_outcomes.update_candidate_audit_daily_backlog"
        ) as helper, patch.dict("os.environ", {"CANDIDATE_AUDIT_DAILY_OUTCOME_UPDATE_ENABLED": "false"}):
            bot._run_candidate_audit_daily_backlog_at_session_close("US")
        helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
