from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import json
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tools.live_preflight import (
    _candidate_consensus_runtime_check,
    _candidate_rvol_clock_check,
    _heartbeat_checks,
    _candidate_actions_live_config_check,
    _config_source_meaning_check,
    _config_checks,
    _kr_live_expansion_guard_check,
    _kr_cap40_confirmation_enforce_check,
    _pathb_kr_exit_policy_check,
    _pathb_cross_run_closed_lifecycle_evidence,
    _pathb_lifecycle_window_check_result,
    _market_session_calendar_check,
    _pathb_market_live_gate_check,
    _pathb_preopen_exit_policy_check,
    _pathb_zone_fill_policy_check,
    _profit_strategy_core_entry_policy_check,
    _profit_strategy_micro_contract_check,
    _runtime_config_drift_check,
    _runtime_config_drift_payload,
    _us_live_strategy_policy_check,
    _us_swing_shadow_runtime_check,
    load_effective_config,
)


class LiveConfigSourceTests(unittest.TestCase):
    def test_us_live_strategy_policy_rejects_reenabling_negative_gap_lane(self) -> None:
        enabled = _us_live_strategy_policy_check(
            {
                "US_MOMENTUM_LIVE_ENABLED": "true",
                "US_GAP_PULLBACK_LIVE_ENABLED": "true",
            },
            "live",
        )
        disabled = _us_live_strategy_policy_check(
            {
                "US_MOMENTUM_LIVE_ENABLED": "true",
                "US_GAP_PULLBACK_LIVE_ENABLED": "false",
            },
            "live",
        )

        self.assertEqual(enabled.status, "FAIL")
        self.assertEqual(disabled.status, "PASS")

    def test_pathb_us_zone_fill_policy_requires_valid_dual_source_enforce(self) -> None:
        config = {
            "base_env": {
                "PATHB_ZONE_FILL_MODE_US": "enforce_wait",
                "PATHB_ZONE_FILL_TOP_THRESHOLD": "0.67",
                "PATHB_ZONE_FILL_REWARD_PCT": "5.0",
            },
            "overrides": {
                "PATHB_ZONE_FILL_MODE_US": "enforce_wait",
                "PATHB_ZONE_FILL_TOP_THRESHOLD": "0.67",
                "PATHB_ZONE_FILL_REWARD_PCT": "5.0",
            },
            "effective": {
                "PATHB_ZONE_FILL_MODE_US": "enforce_wait",
                "PATHB_ZONE_FILL_TOP_THRESHOLD": "0.67",
                "PATHB_ZONE_FILL_REWARD_PCT": "5.0",
            },
        }

        check = _pathb_zone_fill_policy_check(config, "live")

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["market_scope"], "US_only")
        self.assertFalse(check.data["kr_affected"])

    def test_pathb_us_zone_fill_policy_fails_on_single_source_or_invalid_threshold(self) -> None:
        config = {
            "base_env": {"PATHB_ZONE_FILL_MODE_US": "enforce_wait"},
            "overrides": {"PATHB_ZONE_FILL_MODE_US": "enforce_wait"},
            "effective": {
                "PATHB_ZONE_FILL_MODE_US": "enforce_wait",
                "PATHB_ZONE_FILL_TOP_THRESHOLD": "1.2",
                "PATHB_ZONE_FILL_REWARD_PCT": "5.0",
            },
        }

        check = _pathb_zone_fill_policy_check(config, "live")

        self.assertEqual(check.status, "FAIL")
        self.assertIn("top threshold", check.detail)
        self.assertIn("dual-source", check.detail)

    def test_candidate_rvol_clock_detects_us_naive_kst_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    runtime_mode TEXT,
                    market TEXT,
                    session_date TEXT,
                    known_at TEXT,
                    post_open_features_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO audit_candidate_rows VALUES (?,?,?,?,?)",
                (
                    "live",
                    "US",
                    "2026-07-16",
                    "2026-07-16T22:36:33",
                    json.dumps({"rvol_profile_elapsed_min": 786}),
                ),
            )
            conn.commit()
            conn.close()

            with patch("tools.live_preflight.get_runtime_path", return_value=db_path):
                check = _candidate_rvol_clock_check("live")

        self.assertEqual(check.status, "WARN")
        self.assertEqual(check.data["violations"][0]["expected_elapsed_min"], 6)
        self.assertEqual(check.data["allowed_clock_delta_min"], 6)

    def test_candidate_rvol_clock_accepts_correct_us_elapsed_minute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    runtime_mode TEXT,
                    market TEXT,
                    session_date TEXT,
                    known_at TEXT,
                    post_open_features_json TEXT
                )
                """
            )
            conn.execute(
                "INSERT INTO audit_candidate_rows VALUES (?,?,?,?,?)",
                (
                    "live",
                    "US",
                    "2026-07-16",
                    "2026-07-16T22:36:33",
                    json.dumps({"rvol_profile_elapsed_min": 6}),
                ),
            )
            conn.commit()
            conn.close()

            with patch("tools.live_preflight.get_runtime_path", return_value=db_path):
                check = _candidate_rvol_clock_check("live")

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["checked_by_market"]["US"], 1)

    def test_candidate_rvol_clock_uses_latest_capture_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    runtime_mode TEXT,
                    market TEXT,
                    session_date TEXT,
                    known_at TEXT,
                    post_open_features_json TEXT
                )
                """
            )
            conn.executemany(
                "INSERT INTO audit_candidate_rows VALUES (?,?,?,?,?)",
                [
                    (
                        "live",
                        "US",
                        "2026-07-16",
                        "2026-07-16T22:33:00",
                        json.dumps({"rvol_profile_elapsed_min": 783}),
                    ),
                    (
                        "live",
                        "US",
                        "2026-07-16",
                        "2026-07-16T22:36:00",
                        json.dumps({"rvol_profile_elapsed_min": 6}),
                    ),
                ],
            )
            conn.commit()
            conn.close()

            with patch("tools.live_preflight.get_runtime_path", return_value=db_path):
                check = _candidate_rvol_clock_check("live")

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["checked_by_market"]["US"], 1)
        self.assertEqual(
            check.data["latest_capture"]["US"],
            "2026-07-16T22:36:00",
        )

    def test_candidate_consensus_runtime_uses_scheduler_interpreter_dependencies(self) -> None:
        probe = SimpleNamespace(returncode=0, stdout='{"sklearn":"1.9.0"}', stderr="")
        with patch("tools.live_preflight.subprocess.run", return_value=probe) as run_mock:
            result = _candidate_consensus_runtime_check(
                {"CANDIDATE_CONSENSUS_SHADOW_ENABLED": "true"}
            )

        self.assertEqual(result.status, "PASS")
        self.assertEqual(
            run_mock.call_args.args[0][0],
            str(Path(sys.executable)),
        )

        failed_probe = SimpleNamespace(returncode=1, stdout="", stderr="missing sklearn")
        with patch("tools.live_preflight.subprocess.run", return_value=failed_probe):
            failed = _candidate_consensus_runtime_check(
                {"CANDIDATE_CONSENSUS_SHADOW_ENABLED": "true"}
            )
        self.assertEqual(failed.status, "FAIL")

    def test_us_swing_runtime_requires_consistent_authority_and_dependencies(self) -> None:
        effective = {
            "US_SWING_SHADOW_SCHEDULER_ENABLED": "true",
            "US_SWING_AUTHORITY_MODE": "shadow",
            "US_SWING_ORDER_HANDOFF_ENABLED": "false",
            "US_SWING_ORDER_SUBMIT_ENABLED": "false",
            "US_SWING_ORDER_LIVE_ACK": "",
            "US_SWING_PYTHON_EXECUTABLE": "C:/research/python.exe",
        }
        probe = SimpleNamespace(returncode=0, stdout='{"sklearn":"1.0"}', stderr="")
        with patch("tools.live_preflight.Path.is_file", return_value=True), patch(
            "tools.live_preflight.subprocess.run", return_value=probe
        ):
            result = _us_swing_shadow_runtime_check(effective)
        self.assertEqual(result.status, "PASS")

        unsafe = {**effective, "US_SWING_ORDER_SUBMIT_ENABLED": "true"}
        self.assertEqual(_us_swing_shadow_runtime_check(unsafe).status, "FAIL")

        micro = {
            **effective,
            "US_SWING_AUTHORITY_MODE": "micro",
            "US_SWING_ORDER_HANDOFF_ENABLED": "true",
            "US_SWING_ORDER_SUBMIT_ENABLED": "true",
            "US_SWING_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_US_SWING",
            "US_SWING_OPERATOR_MICRO_OVERRIDE_ACK": "I_ACCEPT_MICRO_WITHOUT_FORWARD",
        }
        with patch("tools.live_preflight.Path.is_file", return_value=True), patch(
            "tools.live_preflight.subprocess.run", return_value=probe
        ):
            self.assertEqual(_us_swing_shadow_runtime_check(micro).status, "PASS")

    def test_kr_exit_policy_requires_dual_source_and_blocks_unwired_enforce(self) -> None:
        base = {
            "base_env": {"PATHB_KR_EXIT_POLICY": "EARLY_FULL_V1"},
            "overrides": {"PATHB_KR_EXIT_POLICY": "EARLY_FULL_V1"},
            "effective": {"PATHB_KR_EXIT_POLICY": "EARLY_FULL_V1"},
        }
        self.assertEqual(_pathb_kr_exit_policy_check(base, "live").status, "PASS")

        mismatch = {**base, "overrides": {"PATHB_KR_EXIT_POLICY": "SPLIT_RUNNER_V1"}}
        self.assertEqual(_pathb_kr_exit_policy_check(mismatch, "live").status, "FAIL")

        split = {
            "base_env": {"PATHB_KR_EXIT_POLICY": "SPLIT_RUNNER_V1"},
            "overrides": {"PATHB_KR_EXIT_POLICY": "SPLIT_RUNNER_V1"},
            "effective": {"PATHB_KR_EXIT_POLICY": "SPLIT_RUNNER_V1"},
        }
        result = _pathb_kr_exit_policy_check(split, "live")
        self.assertEqual(result.status, "FAIL")
        self.assertIn("requires live partial-sell wiring", result.detail)

        split["effective"].update({
            "PATHB_KR_SPLIT_RUNNER_LIVE_ENABLED": "true",
            "PATHB_KR_SPLIT_RUNNER_LIVE_ACK": "I_ACCEPT_LIVE_KR_SPLIT_RUNNER",
        })
        self.assertEqual(_pathb_kr_exit_policy_check(split, "live").status, "PASS")

    def test_profit_strategy_contract_allows_only_approved_micro_arms(self) -> None:
        effective = {
            "PROFIT_STRATEGY_MATERIALIZER_ENABLED": "true",
            "PROFIT_STRATEGY_AUTHORITY_MODE": "micro",
            "PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED": "true",
            "PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED": "true",
            "PROFIT_STRATEGY_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_PROFIT_STRATEGIES",
            "PROFIT_STRATEGY_KILL_SWITCH": "false",
            "PROFIT_STRATEGY_ENABLED_IDS": "US_SCHG_BIL_TREND_V1,KR_FACTOR_TREND_V1",
            "PROFIT_STRATEGY_MAX_ORDER_KRW_KR": "100000",
            "PROFIT_STRATEGY_MAX_ORDER_KRW_US": "300000",
            "PROFIT_STRATEGY_MAX_NEW_PER_DAY_KR": "2",
            "PROFIT_STRATEGY_MAX_NEW_PER_DAY_US": "1",
            "PROFIT_STRATEGY_MAX_OPEN_SLOTS": "4",
        }
        self.assertEqual(_profit_strategy_micro_contract_check(effective, "live").status, "PASS")
        effective["PROFIT_STRATEGY_ENABLED_IDS"] += ",US_CONSENSUS_3D_V1"
        self.assertEqual(_profit_strategy_micro_contract_check(effective, "live").status, "FAIL")

    def test_core_analyst_entry_isolation_requires_dual_source_and_exact_ack(self) -> None:
        exact_ack = "I_ACCEPT_CORE_ANALYST_DIRECTION_ISOLATION"
        config = {
            "base_env": {
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_POLICY": "isolated",
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_LIVE_ACK": exact_ack,
            },
            "overrides": {
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_POLICY": "isolated",
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_LIVE_ACK": exact_ack,
            },
            "effective": {
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_POLICY": "isolated",
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_LIVE_ACK": exact_ack,
            },
        }
        self.assertEqual(_profit_strategy_core_entry_policy_check(config, "live").status, "PASS")

        missing_env = {
            **config,
            "base_env": {"PROFIT_STRATEGY_CORE_ANALYST_ENTRY_POLICY": "observe"},
        }
        self.assertEqual(
            _profit_strategy_core_entry_policy_check(missing_env, "live").status,
            "FAIL",
        )

        bad_ack = {
            **config,
            "overrides": {
                **config["overrides"],
                "PROFIT_STRATEGY_CORE_ANALYST_ENTRY_LIVE_ACK": "wrong",
            },
        }
        self.assertEqual(_profit_strategy_core_entry_policy_check(bad_ack, "live").status, "FAIL")

    def test_profit_strategy_contract_rejects_above_operator_approved_market_caps(self) -> None:
        effective = {
            "PROFIT_STRATEGY_MATERIALIZER_ENABLED": "true",
            "PROFIT_STRATEGY_AUTHORITY_MODE": "micro",
            "PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED": "true",
            "PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED": "true",
            "PROFIT_STRATEGY_ORDER_LIVE_ACK": "I_ACCEPT_LIVE_PROFIT_STRATEGIES",
            "PROFIT_STRATEGY_KILL_SWITCH": "false",
            "PROFIT_STRATEGY_ENABLED_IDS": "US_SCHG_BIL_TREND_V1,KR_FACTOR_TREND_V1",
            "PROFIT_STRATEGY_MAX_ORDER_KRW_KR": "100000",
            "PROFIT_STRATEGY_MAX_ORDER_KRW_US": "300001",
            "PROFIT_STRATEGY_MAX_NEW_PER_DAY_KR": "2",
            "PROFIT_STRATEGY_MAX_NEW_PER_DAY_US": "1",
            "PROFIT_STRATEGY_MAX_OPEN_SLOTS": "4",
        }
        self.assertEqual(_profit_strategy_micro_contract_check(effective, "live").status, "FAIL")
        effective["PROFIT_STRATEGY_MAX_ORDER_KRW_US"] = "300000"
        effective["PROFIT_STRATEGY_MAX_ORDER_KRW_KR"] = "100001"
        self.assertEqual(_profit_strategy_micro_contract_check(effective, "live").status, "FAIL")

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
        # 2026-07-22 운영자 위임 판단으로 US만 40 -> 5. 근거(국면게이트 통과 건, 선착순 시뮬):
        #   상한 없음 +2.22% / 4건 +26.17% / 5건 +28.73% / 6건 +20.67% / 3건 -9.57%(과도)
        #   세션당 7건 이상 구간이 거래 95건에 -44.65%.
        # 외부 독립 표본(404신호/56세션)에서도 상한 5가 최적(+0.2448%p)으로 재현됐다.
        #   단 메커니즘은 "많이 사면 나쁘다"가 아니라 "8번째 이후 신호가 나쁘다"다.
        # KR은 표본 n=38로 판정 불가라 40을 유지한다. 롤백: US_DAILY_ENTRY_CAP=40.
        self.assertEqual(effective.get("US_DAILY_ENTRY_CAP"), "5")
        self.assertEqual(effective.get("PATHB_MAX_POSITIONS"), "15")
        self.assertEqual(effective.get("PATHB_MAX_DAILY_ENTRIES"), "40")
        # 2026-06-11 운영자 변경: 고가주 병목 완화 (70만 → 100만)
        self.assertEqual(effective.get("PATHB_ONE_SHARE_OVER_BUDGET_MAX_KRW"), "1000000")
        # 2026-06-11 운영자 결정: 신규 게이트 체계 하 KR PathB 재개
        self.assertEqual(effective.get("PATHB_KR_LIVE_ENABLED"), "true")
        self.assertEqual(effective.get("KR_CLAUDE_PRICE_LIVE_ENABLED"), "false")
        self.assertEqual(effective.get("KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK"), "false")
        self.assertEqual(effective.get("KR_CONTINUATION_NEW_ENTRY_BLOCK"), "true")
        self.assertEqual(effective.get("PATHB_US_LIVE_ENABLED"), "true")
        self.assertEqual(effective.get("PATHB_INTRADAY_ONLY"), "false")
        self.assertEqual(effective.get("US_PATHB_PREOPEN_EXIT_POLICY_MODE"), "enforce")
        self.assertEqual(effective.get("KR_PATHB_PREOPEN_EXIT_POLICY_MODE"), "enforce")
        self.assertEqual(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US"), "enforce")
        self.assertEqual(effective.get("PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR"), "enforce")
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

    def test_config_source_meaning_describes_daily_loss_position_caps_and_preopen_policy(self) -> None:
        config = {
            "env_path": ".env.live",
            "start_config_path": "config/v2_start_config.json",
            "base_env": {"MAX_DAILY_LOSS_PCT": "-8.0", "MAX_POSITIONS": "10"},
            "overrides": {
                "DAILY_LOSS_LIMIT_PCT": "-2.0",
                "PATHB_MAX_POSITIONS": "15",
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "enforce",
                "US_PATHB_SELECTION_RECONCILE_MODE": "enforce",
                "KR_PATHB_SELECTION_RECONCILE_MODE": "enforce",
            },
            "start_config": {"env_overrides": {"DAILY_LOSS_LIMIT_PCT": "-2.0"}},
            "effective": {
                "MAX_DAILY_LOSS_PCT": "-8.0",
                "DAILY_LOSS_LIMIT_PCT": "-2.0",
                "MAX_POSITIONS": "10",
                "PATHB_MAX_POSITIONS": "15",
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "enforce",
                "US_PATHB_SELECTION_RECONCILE_MODE": "enforce",
                "KR_PATHB_SELECTION_RECONCILE_MODE": "enforce",
            },
        }

        check = _config_source_meaning_check(config)

        self.assertEqual(check.status, "PASS")
        keys = check.data["keys"]
        self.assertEqual(keys["MAX_DAILY_LOSS_PCT"]["source"], "env_file")
        self.assertEqual(keys["DAILY_LOSS_LIMIT_PCT"]["source"], "v2_start_config.env_overrides")
        self.assertIn("PathB", keys["PATHB_MAX_POSITIONS"]["used_by"])
        self.assertEqual(keys["US_PATHB_PREOPEN_EXIT_POLICY_MODE"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(keys["KR_PATHB_PREOPEN_EXIT_POLICY_MODE"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(keys["PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(keys["PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(keys["KR_PATHB_SELECTION_RECONCILE_MODE"]["source"], "v2_start_config.env_overrides")
        self.assertIn("enforce can cancel stale WAITING plans", keys["KR_PATHB_SELECTION_RECONCILE_MODE"]["meaning"])
        self.assertNotIn("shadow logs", keys["KR_PATHB_SELECTION_RECONCILE_MODE"]["meaning"])
        self.assertIn("KR does not inherit", keys["PATHB_PREOPEN_EXIT_POLICY_MODE"]["meaning"])
        self.assertFalse(check.data["config_change_allowed"])

    def test_pathb_preopen_exit_policy_check_exposes_market_modes_and_source(self) -> None:
        config = {
            "env_path": ".env.live",
            "start_config_path": "config/v2_start_config.json",
            "base_env": {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce"},
            "overrides": {
                "PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "enforce",
            },
            "start_config": {},
            "effective": {
                "PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "enforce",
            },
        }

        check = _pathb_preopen_exit_policy_check(config)

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["effective_modes"], {"US": "enforce", "KR": "enforce"})
        self.assertEqual(check.data["source_by_market"]["US"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(check.data["source_by_market"]["KR"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(check.data["expected_source_by_market"]["US"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(check.data["expected_source_by_market"]["KR"]["source"], "v2_start_config.env_overrides")
        self.assertEqual(check.data["expected_policy"], {"US": "enforce", "KR": "enforce"})
        self.assertEqual(check.data["source_of_truth"], "config/v2_start_config.json env_overrides for live mode")

    def test_pathb_preopen_exit_policy_check_uses_expected_config_values(self) -> None:
        config = {
            "env_path": ".env.live",
            "start_config_path": "config/v2_start_config.json",
            "base_env": {"PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce"},
            "overrides": {
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "shadow",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "shadow",
            },
            "start_config": {},
            "effective": {
                "PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "US_PATHB_PREOPEN_EXIT_POLICY_MODE": "enforce",
                "KR_PATHB_PREOPEN_EXIT_POLICY_MODE": "shadow",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_US": "enforce",
                "PATHB_PREOPEN_EXIT_POLICY_EXPECTED_KR": "shadow",
            },
        }

        check = _pathb_preopen_exit_policy_check(config)

        self.assertEqual(check.status, "PASS")
        self.assertEqual(check.data["effective_modes"], {"US": "enforce", "KR": "shadow"})
        self.assertEqual(check.data["current_policy"], {"US": "enforce", "KR": "shadow"})
        self.assertEqual(check.data["expected_policy"], {"US": "enforce", "KR": "shadow"})

    def test_kr_live_expansion_guard_requires_shadow_probe_before_strategy_flags(self) -> None:
        check = _kr_live_expansion_guard_check(
            {
                "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": "false",
                "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED": "false",
                "KR_PLAN_A_ORP_SIGNAL_ENABLED": "false",
                "PATHB_KR_LIVE_ENABLED": "true",
                "KR_CLAUDE_PRICE_NEW_ENTRY_BLOCK": "false",
            }
        )

        self.assertEqual(check.status, "PASS")
        self.assertFalse(check.data["live_expansion_allowed"])
        self.assertEqual(check.data["minimum_shadow_or_probe"]["fills"], 30)

    def test_kr_live_expansion_guard_warns_when_strategy_flag_enabled(self) -> None:
        check = _kr_live_expansion_guard_check(
            {
                "KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED": "true",
                "KR_PLAN_A_GAP_PULLBACK_SIGNAL_ENABLED": "false",
                "KR_PLAN_A_ORP_SIGNAL_ENABLED": "false",
            }
        )

        self.assertEqual(check.status, "WARN")
        self.assertIn("KR_PLAN_A_MOMENTUM_SIGNAL_ENABLED", check.data["enabled_strategy_flags"])

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
        # 2026-06-11 운영자 결정: KR 재개 — KR-on/US-on 복원
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

        # KR-off는 KR-on 정책 위반 → WARN, legacy 키가 source로 보고돼야 한다
        self.assertEqual(check.status, "WARN")
        self.assertEqual(check.data["values"]["KR"], "false")
        self.assertEqual(check.data["market_live_gate_source"]["KR"]["source_key"], "KR_CLAUDE_PRICE_LIVE_ENABLED")

    def test_pathb_market_live_gate_warns_when_kr_live_is_disabled(self) -> None:
        # KR-off는 현행 KR-on 정책 위반 → WARN으로 드리프트 감지
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

    def test_market_session_calendar_applies_known_kr_holiday_override(self) -> None:
        now = datetime(2026, 7, 17, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))

        with patch("tools.live_preflight._now_kst", return_value=now):
            check = _market_session_calendar_check()

        kr = check.data["sessions"]["KR"]
        self.assertEqual(check.status, "WARN")
        self.assertTrue(kr["raw_is_session"])
        self.assertTrue(kr["known_holiday_override"])
        self.assertFalse(kr["is_session"])

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

    def test_pathb_cross_run_closed_lifecycle_evidence_is_reported(self) -> None:
        rows = [
            {
                "path_run_id": "path_current",
                "market": "US",
                "runtime_mode": "live",
                "session_date": "2026-05-28",
                "ticker": "NBIS",
                "status": "CLOSED",
                "plan_json": (
                    '{"pathb_closed_lifecycle_evidence": {"path_run_id": "path_previous", '
                    '"event_id": 3915, "execution_id": "0031323229"}, '
                    '"exit_execution_id": "0031323229", '
                    '"close_reason": "CLOSED_CLAUDE_PRICE_TARGET"}'
                ),
            }
        ]

        inconsistent = _pathb_cross_run_closed_lifecycle_evidence(rows)

        self.assertEqual(len(inconsistent), 1)
        self.assertEqual(inconsistent[0]["path_run_id"], "path_current")
        self.assertEqual(inconsistent[0]["evidence_path_run_id"], "path_previous")
        self.assertEqual(inconsistent[0]["evidence_event_id"], 3915)

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

    def test_stopped_process_runtime_drift_is_startup_warning_not_deadlock(self) -> None:
        config = {"effective": {"PATHB_KR_EXIT_POLICY": "SPLIT_RUNNER_V1"}}
        snapshot = {"written_at": "2026-07-15T10:00:00+09:00", "effective": {"PATHB_KR_EXIT_POLICY": "EARLY_FULL_V1"}}
        with patch("tools.live_preflight._latest_runtime_config_snapshot", return_value=(Path("old.json"), snapshot)), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={"pid_path": "live.pid", "pid": 0, "pid_alive": False, "pid_started_at": ""},
        ):
            result = _runtime_config_drift_check(config, "live")
        self.assertEqual(result.status, "WARN")
        self.assertIn("post-start verification required", result.detail)

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
        ), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={"pid_path": "live.pid", "pid": 123, "pid_alive": True, "pid_started_at": ""},
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

    def test_runtime_config_drift_treats_same_second_snapshot_as_fresh(self) -> None:
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
                "pid_started_at": "2026-05-21T12:00:00.334146+09:00",
                "pid_alive": True,
            },
        ):
            check = _runtime_config_drift_check(config, "live")

        self.assertEqual(check.status, "PASS")
        self.assertTrue(check.data["snapshot_fresh_for_process"])

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
        ), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={"pid_path": "live.pid", "pid": 123, "pid_alive": True, "pid_started_at": ""},
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
        ), patch(
            "tools.live_preflight._runtime_pid_state",
            return_value={"pid_path": "live.pid", "pid": 123, "pid_alive": True, "pid_started_at": ""},
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
            [
                "runtime.paper_guardian_heartbeat",
                "runtime.paper_preopen_scheduler_heartbeat",
                "runtime.paper_broker_truth_scheduler_heartbeat",
            ],
        )
        self.assertIn("paper_guardian_heartbeat.json", checks[0].path)
        self.assertIn("paper_preopen_scheduler_heartbeat.json", checks[1].path)
        self.assertIn("paper_broker_truth_scheduler_heartbeat.json", checks[2].path)
        self.assertEqual(checks[0].kwargs["process"], "paper_guardian")
        self.assertEqual(checks[1].kwargs["process"], "paper_preopen_scheduler")
        self.assertEqual(checks[2].kwargs["process"], "paper_broker_truth_scheduler")

    def test_live_optional_heartbeat_checks_use_effective_config(self) -> None:
        with patch(
            "tools.live_preflight._heartbeat_check",
            side_effect=lambda name, path, **kwargs: SimpleNamespace(
                name=name,
                path=str(path),
                kwargs=kwargs,
            ),
        ):
            checks = _heartbeat_checks(
                "live",
                {
                    "CANDIDATE_CONSENSUS_SHADOW_ENABLED": "true",
                    "KR_DISCLOSURE_OBSERVER_ENABLED": "true",
                    "ENABLED_MARKETS": "KR,US",
                },
            )

        names = [check.name for check in checks]
        self.assertIn("runtime.candidate_consensus_shadow_status", names)
        self.assertIn("runtime.candidate_consensus_outcome_status", names)
        self.assertIn("runtime.candidate_consensus_shadow_status_KR", names)
        self.assertIn("runtime.candidate_consensus_outcome_status_KR", names)
        self.assertIn("runtime.candidate_consensus_shadow_status_US", names)
        self.assertIn("runtime.candidate_consensus_outcome_status_US", names)
        self.assertIn("runtime.kr_disclosure_observer_status", names)


if __name__ == "__main__":
    unittest.main()
