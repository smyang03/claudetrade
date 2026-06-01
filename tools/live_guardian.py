from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

try:
    import psutil
except Exception:  # pragma: no cover
    psutil = None  # type: ignore


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = ZoneInfo("Asia/Seoul") if ZoneInfo is not None else None

from bot.session_date import resolve_session_date_str
from runtime_paths import get_runtime_path
from tools.live_preflight import run_preflight
from tools.v2_live_smoke import run_live_smoke


@dataclass
class GuardianFinding:
    name: str
    status: str
    classification: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class GuardianAction:
    name: str
    status: str
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def _now_kst() -> datetime:
    return datetime.now(KST) if KST is not None else datetime.now()


def _guardian_heartbeat_path(mode: str) -> Path:
    runtime_mode = "live" if str(mode or "").lower() == "live" else "paper"
    name = "live_guardian_heartbeat.json" if runtime_mode == "live" else f"{runtime_mode}_guardian_heartbeat.json"
    return get_runtime_path("state", name)


def _write_guardian_heartbeat(
    mode: str,
    *,
    status: str,
    last_success_at: str = "",
    last_error: str = "",
    report_path: str = "",
) -> None:
    path = _guardian_heartbeat_path(mode)
    now = _now_kst().isoformat(timespec="seconds")
    payload = {
        "process": "live_guardian",
        "pid": os.getpid(),
        "last_started_at": now,
        "last_tick_at": now,
        "last_success_at": last_success_at,
        "last_error_at": now if last_error else "",
        "last_error": last_error,
        "next_expected_at": "",
        "healthy": not bool(last_error) and status != "error",
        "status": status,
        "report_path": report_path,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_env(mode: str, env_path: str | Path | None = None) -> str:
    try:
        from dotenv import load_dotenv
    except Exception:
        return ""

    path = Path(env_path) if env_path else ROOT / f".env.{mode}"
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        path = ROOT / ".env"
    if path.exists():
        load_dotenv(dotenv_path=path, override=True)
        return str(path)
    return ""


def _enabled_markets(preflight: dict[str, Any]) -> list[str]:
    raw = str((preflight.get("effective_config") or {}).get("ENABLED_MARKETS") or "KR,US")
    markets = [item.strip().upper() for item in raw.split(",") if item.strip()]
    return [market for market in markets if market in {"KR", "US"}] or ["KR", "US"]


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


def _order_unknown_remediation_commands(data: dict[str, Any]) -> list[str]:
    rows: list[dict[str, Any]] = []
    for key in ("current_session", "previous_session"):
        value = data.get(key)
        if isinstance(value, list):
            rows.extend([row for row in value if isinstance(row, dict)])
    commands: list[str] = []
    seen: set[str] = set()
    for row in rows:
        market = str(row.get("market") or data.get("market") or "ALL").upper()
        ticker = str(row.get("ticker") or "").strip()
        session_date = str(row.get("session_date") or row.get("date") or "").strip()
        order_id = str(row.get("order_id") or row.get("order_no") or row.get("execution_id") or "").strip()
        parts = [sys.executable, "-m", "tools.reconcile_order_truth"]
        if session_date:
            parts.extend(["--date", session_date])
        if market in {"KR", "US"}:
            parts.extend(["--market", market])
        else:
            parts.extend(["--market", "ALL"])
        if ticker:
            parts.extend(["--ticker", ticker])
        if order_id:
            parts.extend(["--order-id", order_id])
        parts.append("--dry-run")
        command = " ".join(parts)
        if command not in seen:
            seen.add(command)
            commands.append(command)
    if not commands:
        commands.append(f"{sys.executable} -m tools.reconcile_order_truth --market ALL --dry-run")
    return commands


def _pathb_stale_active_remediation_commands(data: dict[str, Any]) -> list[str]:
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    markets = sorted({str(row.get("market") or "").upper() for row in rows if isinstance(row, dict) and str(row.get("market") or "").upper() in {"KR", "US"}})
    commands = [f"{sys.executable} tools/pathb_legacy_remediation.py --mode live --write-report"]
    for market in markets:
        commands.append(f"{sys.executable} tools/pathb_legacy_remediation.py --mode live --market {market} --write-report")
    return commands


def classify_preflight_check(
    check: dict[str, Any],
    *,
    mode: str = "live",
    start_bot: bool = False,
    start_dashboard: bool = False,
    ensure_bot: bool = False,
) -> GuardianFinding:
    name = str(check.get("name") or "")
    status = str(check.get("status") or "")
    detail = str(check.get("detail") or "")
    data = check.get("data") if isinstance(check.get("data"), dict) else {}
    category = str(data.get("category") or "")

    if status == "PASS":
        return GuardianFinding(name, status, "pass", detail, data)

    if category == "code_marker":
        return GuardianFinding(name, status, "soft_fail", detail, data)

    if category == "runtime_pid_lock":
        if bool(data.get("auto_fix")):
            return GuardianFinding(name, status, "auto_fixable", detail, data)
        if bool(data.get("alive")) and name == "runtime.bot_pid_lock" and start_bot:
            if ensure_bot:
                if _pid_lock_is_trusted_bot_lock(data):
                    return GuardianFinding(name, status, "accepted_exception", "bot is already running", data)
                return GuardianFinding(
                    name,
                    status,
                    "hard_fail",
                    "pid lock alive but not validated as the expected bot",
                    data,
                )
            return GuardianFinding(name, status, "hard_fail", "active bot PID blocks duplicate start", data)
        if bool(data.get("accepted_exception")):
            return GuardianFinding(name, status, "accepted_exception", detail, data)
        return GuardianFinding(name, status, "soft_fail", detail, data)

    if name == "db.order_unknown_unresolved":
        current = data.get("current_session") if isinstance(data.get("current_session"), list) else []
        previous_with_exposure = (
            data.get("previous_session_with_local_exposure")
            if isinstance(data.get("previous_session_with_local_exposure"), list)
            else []
        )
        classification = "hard_fail" if current or previous_with_exposure else "soft_fail"
        enriched = dict(data)
        enriched["remediation_commands"] = _order_unknown_remediation_commands(enriched)
        return GuardianFinding(name, status, classification, detail, enriched)

    if name == "db.pathb_broker_truth_conflict" and status != "PASS":
        conflicts = data.get("conflicts") if isinstance(data.get("conflicts"), list) else []
        blockers = [item for item in conflicts if isinstance(item, dict) and bool(item.get("do_not_start", True))]
        classification = "hard_fail" if blockers else "soft_fail"
        return GuardianFinding(name, status, classification, detail, data)

    if name == "db.pathb_stale_active_runs":
        previous_with_exposure = (
            data.get("previous_session_with_local_exposure")
            if isinstance(data.get("previous_session_with_local_exposure"), list)
            else []
        )
        classification = "hard_fail" if previous_with_exposure else "soft_fail"
        enriched = dict(data)
        enriched["remediation_commands"] = _pathb_stale_active_remediation_commands(enriched)
        enriched.setdefault(
            "operator_action",
            "read-only: verify broker positions/open orders/fills before audited PathB remediation; never close rows from local DB alone",
        )
        enriched["auto_apply_allowed"] = False
        return GuardianFinding(name, status, classification, detail, enriched)

    if bool(data.get("accepted_exception")):
        return GuardianFinding(name, status, "accepted_exception", detail, data)

    if name.startswith("broker_truth."):
        if status != "PASS":
            runtime_mode = str(mode or "live").lower()
            if runtime_mode == "paper" and name in {
                "broker_truth.snapshot_missing_or_present",
                "broker_truth.snapshot_file_valid",
            } and (
                bool(data.get("startup_expected"))
                or bool(data.get("snapshot_missing"))
                or "missing" in detail.lower()
            ):
                return GuardianFinding(name, status, "soft_fail", detail, data)
            return GuardianFinding(name, status, "hard_fail", detail, data)

    if name == "runtime.pathb_control_state" and status == "FAIL":
        return GuardianFinding(name, status, "hard_fail", detail, data)

    if name in {"kis.token_expiry", "kis.kr_token_refresh", "kis.us_token_refresh"}:
        if status == "FAIL":
            return GuardianFinding(name, status, "hard_fail", detail, data)
        return GuardianFinding(name, status, "auto_fixable", detail, data)

    if name.startswith("dashboard.") and status != "PASS":
        classification = "auto_fixable" if start_dashboard else "soft_fail"
        return GuardianFinding(name, status, classification, detail, data)

    if name.startswith("telegram."):
        return GuardianFinding(name, status, "soft_fail", detail, data)

    if status == "FAIL":
        return GuardianFinding(name, status, "hard_fail", detail, data)

    return GuardianFinding(name, status, "soft_fail", detail, data)


def _classify_smoke(smoke: dict[str, Any]) -> list[GuardianFinding]:
    findings: list[GuardianFinding] = []
    if bool(smoke.get("ok")):
        findings.append(GuardianFinding("smoke.all", "PASS", "pass", "live smoke passed", smoke))
        return findings
    findings.append(GuardianFinding("smoke.all", "FAIL", "hard_fail", "live smoke failed", smoke))
    for item in smoke.get("results") or []:
        if isinstance(item, dict) and not item.get("ok"):
            findings.append(
                GuardianFinding(
                    f"smoke.{item.get('market', 'UNKNOWN')}",
                    "FAIL",
                    "hard_fail",
                    str(item.get("reason") or "market smoke failed"),
                    item,
                )
            )
    return findings


def _remove_stale_pid(path: str | Path, pid: int) -> GuardianAction:
    target = Path(path)
    if pid and _pid_alive(int(pid)):
        return GuardianAction("remove_stale_pid", "SKIP", "pid is alive", {"path": str(target), "pid": pid})
    try:
        if target.exists():
            target.unlink()
        return GuardianAction("remove_stale_pid", "PASS", "stale pid lock removed", {"path": str(target), "pid": pid})
    except Exception as exc:
        return GuardianAction("remove_stale_pid", "FAIL", f"failed to remove stale pid lock: {exc}", {"path": str(target), "pid": pid})


def _refresh_token(markets: list[str]) -> GuardianAction:
    try:
        import kis_api

        refreshed: list[str] = []
        seen_token_files: set[str] = set()
        for market in markets:
            profile = kis_api.get_kis_market_profile(market)
            token_file = str(profile.token_file)
            if token_file in seen_token_files:
                continue
            seen_token_files.add(token_file)
            kis_api.get_access_token(force_refresh=True, market=market)
            refreshed.append(market)
        return GuardianAction("refresh_token", "PASS", "token refresh requested", {"markets": refreshed})
    except Exception as exc:
        return GuardianAction("refresh_token", "FAIL", f"token refresh failed: {exc}", {"markets": markets})


def _start_dashboard() -> GuardianAction:
    script = ROOT / "dashboard" / "dashboard_server.py"
    try:
        kwargs: dict[str, Any] = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen([sys.executable, str(script)], **kwargs)
        return GuardianAction("start_dashboard", "PASS", "dashboard start requested", {"pid": proc.pid, "script": str(script)})
    except Exception as exc:
        return GuardianAction("start_dashboard", "FAIL", f"dashboard start failed: {exc}", {"script": str(script)})


def _start_bot(mode: str) -> GuardianAction:
    command = [sys.executable, str(ROOT / "trading_bot.py")]
    if mode == "live":
        command.append("--live")
    try:
        kwargs: dict[str, Any] = {"cwd": str(ROOT), "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(command, **kwargs)
        return GuardianAction("start_bot", "PASS", "bot start requested", {"pid": proc.pid, "command": command})
    except Exception as exc:
        return GuardianAction("start_bot", "FAIL", f"bot start failed: {exc}", {"command": command})


def _apply_auto_fixes(findings: list[GuardianFinding], *, markets: list[str], start_dashboard: bool) -> list[GuardianAction]:
    actions: list[GuardianAction] = []
    token_refresh_needed = False
    dashboard_start_needed = False
    for finding in findings:
        token_recoverable = finding.name in {
            "kis.token_file",
            "kis.token_expiry",
            "kis.kr_token_refresh",
            "kis.us_token_refresh",
        } and (
            "token file missing" in finding.detail
            or "token expired" in finding.detail
            or "near expiry" in finding.detail
            or finding.name == "kis.token_expiry"
        )
        if finding.classification != "auto_fixable" and not token_recoverable:
            continue
        if finding.data.get("category") == "runtime_pid_lock" and finding.data.get("auto_fix"):
            actions.append(_remove_stale_pid(finding.data.get("path", ""), int(finding.data.get("pid", 0) or 0)))
        elif finding.name in {"kis.token_file", "kis.token_expiry", "kis.kr_token_refresh", "kis.us_token_refresh"}:
            token_refresh_needed = True
        elif finding.name.startswith("dashboard."):
            dashboard_start_needed = True
    if token_refresh_needed:
        actions.append(_refresh_token(markets))
    if dashboard_start_needed and start_dashboard:
        actions.append(_start_dashboard())
    return actions


def _run_smoke(*, mode: str, markets: list[str], env: str = "", session_date: str = "") -> dict[str, Any]:
    _load_env(mode, env or None)
    results = [
        run_live_smoke(
            market=market,
            runtime_mode=mode,
            session_date=session_date or resolve_session_date_str(market),
        )
        for market in markets
    ]
    return {"ok": all(item.get("ok") for item in results), "results": results}


def _pid_lock_is_trusted_bot_lock(data: dict[str, Any]) -> bool:
    return (
        bool(data.get("alive"))
        and data.get("accepted_exception") is True
        and not bool(data.get("mode_mismatch"))
        and not bool(data.get("remediation_required"))
    )


def _pid_set_for_matching_bot_process(preflight: dict[str, Any], mode: str) -> set[int]:
    data = _process_inventory_data(preflight)
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    expected_role = "live_bot" if str(mode or "").lower() == "live" else "paper_bot"
    pids: set[int] = set()
    for row in rows:
        if not isinstance(row, dict) or str(row.get("role") or "") != expected_role:
            continue
        try:
            pids.add(int(row.get("pid") or 0))
        except Exception:
            continue
    pids.discard(0)
    return pids


def _active_bot_lock(preflight: dict[str, Any], mode: str) -> dict[str, Any]:
    matching_pids = _pid_set_for_matching_bot_process(preflight, mode)
    if not matching_pids:
        return {}
    for check in preflight.get("checks", []):
        if str(check.get("name") or "") != "runtime.bot_pid_lock":
            continue
        data = check.get("data") if isinstance(check.get("data"), dict) else {}
        if not _pid_lock_is_trusted_bot_lock(data):
            continue
        pid = int(data.get("pid", 0) or 0)
        if pid not in matching_pids:
            continue
        return {
            "pid": pid,
            "path": str(data.get("path") or ""),
        }
    return {}


def _unmatched_active_bot_lock(preflight: dict[str, Any], mode: str) -> dict[str, Any]:
    matching_pids = _pid_set_for_matching_bot_process(preflight, mode)
    for check in preflight.get("checks", []):
        if str(check.get("name") or "") != "runtime.bot_pid_lock":
            continue
        data = check.get("data") if isinstance(check.get("data"), dict) else {}
        if not _pid_lock_is_trusted_bot_lock(data):
            continue
        pid = int(data.get("pid", 0) or 0)
        if pid in matching_pids:
            continue
        return {
            "pid": pid,
            "path": str(data.get("path") or ""),
            "matching_bot_pids": sorted(matching_pids),
        }
    return {}


def _process_inventory_data(preflight: dict[str, Any]) -> dict[str, Any]:
    for check in preflight.get("checks", []):
        if str(check.get("name") or "") != "runtime.process_inventory":
            continue
        data = check.get("data") if isinstance(check.get("data"), dict) else {}
        return data
    return {}


def _matching_bot_process(preflight: dict[str, Any], mode: str) -> dict[str, Any]:
    data = _process_inventory_data(preflight)
    rows = data.get("rows") if isinstance(data.get("rows"), list) else []
    expected_role = "live_bot" if str(mode or "").lower() == "live" else "paper_bot"
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("role") or "") == expected_role]
    if not matches:
        return {}
    return {
        "source": data.get("source") or "",
        "role": expected_role,
        "pids": [row.get("pid") for row in matches],
        "rows": matches,
    }


