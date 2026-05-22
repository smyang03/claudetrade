from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.live_guardian import (
    _apply_auto_fixes,
    _alert_state_path,
    _guardian_heartbeat_path,
    _maybe_send_telegram_alert,
    _write_guardian_heartbeat,
    _write_guardian_report,
    classify_preflight_check,
    run_guardian_once,
)


class LiveGuardianTests(unittest.TestCase):
    def test_guardian_runtime_outputs_use_runtime_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch("runtime_paths.get_runtime_root", return_value=Path(tmp)):
            heartbeat = _guardian_heartbeat_path("live")
            _write_guardian_heartbeat("live", status="running")
            alert_state = _alert_state_path("live")
            json_path, md_path = _write_guardian_report(
                {
                    "ok": True,
                    "gate": "ALLOW_START",
                    "mode": "live",
                    "enabled_markets": ["KR"],
                    "counts": {
                        "hard_fail": 0,
                        "soft_fail": 0,
                        "accepted_exception": 0,
                        "auto_fixable": 0,
                        "actions": 0,
                    },
                    "findings": [],
                    "actions": [],
                }
            )

            runtime_root = Path(tmp)
            self.assertEqual(heartbeat, runtime_root / "state" / "live_guardian_heartbeat.json")
            self.assertTrue(heartbeat.exists())
            self.assertEqual(json.loads(heartbeat.read_text(encoding="utf-8"))["status"], "running")
            self.assertEqual(alert_state, runtime_root / "state" / "live_guardian_alert_state.json")
            self.assertEqual(json_path.parent, runtime_root / "data" / "v2_reports")
            self.assertEqual(md_path.parent, runtime_root / "data" / "v2_reports")
            self.assertTrue(json_path.exists())
            self.assertTrue(md_path.exists())

    def test_guardian_run_once_loads_env_before_runtime_paths(self) -> None:
        preflight = {
            "ok": True,
            "fail_count": 0,
            "warn_count": 0,
            "checks": [],
            "effective_config": {"ENABLED_MARKETS": "KR"},
        }
        smoke = {"ok": True, "results": [{"ok": True, "market": "KR"}]}

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime_root = root / "runtime"
            env_path = root / "guardian.env"
            env_path.write_text(f"CLAUDETRADE_RUNTIME_DIR={runtime_root.as_posix()}\n", encoding="utf-8")

            with patch("runtime_paths._RUNTIME_ROOT", None), patch.dict(
                os.environ,
                {"CLAUDETRADE_RUNTIME_DIR": ""},
                clear=False,
            ), patch(
                "tools.live_guardian.run_preflight",
                return_value=preflight,
            ), patch(
                "tools.live_guardian._run_smoke",
                return_value=smoke,
            ):
                report = run_guardian_once(mode="live", env=str(env_path), skip_dashboard=True)
                heartbeat = runtime_root / "state" / "live_guardian_heartbeat.json"

                self.assertTrue(heartbeat.exists())
                self.assertEqual(json.loads(heartbeat.read_text(encoding="utf-8"))["status"], "success")
                self.assertEqual(Path(report["report_paths"]["json"]).parent, runtime_root / "data" / "v2_reports")
                self.assertEqual(_alert_state_path("live"), runtime_root / "state" / "live_guardian_alert_state.json")

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

    def test_previous_session_order_unknown_with_local_exposure_is_hard(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "db.order_unknown_unresolved",
                "status": "WARN",
                "detail": "unresolved ORDER_UNKNOWN rows=1",
                "data": {
                    "current_session": [],
                    "previous_session": [{"ticker": "005930"}],
                    "previous_session_with_local_exposure": [{"ticker": "005930", "local_position_qty": 3}],
                },
            }
        )

        self.assertEqual(finding.classification, "hard_fail")

    def test_pathb_broker_truth_conflict_is_hard(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "db.pathb_broker_truth_conflict",
                "status": "FAIL",
                "detail": "PathB broker truth conflicts=1",
                "data": {"conflicts": [{"ticker": "SOFI", "do_not_start": True}]},
            }
        )

        self.assertEqual(finding.classification, "hard_fail")

    def test_recoverable_still_held_pathb_broker_truth_conflict_is_soft(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "db.pathb_broker_truth_conflict",
                "status": "WARN",
                "detail": "PathB broker truth conflicts=1 blockers=0 recoverable_still_held=1",
                "data": {
                    "conflicts": [
                        {
                            "ticker": "SOFI",
                            "do_not_start": False,
                            "suggested_action": "recover_still_held",
                            "pathb_recoverable_still_held": True,
                        }
                    ]
                },
            }
        )

        self.assertEqual(finding.classification, "soft_fail")

    def test_stale_active_with_local_exposure_is_hard(self) -> None:
        finding = classify_preflight_check(
            {
                "name": "db.pathb_stale_active_runs",
                "status": "WARN",
                "detail": "previous-session active Path B rows=1",
                "data": {
                    "previous_session_with_local_exposure": [{"ticker": "005930", "local_position_qty": 3}],
                    "previous_session_no_local_exposure": [],
                },
            }
        )

        self.assertEqual(finding.classification, "hard_fail")

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
