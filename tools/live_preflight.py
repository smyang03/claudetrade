from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover
    dotenv_values = None  # type: ignore

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None

from bot.session_date import resolve_session_date
from runtime_paths import get_runtime_path

LIVE_CONFIG_KEYS = {
    "ENABLED_MARKETS",
    "KR_FIXED_ORDER_KRW",
    "US_FIXED_ORDER_KRW",
    "KR_MIN_ORDER_KRW",
    "US_MIN_ORDER_KRW",
    "KR_MAX_POSITIONS",
    "US_MAX_POSITIONS",
    "DAILY_LOSS_LIMIT_PCT",
    "V2_MAX_DAILY_ENTRIES",
    "V2_LIFECYCLE_ENABLED",
    "V2_FIXED_SIZING_ENABLED",
    "V2_BRAIN_POLICY",
    "V2_FRESH_BRAIN_START",
    "PATHB_MODE",
    "PATHB_ENABLED",
    "PATHB_KR_LIVE_ENABLED",
    "PATHB_US_LIVE_ENABLED",
    "PATHB_TELEGRAM_CONTROL_ENABLED",
    "PATHB_FIXED_ORDER_KRW",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_MIN_CONFIDENCE",
    "PATHB_INTRADAY_ONLY",
    "PATHB_ALLOW_STOP_LOSS_LOWERING",
    "PATHB_ALLOW_SAME_TICKER_WITH_PATHA",
    "PATHB_ORDER_UNKNOWN_HALTS_ENTRY",
    "PATHB_KR_SLIPPAGE_CAP",
    "PATHB_US_SLIPPAGE_CAP",
    "PATHB_SELL_PARTIAL_WAIT_SEC",
    "PATHB_PRE_CLOSE_MARKET_FALLBACK",
    "PATHB_PRE_CLOSE_TIMEOUT_MINUTES",
    "PATHB_EMERGENCY_DISABLE",
    "KR_REENTRY_COOLDOWN_MINUTES",
    "US_REENTRY_COOLDOWN_MINUTES",
    "USD_KRW_RATE",
}

REQUIRED_TABLE_COLUMNS = {
    "v2_decisions": {
        "decision_id",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "prompt_version",
        "brain_snapshot_id",
        "status",
        "payload_json",
    },
    "lifecycle_events": {
        "event_id",
        "event_type",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "decision_id",
        "execution_id",
        "position_id",
        "reason_code",
        "prompt_version",
        "brain_snapshot_id",
        "payload_json",
        "occurred_at",
    },
    "v2_path_runs": {
        "path_run_id",
        "decision_id",
        "path_type",
        "market",
        "runtime_mode",
        "session_date",
        "ticker",
        "status",
        "plan_json",
        "created_at",
        "updated_at",
    },
    "phase_validation_runs": {"id", "phase", "ok", "qa", "simulation_report", "report_json", "created_at"},
}


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _now_kst() -> datetime:
    return datetime.now(KST) if KST is not None else datetime.now()


def _read_env(path: Path) -> dict[str, str]:
    if dotenv_values is not None:
        raw = dotenv_values(path)
        return {str(k): str(v) for k, v in raw.items() if k and v is not None}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _duplicate_env_keys(path: Path) -> dict[str, list[int]]:
    seen: dict[str, list[int]] = {}
    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")
    for idx, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        match = pattern.match(line)
        if match:
            seen.setdefault(match.group(1), []).append(idx)
    return {key: lines for key, lines in seen.items() if len(lines) > 1}


def _load_start_config(base_env: dict[str, str]) -> tuple[Path, dict[str, Any]]:
    raw = str(base_env.get("V2_START_CONFIG_PATH") or "config/v2_start_config.json")
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        return path, {}
    return path, json.loads(path.read_text(encoding="utf-8"))