def _process_inventory_unavailable(preflight: dict[str, Any]) -> dict[str, Any]:
    data = _process_inventory_data(preflight)
    if not data:
        return {
            "source": "missing",
            "warning_kind": "process_inventory_missing",
            "rows": [],
            "operator_action": "inspect processes manually before live bot start",
        }
    if str(data.get("warning_kind") or "") == "process_inventory_unavailable":
        return data
    if str(data.get("source") or "") == "unavailable":
        return data
    return {}


def run_guardian_once(
    *,
    mode: str = "live",
    env: str = "",
    auto_fix: bool = False,
    start_dashboard: bool = False,
    start_bot: bool = False,
    ensure_bot: bool = False,
    dry_run_start: bool = False,
    skip_dashboard: bool = False,
    skip_smoke: bool = False,
    session_date: str = "",
    bot_start_allowed: bool = True,
    bot_start_skip_detail: str = "",
) -> dict[str, Any]:
    env_loaded = _load_env(mode, env or None)
    _write_guardian_heartbeat(mode, status="running")
    preflight = run_preflight(mode, include_dashboard=not skip_dashboard)
    markets = _enabled_markets(preflight)
    bot_start_requested = bool(start_bot or ensure_bot)
    findings = [
        classify_preflight_check(
            check,
            mode=mode,
            start_bot=bot_start_requested,
            start_dashboard=start_dashboard,
            ensure_bot=ensure_bot,
        )
        for check in preflight.get("checks", [])
        if str(check.get("status") or "") != "PASS"
    ]
    actions: list[GuardianAction] = []
    if auto_fix:
        actions = _apply_auto_fixes(findings, markets=markets, start_dashboard=start_dashboard)
        if any(action.status == "PASS" for action in actions):
            preflight = run_preflight(mode, include_dashboard=not skip_dashboard)
            markets = _enabled_markets(preflight)
            findings = [
                classify_preflight_check(
                    check,
                    mode=mode,
                    start_bot=bot_start_requested,
                    start_dashboard=start_dashboard,
                    ensure_bot=ensure_bot,
                )
                for check in preflight.get("checks", [])
                if str(check.get("status") or "") != "PASS"
            ]

    if skip_smoke:
        smoke = {"ok": True, "skipped": True, "reason": "skip_smoke_requested"}
    else:
        smoke = _run_smoke(mode=mode, markets=markets, env=env, session_date=session_date)
        findings.extend(_classify_smoke(smoke))
    active_bot_process = _matching_bot_process(preflight, mode) if bot_start_requested else {}
    if active_bot_process and not ensure_bot:
        findings.append(
            GuardianFinding(
                "runtime.bot_process_inventory",
                "WARN",
                "hard_fail",
                "active bot process blocks duplicate start",
                active_bot_process,
            )
        )
    unmatched_active_bot_lock = (
        _unmatched_active_bot_lock(preflight, mode)
        if bot_start_requested and not active_bot_process
        else {}
    )
    if unmatched_active_bot_lock:
        findings.append(
            GuardianFinding(
                "runtime.bot_pid_lock_inventory_mismatch",
                "WARN",
                "hard_fail",
                "active PID lock has no matching bot process",
                unmatched_active_bot_lock,
            )
        )
    inventory_unavailable = _process_inventory_unavailable(preflight) if bot_start_requested else {}
    if inventory_unavailable and str(mode or "").lower() == "live":
        findings.append(
            GuardianFinding(
                "runtime.process_inventory_start_guard",
                "FAIL",
                "hard_fail",
                "process inventory unavailable; refusing live bot start",
                inventory_unavailable,
            )
        )

    hard_fail = [finding for finding in findings if finding.classification == "hard_fail"]
    soft_fail = [finding for finding in findings if finding.classification == "soft_fail"]
    accepted_exception = [finding for finding in findings if finding.classification == "accepted_exception"]
    auto_fixable = [finding for finding in findings if finding.classification == "auto_fixable"]
    action_fail = [action for action in actions if action.status == "FAIL"]
    allow_start = not hard_fail and not action_fail
    active_bot_lock = _active_bot_lock(preflight, mode) if ensure_bot else {}
    current_blockers = [asdict(finding) for finding in hard_fail]
    historical_remediation_items = [
        asdict(finding)
        for finding in findings
        if finding.classification != "hard_fail"
        and bool((finding.data or {}).get("remediation_commands"))
    ]

    if allow_start and bot_start_requested and (active_bot_lock or active_bot_process):
        actions.append(
            GuardianAction(
                "start_bot",
                "SKIP",
                "bot is already running",
                active_bot_lock or active_bot_process,
            )
        )
    elif allow_start and bot_start_requested and not bot_start_allowed:
        actions.append(
            GuardianAction(
                "start_bot",
                "SKIP",
                bot_start_skip_detail or "bot start suppressed by guardian restart guard",
                {"restart_guard": True},
            )
        )
    elif allow_start and bot_start_requested:
        if dry_run_start:
            action = GuardianAction(
                "start_bot",
                "DRY_RUN",
                "bot start would be requested",
                {"mode": mode, "dry_run": True},
            )
        else:
            action = _start_bot(mode)
        actions.append(action)
        if action.status == "FAIL":
            allow_start = False
            action_fail.append(action)

    report = {
        "ok": allow_start,
        "mode": mode,
        "generated_at": _now_kst().isoformat(timespec="seconds"),
        "env_loaded": env_loaded,
        "enabled_markets": markets,
        "gate": "ALLOW_START" if allow_start else "BLOCK_START",
        "counts": {
            "hard_fail": len(hard_fail),
            "soft_fail": len(soft_fail),
            "accepted_exception": len(accepted_exception),
            "auto_fixable": len(auto_fixable),
            "actions": len(actions),
            "action_fail": len(action_fail),
            "current_blockers": len(current_blockers),
            "historical_remediation_items": len(historical_remediation_items),
        },
        "current_blockers": current_blockers,
        "historical_remediation_items": historical_remediation_items,
        "findings": [asdict(finding) for finding in findings],
        "actions": [asdict(action) for action in actions],
        "preflight": {
            "ok": preflight.get("ok"),
            "fail_count": preflight.get("fail_count"),
            "warn_count": preflight.get("warn_count"),
        },
        "smoke": smoke,
    }
    json_path, md_path = _write_guardian_report(report)
    report["report_paths"] = {"json": str(json_path), "md": str(md_path)}
    _write_guardian_heartbeat(
        mode,
        status="success" if report.get("ok") else "blocked",
        last_success_at=_now_kst().isoformat(timespec="seconds"),
        report_path=str(json_path),
    )
    return report


