from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEvent
from runtime.broker_truth_snapshot import BrokerTruthSnapshot
from runtime.market_resolver import resolve_position_market
from runtime_paths import get_runtime_path


WRITER_PROCESS_KINDS = {"live_bot", "guardian"}
BACKUP_STATE_FILES = (
    "live_open_positions.json",
    "live_pending_orders.json",
    "live_broker_truth_snapshot.json",
    "live_trading_bot.pid",
    "dashboard_server.pid",
)
ACTIVE_PATH_RUN_STATUSES = {
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _safe_label(value: str) -> str:
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "maintenance").strip())
    return label.strip("._") or "maintenance"


def _normalize_mode(value: str) -> str:
    mode = str(value or "live").strip().lower()
    return mode if mode in {"live", "paper"} else "live"


def _normalize_market(value: str) -> str:
    market = str(value or "").strip().upper()
    if market not in {"KR", "US"}:
        raise ValueError(f"unsupported market: {value!r}")
    return market


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value or "").replace(",", "")))
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value or "").replace(",", ""))
    except Exception:
        return float(default)


def _cmdline_list(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item or "").strip()]
    if value is None:
        return []
    return [str(value)]


def _cmdline_text(cmdline: Iterable[str]) -> str:
    return " ".join(str(item) for item in cmdline).replace("\\", "/")


def _cmdline_has_option_value(cmdline: Iterable[str], option: str, value: str) -> bool:
    tokens = [str(item).strip().strip("\"'").lower() for item in cmdline if str(item or "").strip()]
    option_key = str(option or "").strip().lower()
    expected = str(value or "").strip().lower()
    for idx, token in enumerate(tokens):
        if token == option_key:
            if idx + 1 < len(tokens) and tokens[idx + 1].strip("\"'").lower() == expected:
                return True
            continue
        if token.startswith(f"{option_key}=") and token.split("=", 1)[1].strip("\"'").lower() == expected:
            return True
    text = _cmdline_text(tokens).lower()
    pattern = rf"(?:^|\s){re.escape(option_key)}(?:=|\s+){re.escape(expected)}(?:\s|$)"
    return bool(re.search(pattern, text))


def _cmdline_is_live_mode(cmdline: Iterable[str]) -> bool:
    return not _cmdline_has_option_value(cmdline, "--mode", "paper")


def _classify_process(cmdline: Iterable[str]) -> str:
    text = _cmdline_text(cmdline).lower()
    if "trading_bot.py" in text and "--live" in text:
        return "live_bot"
    if "live_guardian.py" in text and "--watch" in text and _cmdline_is_live_mode(cmdline):
        return "guardian"
    if "dashboard_server.py" in text:
        return "dashboard"
    if "preopen_scheduler.py" in text and _cmdline_is_live_mode(cmdline):
        return "preopen_scheduler"
    return ""


def discover_live_processes(process_iter: Iterable[Any] | None = None) -> list[dict[str, Any]]:
    """Return known live-maintenance process candidates without mutating state."""
    if process_iter is None:
        try:
            import psutil  # type: ignore
        except Exception:
            return []
        process_iter = psutil.process_iter(["pid", "name", "cmdline", "status"])

    processes: list[dict[str, Any]] = []
    for proc in process_iter:
        try:
            if isinstance(proc, dict):
                info = dict(proc)
            else:
                info = dict(getattr(proc, "info", {}) or {})
                if not info:
                    info = {
                        "pid": getattr(proc, "pid", None),
                        "name": proc.name(),
                        "cmdline": proc.cmdline(),
                        "status": proc.status(),
                    }
            cmdline = _cmdline_list(info.get("cmdline"))
            kind = _classify_process(cmdline)
            if not kind:
                continue
            processes.append(
                {
                    "pid": info.get("pid"),
                    "name": str(info.get("name") or ""),
                    "kind": kind,
                    "cmdline": cmdline,
                    "status": str(info.get("status") or ""),
                    "alive": bool(info.get("alive", True)),
                }
            )
        except Exception:
            continue
    return sorted(processes, key=lambda item: (str(item.get("kind") or ""), int(item.get("pid") or 0)))


