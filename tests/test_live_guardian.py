from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.live_guardian import (
    _apply_auto_fixes,
    _maybe_send_telegram_alert,
    classify_preflight_check,
    run_guardian_once,
)


class LiveGuardianTests(unittest.TestCase):
    def test_code_marker_fail_is_soft(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "broker_truth.atomic_write_marker",
                "status": "FAIL",
                "detail": "marker missing",
                "data": {"category": "code_marker", "guardian_severity": "soft_fail"},
            }
        )

        self.assertEqual(finding.classification, "soft_fail")

    def test_broker_truth_stale_is_hard(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "broker_truth.kr_stale_state",
                "status": "WARN",
                "detail": "KR snapshot stale",
                "data": {"error": "timeout"},
            }
        )

        self.assertEqual(finding.classification, "hard_fail")

    def test_current_session_order_unknown_is_hard_previous_only_is_soft(self) -> None:
        current = classify_preflight_check(
            {
                "name": "db.order_unknown_unresolved",
                "status": "WARN",
                "detail": "unresolved ORDER_UNKNOWN rows=1",
                "data": {"current_session": [{"ticker": "005930"}], "previous_session": []},
            }
        )
        previous = classify_preflight_check(
            {
                "name": "db.order_unknown_unresolved",
                "status": "WARN",
                "detail": "unresolved ORDER_UNKNOWN rows=1",
                "data": {"current_session": [], "previous_session": [{"ticker": "005930"}]},
            }
        )

        self.assertEqual(current.classification, "hard_fail")
        self.assertEqual(previous.classification, "soft_fail")
        self.assertIn("remediation_commands", current.data)
        self.assertIn("tools.reconcile_order_truth", current.data["remediation_commands"][0])

    def test_telegram_fail_is_soft(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "telegram.pathb_commands",
                "status": "FAIL",
                "detail": "V2/Path B Telegram command failures",
                "data": {"failures": {"/health": "broker_truth status missing"}},
            }
        )

        self.assertEqual(finding.classification, "soft_fail")

    def test_kr_cap40_confirmation_preflight_fail_is_hard(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "config.kr_cap40_confirmation_enforce",
                "status": "FAIL",
                "detail": "KR cap 40 requires confirmation enforce mode",
                "data": {"KR_DAILY_ENTRY_CAP": 40, "KR_CONFIRMATION_GATE_SHADOW": True},
            }
        )

        self.assertEqual(finding.classification, "hard_fail")

    def test_stale_pid_is_auto_fixable_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "live_trading_bot.pid"
            path.write_text(json.dumps({"pid": 123456, "mode": "live"}), encoding="utf-8")
            finding = classify_preflight_check(
                {
                    "name": "runtime.bot_pid_lock",
                    "status": "WARN",
                    "detail": "stale pid lock is present",
                    "data": {
                        "category": "runtime_pid_lock",
                        "path": str(path),
                        "pid": 123456,
                        "alive": False,
                        "auto_fix": True,
                    },
                }
            )

            with patch("tools.live_guardian._pid_alive", return_value=False):
                actions = _apply_auto_fixes([finding], markets=["KR"], start_dashboard=False)

            self.assertEqual(finding.classification, "auto_fixable")
            self.assertEqual(actions[0].status, "PASS")
            self.assertFalse(path.exists())

    def test_active_bot_pid_blocks_duplicate_start(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "runtime.bot_pid_lock",
                "status": "WARN",
                "detail": "pid lock is active",
                "data": {
                    "category": "runtime_pid_lock",
                    "path": "state/live_trading_bot.pid",
                    "pid": 1,
                    "alive": True,
                    "auto_fix": False,
                },
            },
            start_bot=True,
        )

        self.assertEqual(finding.classification, "hard_fail")

    def test_accepted_exception_warning_is_not_soft_fail(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "kis.balance_probe",
                "status": "WARN",
                "detail": "read-only balance check delegated",
                "data": {"accepted_exception": True, "remediation_required": False},
            }
        )

        self.assertEqual(finding.classification, "accepted_exception")

    def test_guardian_counts_accepted_exceptions_separately(self) -> None:
        preflight = {
            "ok": True,
            "fail_count": 0,
            "warn_count": 1,
            "checks": [
                {
                    "name": "kis.balance_probe",
                    "status": "WARN",
                    "detail": "read-only balance check delegated",
                    "data": {"accepted_exception": True, "remediation_required": False},
                }
            ],
            "effective_config": {"ENABLED_MARKETS": "KR"},
        }
        smoke = {"ok": True, "results": [{"ok": True, "market": "KR"}]}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.live_guardian.run_preflight",
            return_value=preflight,
        ), patch(
            "tools.live_guardian._run_smoke",
            return_value=smoke,
        ), patch(
            "tools.live_guardian._write_guardian_report",
            return_value=(Path(tmp) / "guardian.json", Path(tmp) / "guardian.md"),
        ):
            report = run_guardian_once(mode="live")

        self.assertEqual(report["counts"]["accepted_exception"], 1)
        self.assertEqual(report["counts"]["soft_fail"], 0)
        self.assertTrue(report["ok"])

    def test_auto_fix_attempts_missing_token_refresh_without_requiring_soft_classification(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "kis.token_file",
                "status": "FAIL",
                "detail": "token file missing",
                "data": {"path": "state/live_kis_token.json"},
            }
        )
        profile = SimpleNamespace(token_file="state/live_kis_token.json")

        with patch("kis_api.get_kis_market_profile", return_value=profile), patch(
            "kis_api.get_access_token",
            return_value="fresh-token",
        ) as token_mock:
            actions = _apply_auto_fixes([finding], markets=["KR", "US"], start_dashboard=False)

        self.assertEqual(finding.classification, "hard_fail")
        self.assertEqual(actions[0].name, "refresh_token")
        self.assertEqual(actions[0].status, "PASS")
        token_mock.assert_called_once_with(force_refresh=True, market="KR")

    def test_guardian_can_suppress_bot_start_by_restart_guard(self) -> None:
        preflight = {
            "ok": True,
            "fail_count": 0,
            "warn_count": 0,
            "checks": [],
            "effective_config": {"ENABLED_MARKETS": "KR"},
        }
        smoke = {"ok": True, "results": [{"ok": True, "market": "KR"}]}

        with tempfile.TemporaryDirectory() as tmp, patch(
            "tools.live_guardian.run_preflight",
            return_value=preflight,
        ), patch(
            "tools.live_guardian._run_smoke",
            return_value=smoke,
        ), patch(
            "tools.live_guardian._write_guardian_report",
            return_value=(Path(tmp) / "guardian.json", Path(tmp) / "guardian.md"),
        ):
            report = run_guardian_once(
                mode="live",
                start_bot=True,
                bot_start_allowed=False,
                bot_start_skip_detail="cooldown",
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["actions"][0]["name"], "start_bot")
        self.assertEqual(report["actions"][0]["status"], "SKIP")
        self.assertEqual(report["actions"][0]["detail"], "cooldown")

    def test_telegram_alert_sends_only_when_problem_state_changes(self) -> None:
        report = {
            "mode": "live",
            "gate": "BLOCK_START",
            "counts": {"hard_fail": 1, "soft_fail": 0},
            "report_paths": {"md": "data/v2_reports/example.md"},
            "findings": [
                {
                    "name": "broker_truth.kr_stale_state",
                    "status": "WARN",
                    "classification": "hard_fail",
                    "detail": "KR snapshot stale",
                    "data": {},
                }
            ],
            "actions": [],
        }

        with tempfile.TemporaryDirectory() as tmp, patch("telegram_reporter.send", return_value=True) as send_mock:
            state_path = Path(tmp) / "alert_state.json"
            first = _maybe_send_telegram_alert(report, state_path=state_path)
            second = _maybe_send_telegram_alert(report, state_path=state_path)

        self.assertEqual(first.status, "PASS")
        self.assertEqual(second.status, "SKIP")
        send_mock.assert_called_once()

    def test_telegram_alert_sends_recovery_after_problem_clears(self) -> None:
        blocked = {
            "mode": "live",
            "gate": "BLOCK_START",
            "counts": {"hard_fail": 1, "soft_fail": 0},
            "report_paths": {"md": "data/v2_reports/blocked.md"},
            "findings": [
                {
                    "name": "network.kis_rest_python_socket.kr",
                    "status": "FAIL",
                    "classification": "hard_fail",
                    "detail": "socket blocked",
                    "data": {},
                }
            ],
            "actions": [],
        }
        recovered = {
            "mode": "live",
            "gate": "ALLOW_START",
            "counts": {"hard_fail": 0, "soft_fail": 0},
            "report_paths": {"md": "data/v2_reports/recovered.md"},
            "findings": [],
            "actions": [],
        }

        with tempfile.TemporaryDirectory() as tmp, patch("telegram_reporter.send", return_value=True) as send_mock:
            state_path = Path(tmp) / "alert_state.json"
            _maybe_send_telegram_alert(blocked, state_path=state_path)
            recovery = _maybe_send_telegram_alert(recovered, state_path=state_path)

        self.assertEqual(recovery.status, "PASS")
        self.assertTrue(recovery.data["recovered"])
        self.assertEqual(send_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
