from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path

KST = timezone(timedelta(hours=9))
LOG_DIRS = ("system", "risk", "normal", "analysis", "daily_judgment", "hold_advisor")
KEYWORD_KINDS = {
    "ORDER_UNKNOWN": "order_unknown",
    "broker sync protected": "broker_sync_protected",
    "pending sell broker sync protected": "pending_sell_broker_sync_protected",
    "BLOCK_START": "guardian_block_start",
    "broker_truth": "broker_truth",
    "snapshot stale": "broker_truth_stale",
    "token expired": "token_expired",
    "KISTokenExpired": "token_expired",
    "Telegram": "telegram",
    "ANALYST_NEW_BUY_BLOCK": "analyst_new_buy_block",
    "ORDER_SIZE_TOO_SMALL_GATE": "order_size_too_small",
    "ORDER_REJECT": "order_reject",
    "BUY_FAIL": "buy_fail",
    "SELL_FAIL": "sell_fail",
    "Traceback": "traceback",
}


def _now_kst() -> datetime:
    return datetime.now(KST)


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _parse_end_at(raw: str) -> datetime:
    parsed = _parse_dt(raw)
    if parsed is None:
        raise ValueError(f"invalid --end-at: {raw}")
    return parsed


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def _tail_value(path: str | Path) -> str:
    raw = str(path or "")
    return raw.replace(str(ROOT), "").lstrip("\\/")


def _compact_position(pos: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ticker",
        "market",
        "qty",
        "strategy",
        "path_type",
        "entry_route",
        "entry_time",
        "entry_date",
        "entry",
        "display_avg_price",
        "display_current_price",
        "display_currency",
        "pnl_pct",
        "peak_pnl_pct",
        "position_mfe_pct",
        "position_mae_pct",
        "trailing",
        "trail_sl_usd",
        "tp_triggered",
        "pathb_pending_sell_order_no",
        "pathb_pending_sell_qty",
        "pathb_pending_close_reason",
        "broker_reconcile_status",
        "broker_missing_seen_count",
        "manual_reconciliation_required",
        "management_protected",
    )
    return {key: pos.get(key) for key in keys if key in pos}


def _compact_fill(row: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ticker",
        "market",
        "side",
        "order_no",
        "order_status",
        "order_time",
        "fill_time",
        "order_qty",
        "filled_qty",
        "remaining_qty",
        "order_price",
        "avg_price",
    )
    return {key: row.get(key) for key in keys if key in row}


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil  # type: ignore

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def _pid_state(name: str, path: Path) -> dict[str, Any]:
    data = _read_json(path, {})
    pid = _safe_int(data.get("pid") if isinstance(data, dict) else "")
    return {
        "name": name,
        "path": str(path),
        "pid": pid,
        "alive": _process_alive(pid),
        "raw": data if isinstance(data, dict) else {},
    }


def _load_broker_truth(mode: str) -> dict[str, Any]:
    try:
        from runtime.broker_truth_snapshot import BrokerTruthSnapshot

        return BrokerTruthSnapshot(runtime_mode=mode).load_snapshot()
    except Exception:
        return _read_json(get_runtime_path("state", f"{mode}_broker_truth_snapshot.json"), {})


def _api_usage_day(mode: str, day: str) -> dict[str, Any]:
    data = _read_json(get_runtime_path("state", f"{mode}_api_usage.json"), {})
    daily = data.get("daily") if isinstance(data, dict) else {}
    row = daily.get(day) if isinstance(daily, dict) else {}
    return row if isinstance(row, dict) else {}


def _usage_delta(current: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "calls": _safe_int(current.get("calls")) - _safe_int(baseline.get("calls")),
        "input_tokens": _safe_int(current.get("input_tokens")) - _safe_int(baseline.get("input_tokens")),
        "output_tokens": _safe_int(current.get("output_tokens")) - _safe_int(baseline.get("output_tokens")),
        "cost_usd": round(_safe_float(current.get("cost_usd")) - _safe_float(baseline.get("cost_usd")), 6),
    }


