from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from bot.session_date import KST
from tools import broker_truth_scheduler


def _runtime_path(root: Path):
    def _inner(*parts, make_parents=True):
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


class BrokerTruthSchedulerTests(unittest.TestCase):
    def test_us_postclose_window_uses_previous_session_after_kst_0500(self) -> None:
        now = datetime(2026, 6, 4, 5, 10, tzinfo=KST)

        def fake_open(market: str, session_date: str) -> datetime:
            if session_date == "2026-06-03":
                return datetime(2026, 6, 3, 22, 30, tzinfo=KST)
            return datetime(2026, 6, 4, 22, 30, tzinfo=KST)

        def fake_close(market: str, session_date: str) -> datetime:
            if session_date == "2026-06-03":
                return datetime(2026, 6, 4, 5, 0, tzinfo=KST)
            return datetime(2026, 6, 5, 5, 0, tzinfo=KST)

        with patch("tools.broker_truth_scheduler.is_trading_day", return_value=True), patch(
            "tools.broker_truth_scheduler.regular_open_dt",
            side_effect=fake_open,
        ), patch(
            "tools.broker_truth_scheduler.regular_close_dt",
            side_effect=fake_close,
        ):
            window = broker_truth_scheduler.market_refresh_window(
                "US",
                now_dt=now,
                preopen_min=20,
                postclose_min=15,
            )

        self.assertTrue(window["active"])
        self.assertEqual(window["session_date"], "2026-06-03")

    def test_force_refresh_runs_all_markets(self) -> None:
        now = datetime(2026, 6, 3, 20, 0, tzinfo=KST)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tools.broker_truth_scheduler.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.broker_truth_scheduler.broker_truth_report",
                side_effect=lambda **kwargs: {
                    "ok": True,
                    "market": kwargs["market"],
                    "positions": [],
                    "open_orders": [],
                    "today_fills": [],
                    "stale": False,
                    "missing": False,
                    "error": "",
                },
            ) as report_mock:
                summary = broker_truth_scheduler.run_scheduler_once(
                    mode="live",
                    markets=["KR", "US"],
                    force=True,
                    now_dt=now,
                )

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["due"], 2)
        self.assertEqual(summary["refreshed"], 2)
        self.assertEqual(report_mock.call_count, 2)

    def test_active_window_respects_refresh_interval(self) -> None:
        now = datetime(2026, 6, 3, 22, 20, tzinfo=KST)
        active_window = {"market": "US", "active": True, "reason": "inside_refresh_window"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tools.broker_truth_scheduler.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.broker_truth_scheduler.market_refresh_window",
                return_value=active_window,
            ), patch(
                "tools.broker_truth_scheduler.broker_truth_report",
                return_value={
                    "ok": True,
                    "market": "US",
                    "positions": [],
                    "open_orders": [],
                    "today_fills": [],
                    "stale": False,
                    "missing": False,
                    "error": "",
                },
            ) as report_mock:
                first = broker_truth_scheduler.run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    now_dt=now,
                    refresh_interval_min=10,
                )
                second = broker_truth_scheduler.run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    now_dt=now + timedelta(minutes=5),
                    refresh_interval_min=10,
                )

        self.assertEqual(first["due"], 1)
        self.assertEqual(first["refreshed"], 1)
        self.assertEqual(second["due"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(report_mock.call_count, 1)

    def test_inactive_window_skips_without_force(self) -> None:
        now = datetime(2026, 6, 3, 12, 0, tzinfo=KST)
        inactive_window = {"market": "US", "active": False, "reason": "outside_refresh_window"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("tools.broker_truth_scheduler.get_runtime_path", side_effect=_runtime_path(root)), patch(
                "tools.broker_truth_scheduler.market_refresh_window",
                return_value=inactive_window,
            ), patch("tools.broker_truth_scheduler.broker_truth_report") as report_mock:
                summary = broker_truth_scheduler.run_scheduler_once(
                    mode="live",
                    markets=["US"],
                    now_dt=now,
                    refresh_interval_min=10,
                )

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["due"], 0)
        self.assertEqual(summary["skipped"], 1)
        report_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
