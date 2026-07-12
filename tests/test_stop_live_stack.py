from __future__ import annotations

import unittest
from pathlib import Path

from tools import stop_live_stack


class StopLiveStackMatchTests(unittest.TestCase):
    ROOT = Path(r"E:\code\claudetrade")

    def test_headless_relative_cmdline_is_matched_by_cwd(self) -> None:
        # headless watchdog은 -WorkingDirectory $Root + 상대경로로 띄운다.
        # CommandLine에 프로젝트 경로가 없어서 문자열 매칭만으로는 못 잡던 케이스.
        role = stop_live_stack.match_role(
            r'"C:\Users\Unknown\anaconda3\envs\upbit\python.exe" trading_bot.py --live',
            r"E:\code\claudetrade",
            self.ROOT,
        )
        self.assertEqual(role, "live_bot")

    def test_wt_tab_relative_cmdline_is_matched_by_cwd(self) -> None:
        role = stop_live_stack.match_role(
            "python tools\\run_counterfactual_pipeline.py --phase due --market KR,US --loop",
            r"E:\code\claudetrade",
            self.ROOT,
        )
        self.assertEqual(role, "counterfactual_pipeline")

    def test_absolute_cmdline_without_project_cwd_is_matched(self) -> None:
        role = stop_live_stack.match_role(
            r"python E:\code\claudetrade\dashboard\dashboard_server.py",
            r"C:\Windows\System32",
            self.ROOT,
        )
        self.assertEqual(role, "dashboard")

    def test_other_checkout_is_not_matched(self) -> None:
        # 같은 스크립트명이라도 다른 체크아웃이면 건드리지 않는다.
        role = stop_live_stack.match_role(
            "python trading_bot.py --live",
            r"D:\other\repo",
            self.ROOT,
        )
        self.assertIsNone(role)

    def test_unrelated_python_process_is_not_matched(self) -> None:
        role = stop_live_stack.match_role(
            "python -m instiwatch poll",
            r"E:\code\claudetrade",
            self.ROOT,
        )
        self.assertIsNone(role)


if __name__ == "__main__":
    unittest.main()