def _guardian_block_start_causes(
    guardian_report: dict[str, Any],
    guardian_alert: dict[str, Any],
    guardian_heartbeat: dict[str, Any],
) -> list[dict[str, Any]]:
    if str(guardian_report.get("gate") or "") != "BLOCK_START":
        return []
    findings = guardian_report.get("findings") if isinstance(guardian_report.get("findings"), list) else []
    raw_items: list[Any] = list(findings)
    for key in ("fingerprint", "last_fingerprint", "blocking_reasons", "reasons"):
        value = guardian_alert.get(key)
        if isinstance(value, list):
            raw_items.extend(value)
        elif isinstance(value, dict):
            raw_items.extend(value.values())
        elif value:
            raw_items.append(value)
    heartbeat_report = guardian_heartbeat.get("report") if isinstance(guardian_heartbeat.get("report"), dict) else {}
    for key in ("findings", "blocking_reasons"):
        value = heartbeat_report.get(key)
        if isinstance(value, list):
            raw_items.extend(value)

    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, dict):
            code = str(
                item.get("code")
                or item.get("kind")
                or item.get("check")
                or item.get("name")
                or item.get("id")
                or item.get("message")
                or ""
            ).strip()
            message = str(item.get("message") or item.get("detail") or item.get("reason") or "")
        else:
            code = str(item or "").strip()
            message = code
        if not code:
            continue
        key = code.lower()
        if key in seen:
            continue
        seen.add(key)
        action, tool, risk = _guardian_action_for_code(code, message)
        out.append(
            {
                "code": code,
                "message": message[:300],
                "risk_level": risk,
                "blocking": True,
                "operator_action": action,
                "remediation_tool": tool,
            }
        )
    if not out:
        out.append(
            {
                "code": "BLOCK_START",
                "message": "guardian gate blocked live start without detailed findings",
                "risk_level": "P2",
                "blocking": True,
                "operator_action": "guardian report path and heartbeat JSON 확인",
                "remediation_tool": "tools/live_guardian.py",
            }
        )
    return out[:20]


def _guardian_action_for_code(code: str, message: str) -> tuple[str, str, str]:
    text = f"{code} {message}".lower()
    if "pathb" in text and "stale" in text:
        return (
            "PathB stale active run을 broker truth로 대조하고 필요 시 manual reconciliation 처리",
            "PathB ORDER_UNKNOWN/reconcile tools",
            "P1",
        )
    if "broker_truth" in text or "broker truth" in text or "stale_state" in text:
        return (
            "broker truth snapshot freshness와 토큰/조회 오류를 먼저 복구",
            "tools/live_preflight.py --mode live --skip-dashboard --json",
            "P1",
        )
    if "db" in text:
        return (
            "로컬 DB 상태와 최근 lifecycle/order_unknown row를 확인",
            "sqlite/manual DB inspection",
            "P2",
        )
    return ("guardian finding 세부 로그 확인", "tools/live_guardian.py", "P2")


def _hold_advisor_cost_observation(raw_call_summary: dict[str, Any]) -> dict[str, Any]:
    by_label = raw_call_summary.get("by_label") if isinstance(raw_call_summary.get("by_label"), dict) else {}
    hold_counts = {
        str(label): int(count)
        for label, count in by_label.items()
        if str(label).startswith("hold_advisor")
    }
    return {
        "observed_calls": sum(hold_counts.values()),
        "by_label": hold_counts,
        "saved_calls_estimate": 0,
        "cache_enabled": False,
        "safety_critical_cache_bypass": [
            "hard_stop",
            "broker_truth_untrusted",
            "stale_or_error_truth",
            "order_failure",
            "pathb_auto_sell_hold_cooldown_guard",
        ],
    }


