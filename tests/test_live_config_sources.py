from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tools.live_preflight import (
    _heartbeat_checks,
    _candidate_actions_live_config_check,
    _config_checks,
    _kr_cap40_confirmation_enforce_check,
    _pathb_lifecycle_window_check_result,
    _market_session_calendar_check,
    _pathb_market_live_gate_check,
    _runtime_config_drift_check,
    _runtime_config_drift_payload,
    load_effective_config,
)


class LiveConfigSourceTests(unittest.TestCase):
    def test_live_effective_config_has_no_unapproved_conflicts(self) -> None:
        expected = load_effective_config("live")
        snapshot = {
            "written_at": "test",
            "runtime_mode": "live",
            "effective": expected["effective"],
        }
        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ):
            checks, config = _config_checks("live", allow_config_conflicts=False)
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])
        effective = config["effective"]
        order_values = {
            key: int(str(effective.get(key) or "0"))
            for key in (
                "MAX_ORDER_KRW",
                "KR_FIXED_ORDER_KRW",
                "US_FIXED_ORDER_KRW",
                "PATHB_FIXED_ORDER_KRW",
            )
        }
        self.assertEqual(len(set(order_values.values())), 1)
        self.assertGreaterEqual(order_values["MAX_ORDER_KRW"], 50000)
        self.assertLessEqual(order_values["MAX_ORDER_KRW"], 5000000)
        self.assertEqual(effective.get("KR_MAX_POSITIONS"), "20")
        self.assertEqual(effective.get("US_MAX_POSITIONS"), "20")
        self.assertEqual(effective.get("V2_MAX_DAILY_ENTRIES"), "40")
        self.assertEqual(effective.get("KR_DAILY_ENTRY_CAP"), "40")
        self.assertEqual(effective.get("US_DAILY_ENTRY_CAP"), "40")
        self.assertEqual(effective.get("PATHB_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("PATHB_MAX_DAILY_ENTRIES"), "40")
        self.assertEqual(effective.get("PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW"), "700000")
        self.assertEqual(effective.get("PATHB_KR_LIVE_ENABLED"), "true")
        self.assertEqual(effective.get("KR_CLAUDE_PRICE_LIVE_ENABLED"), "false")
        self.assertEqual(effective.get("KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK"), "false")
        self.assertEqual(effective.get("KR_CONTINUATION_NEW_ENTRY_BLOCK"), "true")
        self.assertEqual(effective.get("PATHB_US_LIVE_ENABLED"), "true")
        self.assertEqual(effective.get("PATHB_INTRADAY_ONLY"), "false")
        self.assertEqual(effective.get("KR_MAX_SINGLE_LOSS_PCT"), "-2.0")
        self.assertEqual(effective.get("KR_LOSS_CAP_SHADOW_PCT"), "1.5")
        self.assertEqual(effective.get("KR_REENTRY_COOLDOWN_MINUTES"), "60")
        self.assertEqual(effective.get("US_REENTRY_COOLDOWN_MINUTES"), "60")
        checks_by_name = {item.name: item for item in checks}
        self.assertEqual(checks_by_name["config.pathb_intraday_only"].status, "PASS")
        self.assertEqual(checks_by_name["us.pathb_intraday_only"].status, "PASS")

    def test_start_config_overrides_are_visible(self) -> None:
        config = load_effective_config("live")

        self.assertTrue(config["start_config_loaded"])
        self.assertIn("env_overrides", config["start_config"])
        self.assertEqual(config["effective"].get("PATHB_MODE"), "min_size_live")

    def test_kr_cap40_requires_confirmation_enforce(self) -> None:
        check = _kr_cap40_confirmation_enforce_check(
            {
                "KR_DAILY_ENTRY_CAP": "40",
                "KR_CONFIRMATION_GATE_ENABLED": "true",
                "KR_CONFIRMATION_GATE_SHADOW": "true",
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
            }
        )

        self.assertEqual(check.status, "FAIL")
        self.assertTrue(check.data["KR_CONFIRMATION_GATE_SHADOW"])

    def test_kr_cap40_requires_confirmation_enabled(self) -> None:
        check = _kr_cap40_confirmation_enforce_check(
            {
                "KR_DAILY_ENTRY_CAP": "40",
                "KR_CONFIRMATION_GATE_ENABLED": "false",
                "KR_CONFIRMATION_GATE_SHADOW": "false",
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
            }
        )

        self.assertEqual(check.status, "FAIL")
        self.assertFalse(check.data["KR_CONFIRMATION_GATE_ENABLED"])

    def test_kr_cap40_confirmation_enforce_passes_when_gate_enforced(self) -> None:
        check = _kr_cap40_confirmation_enforce_check(
            {
                "KR_DAILY_ENTRY_CAP": "40",
                "KR_CONFIRMATION_GATE_ENABLED": "true",
                "KR_CONFIRMATION_GATE_SHADOW": "false",
                "KR_CONFIRMATION_GATE_MODE": "FAST_TRIGGER_WITH_HARD_VETO",
            }
        )

        self.assertEqual(check.status, "PASS")

    def test_candidate_actions_live_contract_fails_closed_when_disabled(self) -> None:
        check = _candidate_actions_live_config_check(
            {
                "ENABLE_CLAUDE_CANDIDATE_ACTIONS": "true",
                "ENABLE_ACTION_ROUTING": "false",
                "CANDIDATE_ACTIONS_V2_ENABLED": "false",
            },
            "live",
        )

        self.assertEqual(check.status, "FAIL")
        self.assertIn("ENABLE_ACTION_ROUTING", check.data["disabled"])
        self.assertIn("CANDIDATE_ACTIONS_V2_ENABLED", check.data["disabled"])

    def test_pathb_market_live_gate_accepts_current_kr_on_us_on_policy(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "PATHB_KR_LIVE_ENABLED": "true",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        )

        self.assertEqual(check.status, "PASS")
        self.assertTrue(check.data["policy_match"])
        self.assertFalse(check.data["remediation_required"])

    def test_pathb_market_live_gate_reports_primary_legacy_precedence(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "PATHB_KR_LIVE_ENABLED": "true",
                "KR_CLAUDE_PRICE_LIVE_ENABLED": "false",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        )

        detail = check.data["market_live_gate_source"]["KR"]
        self.assertTrue(detail["effective"])
        self.assertEqual(detail["source_key"], "PATHB_KR_LIVE_ENABLED")
        self.assertEqual(detail["legacy_value"], "false")
        self.assertTrue(detail["legacy_shadowed"])

    def test_pathb_market_live_gate_values_follow_legacy_source_when_primary_missing(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "KR_CLAUDE_PRICE_LIVE_ENABLED": "false",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertEqual(check.data["values"]["KR"], "false")
        self.assertEqual(check.data["market_live_gate_source"]["KR"]["source_key"], "KR_CLAUDE_PRICE_LIVE_ENABLED")

    def test_pathb_market_live_gate_warns_when_kr_live_is_disabled(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "PATHB_KR_LIVE_ENABLED": "false",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertFalse(check.data["policy_match"])
        self.assertTrue(check.data["remediation_required"])
        self.assertIn("KR", check.detail)

    def test_pathb_market_live_gate_warns_when_us_live_is_disabled(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "PATHB_KR_LIVE_ENABLED": "true",
                "PATHB_US_LIVE_ENABLED": "false",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertFalse(check.data["policy_match"])
        self.assertTrue(check.data["remediation_required"])
        self.assertIn("US", check.detail)

    def test_pathb_market_live_gate_warns_when_both_markets_are_disabled(self) -> None:
        check = _pathb_market_live_gate_check(
            {
                "PATHB_KR_LIVE_ENABLED": "false",
                "PATHB_US_LIVE_ENABLED": "false",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertFalse(check.data["policy_match"])
        self.assertTrue(check.data["remediation_required"])
        self.assertIn("KR", check.detail)
        self.assertIn("US", check.detail)

    def test_market_session_calendar_uses_exchange_calendars(self) -> None:
        now = datetime(2026, 5, 21, 2, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        with patch("tools.live_preflight._now_kst", return_value=now):
            check = _market_session_calendar_check()

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["calendar_source"], "exchange_calendars")
        self.assertTrue(check.data["sessions"]["KR"]["is_session"])
        self.assertTrue(check.data["sessions"]["US"]["is_session"])

    def test_pathb_lifecycle_pre_run_missing_path_ids_are_not_remediation_warnings(self) -> None:
        check = _pathb_lifecycle_window_check_result(
            [],
            {
                "rows": [{"event_type": "SAFETY_BLOCKED"}],
                "pre_run": [{"event_type": "SAFETY_BLOCKED"}],
                "post_run": [],
                "decision_id_linkable_count": 1,
                "decision_id_unlinkable_count": 0,
            },
        )

        self.assertEqual(check.status, "PASS")
        self.assertTrue(check.data["accepted_exception"])
        self.assertFalse(check.data["remediation_required"])

    def test_pathb_lifecycle_post_run_missing_path_ids_remain_warnings(self) -> None:
        check = _pathb_lifecycle_window_check_result(
            [],
            {
                "rows": [{"event_type": "FILLED"}],
                "pre_run": [],
                "post_run": [{"event_type": "FILLED"}],
                "decision_id_linkable_count": 1,
                "decision_id_unlinkable_count": 0,
            },
        )

        self.assertEqual(check.status, "WARN")
        self.assertFalse(check.data["accepted_exception"])
        self.assertTrue(check.data["remediation_required"])

    def test_runtime_config_drift_payload_compares_operational_caps(self) -> None:
        config = {
            "effective": {
                "US_DAILY_ENTRY_CAP": "40",
                "PATHB_MAX_DAILY_ENTRIES": "40",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        }
        snapshot = {
            "effective": {
                "US_DAILY_ENTRY_CAP": "1",
                "PATHB_MAX_DAILY_ENTRIES": "20",
                "PATHB_US_LIVE_ENABLED": "true",
            }
        }

        drift = _runtime_config_drift_payload(config, snapshot)

        self.assertEqual(drift["US_DAILY_ENTRY_CAP"]["file_effective"], "40")
        self.assertEqual(drift["US_DAILY_ENTRY_CAP"]["runtime_snapshot"], "1")
        self.assertEqual(drift["PATHB_MAX_DAILY_ENTRIES"]["runtime_snapshot"], "20")
        self.assertNotIn("PATHB_US_LIVE_ENABLED", drift)

    def test_runtime_config_drift_payload_ignores_redacted_snapshot_values(self) -> None:
        config = {
            "effective": {
                "CLAUDE_ANALYST_R1_MAX_TOKENS": "700",
                "CLAUDE_ANALYST_R2_MAX_TOKENS": "900",
            }
        }
        snapshot = {
            "effective": {
                "CLAUDE_ANALYST_R1_MAX_TOKENS": "***",
                "CLAUDE_ANALYST_R2_MAX_TOKENS": "90***00",
            }
        }

        drift = _runtime_config_drift_payload(config, snapshot)

        self.assertNotIn("CLAUDE_ANALYST_R1_MAX_TOKENS", drift)
        self.assertNotIn("CLAUDE_ANALYST_R2_MAX_TOKENS", drift)

    def test_runtime_config_drift_check_describes_snapshot_not_process_memory(self) -> None:
        config = {"effective": {"US_DAILY_ENTRY_CAP": "40"}}
        snapshot = {
            "written_at": "2026-05-16T00:00:00+09:00",
            "runtime_mode": "live",
            "effective": {"US_DAILY_ENTRY_CAP": "1"},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "WARN")
        self.assertIn("latest runtime config snapshot differs from files", check.detail)
        self.assertNotIn("running process", check.detail)
        self.assertEqual(check.data["drift"]["US_DAILY_ENTRY_CAP"]["runtime_snapshot"], "1")

    def test_runtime_config_drift_warns_when_snapshot_predates_pid(self) -> None:
        config = {"effective": {"US_DAILY_ENTRY_CAP": "40"}}
        snapshot = {
            "written_at": "2026-05-21T12:00:00+09:00",
            "runtime_mode": "live",
            "effective": {"US_DAILY_ENTRY_CAP": "40"},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={
                "pid_path": "state/live_trading_bot.pid",
                "pid": 100,
                "pid_started_at": "2026-05-21T12:01:00+09:00",
                "pid_alive": True,
            },
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "WARN")
        self.assertFalse(check.data["snapshot_fresh_for_process"])

    def test_runtime_config_drift_warns_when_live_pid_freshness_is_unverifiable(self) -> None:
        config = {"effective": {"US_DAILY_ENTRY_CAP": "40"}}
        snapshot = {
            "written_at": "2026-05-21T12:00:00+09:00",
            "runtime_mode": "live",
            "effective": {"US_DAILY_ENTRY_CAP": "40"},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={
                "pid_path": "state/live_trading_bot.pid",
                "pid": 100,
                "pid_started_at": "",
                "pid_alive": True,
            },
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "WARN")
        self.assertIn("freshness unverifiable", check.detail)
        self.assertIsNone(check.data["snapshot_fresh_for_process"])

    def test_runtime_config_drift_check_fails_for_critical_live_gate_drift(self) -> None:
        config = {"effective": {"PATHB_KR_LIVE_ENABLED": "true"}}
        snapshot = {
            "written_at": "2026-05-21T11:34:38+09:00",
            "runtime_mode": "live",
            "effective": {"PATHB_KR_LIVE_ENABLED": "false"},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "FAIL")
        self.assertIn("critical runtime config snapshot differs", check.detail)
        self.assertIn("PATHB_KR_LIVE_ENABLED", check.data["critical_drift"])
        self.assertIn("restart", check.data["operator_action"])

    def test_runtime_config_drift_check_fails_when_kr_redesign_key_missing_from_snapshot(self) -> None:
        config = {
            "effective": {
                "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": "false",
                "KR_PATHB_BULL_MODE_GATE_SHADOW": "true",
            }
        }
        snapshot = {
            "written_at": "2026-06-01T12:00:00+09:00",
            "runtime_mode": "live",
            "effective": {},
        }

        with patch(
            "tools.live_preflight._latest_runtime_config_snapshot",
            return_value=(Path("effective_config_live.redacted.json"), snapshot),
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "FAIL")
        self.assertIn("KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED", check.data["critical_drift"])
        self.assertIn("KR_PATHB_BULL_MODE_GATE_SHADOW", check.data["drift"])

    def test_heartbeat_checks_use_runtime_mode_specific_names(self) -> None:
        with patch(
            "tools.live_preflight._heartbeat_check",
            side_effect=lambda name, path, **kwargs: SimpleNamespace(
                name=name,
                path=str(path),
                kwargs=kwargs,
            ),
        ):
            checks = _heartbeat_checks("paper")

        self.assertEqual(
            [check.name for check in checks],
            ["runtime.paper_guardian_heartbeat", "runtime.paper_preopen_scheduler_heartbeat"],
        )
        self.assertIn("paper_guardian_heartbeat.json", checks[0].path)
        self.assertIn("paper_preopen_scheduler_heartbeat.json", checks[1].path)
        self.assertEqual(checks[0].kwargs["process"], "paper_guardian")
        self.assertEqual(checks[1].kwargs["process"], "paper_preopen_scheduler")


if __name__ == "__main__":
    unittest.main()
