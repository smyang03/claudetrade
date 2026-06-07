from __future__ import annotations

import unittest

from tools.live_preflight import _classify_live_process_inventory, _classify_repo_process_role


class LiveProcessInventoryTests(unittest.TestCase):
    def test_live_mode_warns_on_paper_bot_and_legacy_dashboard(self) -> None:
        rows = [
            {"pid": 10, "role": "live_bot"},
            {"pid": 11, "role": "paper_bot"},
            {"pid": 12, "role": "dashboard"},
        ]

        findings = _classify_live_process_inventory(
            rows,
            {12: [5001]},
            mode="live",
            bot_lock={"pid": 10, "alive": True},
            dashboard_lock={"pid": 12, "alive": True},
        )

        codes = {item["code"] for item in findings}
        self.assertIn("paper_bot_concurrent_with_live", codes)
        self.assertIn("legacy_dashboard_port_5001", codes)
        self.assertFalse(any(item["severity"] == "FAIL" for item in findings))

    def test_duplicate_live_bot_is_fail(self) -> None:
        findings = _classify_live_process_inventory(
            [{"pid": 10, "role": "live_bot"}, {"pid": 11, "role": "live_bot"}],
            {},
            mode="live",
        )

        self.assertEqual(findings[0]["code"], "duplicate_live_bot")
        self.assertEqual(findings[0]["severity"], "FAIL")

    def test_duplicate_paper_bot_is_fail_in_paper_mode(self) -> None:
        findings = _classify_live_process_inventory(
            [{"pid": 10, "role": "paper_bot"}, {"pid": 11, "role": "paper_bot"}],
            {},
            mode="paper",
        )

        self.assertEqual(findings[0]["code"], "duplicate_paper_bot")
        self.assertEqual(findings[0]["severity"], "FAIL")

    def test_trading_bot_without_live_flag_is_classified_as_paper(self) -> None:
        self.assertEqual(_classify_repo_process_role(["python", "trading_bot.py"]), "paper_bot")
        self.assertEqual(_classify_repo_process_role(["python", "trading_bot.py", "--paper"]), "paper_bot")
        self.assertEqual(_classify_repo_process_role(["python", "trading_bot.py", "--live"]), "live_bot")

    def test_live_stack_sidecars_are_classified_by_role(self) -> None:
        self.assertEqual(
            _classify_repo_process_role(["python", "tools/broker_truth_scheduler.py", "--mode", "live"]),
            "broker_truth_scheduler",
        )
        self.assertEqual(
            _classify_repo_process_role(["python", "tools/run_counterfactual_pipeline.py"]),
            "counterfactual_pipeline",
        )


if __name__ == "__main__":
    unittest.main()