def _write_guardian_report(report: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = get_runtime_path("data", "v2_reports", make_parents=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = _now_kst().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"live_guardian_{stamp}.json"
    md_path = out_dir / f"live_guardian_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Live Guardian Report {stamp}",
        "",
        f"- ok: {report['ok']}",
        f"- gate: {report['gate']}",
        f"- mode: {report['mode']}",
        f"- enabled_markets: {', '.join(report.get('enabled_markets') or [])}",
        f"- hard_fail: {report['counts']['hard_fail']}",
        f"- soft_fail: {report['counts']['soft_fail']}",
        f"- accepted_exception: {report['counts'].get('accepted_exception', 0)}",
        f"- auto_fixable: {report['counts']['auto_fixable']}",
        f"- current_blockers: {report['counts'].get('current_blockers', 0)}",
        f"- historical_remediation_items: {report['counts'].get('historical_remediation_items', 0)}",
        f"- actions: {report['counts']['actions']}",
        "",
        "## Current Blockers",
        "",
    ]
    for finding in report.get("current_blockers") or []:
        lines.append(
            f"- `{finding.get('name')}` ({finding.get('status')}): {finding.get('detail')}"
        )
    if not report.get("current_blockers"):
        lines.append("- none")
    lines.extend([
        "",
        "## Historical Remediation Items",
        "",
    ])
    for finding in report.get("historical_remediation_items") or []:
        lines.append(
            f"- `{finding.get('name')}` ({finding.get('status')}): {finding.get('detail')}"
        )
        for command in (finding.get("data") or {}).get("remediation_commands") or []:
            lines.append(f"  - remediation: `{command}`")
    if not report.get("historical_remediation_items"):
        lines.append("- none")
    lines.extend([
        "",
        "## Findings",
        "",
    ])
    for finding in report.get("findings", []):
        if finding.get("classification") == "pass":
            continue
        lines.append(
            f"- {finding.get('classification')} `{finding.get('name')}` "
            f"({finding.get('status')}): {finding.get('detail')}"
        )
        operator_action = str((finding.get("data") or {}).get("operator_action") or "").strip()
        if operator_action and operator_action != "none":
            lines.append(f"  - operator_action: {operator_action}")
        if (finding.get("data") or {}).get("auto_apply_allowed") is False:
            lines.append("  - auto_apply_allowed: false")
        for command in (finding.get("data") or {}).get("remediation_commands") or []:
            lines.append(f"  - remediation: `{command}`")
    lines.extend(["", "## Actions", ""])
    for action in report.get("actions", []):
        lines.append(f"- {action.get('status')} `{action.get('name')}`: {action.get('detail')}")
    if not report.get("actions"):
        lines.append("- none")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def _alert_state_path(mode: str) -> Path:
    return get_runtime_path("state", f"{mode}_guardian_alert_state.json")


def _load_alert_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_alert_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _alert_items(report: dict[str, Any], *, include_soft: bool = False) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        classification = str(finding.get("classification") or "")
        if classification == "hard_fail" or (include_soft and classification == "soft_fail"):
            items.append(
                {
                    "kind": "finding",
                    "name": str(finding.get("name") or ""),
                    "status": str(finding.get("status") or ""),
                    "classification": classification,
                    "detail": str(finding.get("detail") or ""),
                }
            )
    for action in report.get("actions") or []:
        if isinstance(action, dict) and str(action.get("status") or "") == "FAIL":
            items.append(
                {
                    "kind": "action",
                    "name": str(action.get("name") or ""),
                    "status": "FAIL",
                    "classification": "action_fail",
                    "detail": str(action.get("detail") or ""),
                }
            )
    return items


def _alert_fingerprint(items: list[dict[str, str]]) -> str:
    return json.dumps(
        [
            {
                "kind": item.get("kind", ""),
                "name": item.get("name", ""),
                "status": item.get("status", ""),
                "classification": item.get("classification", ""),
                "detail": item.get("detail", ""),
            }
            for item in items
        ],
        ensure_ascii=False,
        sort_keys=True,
    )


def _format_alert_message(report: dict[str, Any], items: list[dict[str, str]], *, recovered: bool = False) -> str:
    paths = report.get("report_paths") or {}
    if recovered:
        return (
            "<b>Live Guardian RECOVERED</b>\n"
            f"mode={html.escape(str(report.get('mode') or ''))} "
            f"gate={html.escape(str(report.get('gate') or ''))}\n"
            f"report={html.escape(str(paths.get('md') or paths.get('json') or ''))}"
        )
    counts = report.get("counts") or {}
    lines = [
        "<b>Live Guardian ALERT</b>",
        (
            f"mode={html.escape(str(report.get('mode') or ''))} "
            f"gate={html.escape(str(report.get('gate') or ''))} "
            f"hard={html.escape(str(counts.get('hard_fail', 0)))} "
            f"soft={html.escape(str(counts.get('soft_fail', 0)))}"
        ),
    ]
    for item in items[:8]:
        detail = str(item.get("detail") or "")
        if len(detail) > 160:
            detail = detail[:157] + "..."
        lines.append(
            "- "
            f"{html.escape(str(item.get('classification') or ''))} "
            f"{html.escape(str(item.get('name') or ''))}: "
            f"{html.escape(detail)}"
        )
    if len(items) > 8:
        lines.append(f"- ... +{len(items) - 8} more")
    lines.append(f"report={html.escape(str(paths.get('md') or paths.get('json') or ''))}")
    return "\n".join(lines)


def _maybe_send_telegram_alert(
    report: dict[str, Any],
    *,
    include_soft: bool = False,
    state_path: Path | None = None,
) -> GuardianAction:
    path = state_path or _alert_state_path(str(report.get("mode") or "live"))
    state = _load_alert_state(path)
    previous = str(state.get("fingerprint") or "")
    items = _alert_items(report, include_soft=include_soft)
    fingerprint = _alert_fingerprint(items) if items else ""
    if fingerprint == previous:
        return GuardianAction("telegram_alert", "SKIP", "alert state unchanged", {"state_path": str(path)})
    if not items and not previous:
        return GuardianAction("telegram_alert", "SKIP", "no alert-worthy findings", {"state_path": str(path)})

    recovered = not items and bool(previous)
    message = _format_alert_message(report, items, recovered=recovered)
    try:
        from telegram_reporter import send

        sent = bool(send(message))
        _save_alert_state(
            path,
            {
                "fingerprint": fingerprint,
                "updated_at": _now_kst().isoformat(timespec="seconds"),
                "gate": report.get("gate"),
            },
        )
        return GuardianAction(
            "telegram_alert",
            "PASS" if sent else "SKIP",
            "telegram alert sent" if sent else "telegram credentials missing or send skipped",
            {"state_path": str(path), "recovered": recovered},
        )
    except Exception as exc:
        return GuardianAction("telegram_alert", "FAIL", f"telegram alert failed: {exc}", {"state_path": str(path)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Conservative live-operation guardian.")
    parser.add_argument("--mode", choices=["live", "paper"], default="live")
    parser.add_argument("--env", default="")
    parser.add_argument("--auto-fix", action="store_true")
    parser.add_argument("--start-dashboard", action="store_true")
    parser.add_argument("--start-bot", action="store_true")
    parser.add_argument("--ensure-bot", action="store_true", help="Start the bot if missing; treat an already-running bot as healthy.")
    parser.add_argument("--dry-run-start", action="store_true", help="Report bot start intent without launching a process.")
    parser.add_argument("--skip-dashboard", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true", help="Skip no-broker/no-order smoke to avoid lifecycle writes in report-only QA.")
    parser.add_argument("--session-date", default="")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--restart-cooldown-sec", type=int, default=300)
    parser.add_argument("--max-start-failures", type=int, default=3)
    parser.add_argument("--telegram-alert", action="store_true", help="Send Telegram alert when hard failures change; does not stop or restart the bot.")
    parser.add_argument("--alert-soft", action="store_true", help="Include soft failures in Telegram alert fingerprint.")
    parser.add_argument("--json", action="store_true", dest="print_json")
    args = parser.parse_args()

    iteration = 0
    last_report: dict[str, Any] = {}
    last_bot_start_at = 0.0
    consecutive_start_failures = 0
    while True:
        iteration += 1
        bot_start_allowed = True
        bot_start_skip_detail = ""
        bot_start_requested = bool(args.start_bot or args.ensure_bot)
        if args.watch and bot_start_requested:
            cooldown_left = int(max(0.0, float(args.restart_cooldown_sec or 300) - (time.time() - last_bot_start_at)))
            if last_bot_start_at and cooldown_left > 0:
                bot_start_allowed = False
                bot_start_skip_detail = f"bot restart cooldown active for {cooldown_left}s"
            elif consecutive_start_failures >= int(args.max_start_failures or 3):
                bot_start_allowed = False
                bot_start_skip_detail = (
                    f"bot start suppressed after {consecutive_start_failures} consecutive failures"
                )
        last_report = run_guardian_once(
            mode=args.mode,
            env=args.env,
            auto_fix=args.auto_fix,
            start_dashboard=args.start_dashboard,
            start_bot=args.start_bot,
            ensure_bot=args.ensure_bot,
            dry_run_start=args.dry_run_start,
            skip_dashboard=args.skip_dashboard,
            skip_smoke=args.skip_smoke,
            session_date=args.session_date,
            bot_start_allowed=bot_start_allowed,
            bot_start_skip_detail=bot_start_skip_detail,
        )
        alert_action: GuardianAction | None = None
        if args.telegram_alert:
            alert_action = _maybe_send_telegram_alert(last_report, include_soft=args.alert_soft)
            last_report["telegram_alert"] = asdict(alert_action)
        for action in last_report.get("actions") or []:
            if action.get("name") != "start_bot":
                continue
            if action.get("status") == "PASS":
                last_bot_start_at = time.time()
                consecutive_start_failures = 0
            elif action.get("status") == "FAIL":
                consecutive_start_failures += 1
        if args.print_json:
            print(json.dumps(last_report, ensure_ascii=False, indent=2))
        else:
            paths = last_report.get("report_paths") or {}
            print(
                f"gate={last_report['gate']} hard={last_report['counts']['hard_fail']} "
                f"soft={last_report['counts']['soft_fail']} auto_fixable={last_report['counts']['auto_fixable']}"
            )
            print(f"json={paths.get('json', '')}")
            print(f"md={paths.get('md', '')}")
            if alert_action is not None:
                print(f"telegram_alert={alert_action.status}: {alert_action.detail}")
        if not args.watch:
            break
        if args.max_iterations and iteration >= args.max_iterations:
            break
        time.sleep(max(5, int(args.interval_sec or 60)))
    return 0 if last_report.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