def assert_writer_freeze(processes: Iterable[dict[str, Any]]) -> None:
    active = [
        item
        for item in processes
        if str(item.get("kind") or "") in WRITER_PROCESS_KINDS and bool(item.get("alive", True))
    ]
    if active:
        details = ", ".join(f"{item.get('kind')}:{item.get('pid')}" for item in active)
        raise RuntimeError(f"live writer processes are active: {details}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entry(source: Path, backup: Path, *, role: str) -> dict[str, Any]:
    stat = backup.stat()
    return {
        "role": role,
        "source": str(source),
        "backup": str(backup),
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "sha256": _sha256(backup),
    }


def _copy_optional_file(source: Path, backup_dir: Path, *, role: str, manifest: list[dict[str, Any]]) -> None:
    if not source.exists():
        return
    target = backup_dir / source.name
    shutil.copy2(source, target)
    manifest.append(_manifest_entry(source, target, role=role))


def _copy_best_effort_file(source: Path, backup_dir: Path, *, role: str, optional: bool = True) -> dict[str, Any] | None:
    if not source.exists():
        return None
    target = backup_dir / source.name
    try:
        shutil.copy2(source, target)
        return _manifest_entry(source, target, role=role)
    except OSError as exc:
        if not optional:
            raise
        return {
            "role": role,
            "source": str(source),
            "backup": str(target),
            "optional": True,
            "copied": False,
            "error": str(exc),
        }


def _backup_sqlite_db(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(target)) as dst:
        src.backup(dst)
    with sqlite3.connect(str(target)) as conn:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    if not row or str(row[0]).lower() != "ok":
        raise RuntimeError(f"backup integrity_check failed: {target}")


def create_live_backup(
    label: str,
    *,
    mode: str = "live",
    backup_root: str | Path | None = None,
    db_path: str | Path | None = None,
) -> Path:
    mode_key = _normalize_mode(mode)
    backup_base = Path(backup_root) if backup_root else get_runtime_path("data", "backups")
    backup_dir = backup_base / f"live_maintenance_{_timestamp_slug()}_{_safe_label(label)}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    source_db = Path(db_path) if db_path else get_runtime_path("data", "v2_event_store.db", make_parents=False)
    if not source_db.exists():
        raise FileNotFoundError(f"event store DB not found: {source_db}")

    manifest: list[dict[str, Any]] = []
    optional_errors: list[dict[str, Any]] = []
    db_target = backup_dir / source_db.name
    _backup_sqlite_db(source_db, db_target)
    manifest.append(_manifest_entry(source_db, db_target, role="sqlite_backup"))
    for suffix in ("-wal", "-shm"):
        entry = _copy_best_effort_file(Path(str(source_db) + suffix), backup_dir, role=f"sqlite{suffix}")
        if entry is None:
            continue
        if entry.get("copied") is False:
            optional_errors.append(entry)
            continue
        manifest.append(entry)

    for name in BACKUP_STATE_FILES:
        _copy_optional_file(
            get_runtime_path("state", name, make_parents=False),
            backup_dir,
            role="state",
            manifest=manifest,
        )

    manifest_payload = {
        "created_at": _now(),
        "mode": mode_key,
        "label": label,
        "files": manifest,
        "optional_errors": optional_errors,
    }
    (backup_dir / "manifest.json").write_text(
        json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return backup_dir


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception:
        return default


def _atomic_json_dump(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _position_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_open_positions.json", make_parents=False)


def _path_run_id_from_position(pos: dict[str, Any]) -> str:
    return str(pos.get("pathb_path_run_id") or pos.get("path_run_id") or "").strip()


def _infer_position_market(pos: dict[str, Any]) -> str:
    resolved = resolve_position_market(pos, unknown="")
    if resolved:
        return resolved
    path_run_id = _path_run_id_from_position(pos)
    if "_US_" in path_run_id:
        return "US"
    if "_KR_" in path_run_id:
        return "KR"
    return ""


def _position_matches(pos: dict[str, Any], *, market: str, ticker: str, path_run_id: str = "") -> bool:
    market_key = _normalize_market(market)
    if _infer_position_market(pos) != market_key:
        return False
    if _ticker_key(market_key, pos.get("ticker")) != _ticker_key(market_key, ticker):
        return False
    if path_run_id and _path_run_id_from_position(pos) != str(path_run_id).strip():
        return False
    return True


def _market_broker_truth(broker_truth: dict[str, Any], market: str) -> dict[str, Any]:
    market_key = _normalize_market(market)
    markets = broker_truth.get("markets") if isinstance(broker_truth, dict) else None
    if isinstance(markets, dict):
        data = markets.get(market_key)
        return dict(data) if isinstance(data, dict) else {"missing": True, "stale": True}
    return dict(broker_truth or {})


def _row_order_id(row: dict[str, Any]) -> str:
    return str(row.get("order_no") or row.get("execution_id") or row.get("odno") or "").strip()


def _row_ticker(row: dict[str, Any], market: str) -> str:
    return _ticker_key(market, row.get("ticker") or row.get("pdno") or row.get("ovrs_pdno") or "")


def _is_sell(row: dict[str, Any]) -> bool:
    side = str(row.get("side") or row.get("sll_buy_dvsn_cd_name") or row.get("trad_dvsn_name") or "").strip().lower()
    return side in {"sell", "s", "매도"} or "sell" in side or "매도" in side


def broker_position_evidence(
    broker_truth: dict[str, Any],
    *,
    market: str,
    ticker: str,
    sell_order_id: str = "",
) -> dict[str, Any]:
    market_key = _normalize_market(market)
    data = _market_broker_truth(broker_truth, market_key)
    target_ticker = _ticker_key(market_key, ticker)
    positions = [
        row
        for row in data.get("positions", []) or []
        if _row_ticker(row, market_key) == target_ticker and _safe_int(row.get("qty"), 0) > 0
    ]
    open_orders = [
        row
        for row in data.get("open_orders", []) or []
        if _row_ticker(row, market_key) == target_ticker and _safe_int(row.get("remaining_qty"), 0) > 0
    ]
    fills = [
        row
        for row in data.get("today_fills", []) or []
        if _row_ticker(row, market_key) == target_ticker and _safe_int(row.get("filled_qty"), 0) > 0
    ]
    sell_order_key = str(sell_order_id or "").strip()
    if sell_order_key:
        sell_fills = [row for row in fills if _row_order_id(row) == sell_order_key and _is_sell(row)]
        sell_open_orders = [row for row in open_orders if _row_order_id(row) == sell_order_key]
    else:
        sell_fills = [row for row in fills if _is_sell(row)]
        sell_open_orders = open_orders
    return {
        "missing": bool(data.get("missing")),
        "stale": bool(data.get("stale")),
        "last_success_at": str(data.get("last_success_at") or ""),
        "error": str(data.get("error") or ""),
        "positions": positions,
        "open_orders": open_orders,
        "today_fills": fills,
        "sell_fills": sell_fills,
        "sell_open_orders": sell_open_orders,
        "has_position": bool(positions),
        "has_open_order": bool(open_orders),
        "has_sell_fill": bool(sell_fills),
        "sell_order_id": sell_order_key,
    }


def _status_transition_for_absent_position(
    *,
    evidence: dict[str, Any],
    sell_order_id: str = "",
) -> tuple[str, str, str]:
    if evidence.get("has_sell_fill"):
        return "remove_local", "CLOSED", "BROKER_SELL_FILL_RECONCILED"
    if str(sell_order_id or "").strip():
        return "manual_review", "", "BROKER_POSITION_ABSENT_SELL_FILL_UNCONFIRMED"
    return "remove_local", "CANCELLED", "BROKER_POSITION_ABSENT_RECONCILED"


def _event_type_for_next_status(next_status: str) -> str:
    if next_status == "CLOSED":
        return "CLOSED"
    return "ORDER_CANCELLED"


def _plan_update_for_next_status(next_status: str, *, evidence: dict[str, Any], reason_code: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "broker_position_absent_reconciled": True,
        "broker_position_reconciled_at": _now(),
        "broker_truth_last_success_at": evidence.get("last_success_at", ""),
        "broker_truth_evidence": {
            "positions": evidence.get("positions", []),
            "open_orders": evidence.get("open_orders", []),
            "today_fills": evidence.get("today_fills", []),
            "sell_fills": evidence.get("sell_fills", []),
            "sell_order_id": evidence.get("sell_order_id", ""),
        },
    }
    if next_status == "CLOSED":
        sell_fill = (evidence.get("sell_fills") or [{}])[0]
        payload.update(
            {
                "exit_fill_confirmed": True,
                "exit_execution_id": _row_order_id(sell_fill),
                "exit_fill_qty": _safe_int(sell_fill.get("filled_qty"), 0),
                "actual_exit_price": _safe_float(sell_fill.get("avg_price") or sell_fill.get("fill_price") or sell_fill.get("price")),
                "close_reason": reason_code,
            }
        )
    else:
        payload.update({"cancel_reason": reason_code, "order_absent_reconciled": True})
    return payload


def _append_reconcile_event(
    store: EventStore,
    run: dict[str, Any] | None,
    *,
    market: str,
    ticker: str,
    path_run_id: str,
    next_status: str,
    reason_code: str,
    evidence: dict[str, Any],
    operator: str = "",
    reason: str = "",
) -> None:
    run = run or {}
    sell_fill = (evidence.get("sell_fills") or [{}])[0]
    store.append(
        LifecycleEvent(
            event_type=_event_type_for_next_status(next_status),
            market=market,
            runtime_mode=str(run.get("runtime_mode") or "live"),
            session_date=str(run.get("session_date") or ""),
            ticker=ticker,
            decision_id=str(run.get("decision_id") or f"manual_reconcile_{path_run_id or ticker}"),
            execution_id=_row_order_id(sell_fill) or evidence.get("sell_order_id") or None,
            prompt_version="live_maintenance",
            brain_snapshot_id="manual_reconcile",
            reason_code=reason_code,
            payload={
                "source": "tools.live_maintenance",
                "path_type": "claude_price",
                "path_run_id": path_run_id,
                "operator": operator,
                "reason": reason or reason_code,
                "reconciled_at": _now(),
                "next_status": next_status,
                "broker_evidence": evidence,
            },
        )
    )


def reconcile_local_position_against_broker(
    *,
    market: str,
    ticker: str,
    path_run_id: str = "",
    broker_truth: dict[str, Any],
    mode: str = "live",
    store: EventStore | None = None,
    store_path: str | Path | None = None,
    positions_path: str | Path | None = None,
    sell_order_id: str = "",
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    operator: str = "",
    reason: str = "",
) -> dict[str, Any]:
    market_key = _normalize_market(market)
    ticker_key = _ticker_key(market_key, ticker)
    positions_file = Path(positions_path) if positions_path else _position_path(mode)
    positions_raw = _load_json(positions_file, [])
    positions = positions_raw if isinstance(positions_raw, list) else []
    matches = [
        idx
        for idx, pos in enumerate(positions)
        if isinstance(pos, dict) and _position_matches(pos, market=market_key, ticker=ticker_key, path_run_id=path_run_id)
    ]
    local_position = positions[matches[0]] if matches else None
    local_sell_order_id = ""
    if isinstance(local_position, dict):
        local_sell_order_id = str(local_position.get("pathb_pending_sell_order_no") or "").strip()
    effective_sell_order_id = str(sell_order_id or local_sell_order_id or "").strip()
    evidence = broker_position_evidence(
        broker_truth,
        market=market_key,
        ticker=ticker_key,
        sell_order_id=effective_sell_order_id,
    )
    if store is not None:
        run_store = store
    elif store_path or path_run_id:
        run_store = EventStore(store_path) if store_path else EventStore()
    else:
        run_store = None
    run = run_store.find_path_run(path_run_id) if run_store is not None and path_run_id else None

    action = "manual_review"
    next_status = ""
    reason_code = "BROKER_TRUTH_UNAVAILABLE"
    if evidence.get("missing") or evidence.get("stale"):
        reason_code = "BROKER_TRUTH_MISSING_OR_STALE"
    elif evidence.get("has_position"):
        action = "keep"
        reason_code = "BROKER_POSITION_EXISTS"
    elif evidence.get("has_open_order"):
        action = "manual_review"
        reason_code = "BROKER_OPEN_ORDER_EXISTS"
    else:
        action, next_status, reason_code = _status_transition_for_absent_position(
            evidence=evidence,
            sell_order_id=effective_sell_order_id,
        )

    changes: list[dict[str, Any]] = []
    after_positions = list(positions)
    if action == "remove_local" and matches:
        after_positions = [pos for idx, pos in enumerate(positions) if idx not in set(matches)]
        changes.append({"type": "remove_local_position", "count": len(matches), "positions_path": str(positions_file)})
    if action == "remove_local" and run is not None and next_status:
        changes.append(
            {
                "type": "update_path_run",
                "path_run_id": path_run_id,
                "status_before": run.get("status"),
                "status_after": next_status,
            }
        )
    elif action == "remove_local" and path_run_id:
        changes.append({"type": "path_run_missing", "path_run_id": path_run_id})

    applied = False
    if not dry_run and action == "remove_local":
        if backup_dir is None:
            raise ValueError("backup_dir is required for apply-mode local position reconciliation")
        _atomic_json_dump(positions_file, after_positions)
        if run_store is not None and run is not None and next_status:
            run_store.update_path_run(
                path_run_id,
                status=next_status,
                plan=_plan_update_for_next_status(next_status, evidence=evidence, reason_code=reason_code),
                merge_plan=True,
            )
            _append_reconcile_event(
                run_store,
                run,
                market=market_key,
                ticker=ticker_key,
                path_run_id=path_run_id,
                next_status=next_status,
                reason_code=reason_code,
                evidence=evidence,
                operator=operator,
                reason=reason,
            )
        applied = True

    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "applied": applied,
        "mode": _normalize_mode(mode),
        "market": market_key,
        "ticker": ticker_key,
        "path_run_id": path_run_id,
        "action": action,
        "reason_code": reason_code,
        "next_status": next_status,
        "local_position_found": local_position is not None,
        "local_match_count": len(matches),
        "status_before": run.get("status") if run else "",
        "broker_evidence": evidence,
        "sell_order_id": effective_sell_order_id,
        "positions_before_count": len(positions),
        "positions_after_count": len(after_positions),
        "positions_path": str(positions_file),
        "backup_dir": str(backup_dir or ""),
        "changes": changes,
    }


def reconcile_positions_against_broker(
    *,
    market: str,
    broker_truth: dict[str, Any],
    mode: str = "live",
    store_path: str | Path | None = None,
    positions_path: str | Path | None = None,
    dry_run: bool = True,
    backup_dir: str | Path | None = None,
    operator: str = "",
    reason: str = "",
) -> dict[str, Any]:
    market_key = _normalize_market(market)
    positions_file = Path(positions_path) if positions_path else _position_path(mode)
    positions_raw = _load_json(positions_file, [])
    positions = positions_raw if isinstance(positions_raw, list) else []
    target_positions = [
        pos
        for pos in positions
        if isinstance(pos, dict) and _infer_position_market(pos) == market_key
    ]
    store = EventStore(store_path) if store_path else EventStore()
    results: list[dict[str, Any]] = []
    for pos in target_positions:
        result = reconcile_local_position_against_broker(
            market=market_key,
            ticker=str(pos.get("ticker") or ""),
            path_run_id=_path_run_id_from_position(pos),
            broker_truth=broker_truth,
            mode=mode,
            store=store,
            positions_path=positions_file,
            dry_run=dry_run,
            backup_dir=backup_dir,
            operator=operator,
            reason=reason,
        )
        results.append(result)

    remove_keys = {
        (item.get("ticker"), item.get("path_run_id"))
        for item in results
        if item.get("action") == "remove_local"
    }
    after_positions = [
        pos
        for pos in positions
        if (
            _ticker_key(market_key, pos.get("ticker")) if isinstance(pos, dict) else "",
            _path_run_id_from_position(pos) if isinstance(pos, dict) else "",
        )
        not in remove_keys
    ]
    return {
        "ok": True,
        "dry_run": bool(dry_run),
        "mode": _normalize_mode(mode),
        "market": market_key,
        "positions_before_count": len(positions),
        "market_positions_before_count": len(target_positions),
        "positions_after_count": len(after_positions),
        "removed_count": len(positions) - len(after_positions),
        "positions_path": str(positions_file),
        "backup_dir": str(backup_dir or ""),
        "results": results,
    }


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _effective_env(mode: str = "live") -> dict[str, str]:
    mode_key = _normalize_mode(mode)
    effective: dict[str, str] = {}
    effective.update(_read_env_file(ROOT / ".env"))
    effective.update(_read_env_file(ROOT / f".env.{mode_key}"))
    effective.update({key: str(value) for key, value in os.environ.items()})
    return effective


def _credential_policy_summary(mode: str = "live") -> dict[str, Any]:
    effective = _effective_env(mode)
    us_account = bool(str(effective.get("KIS_ACCOUNT_NO_US", "")).strip())
    kr_account = bool(str(effective.get("KIS_ACCOUNT_NO", "")).strip())
    us_app = bool(str(effective.get("KIS_APP_KEY_US", "")).strip() and str(effective.get("KIS_APP_SECRET_US", "")).strip())
    kr_app = bool(str(effective.get("KIS_APP_KEY", "")).strip() and str(effective.get("KIS_APP_SECRET", "")).strip())
    mode = "separate_us" if us_account and us_app else "fallback_shared_kr" if kr_account and kr_app else "missing"
    accepted = _truthy(effective.get("KIS_US_CREDENTIAL_FALLBACK_ACCEPTED"))
    return {
        "credential_mode": mode,
        "fallback_to_kr_allowed": mode == "fallback_shared_kr",
        "fallback_accepted_by_policy": accepted,
        "accepted_exception": mode == "fallback_shared_kr" and accepted,
        "warning": mode == "fallback_shared_kr" and not accepted,
    }


def broker_truth_report(
    *,
    mode: str = "live",
    market: str = "US",
    refresh: bool = False,
    snapshot_path: str | Path | None = None,
    ttl_sec: int = 30,
) -> dict[str, Any]:
    market_key = _normalize_market(market)
    snapshot = BrokerTruthSnapshot(runtime_mode=_normalize_mode(mode), path=snapshot_path)
    if refresh:
        snapshot.refresh_market(market_key, force=True, ttl_sec=ttl_sec)
    data = snapshot.market_snapshot(market_key, ttl_sec=ttl_sec)
    return {
        "ok": not bool(data.get("missing")) and not bool(data.get("stale")),
        "mode": _normalize_mode(mode),
        "market": market_key,
        "positions": data.get("positions", []) or [],
        "open_orders": data.get("open_orders", []) or [],
        "today_fills": data.get("today_fills", []) or [],
        "last_success_at": str(data.get("last_success_at") or ""),
        "stale": bool(data.get("stale")),
        "missing": bool(data.get("missing")),
        "error": str(data.get("error") or ""),
        "account_summary": data.get("account_summary", {}) or {},
        "credential_policy": _credential_policy_summary(mode) if market_key == "US" else {},
    }


def _load_broker_truth_payload(args: argparse.Namespace) -> dict[str, Any]:
    market_key = _normalize_market(args.market)
    snapshot = BrokerTruthSnapshot(runtime_mode=_normalize_mode(args.mode), path=args.snapshot_path or None)
    if getattr(args, "refresh", False):
        snapshot.refresh_market(market_key, force=True, ttl_sec=30)
    data = snapshot.market_snapshot(market_key, ttl_sec=30)
    return data


def _print_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _ensure_apply_preconditions(args: argparse.Namespace) -> Path:
    processes = discover_live_processes()
    assert_writer_freeze(processes)
    backup_dir = Path(args.backup_dir) if getattr(args, "backup_dir", "") else create_live_backup(
        f"before_{args.command}",
        mode=args.mode,
        db_path=args.store_path or None,
    )
    return backup_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Guarded live maintenance helpers.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="Report live maintenance processes.")
    status.add_argument("--mode", default="live")
    status.add_argument("--json", action="store_true")
    status.add_argument("--require-frozen", action="store_true")

    backup = subparsers.add_parser("backup", help="Create a timestamped live-state backup.")
    backup.add_argument("--mode", default="live")
    backup.add_argument("--label", default="maintenance")
    backup.add_argument("--backup-root", default="")
    backup.add_argument("--db-path", default="")
    backup.add_argument("--json", action="store_true")

    truth = subparsers.add_parser("broker-truth", help="Report broker truth snapshot for one market.")
    truth.add_argument("--mode", default="live")
    truth.add_argument("--market", default="US", choices=["KR", "US"])
    truth.add_argument("--refresh", action="store_true")
    truth.add_argument("--snapshot-path", default="")
    truth.add_argument("--json", action="store_true")

    def add_reconcile_common(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--mode", default="live")
        sub.add_argument("--market", default="US", choices=["KR", "US"])
        sub.add_argument("--snapshot-path", default="")
        sub.add_argument("--store-path", default="")
        sub.add_argument("--positions-path", default="")
        sub.add_argument("--backup-dir", default="")
        sub.add_argument("--operator", default="")
        sub.add_argument("--reason", default="")
        sub.add_argument("--refresh", action="store_true")
        sub.add_argument("--json", action="store_true")
        group = sub.add_mutually_exclusive_group()
        group.add_argument("--dry-run", action="store_true", default=True)
        group.add_argument("--apply", action="store_true")

    one = subparsers.add_parser("reconcile-position", help="Reconcile one local position against broker truth.")
    add_reconcile_common(one)
    one.add_argument("--ticker", required=True)
    one.add_argument("--path-run-id", default="")
    one.add_argument("--order-id", default="")
    one.add_argument("--sell-order-id", default="")

    many = subparsers.add_parser("reconcile-positions", help="Reconcile local positions for one market.")
    add_reconcile_common(many)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "status":
            processes = discover_live_processes()
            payload = {
                "ok": True,
                "mode": _normalize_mode(args.mode),
                "frozen": True,
                "processes": processes,
            }
            try:
                assert_writer_freeze(processes)
            except RuntimeError as exc:
                payload["frozen"] = False
                payload["error"] = str(exc)
                if args.require_frozen:
                    _print_payload(payload, as_json=args.json)
                    return 2
            _print_payload(payload, as_json=args.json)
            return 0

        if args.command == "backup":
            backup_dir = create_live_backup(
                args.label,
                mode=args.mode,
                backup_root=args.backup_root or None,
                db_path=args.db_path or None,
            )
            _print_payload({"ok": True, "backup_dir": str(backup_dir)}, as_json=args.json)
            return 0

        if args.command == "broker-truth":
            _print_payload(
                broker_truth_report(
                    mode=args.mode,
                    market=args.market,
                    refresh=args.refresh,
                    snapshot_path=args.snapshot_path or None,
                ),
                as_json=args.json,
            )
            return 0

        if args.command == "reconcile-position":
            broker_truth = _load_broker_truth_payload(args)
            backup_dir = ""
            if args.apply:
                if bool(broker_truth.get("missing")) or bool(broker_truth.get("stale")):
                    raise RuntimeError("broker truth is missing or stale; refresh before apply")
                backup_dir = str(_ensure_apply_preconditions(args))
            result = reconcile_local_position_against_broker(
                market=args.market,
                ticker=args.ticker,
                path_run_id=args.path_run_id,
                broker_truth=broker_truth,
                mode=args.mode,
                store_path=args.store_path or None,
                positions_path=args.positions_path or None,
                sell_order_id=args.sell_order_id,
                dry_run=not bool(args.apply),
                backup_dir=backup_dir or None,
                operator=args.operator,
                reason=args.reason,
            )
            result["order_id"] = args.order_id
            _print_payload(result, as_json=args.json)
            return 0

        if args.command == "reconcile-positions":
            broker_truth = _load_broker_truth_payload(args)
            backup_dir = ""
            if args.apply:
                if bool(broker_truth.get("missing")) or bool(broker_truth.get("stale")):
                    raise RuntimeError("broker truth is missing or stale; refresh before apply")
                backup_dir = str(_ensure_apply_preconditions(args))
            result = reconcile_positions_against_broker(
                market=args.market,
                broker_truth=broker_truth,
                mode=args.mode,
                store_path=args.store_path or None,
                positions_path=args.positions_path or None,
                dry_run=not bool(args.apply),
                backup_dir=backup_dir or None,
                operator=args.operator,
                reason=args.reason,
            )
            _print_payload(result, as_json=args.json)
            return 0

    except Exception as exc:
        _print_payload({"ok": False, "command": getattr(args, "command", ""), "error": str(exc)}, as_json=True)
        return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