def _risk_axes(latest: dict[str, Any]) -> dict[str, Any]:
    broker = latest.get("broker_truth") if isinstance(latest.get("broker_truth"), dict) else {}
    protected = latest.get("protected_positions") if isinstance(latest.get("protected_positions"), list) else []
    manual_required = sum(1 for row in protected if isinstance(row, dict) and row.get("manual_reconciliation_required"))
    if bool((latest.get("guardian") or {}).get("gate") == "BLOCK_START"):
        manual_required += 1
    if bool(broker.get("missing")) or bool(broker.get("stale")) or str(broker.get("error") or ""):
        manual_required += 1
    return {
        "broker_positions": int(broker.get("positions_count") or 0),
        "broker_open_orders": int(broker.get("open_orders_count") or 0),
        "local_open_positions": int(latest.get("open_positions_count") or 0),
        "protected_positions": len(protected),
        "pending_sells": len(latest.get("pending_sells") or []),
        "order_unknown_events": int(latest.get("order_unknown_event_count_us_total") or 0),
        "manual_action_required": int(manual_required),
    }


class OvernightMonitor:
    def __init__(
        self,
        *,
        mode: str,
        market: str,
        session_date: str,
        start_at: datetime,
        end_at: datetime,
        interval_sec: int,
        out_dir: Path,
    ) -> None:
        self.mode = mode
        self.market = market.upper()
        self.session_date = session_date
        self.start_at = start_at
        self.end_at = end_at
        self.interval_sec = max(15, int(interval_sec or 60))
        self.out_dir = out_dir
        self.events_path = out_dir / "events.jsonl"
        self.progress_path = out_dir / "progress.json"
        self.final_json_path = out_dir / "final_report.json"
        self.final_md_path = out_dir / "final_report.md"
        self.offsets: dict[str, int] = {}
        self.raw_seen: set[str] = set()
        self.log_counts: Counter[str] = Counter()
        self.log_samples: list[dict[str, Any]] = []
        self.decision_events: list[dict[str, Any]] = []
        self.raw_call_counts: Counter[str] = Counter()
        self.raw_call_models: Counter[str] = Counter()
        self.raw_call_tokens = {"input_tokens": 0, "output_tokens": 0, "duration_ms": 0}
        self.raw_call_samples: list[dict[str, Any]] = []
        self.snapshots: list[dict[str, Any]] = []
        self.state_counts: Counter[str] = Counter()
        self.state_samples: list[dict[str, Any]] = []
        self.status = "running"
        self.baseline_usage = _api_usage_day(mode, start_at.date().isoformat())

    def initialize(self) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        for path in self._log_files():
            self.offsets[str(path)] = path.stat().st_size
        decisions = get_runtime_path("state", f"{self.mode}_decisions.jsonl")
        if decisions.exists():
            self.offsets[str(decisions)] = decisions.stat().st_size
        raw_dir = get_runtime_path("logs", "raw_calls")
        if raw_dir.exists():
            self.raw_seen = {str(path) for path in raw_dir.glob("*.json")}
        _append_jsonl(
            self.events_path,
            {
                "type": "monitor_started",
                "at": _now_kst().isoformat(timespec="seconds"),
                "mode": self.mode,
                "market": self.market,
                "session_date": self.session_date,
                "end_at": self.end_at.isoformat(timespec="seconds"),
                "pid": os.getpid(),
                "read_only": True,
            },
        )

    def _log_files(self) -> list[Path]:
        cutoff = _now_kst() - timedelta(days=2)
        paths: list[Path] = []
        for name in LOG_DIRS:
            base = get_runtime_path("logs", name)
            if not base.exists():
                continue
            for path in base.glob("*"):
                if not path.is_file() or path.suffix.lower() not in {".jsonl", ".log"}:
                    continue
                try:
                    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=KST)
                except Exception:
                    continue
                if mtime >= cutoff:
                    paths.append(path)
        return sorted(paths)

    def _read_new_lines(self, path: Path) -> list[str]:
        key = str(path)
        try:
            size = path.stat().st_size
        except Exception:
            return []
        previous = self.offsets.get(key)
        if previous is None:
            previous = 0
        if size < previous:
            previous = 0
        if size == previous:
            return []
        try:
            with path.open("rb") as f:
                f.seek(previous)
                raw = f.read(size - previous)
            self.offsets[key] = size
            text = raw.decode("utf-8", errors="replace")
            return text.splitlines()
        except Exception as exc:
            self._record_issue("monitor_read_error", f"{path}: {exc}", path=path)
            return []

    def _record_issue(self, kind: str, message: str, *, path: Path | str = "", level: str = "WARNING") -> None:
        self.log_counts[kind] += 1
        sample = {
            "at": _now_kst().isoformat(timespec="seconds"),
            "kind": kind,
            "level": level,
            "path": _tail_value(path),
            "message": str(message or "")[:500],
        }
        if len(self.log_samples) < 300:
            self.log_samples.append(sample)
        _append_jsonl(self.events_path, {"type": "issue", **sample})

    def _record_observation(self, kind: str, message: str, *, payload: dict[str, Any] | None = None) -> None:
        self.state_counts[kind] += 1
        sample = {
            "at": _now_kst().isoformat(timespec="seconds"),
            "kind": kind,
            "message": str(message or "")[:500],
            "payload": payload or {},
        }
        if len(self.state_samples) < 300:
            self.state_samples.append(sample)
        _append_jsonl(self.events_path, {"type": "state_observation", **sample})

    def _classify_line(self, path: Path, line: str) -> None:
        if not line.strip():
            return
        level = ""
        message = line.strip()
        timestamp = ""
        try:
            item = json.loads(line)
            if isinstance(item, dict):
                level = str(item.get("level") or "").upper()
                message = str(item.get("message") or item.get("event") or line)
                timestamp = str(item.get("timestamp") or item.get("ts") or "")
        except Exception:
            match = re.search(r"\[(ERROR|WARNING|CRITICAL|INFO)\s*\]", line)
            if match:
                level = match.group(1).upper()
        text = f"{level} {message}"
        if level in {"ERROR", "CRITICAL"}:
            self._record_issue("log_error", message, path=path, level=level)
        elif level == "WARNING":
            self._record_issue("log_warning", message, path=path, level=level)
        for keyword, kind in KEYWORD_KINDS.items():
            if keyword in text:
                self._record_issue(kind, message, path=path, level=level or "INFO")
                break
        if timestamp:
            parsed = _parse_dt(timestamp)
            if parsed and parsed < self.start_at - timedelta(minutes=5):
                return

    def scan_logs(self) -> None:
        for path in self._log_files():
            for line in self._read_new_lines(path):
                self._classify_line(path, line)

    def scan_decisions(self) -> None:
        path = get_runtime_path("state", f"{self.mode}_decisions.jsonl")
        for line in self._read_new_lines(path):
            try:
                item = json.loads(line)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            if self.market and str(item.get("market") or "").upper() != self.market:
                continue
            kind = str(item.get("type") or item.get("action") or "").upper()
            if kind not in {"ENTRY", "CLOSED", "HOLD_REVIEW", "SELL", "BUY"}:
                continue
            row = {
                "timestamp": item.get("timestamp"),
                "type": item.get("type") or item.get("action"),
                "market": item.get("market"),
                "ticker": item.get("ticker"),
                "strategy": item.get("strategy") or item.get("path_type"),
                "order_no": item.get("order_no") or item.get("v2_execution_id"),
                "qty": item.get("qty"),
                "entry_price_native": item.get("entry_price_native"),
                "exit_price_native": item.get("exit_price_native"),
                "exit_reason": item.get("exit_reason"),
                "pnl_pct": item.get("pnl_pct"),
                "pnl_krw": item.get("pnl_krw"),
                "broker_fill_confirmed": item.get("broker_fill_confirmed"),
                "broker_fill_source": item.get("broker_fill_source"),
                "hold_action": item.get("hold_action"),
                "queued_sell": item.get("queued_sell"),
            }
            self.decision_events.append(row)
            if len(self.decision_events) > 500:
                self.decision_events = self.decision_events[-500:]
            _append_jsonl(self.events_path, {"type": "decision", **row})

    def scan_raw_calls(self) -> None:
        raw_dir = get_runtime_path("logs", "raw_calls")
        if not raw_dir.exists():
            return
        for path in sorted(raw_dir.glob("*.json")):
            key = str(path)
            if key in self.raw_seen:
                continue
            self.raw_seen.add(key)
            data = _read_json(path, {})
            if not isinstance(data, dict):
                continue
            market = str(data.get("market") or "").upper()
            if self.market and market and market != self.market:
                continue
            label = str(data.get("label") or "unknown")
            model = str(data.get("model") or "unknown")
            tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
            input_tokens = _safe_int(tokens.get("input"))
            output_tokens = _safe_int(tokens.get("output"))
            duration_ms = _safe_int(data.get("duration_ms"))
            self.raw_call_counts[label] += 1
            self.raw_call_models[model] += 1
            self.raw_call_tokens["input_tokens"] += input_tokens
            self.raw_call_tokens["output_tokens"] += output_tokens
            self.raw_call_tokens["duration_ms"] += duration_ms
            if len(self.raw_call_samples) < 200 or duration_ms >= 30000:
                self.raw_call_samples.append(
                    {
                        "timestamp": data.get("timestamp"),
                        "market": market,
                        "label": label,
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "duration_ms": duration_ms,
                        "path": _tail_value(path),
                    }
                )
                self.raw_call_samples = self.raw_call_samples[-300:]
            _append_jsonl(
                self.events_path,
                {
                    "type": "claude_call",
                    "timestamp": data.get("timestamp"),
                    "market": market,
                    "label": label,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "duration_ms": duration_ms,
                    "path": _tail_value(path),
                },
            )

    def snapshot_state(self) -> dict[str, Any]:
        positions = _read_json(get_runtime_path("state", f"{self.mode}_open_positions.json"), [])
        if not isinstance(positions, list):
            positions = []
        market_positions = [
            _compact_position(row)
            for row in positions
            if isinstance(row, dict) and str(row.get("market") or "").upper() == self.market
        ]
        protected = [
            row
            for row in market_positions
            if row.get("broker_reconcile_status")
            or row.get("manual_reconciliation_required")
            or row.get("management_protected")
        ]
        pending_sells = [row for row in market_positions if row.get("pathb_pending_sell_order_no")]
        broker = _load_broker_truth(self.mode)
        market_data = ((broker.get("markets") or {}).get(self.market) or {}) if isinstance(broker, dict) else {}
        positions_broker = market_data.get("positions") if isinstance(market_data.get("positions"), list) else []
        open_orders = market_data.get("open_orders") if isinstance(market_data.get("open_orders"), list) else []
        fills = market_data.get("today_fills") if isinstance(market_data.get("today_fills"), list) else []
        market_fills = [_compact_fill(row) for row in fills if isinstance(row, dict)]
        guardian_heartbeat = _read_json(get_runtime_path("state", f"{self.mode}_guardian_heartbeat.json"), {})
        guardian_alert = _read_json(get_runtime_path("state", f"{self.mode}_guardian_alert_state.json"), {})
        guardian_report = {}
        if isinstance(guardian_heartbeat, dict) and guardian_heartbeat.get("report_path"):
            guardian_report = _read_json(Path(str(guardian_heartbeat.get("report_path"))), {})
        api_day = _now_kst().date().isoformat()
        api_usage = _api_usage_day(self.mode, api_day)
        order_unknown = _read_json(get_runtime_path("state", f"{self.mode}_v2_order_unknown.json"), {})
        recent_unknown = []
        if isinstance(order_unknown, dict):
            for row in order_unknown.get("events") or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("market") or "").upper() == self.market:
                    recent_unknown.append(row)
        snapshot = {
            "at": _now_kst().isoformat(timespec="seconds"),
            "open_positions_count": len(market_positions),
            "open_positions": market_positions,
            "protected_positions": protected,
            "pending_sells": pending_sells,
            "broker_truth": {
                "missing": bool(market_data.get("missing")),
                "stale": bool(market_data.get("stale")),
                "error": str(market_data.get("error") or ""),
                "last_success_at": str(market_data.get("last_success_at") or ""),
                "last_attempt_at": str(market_data.get("last_attempt_at") or ""),
                "ttl_sec": market_data.get("ttl_sec"),
                "positions_count": len(positions_broker),
                "open_orders_count": len(open_orders),
                "today_fills_count": len(market_fills),
                "positions": [_compact_position(row) for row in positions_broker if isinstance(row, dict)],
                "open_orders": [_compact_fill(row) for row in open_orders if isinstance(row, dict)],
                "today_fills": market_fills[-40:],
            },
            "guardian": {
                "heartbeat": guardian_heartbeat if isinstance(guardian_heartbeat, dict) else {},
                "alert": guardian_alert if isinstance(guardian_alert, dict) else {},
                "ok": guardian_report.get("ok") if isinstance(guardian_report, dict) else None,
                "gate": guardian_report.get("gate") if isinstance(guardian_report, dict) else "",
                "counts": guardian_report.get("counts") if isinstance(guardian_report, dict) else {},
                "findings": (guardian_report.get("findings") or [])[:20] if isinstance(guardian_report, dict) else [],
                "block_start_causes": _guardian_block_start_causes(
                    guardian_report if isinstance(guardian_report, dict) else {},
                    guardian_alert if isinstance(guardian_alert, dict) else {},
                    guardian_heartbeat if isinstance(guardian_heartbeat, dict) else {},
                ),
            },
            "api_usage_today": api_usage,
            "api_usage_delta_since_start": _usage_delta(api_usage, self.baseline_usage),
            "order_unknown_event_count_us_total": len(recent_unknown),
            "pid_state": [
                _pid_state("trading_bot", get_runtime_path("state", f"{self.mode}_trading_bot.pid")),
                _pid_state("dashboard", get_runtime_path("state", "dashboard_server.pid")),
                _pid_state("guardian", get_runtime_path("state", f"{self.mode}_guardian_heartbeat.json")),
                _pid_state("preopen_scheduler", get_runtime_path("state", "preopen_scheduler_heartbeat.json")),
            ],
        }
        if snapshot["broker_truth"]["stale"] or snapshot["broker_truth"]["missing"] or snapshot["broker_truth"]["error"]:
            self._record_issue(
                "broker_truth_untrusted",
                f"{self.market} broker truth missing={snapshot['broker_truth']['missing']} "
                f"stale={snapshot['broker_truth']['stale']} error={snapshot['broker_truth']['error']}",
            )
        if protected:
            self._record_observation(
                "protected_position",
                f"{len(protected)} protected {self.market} positions",
                payload={"count": len(protected)},
            )
        if pending_sells:
            self._record_issue("pending_sell_local_state", f"{len(pending_sells)} {self.market} positions have pending sell fields")
        if snapshot["guardian"]["gate"] == "BLOCK_START":
            self._record_issue("guardian_block_start", "live guardian gate=BLOCK_START")
        self.snapshots.append(snapshot)
        self.snapshots = self.snapshots[-200:]
        _append_jsonl(self.events_path, {"type": "snapshot", **snapshot})
        return snapshot

    def cycle(self) -> None:
        self.scan_logs()
        self.scan_decisions()
        self.scan_raw_calls()
        snapshot = self.snapshot_state()
        self.write_progress(snapshot)

    def write_progress(self, latest_snapshot: dict[str, Any] | None = None) -> None:
        progress = self.build_report(final=False, latest_snapshot=latest_snapshot)
        _write_json(self.progress_path, progress)
        self.write_markdown(progress, self.out_dir / "progress.md")

    def build_report(self, *, final: bool, latest_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        latest = latest_snapshot or (self.snapshots[-1] if self.snapshots else self.snapshot_state())
        raw_call_summary = {
            "calls_since_start_observed_from_raw_files": sum(self.raw_call_counts.values()),
            "by_label": dict(self.raw_call_counts.most_common()),
            "by_model": dict(self.raw_call_models.most_common()),
            "tokens_observed_from_raw_files": dict(self.raw_call_tokens),
            "samples": self.raw_call_samples[-80:],
        }
        state_counts = dict(self.state_counts.most_common())
        return {
            "status": "completed" if final else self.status,
            "generated_at": _now_kst().isoformat(timespec="seconds"),
            "start_at": self.start_at.isoformat(timespec="seconds"),
            "end_at": self.end_at.isoformat(timespec="seconds"),
            "mode": self.mode,
            "market": self.market,
            "session_date": self.session_date,
            "read_only": True,
            "output_dir": str(self.out_dir),
            "latest_snapshot": latest,
            "decision_events_since_start": self.decision_events,
            "claude_usage_since_start": raw_call_summary,
            "hold_advisor_cost_observation": _hold_advisor_cost_observation(raw_call_summary),
            "log_issue_counts_since_start": dict(self.log_counts.most_common()),
            "log_issue_samples": self.log_samples[-120:],
            "state_observation_counts_since_start": state_counts,
            "state_observation_samples": self.state_samples[-120:],
            "risk_axes": _risk_axes(latest),
            "snapshots_recorded": len(self.snapshots),
        }

    def write_markdown(self, report: dict[str, Any], path: Path) -> None:
        latest = report.get("latest_snapshot") or {}
        broker = latest.get("broker_truth") or {}
        guardian = latest.get("guardian") or {}
        usage = latest.get("api_usage_delta_since_start") or {}
        claude = report.get("claude_usage_since_start") or {}
        hold_cost = report.get("hold_advisor_cost_observation") or {}
        risk_axes = report.get("risk_axes") or {}
        lines = [
            "# US Overnight Monitor Report",
            "",
            f"- status: {report.get('status')}",
            f"- generated_at: {report.get('generated_at')}",
            f"- monitor_window: {report.get('start_at')} ~ {report.get('end_at')}",
            f"- mode/market/session: {report.get('mode')} / {report.get('market')} / {report.get('session_date')}",
            f"- read_only: {report.get('read_only')}",
            "",
            "## Current Operations",
            "",
            f"- guardian_gate: {guardian.get('gate')} ok={guardian.get('ok')} status={(guardian.get('heartbeat') or {}).get('status')}",
            f"- broker_truth: missing={broker.get('missing')} stale={broker.get('stale')} error={broker.get('error')} last_success={broker.get('last_success_at')}",
            f"- broker_positions/open_orders/fills: {broker.get('positions_count')} / {broker.get('open_orders_count')} / {broker.get('today_fills_count')}",
            f"- open_positions_count: {latest.get('open_positions_count')}",
            f"- protected_positions: {len(latest.get('protected_positions') or [])}",
            f"- pending_sells: {len(latest.get('pending_sells') or [])}",
            "",
            "## Risk Axes",
            "",
            f"- broker_exposure: positions={risk_axes.get('broker_positions')} open_local_positions={risk_axes.get('local_open_positions')}",
            f"- open_orders: broker={risk_axes.get('broker_open_orders')} pending_sell_local={risk_axes.get('pending_sells')}",
            f"- local_unresolved_state: protected={risk_axes.get('protected_positions')} order_unknown_events={risk_axes.get('order_unknown_events')}",
            f"- manual_action_required: {risk_axes.get('manual_action_required')}",
            "",
            "## Trading Events Since Monitor Start",
            "",
        ]
        decisions = report.get("decision_events_since_start") or []
        if decisions:
            for row in decisions[-40:]:
                lines.append(
                    f"- {row.get('timestamp')} {row.get('type')} {row.get('ticker')} "
                    f"qty={row.get('qty')} order={row.get('order_no')} "
                    f"exit={row.get('exit_reason')} pnl={row.get('pnl_pct')}"
                )
        else:
            lines.append("- no entry/closed/hold-review events observed after monitor start")
        lines.extend(
            [
                "",
                "## Claude Usage",
                "",
                f"- api_usage_delta_since_start: calls={usage.get('calls')} input={usage.get('input_tokens')} output={usage.get('output_tokens')} cost_usd={usage.get('cost_usd')}",
                f"- raw_call_files_observed: {claude.get('calls_since_start_observed_from_raw_files')}",
                f"- by_label: {claude.get('by_label')}",
                f"- by_model: {claude.get('by_model')}",
                f"- hold_advisor_calls: total={hold_cost.get('observed_calls')} by_label={hold_cost.get('by_label')} saved_calls_estimate={hold_cost.get('saved_calls_estimate')}",
                "",
                "## State Observations",
                "",
            ]
        )
        observations = report.get("state_observation_counts_since_start") or {}
        if observations:
            for key, count in observations.items():
                lines.append(f"- {key}: {count}")
        else:
            lines.append("- no state observations recorded after monitor start")
        guardian_causes = guardian.get("block_start_causes") or []
        if guardian_causes:
            lines.extend(["", "## Guardian Block Causes", ""])
            for row in guardian_causes:
                lines.append(
                    f"- {row.get('code')}: risk={row.get('risk_level')} blocking={row.get('blocking')} "
                    f"action={row.get('operator_action')} tool={row.get('remediation_tool')}"
                )
        lines.extend(
            [
                "",
                "## Issues",
                "",
            ]
        )
        counts = report.get("log_issue_counts_since_start") or {}
        if counts:
            for key, count in counts.items():
                lines.append(f"- {key}: {count}")
        else:
            lines.append("- no warning/error keyword issues observed after monitor start")
        samples = report.get("log_issue_samples") or []
        if samples:
            lines.extend(["", "## Recent Issue Samples", ""])
            for row in samples[-30:]:
                lines.append(f"- {row.get('at')} [{row.get('kind')}] {row.get('message')}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def finalize(self) -> None:
        self.status = "completed"
        report = self.build_report(final=True)
        _write_json(self.final_json_path, report)
        self.write_markdown(report, self.final_md_path)
        _append_jsonl(
            self.events_path,
            {
                "type": "monitor_completed",
                "at": _now_kst().isoformat(timespec="seconds"),
                "final_json": str(self.final_json_path),
                "final_md": str(self.final_md_path),
            },
        )

    def run(self, *, once: bool = False) -> None:
        self.initialize()
        self.cycle()
        if once:
            self.finalize()
            return
        while _now_kst() < self.end_at:
            remaining = (self.end_at - _now_kst()).total_seconds()
            time.sleep(max(1, min(self.interval_sec, remaining)))
            self.cycle()
        self.finalize()


def _default_session_date(now: datetime) -> str:
    # KST midnight through morning belongs to the prior US trading date.
    if now.hour < 9:
        return (now.date() - timedelta(days=1)).isoformat()
    return now.date().isoformat()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only overnight US live monitor.")
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--market", default="US")
    parser.add_argument("--session-date", default="")
    parser.add_argument("--end-at", required=True, help="ISO timestamp; KST assumed when timezone is omitted")
    parser.add_argument("--interval-sec", type=int, default=60)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    start_at = _now_kst()
    end_at = _parse_end_at(args.end_at)
    if end_at <= start_at and not args.once:
        raise SystemExit(f"--end-at must be in the future: {end_at.isoformat()}")
    session_date = args.session_date or _default_session_date(start_at)
    out_dir = Path(args.out_dir) if args.out_dir else ROOT / "docs" / "reports" / (
        f"overnight_us_monitor_{start_at.strftime('%Y%m%d_%H%M%S')}"
    )
    monitor = OvernightMonitor(
        mode=str(args.mode or "live").lower(),
        market=str(args.market or "US").upper(),
        session_date=session_date,
        start_at=start_at,
        end_at=end_at,
        interval_sec=args.interval_sec,
        out_dir=out_dir,
    )
    monitor.run(once=bool(args.once))
    print(json.dumps({"out_dir": str(out_dir), "final_md": str(monitor.final_md_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
