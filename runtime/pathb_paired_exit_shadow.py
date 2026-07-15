from __future__ import annotations

"""Read-only KR PathB paired exit observer.

The observer consumes defensive snapshots from IntradayMinuteCache.  It never
calls a broker/provider, changes a live plan/position or returns a live exit
decision.  Arm A mirrors the current early-tier policy.  Arm B waits for a
3.6% split, realizes an integer half, then hands the remainder to the runner.
"""

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import statistics
import threading
from typing import Any, Callable
from zoneinfo import ZoneInfo

from execution.claude_price_adapter import round_down_to_kr_tick, round_up_to_kr_tick
from runtime_paths import get_runtime_path


KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "pathb_kr_paired_exit_shadow_v1"
DEFAULT_POLICY = "EARLY_FULL_V1"
CHALLENGER_POLICY = "SPLIT_RUNNER_V1"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    return _float(os.getenv(name), default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _policy_parameters() -> dict[str, Any]:
    return {
        "early_enabled": _env_bool("PATHB_EARLY_TIER_ENABLED", False),
        "early_fraction": _env_float(
            "PATHB_EARLY_TIER_TARGET_FRACTION_KR",
            _env_float("PATHB_EARLY_TIER_TARGET_FRACTION", 0.4),
        ),
        "early_giveback": _env_float("PATHB_EARLY_TIER_PEAK_GIVEBACK_PCT", 0.006),
        "tier1_pct": _env_float("PATHB_LADDER_TIER1_PCT", 1.2),
        "tier2_pct": _env_float("PATHB_LADDER_TIER2_PCT", 2.0),
        "tier3_pct": _env_float("PATHB_LADDER_TIER3_PCT", 3.0),
        "tier4_pct": _env_float("PATHB_LADDER_TIER4_PCT", 4.0),
        "tier2_buffer": _env_float("PATHB_LADDER_TIER2_FLOOR_BUFFER_PCT", 0.005),
        "tier3_giveback": _env_float("PATHB_LADDER_TIER3_PEAK_GIVEBACK_PCT", 0.010),
        "tier4_giveback": _env_float("PATHB_LADDER_TIER4_PEAK_GIVEBACK_PCT", 0.012),
        "split_trigger_pct": _env_float("PATHB_KR_SPLIT_RUNNER_TRIGGER_PCT", 3.6),
        "split_fraction": _env_float("PATHB_KR_SPLIT_RUNNER_FRACTION", 0.5),
        "round_trip_cost_pct": _env_float("PATHB_KR_PAIRED_ROUND_TRIP_COST_PCT", 0.21),
        "base_slippage_pct": _env_float("PATHB_KR_PAIRED_BASE_SLIPPAGE_PCT", 0.10),
        "partial_extra_slippage_pct": _env_float("PATHB_KR_PAIRED_PARTIAL_EXTRA_SLIPPAGE_PCT", 0.30),
    }


def _new_arm(qty: int, entry: float) -> dict[str, Any]:
    return {
        "status": "PRE_SPLIT",
        "remaining_qty": int(qty),
        "peak_price": float(entry),
        "last_price": float(entry),
        "last_bar_ts": "",
        "realized_net_contribution_pct": 0.0,
        "exit_owner": "",
        "closed_at": "",
        "events": 0,
        "split_triggered": False,
        "split_triggered_at": "",
    }


def _protective_owner(price: float, hard_stop: float, loss_cap: float) -> str:
    if loss_cap > 0 and price <= loss_cap and (hard_stop <= 0 or loss_cap >= hard_stop):
        return "loss_cap"
    if hard_stop > 0 and price <= hard_stop:
        return "hard_stop"
    return ""


def _a_profit_floor(position: dict[str, Any], peak: float) -> tuple[str, float]:
    entry = _float(position.get("entry_price"))
    target = _float(position.get("target_price"))
    params = dict(position.get("parameters") or {})
    if entry <= 0 or peak <= 0:
        return "", 0.0
    mfe_pct = (peak / entry - 1.0) * 100.0
    if mfe_pct >= _float(params.get("tier4_pct"), 4.0):
        return "tier4", round_down_to_kr_tick(peak * (1.0 - _float(params.get("tier4_giveback"), 0.012)))
    if mfe_pct >= _float(params.get("tier3_pct"), 3.0):
        return "tier3", round_down_to_kr_tick(peak * (1.0 - _float(params.get("tier3_giveback"), 0.010)))

    owner = ""
    floor = 0.0
    if mfe_pct >= _float(params.get("tier2_pct"), 2.0):
        owner = "tier2"
        floor = entry * (1.0 + _float(params.get("tier2_buffer"), 0.005))
    elif mfe_pct >= _float(params.get("tier1_pct"), 1.2):
        owner = "tier1"
        floor = entry

    target_gain_pct = (target / entry - 1.0) * 100.0 if target > entry else 0.0
    early_act = target_gain_pct * _float(params.get("early_fraction"), 0.4)
    if bool(params.get("early_enabled")) and early_act > 0 and mfe_pct >= early_act:
        early_floor = round_down_to_kr_tick(max(entry, peak * (1.0 - _float(params.get("early_giveback"), 0.006))))
        if early_floor > floor:
            owner, floor = "early_target", early_floor
    return owner, round_down_to_kr_tick(floor) if floor > 0 else 0.0


class PairedExitShadowObserver:
    def __init__(
        self,
        *,
        state_path: Path | None = None,
        event_path: Path | None = None,
        heartbeat_path: Path | None = None,
        now_func: Callable[[], str] | None = None,
    ) -> None:
        self.state_path = state_path or get_runtime_path("state", "pathb_kr_paired_exit_state.json")
        self.event_path = event_path or get_runtime_path("data", "shadow", "pathb_kr_paired_exit_events.jsonl")
        self.heartbeat_path = heartbeat_path or get_runtime_path("state", "pathb_kr_paired_exit_heartbeat.json")
        self._now = now_func or _now_iso
        self._lock = threading.RLock()
        self._state = self._load_state()

    def _load_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("positions"), dict):
                return payload
        except Exception:
            pass
        return {"schema_version": SCHEMA_VERSION, "updated_at": "", "positions": {}}

    def enabled(self) -> bool:
        return str(os.getenv("PATHB_KR_PAIRED_EXIT_SHADOW_ENABLED", "false")).strip().lower() in {
            "1", "true", "yes", "y", "on"
        }

    def register_position(
        self,
        *,
        path_run_id: str,
        ticker: str,
        session_date: str,
        entry_price: float,
        qty: int,
        target_price: float,
        hard_stop: float = 0.0,
        loss_cap: float = 0.0,
        filled_at: str = "",
        position_id: str = "",
        exit_policy_version: str = DEFAULT_POLICY,
    ) -> bool:
        if not self.enabled() or not path_run_id or not ticker or entry_price <= 0 or qty <= 0:
            return False
        key = str(position_id or path_run_id)
        with self._lock:
            if key in self._state["positions"]:
                return False
            params = _policy_parameters()
            split_qty = int(math.floor(qty * params["split_fraction"]))
            fallback_reason = ""
            if split_qty <= 0:
                fallback_reason = "A_FALLBACK_QTY1"
            elif target_price > 0 and (target_price / entry_price - 1.0) * 100.0 <= params["split_trigger_pct"]:
                fallback_reason = "A_FALLBACK_TARGET_BELOW_SPLIT"
            now = self._now()
            row = {
                "position_id": key,
                "path_run_id": str(path_run_id),
                "market": "KR",
                "ticker": str(ticker),
                "session_date": str(session_date),
                "entry_price": float(entry_price),
                "original_qty": int(qty),
                "target_price": float(target_price or 0.0),
                "hard_stop": float(hard_stop or 0.0),
                "loss_cap": float(loss_cap or 0.0),
                "filled_at": str(filled_at or now),
                "registered_at": now,
                "exit_policy_version": str(exit_policy_version or DEFAULT_POLICY),
                "challenger_policy_version": CHALLENGER_POLICY,
                "eligible": not bool(fallback_reason),
                "fallback_reason": fallback_reason,
                "parameters": params,
                "last_cache_watermark": "",
                "cache_gap_count": 0,
                "arms": {"A": _new_arm(qty, entry_price), "B": _new_arm(qty, entry_price)},
            }
            self._state["positions"][key] = row
            self._persist(now)
            self._event(row, "REGISTERED", arm="SYSTEM", exit_owner="observer_register")
            self._heartbeat(status="running", last_event_at=now)
            return True

    def active_tickers(self) -> list[str]:
        with self._lock:
            return sorted({
                str(row.get("ticker") or "")
                for row in self._state["positions"].values()
                if any(str(arm.get("status") or "") != "CLOSED" for arm in (row.get("arms") or {}).values())
                and str(row.get("ticker") or "")
            })

    def consume_snapshot(self, ticker: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled():
            return {"enabled": False, "processed_bars": 0}
        now = self._now()
        with self._lock:
            matches = [
                row for row in self._state["positions"].values()
                if str(row.get("ticker") or "") == str(ticker or "")
                and any(str(arm.get("status") or "") != "CLOSED" for arm in (row.get("arms") or {}).values())
            ]
            if not matches:
                return {"enabled": True, "processed_bars": 0, "positions": 0}
            if bool(snapshot.get("stale")) or not list(snapshot.get("bars") or []):
                for row in matches:
                    row["cache_gap_count"] = _int(row.get("cache_gap_count")) + 1
                self._persist(now)
                self._heartbeat(
                    status="stale",
                    last_error=str(snapshot.get("reason") or "cache_stale_or_missing"),
                    last_cache_watermark=str(snapshot.get("watermark") or ""),
                )
                return {"enabled": True, "processed_bars": 0, "positions": len(matches), "stale": True}

            processed = 0
            bars = sorted((dict(bar) for bar in list(snapshot.get("bars") or [])), key=lambda item: str(item.get("ts") or ""))
            for row in matches:
                # Pin the exact producer snapshot before emitting any virtual-fill event.
                # This preserves a traceable, read-only data contract for every A/B decision.
                row["last_cache_watermark"] = str(snapshot.get("watermark") or "")
                newest = max(
                    str(((row.get("arms") or {}).get("A") or {}).get("last_bar_ts") or ""),
                    str(((row.get("arms") or {}).get("B") or {}).get("last_bar_ts") or ""),
                )
                filled_at = _parse_dt(row.get("filled_at"))
                for bar in bars:
                    ts = str(bar.get("ts") or "")
                    bar_dt = _parse_dt(ts)
                    if not ts or ts <= newest or (filled_at is not None and bar_dt is not None and bar_dt < filled_at):
                        continue
                    price = _float(bar.get("close"))
                    if price <= 0:
                        continue
                    self._step_position(row, ts=ts, price=price)
                    processed += 1
            self._persist(now)
            self._heartbeat(
                status="running",
                last_success_at=now,
                last_cache_watermark=str(snapshot.get("watermark") or ""),
            )
            return {"enabled": True, "processed_bars": processed, "positions": len(matches), "stale": False}

    def record_live_exit(
        self,
        *,
        path_run_id: str,
        price: float,
        close_reason: str,
        filled_at: str,
        execution_id: str = "",
    ) -> bool:
        """Anchor Arm A to confirmed broker truth; never returns a live decision."""

        if not self.enabled() or not path_run_id or price <= 0:
            return False
        with self._lock:
            row = next(
                (
                    item
                    for item in self._state.get("positions", {}).values()
                    if str(item.get("path_run_id") or "") == str(path_run_id)
                ),
                None,
            )
            if not isinstance(row, dict):
                return False
            ts = str(filled_at or self._now())
            owner = f"live:{str(close_reason or 'UNKNOWN').upper()}"
            arm_a = (row.get("arms") or {}).get("A") or {}
            self._apply_actual_baseline(
                row,
                arm_a,
                ts=ts,
                price=float(price),
                owner=owner,
                execution_id=str(execution_id or ""),
            )
            row["actual_live_exit"] = {
                "filled_at": ts,
                "price": float(price),
                "close_reason": str(close_reason or "UNKNOWN").upper(),
                "execution_id": str(execution_id or ""),
            }

            # Only hard protective exits are shared with the challenger. A
            # live profit exit must not terminate B's counterfactual runner.
            safety_reasons = {
                "CLOSED_LOSS_CAP",
                "CLOSED_HARD_STOP",
                "CLOSED_CLAUDE_PRICE_STOP",
                "CLOSED_EMERGENCY",
                "CLOSED_PANIC",
            }
            arm_b = (row.get("arms") or {}).get("B") or {}
            if str(close_reason or "").upper() in safety_reasons and str(arm_b.get("status") or "") != "CLOSED":
                self._realize_actual_remaining(
                    row,
                    arm_b,
                    arm_name="B",
                    ts=ts,
                    price=float(price),
                    owner=f"shared_safety:{str(close_reason or 'UNKNOWN').upper()}",
                    execution_id=str(execution_id or ""),
                )
            self._persist(self._now())
            self._heartbeat(status="running" if self.active_tickers() else "idle", last_live_exit_at=ts)
            return True

    def _apply_actual_baseline(
        self,
        row: dict[str, Any],
        arm: dict[str, Any],
        *,
        ts: str,
        price: float,
        owner: str,
        execution_id: str,
    ) -> None:
        entry = _float(row.get("entry_price"))
        cost = _float((row.get("parameters") or {}).get("round_trip_cost_pct"), 0.21)
        contribution = (price / entry - 1.0) * 100.0 - cost if entry > 0 else 0.0
        arm["realized_net_contribution_pct"] = contribution
        arm["remaining_qty"] = 0
        arm["status"] = "CLOSED"
        arm["exit_owner"] = owner
        arm["closed_at"] = ts
        arm["baseline_source"] = "broker_truth"
        arm["events"] = _int(arm.get("events")) + 1
        self._event(
            row,
            "LIVE_BASELINE_FILL",
            arm="A",
            exit_owner=owner,
            bar_ts=ts,
            observed_price=price,
            virtual_fill_price=price,
            requested_qty=_int(row.get("original_qty")),
            executable_qty=_int(row.get("original_qty")),
            remaining_qty=0,
            fee_pct=cost,
            slippage_pct=0.0,
            net_contribution_pct=contribution,
            execution_id=execution_id,
        )

    def _realize_actual_remaining(
        self,
        row: dict[str, Any],
        arm: dict[str, Any],
        *,
        arm_name: str,
        ts: str,
        price: float,
        owner: str,
        execution_id: str,
    ) -> None:
        qty = _int(arm.get("remaining_qty"))
        if qty <= 0:
            return
        entry = _float(row.get("entry_price"))
        original_qty = max(1, _int(row.get("original_qty")))
        cost = _float((row.get("parameters") or {}).get("round_trip_cost_pct"), 0.21)
        contribution = (qty / original_qty) * (((price / entry - 1.0) * 100.0 if entry > 0 else 0.0) - cost)
        arm["realized_net_contribution_pct"] = _float(arm.get("realized_net_contribution_pct")) + contribution
        arm["remaining_qty"] = 0
        arm["status"] = "CLOSED"
        arm["exit_owner"] = owner
        arm["closed_at"] = ts
        arm["events"] = _int(arm.get("events")) + 1
        self._event(
            row,
            "SHARED_SAFETY_FILL",
            arm=arm_name,
            exit_owner=owner,
            bar_ts=ts,
            observed_price=price,
            virtual_fill_price=price,
            requested_qty=qty,
            executable_qty=qty,
            remaining_qty=0,
            fee_pct=cost,
            slippage_pct=0.0,
            net_contribution_pct=contribution,
            execution_id=execution_id,
        )

    def _step_position(self, row: dict[str, Any], *, ts: str, price: float) -> None:
        a = (row.get("arms") or {})["A"]
        b = (row.get("arms") or {})["B"]
        self._step_a(row, a, ts=ts, price=price, arm_name="A")
        if str(b.get("status") or "") == "CLOSED":
            return
        if row.get("fallback_reason"):
            before = str(b.get("status") or "")
            self._step_a(row, b, ts=ts, price=price, arm_name="B", fallback_owner=str(row["fallback_reason"]))
            if before != "CLOSED" and str(b.get("status") or "") == "CLOSED":
                b["status"] = "CLOSED"
            return
        self._step_b(row, b, ts=ts, price=price)

    def _step_a(
        self,
        row: dict[str, Any],
        arm: dict[str, Any],
        *,
        ts: str,
        price: float,
        arm_name: str,
        fallback_owner: str = "",
    ) -> None:
        if str(arm.get("status") or "") == "CLOSED":
            return
        arm["peak_price"] = max(_float(arm.get("peak_price")), price)
        arm["last_price"] = price
        arm["last_bar_ts"] = ts
        owner = _protective_owner(price, _float(row.get("hard_stop")), _float(row.get("loss_cap")))
        target = _float(row.get("target_price"))
        if not owner and target > 0 and price >= target:
            owner = "target"
        if not owner:
            ladder_owner, floor = _a_profit_floor(row, _float(arm.get("peak_price")))
            if floor > 0 and price <= floor:
                owner = ladder_owner
        if not owner and self._is_preclose(ts):
            owner = "pre_close"
        if owner:
            self._close_arm(row, arm, arm_name=arm_name, ts=ts, price=price, owner=fallback_owner or owner)

    def _step_b(self, row: dict[str, Any], arm: dict[str, Any], *, ts: str, price: float) -> None:
        arm["peak_price"] = max(_float(arm.get("peak_price")), price)
        arm["last_price"] = price
        arm["last_bar_ts"] = ts
        owner = _protective_owner(price, _float(row.get("hard_stop")), _float(row.get("loss_cap")))
        if owner:
            self._close_arm(row, arm, arm_name="B", ts=ts, price=price, owner=owner)
            return

        entry = _float(row.get("entry_price"))
        params = dict(row.get("parameters") or {})
        trigger_price = round_up_to_kr_tick(
            entry * (1.0 + _float(params.get("split_trigger_pct"), 3.6) / 100.0)
        )
        if str(arm.get("status") or "") == "PRE_SPLIT" and price >= trigger_price:
            sell_qty = int(math.floor(_int(row.get("original_qty")) * _float(params.get("split_fraction"), 0.5)))
            if sell_qty <= 0:
                row["fallback_reason"] = "A_FALLBACK_QTY1"
                self._step_a(row, arm, ts=ts, price=price, arm_name="B", fallback_owner="A_FALLBACK_QTY1")
                return
            self._realize(
                row,
                arm,
                arm_name="B",
                ts=ts,
                price=price,
                qty=sell_qty,
                owner="split_runner_partial",
                partial=True,
            )
            arm["remaining_qty"] = max(0, _int(arm.get("remaining_qty")) - sell_qty)
            arm["status"] = "RUNNER"
            arm["exit_owner"] = "split_runner_partial"
            arm["split_triggered"] = True
            arm["split_triggered_at"] = ts

        if str(arm.get("status") or "") == "RUNNER":
            target = _float(row.get("target_price"))
            if target > 0 and price >= target:
                self._close_arm(row, arm, arm_name="B", ts=ts, price=price, owner="runner_target")
                return
            mfe_pct = (max(_float(arm.get("peak_price")), price) / entry - 1.0) * 100.0 if entry > 0 else 0.0
            floor = 0.0
            runner_owner = ""
            if mfe_pct >= _float(params.get("tier4_pct"), 4.0):
                floor = round_down_to_kr_tick(
                    _float(arm.get("peak_price")) * (1.0 - _float(params.get("tier4_giveback"), 0.012))
                )
                runner_owner = "runner_tier4"
            elif mfe_pct >= _float(params.get("tier3_pct"), 3.0):
                floor = round_down_to_kr_tick(
                    _float(arm.get("peak_price")) * (1.0 - _float(params.get("tier3_giveback"), 0.010))
                )
                runner_owner = "runner_tier3"
            if floor > 0 and price <= floor:
                self._close_arm(row, arm, arm_name="B", ts=ts, price=price, owner=runner_owner)
                return
        if str(arm.get("status") or "") != "CLOSED" and self._is_preclose(ts):
            self._close_arm(row, arm, arm_name="B", ts=ts, price=price, owner="pre_close")

    @staticmethod
    def _is_preclose(ts: str) -> bool:
        parsed = _parse_dt(ts)
        if parsed is None:
            return False
        local = parsed.astimezone(KST)
        return (local.hour, local.minute) >= (15, 20)

    def _close_arm(
        self,
        row: dict[str, Any],
        arm: dict[str, Any],
        *,
        arm_name: str,
        ts: str,
        price: float,
        owner: str,
    ) -> None:
        qty = _int(arm.get("remaining_qty"))
        if qty > 0:
            self._realize(row, arm, arm_name=arm_name, ts=ts, price=price, qty=qty, owner=owner, partial=False)
        arm["remaining_qty"] = 0
        arm["status"] = "CLOSED"
        arm["exit_owner"] = owner
        arm["closed_at"] = ts

    def _realize(
        self,
        row: dict[str, Any],
        arm: dict[str, Any],
        *,
        arm_name: str,
        ts: str,
        price: float,
        qty: int,
        owner: str,
        partial: bool,
    ) -> None:
        params = dict(row.get("parameters") or {})
        slip_pct = _float(params.get("base_slippage_pct"), 0.10)
        if partial:
            slip_pct += _float(params.get("partial_extra_slippage_pct"), 0.30)
        fill = round_down_to_kr_tick(price * (1.0 - slip_pct / 100.0))
        entry = _float(row.get("entry_price"))
        original_qty = max(1, _int(row.get("original_qty")))
        weight = qty / original_qty
        gross_pct = (fill / entry - 1.0) * 100.0 if entry > 0 else 0.0
        contribution = weight * (gross_pct - _float(params.get("round_trip_cost_pct"), 0.21))
        arm["realized_net_contribution_pct"] = _float(arm.get("realized_net_contribution_pct")) + contribution
        arm["events"] = _int(arm.get("events")) + 1
        self._event(
            row,
            "VIRTUAL_FILL",
            arm=arm_name,
            exit_owner=owner,
            bar_ts=ts,
            observed_price=price,
            virtual_fill_price=fill,
            requested_qty=qty,
            executable_qty=qty,
            remaining_qty=max(0, _int(arm.get("remaining_qty")) - qty),
            fee_pct=_float(params.get("round_trip_cost_pct"), 0.21),
            slippage_pct=slip_pct,
            net_contribution_pct=contribution,
        )

    def _event(self, row: dict[str, Any], event: str, *, arm: str, exit_owner: str, **extra: Any) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "authority": "SHADOW_ONLY_NO_ORDER_EFFECT",
            "recorded_at": self._now(),
            "event": event,
            "position_id": row.get("position_id"),
            "path_run_id": row.get("path_run_id"),
            "market": "KR",
            "ticker": row.get("ticker"),
            "session_date": row.get("session_date"),
            "arm": arm,
            "policy_version": row.get("exit_policy_version") if arm == "A" else row.get("challenger_policy_version"),
            "exit_owner": exit_owner,
            "cache_watermark": row.get("last_cache_watermark", ""),
            **extra,
        }
        _append_jsonl(self.event_path, payload)

    def _persist(self, now: str) -> None:
        self._state["schema_version"] = SCHEMA_VERSION
        self._state["updated_at"] = now
        _atomic_write_json(self.state_path, self._state)

    def _heartbeat(self, *, status: str, **updates: Any) -> None:
        now = self._now()
        payload = self.summary(now=now)
        payload.update({
            "process": "pathb_kr_paired_exit_observer",
            "status": status,
            "last_tick_at": now,
            "pid": os.getpid(),
            **updates,
        })
        _atomic_write_json(self.heartbeat_path, payload)

    def touch(self) -> None:
        if self.enabled():
            self._heartbeat(status="running" if self.active_tickers() else "idle")

    def summary(self, *, now: str | None = None) -> dict[str, Any]:
        now_text = now or self._now()
        now_dt = _parse_dt(now_text) or datetime.now(timezone.utc)
        cutoff = now_dt - timedelta(days=7)
        positions = list(self._state.get("positions", {}).values())
        recent = [row for row in positions if (_parse_dt(row.get("registered_at")) or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff]
        eligible = [row for row in positions if bool(row.get("eligible"))]
        recent_eligible = [row for row in recent if bool(row.get("eligible"))]
        triggered = [row for row in positions if bool(((row.get("arms") or {}).get("B") or {}).get("split_triggered"))]
        recent_triggered = [
            row for row in triggered
            if (_parse_dt(((row.get("arms") or {}).get("B") or {}).get("split_triggered_at"))
                or datetime.min.replace(tzinfo=timezone.utc)) >= cutoff
        ]
        completed = [
            row for row in positions
            if all(str(arm.get("status") or "") == "CLOSED" for arm in (row.get("arms") or {}).values())
        ]
        # A paired sample is decision-grade only after Arm A is anchored to a
        # confirmed live broker fill. Virtual-vs-virtual rows remain visible
        # operationally but cannot advance the enforce gate.
        completed_eligible = [
            row
            for row in completed
            if bool(row.get("eligible"))
            and str((((row.get("arms") or {}).get("A") or {}).get("baseline_source") or "")) == "broker_truth"
        ]

        def _paired_delta(row: dict[str, Any]) -> float:
            arms = row.get("arms") or {}
            return _float((arms.get("B") or {}).get("realized_net_contribution_pct")) - _float(
                (arms.get("A") or {}).get("realized_net_contribution_pct")
            )

        def _lower_bound(values: list[float]) -> float | None:
            if len(values) < 2:
                return None
            mean = statistics.fmean(values)
            if len(set(round(value, 12) for value in values)) == 1:
                return mean
            return mean - 1.645 * statistics.stdev(values) / math.sqrt(len(values))

        paired_deltas = [_paired_delta(row) for row in completed_eligible]
        mean_delta = statistics.fmean(paired_deltas) if paired_deltas else None
        total_delta = sum(paired_deltas) if paired_deltas else 0.0
        ex_top3 = sorted(paired_deltas, reverse=True)[3:] if len(paired_deltas) > 3 else []
        ex_top3_total = sum(ex_top3) if ex_top3 else None
        gap_free_rows = [row for row in completed_eligible if _int(row.get("cache_gap_count")) == 0]
        gap_free_deltas = [_paired_delta(row) for row in gap_free_rows]
        gap_free_mean = statistics.fmean(gap_free_deltas) if gap_free_deltas else None

        weekly: dict[str, list[float]] = {}
        for row in completed_eligible:
            try:
                parsed_date = datetime.fromisoformat(str(row.get("session_date") or "")[:10]).date()
                year, week, _weekday = parsed_date.isocalendar()
                block_key = f"{year}-W{week:02d}"
            except Exception:
                block_key = "UNKNOWN"
            weekly.setdefault(block_key, []).append(_paired_delta(row))
        weekly_means = [statistics.fmean(values) for values in weekly.values() if values]
        block_lcb_5pct = _lower_bound(weekly_means)

        integer_relevant = [
            row for row in positions
            if not str(row.get("fallback_reason") or "").startswith("A_FALLBACK_TARGET_BELOW_SPLIT")
        ]
        integer_executable = [row for row in integer_relevant if _int(row.get("original_qty")) >= 2]
        integer_execution_rate = (
            len(integer_executable) / len(integer_relevant) if integer_relevant else None
        )
        virtual_fill_completion_rate = (
            len(completed_eligible) / len(eligible) if eligible else None
        )

        def _completed_at(row: dict[str, Any]) -> datetime:
            closed = [
                _parse_dt(arm.get("closed_at"))
                for arm in (row.get("arms") or {}).values()
                if _parse_dt(arm.get("closed_at")) is not None
            ]
            return max(closed) if closed else datetime.min.replace(tzinfo=timezone.utc)

        recent_completed_eligible = [row for row in completed_eligible if _completed_at(row) >= cutoff]
        registration_pace = len(recent_eligible)
        completion_pace = len(recent_completed_eligible)
        remaining = max(0, 15 - len(completed_eligible))
        eta_days = (
            math.ceil(remaining * 7 / completion_pace)
            if completion_pace > 0 and remaining > 0
            else (0 if remaining == 0 else None)
        )
        intake_eta_days = (
            math.ceil(max(0, 15 - len(eligible)) * 7 / registration_pace)
            if registration_pace > 0 and len(eligible) < 15
            else (0 if len(eligible) >= 15 else None)
        )
        gate_checks = {
            "n_at_least_15": len(completed_eligible) >= 15,
            "mean_delta_positive": mean_delta is not None and mean_delta > 0,
            "weekly_block_lcb_positive": block_lcb_5pct is not None and block_lcb_5pct > 0,
            "ex_top3_total_positive": ex_top3_total is not None and ex_top3_total > 0,
            "integer_execution_rate_ok": integer_execution_rate is not None and integer_execution_rate >= 0.85,
            "virtual_fill_completion_rate_ok": (
                virtual_fill_completion_rate is not None and virtual_fill_completion_rate >= 0.90
            ),
            "gap_free_sign_positive": gap_free_mean is not None and gap_free_mean > 0,
        }
        statistical_gate_pass = all(gate_checks.values())
        return {
            "schema_version": SCHEMA_VERSION,
            "authority": "SHADOW_ONLY_NO_ORDER_EFFECT",
            "enabled": self.enabled(),
            "generated_at": now_text,
            "positions_total": len(positions),
            "positions_active": len(positions) - len(completed),
            "paired_eligible_total": len(eligible),
            "paired_triggered_total": len(triggered),
            "paired_triggered_7d": len(recent_triggered),
            "paired_completed_total": len(completed),
            "paired_completed_eligible_total": len(completed_eligible),
            "gate_sample_total": len(completed_eligible),
            "new_positions_7d": len(recent),
            "paired_eligible_7d": len(recent_eligible),
            "paired_completed_eligible_7d": len(recent_completed_eligible),
            "paired_pace_per_week": registration_pace,
            "paired_completion_pace_per_week": completion_pace,
            "n15_eta_days": eta_days,
            "n15_intake_eta_days": intake_eta_days,
            "paired_mean_delta_pct": mean_delta,
            "paired_total_delta_pct": total_delta,
            "weekly_block_count": len(weekly_means),
            "weekly_block_lcb_5pct": block_lcb_5pct,
            "ex_top3_sample_count": len(ex_top3),
            "ex_top3_total_delta_pct": ex_top3_total,
            "gap_free_sample_count": len(gap_free_deltas),
            "gap_free_mean_delta_pct": gap_free_mean,
            "integer_execution_rate": integer_execution_rate,
            "virtual_fill_completion_rate": virtual_fill_completion_rate,
            "gate_checks": gate_checks,
            "statistical_gate_pass": statistical_gate_pass,
            "operator_review_candidate": statistical_gate_pass,
            "enforce_ready": False,
            "enforce_block_reason": (
                "operator_approval_and_live_execution_wiring_required"
                if statistical_gate_pass
                else "paired_forward_gate_not_met"
            ),
            "clock_status": (
                "COMPLETE"
                if len(completed_eligible) >= 15
                else ("RUNNING" if registration_pace > 0 or completion_pace > 0 else "STARVED")
            ),
            "active_tickers": self.active_tickers(),
            "state_updated_at": self._state.get("updated_at", ""),
        }


_DEFAULT: PairedExitShadowObserver | None = None
_DEFAULT_LOCK = threading.Lock()


def get_paired_exit_observer() -> PairedExitShadowObserver:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = PairedExitShadowObserver()
        return _DEFAULT