def _norm_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def load_effective_config(mode: str) -> dict[str, Any]:
    env_path = ROOT / f".env.{mode}"
    if not env_path.exists():
        env_path = ROOT / ".env"
    base_env = _read_env(env_path) if env_path.exists() else {}
    start_path, start_config = _load_start_config(base_env)
    overrides: dict[str, str] = {}
    disabled = str(base_env.get("V2_START_CONFIG_DISABLED", "")).strip().lower() in {"1", "true", "yes", "y", "on"}
    if mode == "live" and not disabled:
        raw_overrides = start_config.get("env_overrides") or {}
        if isinstance(raw_overrides, dict):
            overrides = {
                str(k): str(v).lower() if isinstance(v, bool) else str(v)
                for k, v in raw_overrides.items()
                if v is not None
            }
    effective = dict(base_env)
    effective.update(overrides)
    return {
        "env_path": str(env_path),
        "start_config_path": str(start_path),
        "start_config_loaded": bool(start_config),
        "start_config_disabled": disabled,
        "base_env": base_env,
        "overrides": overrides,
        "effective": effective,
        "start_config": start_config,
    }


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_value(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return default


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return default


def _repo_text(*parts: str) -> str:
    path = ROOT.joinpath(*parts)
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _session_date_guess(market: str) -> str:
    """Preflight session date using the same boundary rules as TradingBot."""
    now = _now_kst()
    return resolve_session_date(market, now).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            pass
    try:
        if sys.platform.startswith("win"):
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in (result.stdout or "")
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _pid_lock_check(name: str, path: Path, *, expected_mode: str = "") -> CheckResult:
    data: dict[str, Any] = {"path": str(path), "category": "runtime_pid_lock"}
    if not path.exists():
        return CheckResult(name, "PASS", "pid lock file is absent", data)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            data["state"] = "invalid_json_root"
            return CheckResult(name, "WARN", "pid lock file root is not an object", data)
    except Exception as exc:
        data["state"] = "unreadable"
        data["error"] = str(exc)
        return CheckResult(name, "WARN", f"pid lock file unreadable: {exc}", data)

    pid = _int_value(raw.get("pid"), 0)
    alive = _pid_alive(pid)
    data.update({"pid": pid, "alive": alive, "state": raw, "auto_fix": not alive})
    if expected_mode:
        actual_mode = str(raw.get("mode", "") or "")
        data["expected_mode"] = expected_mode
        if actual_mode and actual_mode != expected_mode:
            data["mode_mismatch"] = actual_mode
    if alive:
        return CheckResult(name, "WARN", "pid lock is active; do not start a duplicate process", data)
    return CheckResult(name, "WARN", "stale pid lock is present; guardian may remove it after process check", data)


def _default_kis_base_url(mode: str) -> str:
    return (
        "https://openapivts.koreainvestment.com:29443"
        if str(mode or "").lower() == "paper"
        else "https://openapi.koreainvestment.com:9443"
    )


def _host_port_from_url(raw_url: str, *, default_url: str) -> tuple[str, int, str]:
    raw = str(raw_url or "").strip() or default_url
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = str(parsed.hostname or "").strip()
    if not host:
        fallback = urlparse(default_url)
        host = str(fallback.hostname or "").strip()
    port = int(parsed.port or (443 if parsed.scheme == "https" else 80))
    return host, port, raw


def _kis_socket_check(name: str, raw_url: str, *, mode: str, timeout_sec: float) -> CheckResult:
    host, port, normalized_url = _host_port_from_url(raw_url, default_url=_default_kis_base_url(mode))
    data = {
        "url": normalized_url,
        "host": host,
        "port": port,
        "timeout_sec": timeout_sec,
        "python_executable": sys.executable,
        "powershell_check": f"Test-NetConnection {host} -Port {port}",
        "firewall_allow_rule": (
            f'New-NetFirewallRule -DisplayName "Allow KIS API Python {port}" '
            f'-Direction Outbound -Program "{sys.executable}" '
            f"-Action Allow -Protocol TCP -RemotePort {port}"
        ),
    }
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            pass
        return CheckResult(name, "PASS", f"Python can open TCP connection to {host}:{port}", data)
    except Exception as exc:
        data["error"] = f"{type(exc).__name__}: {exc}"
        return CheckResult(name, "FAIL", f"Python cannot open TCP connection to {host}:{port}", data)


def _kis_network_checks(config: dict[str, Any], mode: str) -> list[CheckResult]:
    effective: dict[str, str] = config.get("effective", {})
    timeout_sec = _float_value(effective.get("KIS_NETWORK_CHECK_TIMEOUT_SEC", "3"), 3.0)
    default_url = _default_kis_base_url(mode)
    kr_url = str(effective.get("KIS_BASE_URL") or default_url)
    us_url = str(effective.get("KIS_BASE_URL_US") or kr_url)
    targets = [("KR", kr_url)]
    if us_url != kr_url:
        targets.append(("US", us_url))
    return [
        _kis_socket_check(f"network.kis_rest_python_socket.{market.lower()}", url, mode=mode, timeout_sec=timeout_sec)
        for market, url in targets
    ]


def _config_checks(mode: str, allow_config_conflicts: bool) -> tuple[list[CheckResult], dict[str, Any]]:
    checks: list[CheckResult] = []
    config = load_effective_config(mode)
    env_path = Path(config["env_path"])
    start_config = config["start_config"]
    base_env: dict[str, str] = config["base_env"]
    overrides: dict[str, str] = config["overrides"]
    effective: dict[str, str] = config["effective"]

    if env_path.exists():
        checks.append(CheckResult("config.env_file", "PASS", f"loaded {env_path}"))
    else:
        checks.append(CheckResult("config.env_file", "FAIL", f"missing env file for mode={mode}", {"path": str(env_path)}))

    duplicates = _duplicate_env_keys(env_path) if env_path.exists() else {}
    checks.append(
        CheckResult(
            "config.duplicate_env_keys",
            "FAIL" if duplicates else "PASS",
            "duplicate keys in env file" if duplicates else "no duplicate env keys",
            {"duplicates": duplicates},
        )
    )

    conflicts = {
        key: {"env": base_env[key], "start_config": overrides[key]}
        for key in sorted(set(base_env) & set(overrides) & LIVE_CONFIG_KEYS)
        if str(base_env[key]) != str(overrides[key])
    }
    checks.append(
        CheckResult(
            "config.env_vs_start_config",
            "WARN" if conflicts and allow_config_conflicts else ("FAIL" if conflicts else "PASS"),
            "start config overrides env values" if conflicts else "no live config conflicts",
            {"conflicts": conflicts},
        )
    )

    internal_conflicts = {}
    for key, value in overrides.items():
        if key in start_config and _norm_config_value(start_config.get(key)) != _norm_config_value(value):
            internal_conflicts[key] = {
                "top_level": _norm_config_value(start_config.get(key)),
                "env_overrides": _norm_config_value(value),
            }
    checks.append(
        CheckResult(
            "config.start_config_internal",
            "WARN" if internal_conflicts and allow_config_conflicts else ("FAIL" if internal_conflicts else "PASS"),
            "v2_start_config top-level values differ from env_overrides" if internal_conflicts else "start config is internally consistent",
            {"conflicts": internal_conflicts},
        )
    )

    important = {key: effective.get(key, "") for key in sorted(LIVE_CONFIG_KEYS) if key in effective}
    checks.append(CheckResult("config.effective_values", "PASS", "effective live values captured", {"values": important}))
    if effective.get("PATHB_INTRADAY_ONLY", "").lower() != "true":
        checks.append(CheckResult("config.pathb_intraday_only", "WARN", "Path B is not forced intraday", {"value": effective.get("PATHB_INTRADAY_ONLY")}))
    else:
        checks.append(CheckResult("config.pathb_intraday_only", "PASS", "Path B intraday-only is enabled"))

    pathb_market_gates = {
        "KR": effective.get("PATHB_KR_LIVE_ENABLED", "true"),
        "US": effective.get("PATHB_US_LIVE_ENABLED", "true"),
    }
    disabled_pathb_markets = [
        market for market, value in pathb_market_gates.items() if not _truthy(value)
    ]
    checks.append(
        CheckResult(
            "config.pathb_market_live_gates",
            "FAIL" if disabled_pathb_markets else "PASS",
            "Path B market live gates disabled: " + ",".join(disabled_pathb_markets)
            if disabled_pathb_markets
            else "Path B market live gates are enabled for KR/US",
            {"values": pathb_market_gates},
        )
    )

    enabled_markets = {
        item.strip().upper()
        for item in str(effective.get("ENABLED_MARKETS", "") or "").split(",")
        if item.strip()
    }
    checks.append(
        CheckResult(
            "config.kr_us_enabled",
            "PASS" if {"KR", "US"}.issubset(enabled_markets) else "FAIL",
            f"ENABLED_MARKETS={','.join(sorted(enabled_markets)) or '-'}",
            {"enabled_markets": sorted(enabled_markets)},
        )
    )

    pathb_values = {
        key: effective.get(key, "")
        for key in (
            "PATHB_ENABLED",
            "PATHB_MODE",
            "PATHB_KR_LIVE_ENABLED",
            "PATHB_US_LIVE_ENABLED",
            "PATHB_MAX_POSITIONS",
            "PATHB_MAX_DAILY_ENTRIES",
            "PATHB_INTRADAY_ONLY",
            "PATHB_EMERGENCY_DISABLE",
            "PATHB_FIXED_ORDER_KRW",
            "PATHB_MIN_CONFIDENCE",
        )
    }
    pathb_failures = []
    if not _truthy(effective.get("PATHB_ENABLED")):
        pathb_failures.append("PATHB_ENABLED is not true")
    if _truthy(effective.get("PATHB_EMERGENCY_DISABLE")):
        pathb_failures.append("PATHB_EMERGENCY_DISABLE is true")
    if _int_value(effective.get("PATHB_MAX_POSITIONS")) <= 0:
        pathb_failures.append("PATHB_MAX_POSITIONS <= 0")
    if _int_value(effective.get("PATHB_MAX_DAILY_ENTRIES")) <= 0:
        pathb_failures.append("PATHB_MAX_DAILY_ENTRIES <= 0")
    checks.append(
        CheckResult(
            "config.pathb_limits",
            "FAIL" if pathb_failures else "PASS",
            "; ".join(pathb_failures) if pathb_failures else "Path B live limits are usable",
            {"values": pathb_values},
        )
    )

    us_budget = _float_value(effective.get("US_FIXED_ORDER_KRW"))
    us_min = _float_value(effective.get("US_MIN_ORDER_KRW"))
    fx = _float_value(effective.get("USD_KRW_RATE"))
    fx_failures = []
    if us_budget <= 0:
        fx_failures.append("US_FIXED_ORDER_KRW <= 0")
    if us_min <= 0:
        fx_failures.append("US_MIN_ORDER_KRW <= 0")
    if fx <= 0:
        fx_failures.append("USD_KRW_RATE <= 0")
    checks.append(
        CheckResult(
            "config.us_sizing_fx",
            "FAIL" if fx_failures else "PASS",
            "; ".join(fx_failures) if fx_failures else "US KRW sizing and FX fallback are present",
            {"US_FIXED_ORDER_KRW": us_budget, "US_MIN_ORDER_KRW": us_min, "USD_KRW_RATE": fx},
        )
    )
    primary_tokens = _int_value(effective.get("CLAUDE_SELECTION_MAX_TOKENS"), 0)
    retry_tokens = _int_value(effective.get("CLAUDE_SELECTION_RETRY_MAX_TOKENS"), 0)
    token_failures = []
    if primary_tokens < 3200:
        token_failures.append("CLAUDE_SELECTION_MAX_TOKENS < 3200")
    if retry_tokens < 1800:
        token_failures.append("CLAUDE_SELECTION_RETRY_MAX_TOKENS < 1800")
    checks.append(
        CheckResult(
            "claude.max_tokens_sufficient",
            "FAIL" if token_failures else "PASS",
            "; ".join(token_failures) if token_failures else "Claude selection token limits are sufficient for price_targets",
            {"CLAUDE_SELECTION_MAX_TOKENS": primary_tokens, "CLAUDE_SELECTION_RETRY_MAX_TOKENS": retry_tokens},
        )
    )
    checks.append(
        CheckResult(
            "us.cash_sizing",
            "PASS" if us_budget > 0 and fx > 0 else "FAIL",
            "US KRW budget can be converted to USD sizing" if us_budget > 0 and fx > 0 else "US cash sizing cannot be computed",
            {"budget_krw": us_budget, "fx": fx, "approx_budget_usd": round(us_budget / fx, 2) if fx > 0 else 0},
        )
    )
    checks.append(
        CheckResult(
            "us.pathb_intraday_only",
            "PASS" if _truthy(effective.get("PATHB_INTRADAY_ONLY")) else "WARN",
            "US Path B follows intraday-only policy via shared config" if _truthy(effective.get("PATHB_INTRADAY_ONLY")) else "Path B intraday-only is disabled",
            {"PATHB_INTRADAY_ONLY": effective.get("PATHB_INTRADAY_ONLY")},
        )
    )

    us_account = str(effective.get("KIS_ACCOUNT_NO_US") or "").strip()
    kr_account = str(effective.get("KIS_ACCOUNT_NO") or "").strip()
    us_app = str(effective.get("KIS_APP_KEY_US") or "").strip()
    kr_app = str(effective.get("KIS_APP_KEY") or "").strip()
    us_secret = str(effective.get("KIS_APP_SECRET_US") or "").strip()
    kr_secret = str(effective.get("KIS_APP_SECRET") or "").strip()
    is_paper_us = str(effective.get("KIS_IS_PAPER_US") or "").strip().lower()
    cred_status = "PASS"
    cred_notes = []
    if not us_account and not kr_account:
        cred_status = "FAIL"
        cred_notes.append("US account missing and no KR account fallback")
    elif not us_account:
        cred_status = "WARN"
        cred_notes.append("KIS_ACCOUNT_NO_US missing; KR account fallback will be used")
    if (not us_app or not us_secret) and (not kr_app or not kr_secret):
        cred_status = "FAIL"
        cred_notes.append("US app key/secret missing and no KR app fallback")
    elif not us_app or not us_secret:
        cred_status = "WARN" if cred_status != "FAIL" else cred_status
        cred_notes.append("US-specific app key/secret missing; common key fallback will be used")
    if is_paper_us == "true" and mode == "live":
        cred_status = "FAIL"
        cred_notes.append("KIS_IS_PAPER_US=true in live mode")
    us_credential_mode = (
        "separate_us"
        if us_account and us_app and us_secret
        else "fallback_shared_kr"
        if kr_account and kr_app and kr_secret
        else "missing"
    )
    checks.append(
        CheckResult(
            "kis.us_credentials",
            cred_status,
            "; ".join(cred_notes) if cred_notes else "US live credentials/fallbacks are explicit",
            {
                "KIS_ACCOUNT_NO_US_present": bool(us_account),
                "KIS_APP_KEY_US_present": bool(us_app),
                "KIS_APP_SECRET_US_present": bool(us_secret),
                "KIS_IS_PAPER_US": is_paper_us,
                "credential_mode": us_credential_mode,
                "fallback_to_kr_allowed": us_credential_mode == "fallback_shared_kr",
            },
        )
    )
    return checks, config


def _db_checks(mode: str = "live") -> list[CheckResult]:
    checks: list[CheckResult] = []
    from lifecycle.event_store import EventStore
    from lifecycle.models import LifecycleEvent

    store = EventStore()
    kr_session = _session_date_guess("KR")
    us_session = _session_date_guess("US")
    current_sessions = {"KR": kr_session, "US": us_session}
    active_statuses = {
        "WAITING",
        "HIT",
        "ORDER_SENT",
        "ORDER_ACKED",
        "PARTIAL_FILLED",
        "FILLED",
        "SELL_SENT",
        "SELL_ACKED",
        "SELL_PARTIAL_FILLED",
        "ORDER_UNKNOWN",
    }
    checks.append(CheckResult("db.live_path", "PASS", "live event store path resolved", {"path": str(store.path)}))
    with store.connect() as conn:
        journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
        checks.append(CheckResult("db.wal_mode", "PASS" if str(journal).lower() == "wal" else "FAIL", f"journal_mode={journal}"))
        schema_missing: dict[str, list[str]] = {}
        for table, required in REQUIRED_TABLE_COLUMNS.items():
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            found = {str(row[1]) for row in rows}
            missing = sorted(required - found)
            if missing:
                schema_missing[table] = missing
            checks.append(
                CheckResult(
                    f"db.schema.{table}",
                    "FAIL" if missing else "PASS",
                    f"missing columns: {missing}" if missing else "required columns present",
                    {"path": str(store.path), "missing": missing},
                )
            )
        checks.append(
            CheckResult(
                "db.event_store_schema",
                "FAIL" if schema_missing else "PASS",
                "schema mismatch found" if schema_missing else "required event-store tables are present",
                {"missing": schema_missing},
            )
        )
        invalid_json = 0
        for row in conn.execute("SELECT path_run_id, plan_json FROM v2_path_runs ORDER BY updated_at DESC LIMIT 200").fetchall():
            try:
                json.loads(row[1] or "{}")
            except Exception:
                invalid_json += 1
        checks.append(CheckResult("db.path_run_json", "FAIL" if invalid_json else "PASS", "recent plan_json decode check", {"invalid_recent_rows": invalid_json}))
        checks.append(CheckResult("db.path_run_plan_json_valid", "FAIL" if invalid_json else "PASS", "recent Path B plan_json rows parse cleanly", {"invalid_recent_rows": invalid_json}))

        unknown_rows = conn.execute(
            """
            SELECT market, runtime_mode, session_date, ticker, path_run_id, status, updated_at
            FROM v2_path_runs
            WHERE runtime_mode=? AND status='ORDER_UNKNOWN'
            ORDER BY updated_at DESC
            LIMIT 50
            """,
            (mode,),
        ).fetchall()
        current_unknown: list[dict[str, Any]] = []
        previous_unknown: list[dict[str, Any]] = []
        for row in unknown_rows:
            item = dict(row)
            session_for_market = current_sessions.get(str(item.get("market") or ""))
            if item.get("session_date") == session_for_market:
                current_unknown.append(item)
            else:
                previous_unknown.append(item)
        checks.append(
            CheckResult(
                "db.order_unknown_unresolved",
                "WARN" if unknown_rows else "PASS",
                f"unresolved ORDER_UNKNOWN rows={len(unknown_rows)}" if unknown_rows else "no unresolved Path B ORDER_UNKNOWN rows",
                {"current_session": current_unknown, "previous_session": previous_unknown},
            )
        )

        stale_rows = conn.execute(
            f"""
            SELECT market, runtime_mode, session_date, ticker, path_run_id, status, updated_at
            FROM v2_path_runs
            WHERE runtime_mode=? AND status IN ({','.join('?' for _ in active_statuses)})
            ORDER BY updated_at DESC
            LIMIT 100
            """,
            (mode, *sorted(active_statuses)),
        ).fetchall()
        stale_active: list[dict[str, Any]] = []
        for row in stale_rows:
            item = dict(row)
            session_for_market = current_sessions.get(str(item.get("market") or ""))
            if item.get("session_date") != session_for_market:
                stale_active.append(item)
        checks.append(
            CheckResult(
                "db.pathb_stale_active_runs",
                "WARN" if stale_active else "PASS",
                f"previous-session active Path B rows={len(stale_active)}" if stale_active else "no previous-session active Path B rows",
                {"rows": stale_active[:30], "current_sessions": current_sessions},
            )
        )

        recent_events = conn.execute(
            """
            SELECT event_type, market, runtime_mode, session_date, ticker, payload_json
            FROM lifecycle_events
            WHERE runtime_mode=?
            ORDER BY event_id DESC
            LIMIT 1000
            """,
            (mode,),
        ).fetchall()
        event_by_path_and_type: set[tuple[str, str]] = set()
        pathb_events_missing_id = []
        invalid_event_market_runtime = []
        for row in recent_events:
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except Exception:
                payload = {}
            path_run_id = str(payload.get("path_run_id") or "").strip()
            event_type = str(row["event_type"] or "")
            if path_run_id:
                event_by_path_and_type.add((path_run_id, event_type))
            path_type = str(payload.get("path_type") or payload.get("buy_path") or "")
            if (event_type.startswith("CLAUDE_PRICE") or path_type in {"claude_price", "path_b"}) and not path_run_id:
                pathb_events_missing_id.append({"event_type": event_type, "market": row["market"], "ticker": row["ticker"]})
            if row["market"] not in {"KR", "US"} or row["runtime_mode"] not in {"live", "paper"}:
                invalid_event_market_runtime.append(
                    {
                        "event_type": event_type,
                        "market": row["market"],
                        "runtime_mode": row["runtime_mode"],
                        "ticker": row["ticker"],
                    }
                )

        recent_runs = conn.execute(
            """
            SELECT path_run_id, market, runtime_mode, session_date, ticker, status
            FROM v2_path_runs
            WHERE runtime_mode=? AND path_type='claude_price'
            ORDER BY updated_at DESC
            LIMIT 500
            """,
            (mode,),
        ).fetchall()
        inconsistent_runs = []
        for row in recent_runs:
            status = str(row["status"] or "")
            if status in {"FILLED", "PARTIAL_FILLED"} and (row["path_run_id"], "FILLED") not in event_by_path_and_type:
                inconsistent_runs.append({**dict(row), "missing_event": "FILLED"})
            if status == "CLOSED" and (row["path_run_id"], "CLOSED") not in event_by_path_and_type:
                inconsistent_runs.append({**dict(row), "missing_event": "CLOSED"})
        checks.append(
            CheckResult(
                "db.pathb_lifecycle_consistency",
                "WARN" if inconsistent_runs or pathb_events_missing_id else "PASS",
                "Path B lifecycle consistency warnings" if inconsistent_runs or pathb_events_missing_id else "recent Path B lifecycle rows are internally consistent",
                {
                    "missing_events": inconsistent_runs[:30],
                    "pathb_events_missing_path_run_id": pathb_events_missing_id[:30],
                },
            )
        )
        checks.append(
            CheckResult(
                "db.market_runtime_isolation",
                "FAIL" if invalid_event_market_runtime else "PASS",
                "invalid market/runtime values found" if invalid_event_market_runtime else "recent events use valid market/runtime values",
                {"invalid": invalid_event_market_runtime[:30]},
            )
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_store = EventStore(Path(tmp) / "events.db")
        tmp_store.create_decision(
            decision_id="preflight_decision",
            market="KR",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker="005930",
            prompt_version="preflight",
            brain_snapshot_id="preflight_brain",
        )
        tmp_store.append(
            LifecycleEvent(
                event_type="CLAUDE_TRADE_READY",
                market="KR",
                runtime_mode="live",
                session_date="2026-04-27",
                ticker="005930",
                decision_id="preflight_decision",
                prompt_version="preflight",
                brain_snapshot_id="preflight_brain",
            )
        )
        tmp_store.create_path_run(
            path_run_id="preflight_path",
            decision_id="preflight_decision",
            path_type="claude_price",
            market="KR",
            runtime_mode="live",
            session_date="2026-04-27",
            ticker="005930",
            status="WAITING",
            plan={"buy_zone_low": 52000, "sell_target": 54500},
        )
        tmp_store.update_path_run("preflight_path", status="FILLED", plan={"filled_qty": 1}, merge_plan=True)
        reopened = EventStore(Path(tmp) / "events.db")
        run = reopened.find_path_run("preflight_path")
        ok = bool(run and run["status"] == "FILLED" and run["plan"].get("buy_zone_low") == 52000 and run["plan"].get("filled_qty") == 1)
        checks.append(CheckResult("db.roundtrip_temp", "PASS" if ok else "FAIL", "temp DB decision/event/path_run round trip"))
    return checks


def _token_checks(mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    token_path = ROOT / "state" / f"{mode}_kis_token.json"
    if not token_path.exists():
        return [
            CheckResult("kis.token_file", "FAIL", "token file missing", {"path": str(token_path)}),
            CheckResult("kis.kr_token_refresh", "FAIL", "token file missing", {"path": str(token_path)}),
            CheckResult("kis.us_token_refresh", "FAIL", "token file missing", {"path": str(token_path)}),
        ]
    try:
        data = json.loads(token_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return [
            CheckResult("kis.token_file", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
            CheckResult("kis.kr_token_refresh", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
            CheckResult("kis.us_token_refresh", "FAIL", f"token file unreadable: {exc}", {"path": str(token_path)}),
        ]
    expires_raw = str(data.get("expires_at", "") or "")
    issued_raw = str(data.get("issued_at", "") or "")
    try:
        expires_at = datetime.fromisoformat(expires_raw)
        now = datetime.now(tz=expires_at.tzinfo) if expires_at.tzinfo else datetime.now()
        minutes_left = (expires_at - now).total_seconds() / 60.0
        status = "FAIL" if minutes_left <= 0 else ("WARN" if minutes_left < 180 else "PASS")
        detail = f"token expires_at={expires_raw}, minutes_left={minutes_left:.1f}"
        checks.append(
            CheckResult(
                "kis.token_expiry",
                status,
                detail,
                {
                    "path": str(token_path),
                    "issued_at": issued_raw,
                    "expires_at": expires_raw,
                    "minutes_left": round(minutes_left, 1),
                    "context": data.get("context", {}),
                },
            )
        )
        trading_text = _repo_text("trading_bot.py")
        helper_ok = "_get_balance_with_token_refresh" in trading_text and bool(
            re.search(r"get_access_token\(\s*force_refresh\s*=\s*True", trading_text)
        )
        refresh_status = "FAIL" if status == "FAIL" or not helper_ok else ("WARN" if status == "WARN" else "PASS")
        refresh_detail = (
            "token expired or refresh helper missing"
            if refresh_status == "FAIL"
            else ("token is near expiry; forced refresh helper is present" if refresh_status == "WARN" else "token valid and forced refresh helper is present")
        )
        checks.append(CheckResult("kis.kr_token_refresh", refresh_status, refresh_detail, {"minutes_left": round(minutes_left, 1), "helper": helper_ok}))
        checks.append(CheckResult("kis.us_token_refresh", refresh_status, refresh_detail, {"minutes_left": round(minutes_left, 1), "helper": helper_ok}))
    except Exception as exc:
        checks.append(CheckResult("kis.token_expiry", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
        checks.append(CheckResult("kis.kr_token_refresh", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
        checks.append(CheckResult("kis.us_token_refresh", "FAIL", f"cannot parse token expiry: {exc}", {"expires_at": expires_raw}))
    checks.append(
        CheckResult(
            "kis.balance_probe",
            "WARN",
            "preflight does not call live balance APIs; verify bot startup API Health Check before live trading",
            {"reason": "network/order side effects intentionally avoided"},
        )
    )
    return checks


def _state_checks(config: dict[str, Any], mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    effective: dict[str, str] = config.get("effective", {})
    checks.append(
        _pid_lock_check(
            "runtime.bot_pid_lock",
            get_runtime_path("state", f"{mode}_trading_bot.pid"),
            expected_mode=mode,
        )
    )
    checks.append(
        _pid_lock_check(
            "runtime.dashboard_pid_lock",
            get_runtime_path("state", "dashboard_server.pid"),
            expected_mode="dashboard_server",
        )
    )
    brain_candidates = [
        ROOT / "state" / "brain.json",
        ROOT / "claude_memory" / "brain.json",
        ROOT / "brain.json",
    ]
    parsed_brains = []
    brain_errors = []
    for path in brain_candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                brain_errors.append({"path": str(path), "error": "root is not object"})
            else:
                parsed_brains.append({"path": str(path), "keys": sorted(data.keys())[:20]})
        except Exception as exc:
            brain_errors.append({"path": str(path), "error": str(exc)})
    fresh_brain = _truthy(effective.get("V2_FRESH_BRAIN_START"))
    if brain_errors:
        status = "FAIL"
        detail = "brain JSON parse/root errors found"
    elif parsed_brains:
        status = "PASS"
        detail = "brain JSON candidates parse cleanly"
    elif fresh_brain:
        status = "PASS"
        detail = "brain JSON missing but fresh V2 brain start is enabled"
    else:
        status = "WARN"
        detail = "brain JSON missing and fresh V2 brain start is not enabled"
    checks.append(
        CheckResult(
            "state.brain_json_valid",
            status,
            detail,
            {"parsed": parsed_brains, "errors": brain_errors, "fresh_brain_start": fresh_brain},
        )
    )

    control_path = ROOT / "state" / f"{mode}_pathb_control.json"
    if not control_path.exists():
        checks.append(
            CheckResult(
                "runtime.pathb_control_state",
                "PASS",
                "Path B runtime control file missing; default operator state is enabled",
                {"path": str(control_path), "default_enabled": True},
            )
        )
    else:
        try:
            data = json.loads(control_path.read_text(encoding="utf-8"))
            enabled = bool(data.get("enabled", True))
            emergency = bool(data.get("emergency_disabled", False))
            status = "FAIL" if emergency else ("WARN" if not enabled else "PASS")
            detail = "Path B emergency-disabled" if emergency else ("Path B operator-disabled" if not enabled else "Path B operator control allows live operation")
            checks.append(CheckResult("runtime.pathb_control_state", status, detail, {"path": str(control_path), "state": data}))
        except Exception as exc:
            checks.append(CheckResult("runtime.pathb_control_state", "FAIL", f"Path B control state unreadable: {exc}", {"path": str(control_path)}))
    snapshot_path = ROOT / "state" / f"{mode}_broker_truth_snapshot.json"
    if not snapshot_path.exists():
        checks.append(CheckResult("broker_truth.snapshot_missing_or_present", "WARN", "broker truth snapshot is missing; bot startup should create it", {"path": str(snapshot_path)}))
        checks.append(CheckResult("broker_truth.snapshot_file_valid", "WARN", "snapshot file missing before bot startup", {"path": str(snapshot_path)}))
    else:
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
            markets = data.get("markets") if isinstance(data.get("markets"), dict) else {}
            checks.append(CheckResult("broker_truth.snapshot_missing_or_present", "PASS", "broker truth snapshot exists", {"path": str(snapshot_path)}))
            checks.append(CheckResult("broker_truth.snapshot_file_valid", "PASS", "broker truth snapshot JSON parses", {"path": str(snapshot_path)}))
            for market in ("KR", "US"):
                item = markets.get(market) if isinstance(markets.get(market), dict) else {}
                status = "PASS"
                detail = f"{market} snapshot available"
                if not item or bool(item.get("missing", True)):
                    status = "WARN"
                    detail = f"{market} snapshot missing/account lookup pending"
                elif bool(item.get("stale", False)):
                    status = "WARN"
                    detail = f"{market} snapshot stale"
                checks.append(
                    CheckResult(
                        f"broker_truth.{market.lower()}_stale_state",
                        status,
                        detail,
                        {
                            "last_success_at": item.get("last_success_at", ""),
                            "last_attempt_at": item.get("last_attempt_at", ""),
                            "ttl_sec": item.get("ttl_sec", ""),
                            "error": item.get("error", ""),
                        },
                    )
                )
        except Exception as exc:
            checks.append(CheckResult("broker_truth.snapshot_missing_or_present", "PASS", "snapshot file exists", {"path": str(snapshot_path)}))
            checks.append(CheckResult("broker_truth.snapshot_file_valid", "FAIL", f"broker truth snapshot JSON error: {exc}", {"path": str(snapshot_path)}))
    return checks


def _static_code_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    trading = _repo_text("trading_bot.py")
    kis = _repo_text("kis_api.py")
    pathb = _repo_text("runtime", "pathb_runtime.py")
    v2_runtime = _repo_text("runtime", "v2_lifecycle_runtime.py")
    arbiter = _repo_text("execution", "path_arbiter.py")
    sell = _repo_text("execution", "claude_price_sell_manager.py")
    adapter = _repo_text("execution", "claude_price_adapter.py")
    analysts = _repo_text("minority_report", "analysts.py")
    dashboard = _repo_text("dashboard", "dashboard_server.py")
    ops_summary = _repo_text("interface", "v2_ops_summary.py")
    broker_truth = _repo_text("runtime", "broker_truth_snapshot.py")
    v2_telegram = _repo_text("interface", "v2_telegram.py")
    telegram_reporter = _repo_text("telegram_reporter.py")
    telegram_commander = _repo_text("telegram_commander.py")

    def _func_block(source: str, name: str) -> str:
        match = re.search(rf"^def\s+{re.escape(name)}\b.*?(?=^def\s+|\Z)", source, re.M | re.S)
        return match.group(0) if match else ""

    selection_retry_prompt = _func_block(analysts, "_build_selection_retry_prompt")

    atomic_write_present = (
        ("tmp.replace(self.path)" in broker_truth or "os.replace(tmp, self.path)" in broker_truth)
        and "mask_sensitive" in broker_truth
    )
    token_refresh_helper_present = "_get_balance_with_token_refresh" in trading and bool(
        re.search(r"get_access_token\(\s*force_refresh\s*=\s*True", trading)
    )
    markers = {
        "code.token_refresh_helper": token_refresh_helper_present,
        "code.pathb_startup_recovery": "self.pathb.recover_on_startup()" in trading,
        "code.session_active_attribute": "self.session_active = False" in trading and "self.session_active = True" in trading,
        "code.current_market_attribute": "self.current_market = market" in trading,
        "code.price_cache_clear_on_session_open": "price cache cleared at session_open" in trading,
        "code.partial_buy_exit_runtime": '{"FILLED", "PARTIAL_FILLED"}' in pathb,
        "code.partial_buy_exit_manager": 'status not in {"FILLED", "PARTIAL_FILLED"}' in sell,
        "code.cold_start_brain_fallback": "pathb_cold_start_" in pathb,
        "code.cancel_if_open_above": 'signal.reason == "cancel_if_open_above"' in pathb,
        "code.pathb_runtime_ready": "PathBRuntime" in trading and "self.pathb.recover_on_startup()" in trading,
        "code.pathb_plan_registration": "register_from_selection_meta" in pathb and "price_targets" in pathb and "create_path_run" in adapter,
        "code.pathb_buy_scan": "scan_waiting_entries" in pathb and "cancel_if_open_above" in pathb and "ZONE_EDGE_NO_VALID_LIMIT" in adapter,
        "code.pathb_sell_scan": "scan_exits" in pathb and '{"FILLED", "PARTIAL_FILLED"}' in pathb and "CLOSED_CLAUDE_PRICE_TARGET" in sell,
        "code.pathb_preclose_fallback": "_pre_close_force_exit" in pathb and "PATHB_PRE_CLOSE_MARKET_FALLBACK" in _repo_text("config", "v2.py"),
        "code.pathb_kill_switch": "PATHB_EMERGENCY_DISABLE" in _repo_text("config", "v2.py") and "def emergency_disable" in pathb and "/pathb_kill" in telegram_commander,
        "code.buy_partial_fill_exit_protected": '{"FILLED", "PARTIAL_FILLED"}' in pathb and "mark_partial_filled" in adapter,
        "code.sell_partial_fallback": "mark_sell_partial" in sell and "market_fallback_wait_sec" in sell,
        "code.order_acked_stuck_recovery": "recover_on_startup" in pathb and "ORDER_ACKED" in pathb and "broker" in pathb.lower(),
        "code.pending_order_session_close": "_clear_pending_orders_for_market" in trading and "pending order remained at session_close" in trading,
        "code.path_a_entry_flow_present": "_v2_arbitrate_path_a_entry" in trading and "_v2_safety_decision" in trading and "place_order" in trading,
        "code.path_arbiter_wired": "PathExecutionArbiter" in v2_runtime and "arbitrate_path_a_entry" in trading and "PATHB_ORDER_UNKNOWN_SAME_TICKER" in arbiter,
        "code.same_day_reentry_guard_wired": "SameDayReentryGuard" in v2_runtime and "_v2_same_day_reentry_decision" in trading and "KR_REENTRY_COOLDOWN_MINUTES" in _repo_text("config", "v2.py"),
        "claude.price_targets_required": "price_targets is required for every trade_ready ticker" in analysts,
        "claude.retry_prompt_omits_price_targets": (
            "DO NOT include price_targets in this response" in selection_retry_prompt
            and '"price_targets"' not in selection_retry_prompt
            and "entry_rationale" not in selection_retry_prompt
        ),
        "claude.no_same_session_watch_chase": "Do not promote a ticker to trade_ready solely because it moved after watch_only earlier in the same session" in analysts,
        "dashboard.path_comparison": "pathbCompareChart" in dashboard and "path_comparison" in ops_summary,
        "dashboard.pathb_state_truth": "path_runs_for_session" in ops_summary and "path_b_live" in ops_summary,
        "dashboard.broker_truth_uses_snapshot": "broker_truth" in ops_summary and "load_broker_truth_snapshot" in ops_summary and "pathb-broker-truth" in dashboard,
        "telegram.broker_truth_uses_snapshot": "broker_truth" in v2_telegram and "_cmd_positions_from_broker_truth" in telegram_commander,
        "broker_truth.atomic_write_marker": atomic_write_present,
        "broker_truth.positions_from_broker": "positions" in broker_truth and "normalize_position" in broker_truth,
        "broker_truth.open_orders_from_broker": "open_orders" in broker_truth and "remaining_qty" in broker_truth,
        "broker_truth.today_fills_from_broker": "today_fills" in broker_truth and "filled_qty" in broker_truth,
        "order_unknown.path_a_b_disambiguation": "path_a_origin_possible" in pathb and "_path_a_lifecycle_evidence" in pathb,
        "order_unknown.in_memory_pending_checked": "_path_a_pending_evidence" in pathb and "pending_orders" in pathb,
        "order_unknown.session_recheck_wired": "next_broker_truth_recheck_at" in pathb and "reconcile_order_unknowns" in pathb,
        "order_unknown.session_end_unresolved_marking": "session_end_unresolved" in pathb and "finalize_order_unknowns_at_session_close" in trading,
        "dashboard.order_unknown_visibility": "ORDER_UNKNOWN" in ops_summary and "path_run_id" in ops_summary,
        "dashboard.candidate_funnel": "missing_price_targets" in ops_summary and "registered_plans" in ops_summary and "Claude 입력 후보" in dashboard,
        "telegram.path_label_alerts": "_path_label" in telegram_reporter and "buy_path" in telegram_reporter and "B플랜" in telegram_reporter,
        "telegram.timeout_nonblocking": "getUpdates" in telegram_commander and "timeout=35" in telegram_commander and "폴링 오류" in telegram_commander,
        "session.us_session_date_logic_present": "US는 KST 자정을 넘어도 ET 기준 날짜" in trading and "market == \"US\"" in trading,
        "us.market_session_cutoff": "NEW_ENTRY_CUTOFF_MINUTES_BEFORE_CLOSE" in _repo_text("config", "v2.py") and "_pre_close_force_exit" in pathb,
        "us.order_unknown_scope": "block_state(market=market" in v2_runtime and "paused_markets" in _repo_text("execution", "order_state.py"),
    }
    for name, ok in markers.items():
        checks.append(
            CheckResult(
                name,
                "PASS" if ok else "FAIL",
                "marker found" if ok else "marker missing",
                {"category": "code_marker", "guardian_severity": "soft_fail"},
            )
        )

    timing_runtime_present = "WAIT_TIMING" in trading and "TIMING_EXPIRED" in trading
    timing_enum_present = "WAIT_TIMING" in _repo_text("lifecycle", "models.py") and "TIMING_EXPIRED" in _repo_text("lifecycle", "models.py")
    checks.append(
        CheckResult(
            "code.wait_timing_recorded",
            "PASS" if timing_runtime_present else ("WARN" if timing_enum_present else "FAIL"),
            "Path A WAIT_TIMING/TIMING_EXPIRED runtime markers found"
            if timing_runtime_present
            else ("lifecycle enum supports WAIT_TIMING/TIMING_EXPIRED, but Path A runtime wiring is not proven" if timing_enum_present else "timing lifecycle markers missing"),
            {
                "runtime_markers": timing_runtime_present,
                "enum_markers": timing_enum_present,
                "category": "code_marker",
                "guardian_severity": "soft_fail",
            },
        )
    )

    kr_order_block = _func_block(kis, "_build_order_body_kr")
    kr_payload_ok = all(
        marker in kr_order_block
        for marker in (
            "ORD_QTY",
            "str(qty_i)",
            '"ORD_UNPR": "0" if price_i == 0 else str(price_i)',
            '"EXCG_ID_DVSN_CD": "KRX"',
            '"CNDT_PRIC": ""',
        )
    )
    checks.append(
        CheckResult(
            "code.kr_order_payload_normalized",
            "PASS" if kr_payload_ok else "FAIL",
            "KR order qty/price payload is normalized" if kr_payload_ok else "KR order payload normalization marker missing",
        )
    )

    kr_place_block = _func_block(kis, "_place_order_kr")
    kr_recovery_ok = (
        "_find_recent_order_truth_kr" in kr_place_block
        and "retry_skipped=state_unknown" in kr_place_block
        and "retrying once" in kr_place_block
        and "_submit_order_kr_once" in kr_place_block
    )
    blind_kr_retry = "_retry_kis" in kr_place_block
    checks.append(
        CheckResult(
            "code.kr_order_500_recovery_wired",
            "PASS" if kr_recovery_ok and not blind_kr_retry else "FAIL",
            "KR HTTP 500 uses broker truth then one retry" if kr_recovery_ok and not blind_kr_retry else "KR HTTP 500 recovery wiring incomplete or blind retry present",
            {"blind_retry_in_place_order_kr": blind_kr_retry},
        )
    )

    us_place_block = _func_block(kis, "_place_order_us")
    us_recovery_ok = (
        "_find_recent_order_truth_us" in us_place_block
        and "retry_skipped=state_unknown" in us_place_block
        and "retrying once" in us_place_block
        and "_submit_order_us_once" in us_place_block
    )
    blind_us_retry = "_retry_kis" in us_place_block
    checks.append(
        CheckResult(
            "code.us_order_500_recovery_wired",
            "PASS" if us_recovery_ok and not blind_us_retry else "FAIL",
            "US HTTP 500 uses broker truth then one retry" if us_recovery_ok and not blind_us_retry else "US HTTP 500 recovery wiring incomplete or blind retry present",
            {"blind_retry_in_place_order_us": blind_us_retry},
        )
    )

    raw_error_ok = all(
        marker in kis
        for marker in ("_raise_order_http_error", "_response_text", "_mask_order_body", "status_code")
    )
    checks.append(
        CheckResult(
            "code.order_error_raw_response_logged",
            "PASS" if raw_error_ok else "FAIL",
            "order HTTP errors preserve raw response and masked payload" if raw_error_ok else "order HTTP error logging marker missing",
        )
    )

    truth_ok = all(
        marker in kis
        for marker in ("inquire_daily_ccld_kr", "inquire_ccnl_us", "_find_recent_order_truth_kr", "_find_recent_order_truth_us")
    )
    checks.append(
        CheckResult(
            "broker_truth_query_available",
            "PASS" if truth_ok else "FAIL",
            "KR/US broker-truth query helpers are present" if truth_ok else "broker-truth query helper missing",
        )
    )

    us_price_block = _func_block(kis, "_submit_order_us_once")
    checks.append(
        CheckResult(
            "us.order_price_format",
            "PASS" if 'f"{price_f:.2f}"' in us_price_block and "OVRS_ORD_UNPR" in us_price_block else "FAIL",
            "US order price uses two decimal places" if 'f"{price_f:.2f}"' in us_price_block else "US order price format marker missing",
        )
    )

    sell_partial_refs = len(re.findall(r"\bmark_sell_partial\s*\(", pathb + trading + sell))
    status = "WARN" if sell_partial_refs <= 1 else "PASS"
    checks.append(
        CheckResult(
            "code.sell_partial_runtime_wiring",
            status,
            "SELL_PARTIAL_FILLED helper exists but runtime callback wiring is not proven" if status == "WARN" else "sell partial marker wired",
            {"mark_sell_partial_references": sell_partial_refs},
        )
    )

    try:
        import kis_api

        fallback = {str(t).upper() for t in getattr(kis_api, "_US_FALLBACK_UNIVERSE", [])}
        mapped = set()
        for values in getattr(kis_api, "_US_EXCHANGE_MAP", {}).values():
            mapped.update(str(t).upper() for t in values)
        missing = sorted(fallback - mapped)
        checks.append(
            CheckResult(
                "code.us_exchange_map_coverage",
                "FAIL" if missing else "PASS",
                f"missing US exchange mappings: {missing}" if missing else "US fallback universe exchange mappings are complete",
                {"missing": missing, "fallback_count": len(fallback)},
            )
        )
        checks.append(
            CheckResult(
                "us.exchange_map_coverage",
                "FAIL" if missing else "PASS",
                f"missing US exchange mappings: {missing}" if missing else "US fallback universe exchange mappings are complete",
                {"missing": missing, "fallback_count": len(fallback)},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("code.us_exchange_map_coverage", "FAIL", f"cannot inspect US exchange map: {exc}"))
        checks.append(CheckResult("us.exchange_map_coverage", "FAIL", f"cannot inspect US exchange map: {exc}"))

    session_data = {
        "now_kst": _now_kst().isoformat(timespec="seconds"),
        "KR_session_date_guess": _session_date_guess("KR"),
        "US_session_date_guess": _session_date_guess("US"),
    }
    checks.append(
        CheckResult(
            "market.session_calendar",
            "WARN",
            "calendar API is not called by preflight; verify holidays/early-close operationally",
            session_data,
        )
    )
    return checks


def _pathb_feature_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    from config.v2 import V2Config
    from decision.claude_price_plan import make_price_plan
    from execution.claude_price_adapter import ClaudePriceAdapter
    from execution.claude_price_sell_manager import ClaudePriceSellManager
    from execution.safety_gate import PathBSafetyGate, SafetyContext
    from lifecycle.event_store import EventStore

    with tempfile.TemporaryDirectory() as tmp:
        cfg = V2Config(pathb_fixed_order_krw=100_000, pathb_max_positions=1, pathb_max_daily_entries=1)
        store = EventStore(Path(tmp) / "events.db")
        adapter = ClaudePriceAdapter(store, cfg)
        plan = make_price_plan(
            decision_id="dec1",
            ticker="005930",
            market="KR",
            session_date="2026-04-27",
            buy_zone_low=52_000,
            buy_zone_high=52_500,
            sell_target=54_500,
            stop_loss=51_000,
            hold_days=1,
            confidence=0.7,
            cancel_if_open_above=53_000,
        )
        path_run_id = adapter.register_plan(plan, runtime_mode="live", brain_snapshot_id="brain1")
        cancel = adapter.check_entry(path_run_id, 53_100)
        checks.append(CheckResult("pathb.cancel_if_open_above", "PASS" if cancel.reason == "cancel_if_open_above" else "FAIL", cancel.reason))

        edge = adapter.compute_buy_limit("KR", 51_200, 51_000)
        checks.append(CheckResult("pathb.limit_edge_guard", "PASS" if edge < 51_200 else "FAIL", "limit below current should be blocked by check_entry", {"computed_limit": edge}))

        ctx = SafetyContext(
            market="KR",
            runtime_mode="live",
            ticker="005930",
            price_krw=200_000,
            qty=0,
            order_cost_krw=0,
            cash_krw=100_000,
            min_order_krw=100_000,
            market_open=True,
            broker_trust_level="trusted",
        )
        decision = PathBSafetyGate(cfg).evaluate(ctx, plan=plan)
        checks.append(CheckResult("pathb.qty_zero_blocks", "PASS" if not decision.passed and decision.reason_code == "INVALID_PRICE" else "FAIL", decision.reason_code))

        adapter.mark_partial_filled(path_run_id, price=52_200, qty=1, execution_id="ord1", runtime_mode="live", brain_snapshot_id="brain1")
        exit_signal = ClaudePriceSellManager(adapter, cfg).check_exit(path_run_id, 50_900)
        checks.append(
            CheckResult(
                "pathb.partial_buy_exit_protected",
                "PASS" if exit_signal.signal and exit_signal.close_reason == "CLOSED_CLAUDE_PRICE_STOP" else "FAIL",
                exit_signal.close_reason or exit_signal.reason,
            )
        )
    return checks


def _dashboard_checks() -> list[CheckResult]:
    try:
        from dashboard.dashboard_server import app
    except Exception as exc:
        return [CheckResult("dashboard.import", "FAIL", f"dashboard import failed: {exc}")]
    client = app.test_client()
    checks: list[CheckResult] = []
    try:
        page = client.get("/pathb")
        body = page.get_data(as_text=True)
        ok = page.status_code == 200 and all(
            marker in body
            for marker in ("pathbPnlChart", "pathbOutcomeChart", "pathbStatusChart", "pathbCompareChart")
        )
        checks.append(CheckResult("dashboard.pathb_page", "PASS" if ok else "FAIL", f"status={page.status_code}"))
    except Exception as exc:
        checks.append(CheckResult("dashboard.pathb_page", "FAIL", f"/pathb crashed: {exc}"))
    try:
        api = client.get("/api/v2/ops?market=KR&mode=live")
        data = api.get_json(silent=True) or {}
        ok = api.status_code == 200 and data.get("ok") is True and "path_b_live" in data and "broker_truth" in data
        checks.append(CheckResult("dashboard.pathb_api", "PASS" if ok else "FAIL", f"status={api.status_code}", {"keys": sorted(data.keys())}))
        pathb_live = data.get("path_b_live") or {}
        comparison = pathb_live.get("path_comparison") or {}
        cmp_ok = bool(comparison.get("path_a")) and bool(comparison.get("path_b"))
        checks.append(
            CheckResult(
                "dashboard.path_comparison",
                "PASS" if cmp_ok else "FAIL",
                "A/B path comparison is exposed" if cmp_ok else "path comparison missing from ops API",
                {"comparison_keys": sorted(comparison.keys())},
            )
        )
        selection = pathb_live.get("selection") or {}
        counts = selection.get("counts") or {}
        funnel_ok = all(key in counts for key in ("universe", "watchlist", "raw_trade_ready", "applied_trade_ready", "price_targets", "registered_plans"))
        checks.append(
            CheckResult(
                "dashboard.candidate_funnel_runtime",
                "PASS" if funnel_ok else "WARN",
                "candidate funnel is exposed" if funnel_ok else "candidate funnel counts are incomplete",
                {"counts": counts},
            )
        )
        broker_truth = data.get("broker_truth") or {}
        broker_markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
        checks.append(
            CheckResult(
                "dashboard.broker_truth_api",
                "PASS" if all(m in broker_markets for m in ("KR", "US")) else "FAIL",
                "broker truth snapshot is exposed through ops API" if all(m in broker_markets for m in ("KR", "US")) else "broker truth missing from ops API",
                {"markets": sorted(broker_markets.keys()) if isinstance(broker_markets, dict) else []},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("dashboard.pathb_api", "FAIL", f"/api/v2/ops crashed: {exc}"))
    return checks


def _telegram_checks() -> list[CheckResult]:
    checks: list[CheckResult] = []
    commander_text = _repo_text("telegram_commander.py")
    core_commands = ["/status", "/health", "/positions", "/errors"]
    core_missing = [cmd for cmd in core_commands if cmd not in commander_text and (cmd != "/positions" or "/pos" not in commander_text)]
    checks.append(
        CheckResult(
            "telegram.core_commands",
            "FAIL" if core_missing else "PASS",
            f"missing core command markers: {core_missing}" if core_missing else "core Telegram command markers are present",
            {"missing": core_missing},
        )
    )
    try:
        from telegram_reporter import _path_label

        label_ok = _path_label("path_a").startswith("A플랜") and _path_label("path_b").startswith("B플랜")
        checks.append(
            CheckResult(
                "telegram.path_label_alerts",
                "PASS" if label_ok else "FAIL",
                "A/B path labels render in alerts" if label_ok else "A/B path labels are wrong",
                {"path_a": _path_label("path_a"), "path_b": _path_label("path_b")},
            )
        )
    except Exception as exc:
        checks.append(CheckResult("telegram.path_label_alerts", "FAIL", f"path label import/check failed: {exc}"))

    try:
        from interface.v2_telegram import handle_v2_command
    except Exception as exc:
        checks.append(CheckResult("telegram.import", "FAIL", f"telegram command import failed: {exc}"))
        return checks

    class _Risk:
        halted = False
        halt_reason = ""
        cash = 0
        positions: list[dict[str, Any]] = []

        def equity(self) -> float:
            return 0.0

        def daily_return(self) -> float:
            return 0.0

    class _PathB:
        def status(self) -> dict[str, Any]:
            return {
                "enabled": True,
                "operator_enabled": True,
                "emergency_disabled": False,
                "mode": "min_size_live",
                "runtime_mode": "live",
                "fixed_order_krw": 100000,
                "max_positions": 10,
                "max_daily_entries": 10,
                "min_confidence": 0.5,
            }

        def set_enabled(self, enabled: bool, *, updated_by: str, reason: str) -> None:
            return None

        def emergency_disable(self, *, updated_by: str, reason: str) -> None:
            return None

        def close_all_open(self, market: str, *, reason: str) -> int:
            return 0

    class _Bot:
        risk = _Risk()
        pathb = _PathB()
        pending_orders: list[dict[str, Any]] = []
        current_market = "KR"

    commands = ["/health", "/errors", "/pathb_status", "/pathb_on", "/pathb_off", "/pathb_kill", "/pathb_closeall"]
    failures: dict[str, str] = {}
    for command in commands:
        try:
            response = handle_v2_command(command, _Bot())
            if not response:
                failures[command] = "empty response"
            if command == "/health" and "broker_truth" not in response:
                failures[command] = "broker_truth status missing"
        except Exception as exc:
            failures[command] = str(exc)
    checks.append(
        CheckResult(
            "telegram.pathb_commands",
            "FAIL" if failures else "PASS",
            "V2/Path B Telegram commands responded" if not failures else "V2/Path B Telegram command failures",
            {"failures": failures},
        )
    )
    checks.append(
        CheckResult(
            "telegram.positions_uses_broker_snapshot",
            "PASS" if "_cmd_positions_from_broker_truth" in commander_text and "build_v2_ops_summary" in commander_text else "FAIL",
            "/positions uses broker truth snapshot first" if "_cmd_positions_from_broker_truth" in commander_text else "/positions broker truth helper missing",
        )
    )
    return checks


def _ops_summary_checks(mode: str) -> list[CheckResult]:
    checks: list[CheckResult] = []
    try:
        from interface.v2_ops_summary import build_v2_ops_summary
    except Exception as exc:
        return [CheckResult("ops_summary.import", "FAIL", f"ops summary import failed: {exc}")]

    for market in ("KR", "US"):
        try:
            summary = build_v2_ops_summary(market=market, runtime_mode=mode, session_date=_session_date_guess(market))
            pathb = summary.get("path_b_live") or {}
            comparison = pathb.get("path_comparison") or {}
            lifecycle = summary.get("lifecycle") or {}
            broker_truth = summary.get("broker_truth") or {}
            unknown = lifecycle.get("order_unknown") or []
            closed = lifecycle.get("closed") or []
            checks.append(
                CheckResult(
                    f"{market.lower()}.today_order_unknown_review",
                    "WARN" if unknown else "PASS",
                    f"{market} ORDER_UNKNOWN rows={len(unknown)}" if unknown else f"{market} has no ORDER_UNKNOWN rows in ops summary",
                    {"rows": unknown[-20:]},
                )
            )
            checks.append(
                CheckResult(
                    f"{market.lower()}.broker_truth_summary",
                    "PASS" if market in (broker_truth.get("markets") or {}) else "FAIL",
                    f"{market} broker truth summary exposed" if market in (broker_truth.get("markets") or {}) else f"{market} broker truth summary missing",
                    {"market_data": (broker_truth.get("markets") or {}).get(market, {})},
                )
            )
            checks.append(
                CheckResult(
                    f"{market.lower()}.closed_positions_review",
                    "PASS",
                    f"{market} closed rows={len(closed)}",
                    {"rows": closed[-20:]},
                )
            )
            if market == "KR":
                pathb_closed = int(((comparison.get("path_b") or {}).get("closed") or 0))
                checks.append(
                    CheckResult(
                        "kr.pathb_no_closed_explained",
                        "PASS",
                        "Path B closed count is derived from v2_path_runs/path_comparison",
                        {"path_b_closed": pathb_closed, "comparison": comparison},
                    )
                )
        except Exception as exc:
            checks.append(CheckResult(f"{market.lower()}.ops_summary", "FAIL", f"{market} ops summary failed: {exc}"))
    return checks


def run_preflight(mode: str = "live", *, allow_config_conflicts: bool = False, include_dashboard: bool = True) -> dict[str, Any]:
    checks: list[CheckResult] = []
    config_checks, config = _config_checks(mode, allow_config_conflicts)
    checks.extend(config_checks)
    checks.extend(_kis_network_checks(config, mode))
    checks.extend(_db_checks(mode))
    checks.extend(_token_checks(mode))
    checks.extend(_state_checks(config, mode))
    checks.extend(_static_code_checks())
    checks.extend(_pathb_feature_checks())
    if include_dashboard:
        checks.extend(_dashboard_checks())
    checks.extend(_telegram_checks())
    checks.extend(_ops_summary_checks(mode))
    fail_count = sum(1 for check in checks if check.status == "FAIL")
    warn_count = sum(1 for check in checks if check.status == "WARN")
    report = {
        "ok": fail_count == 0,
        "mode": mode,
        "generated_at": _now_kst().isoformat(timespec="seconds"),
        "fail_count": fail_count,
        "warn_count": warn_count,
        "checks": [asdict(check) for check in checks],
        "effective_config": {
            key: config["effective"].get(key, "")
            for key in sorted(LIVE_CONFIG_KEYS)
            if key in config["effective"]
        },
    }
    return report


def _write_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = ROOT / "data" / "v2_reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"live_preflight_{stamp}.json"
    md_path = out_dir / f"live_preflight_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Live Preflight Report {stamp}",
        "",
        f"- ok: {report['ok']}",
        f"- mode: {report['mode']}",
        f"- fail_count: {report['fail_count']}",
        f"- warn_count: {report['warn_count']}",
        "",
        "## Checks",
        "",
    ]
    for check in report["checks"]:
        lines.append(f"- {check['status']} `{check['name']}` - {check['detail']}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Live preflight for V2/Path B production operation.")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--allow-config-conflicts", action="store_true")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--json", action="store_true", dest="print_json")
    args = parser.parse_args()

    report = run_preflight(
        args.mode,
        allow_config_conflicts=args.allow_config_conflicts,
        include_dashboard=not args.skip_dashboard,
    )
    json_path, md_path = _write_report(report)
    if args.print_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"ok={report['ok']} fail={report['fail_count']} warn={report['warn_count']}")
        print(f"json={json_path}")
        print(f"md={md_path}")
        for check in report["checks"]:
            if check["status"] != "PASS":
                print(f"{check['status']} {check['name']}: {check['detail']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
