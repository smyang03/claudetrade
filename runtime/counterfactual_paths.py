from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from runtime_paths import get_runtime_path


COMMON_PATHS = [
    "immediate",
    "or_break",
    "vwap_reclaim",
    "volume_surge",
    "pullback_reclaim",
    "wait_30m",
    "wait_60m",
    "no_entry",
]

MARKET_EXTRA_PATHS = {
    "KR": ["vi_safe_reclaim", "orderbook_support"],
    "US": ["premarket_high_break", "new_high_reclaim", "news_volume_surge"],
}


def counterfactual_path_names(market: str) -> list[str]:
    market_key = str(market or "").upper()
    return COMMON_PATHS + list(MARKET_EXTRA_PATHS.get(market_key, []))


def deterministic_candidate_key(*, runtime_mode: str, session_date: str, market: str, ticker: str, known_at: str, action: str) -> str:
    raw = "|".join([runtime_mode, session_date, market, ticker, known_at, action])
    return "cf_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on", "ok"}


def _num(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _known_plus_minutes(known_at: str, minutes: int) -> str:
    try:
        return (datetime.fromisoformat(str(known_at).replace("Z", "+00:00")) + timedelta(minutes=minutes)).isoformat(
            timespec="seconds"
        )
    except Exception:
        return ""


def build_counterfactual_rows(
    *,
    runtime_mode: str,
    session_date: str,
    market: str,
    ticker: str,
    trade_ready_action: str,
    known_at: str,
    signal_time: str = "",
    candidate_key: str = "",
    call_id: str | None = None,
    actual_path: str = "",
    context: dict[str, Any] | None = None,
    metadata_overrides: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    market_key = str(market or "").upper()
    ticker_key = str(ticker or "").strip().upper() if market_key == "US" else str(ticker or "").strip()
    known = str(known_at or datetime.now().isoformat(timespec="seconds"))
    action = str(trade_ready_action or "")
    key = candidate_key or deterministic_candidate_key(
        runtime_mode=str(runtime_mode or "live"),
        session_date=str(session_date or ""),
        market=market_key,
        ticker=ticker_key,
        known_at=known,
        action=action,
    )
    ctx = dict(context or {})
    current_price = ctx.get("current_price")
    overrides = dict(metadata_overrides or {})
    rows: list[dict[str, Any]] = []
    current_price_num = _num(current_price)
    volume_ratio = _num(ctx.get("volume_ratio_open") or ctx.get("volume_acceleration"))
    pullback_pct = _num(ctx.get("pullback_from_high_pct"))
    for path_name in counterfactual_path_names(market_key):
        status = "PENDING"
        trigger_time = None
        trigger_price = None
        trigger_reason = ""
        entry_price = None
        entry_delay_min = None
        metadata = {
            "source": "counterfactual_paths",
            "context": ctx,
            "kr_confirmation": {
                "mode": ctx.get("kr_confirmation_gate_mode"),
                "score": ctx.get("kr_confirmation_score"),
                "score_items": ctx.get("kr_confirmation_score_items"),
                "threshold": ctx.get("kr_confirmation_threshold"),
                "state": ctx.get("kr_confirmation_state"),
                "reason": ctx.get("kr_confirmation_reason"),
                "fast_window_ok": ctx.get("kr_confirmation_fast_window_ok"),
                "fast_window_elapsed_min": ctx.get("kr_confirmation_fast_window_elapsed_min"),
            },
            "microstructure": {
                "vi_state": ctx.get("vi_state"),
                "vi_active": ctx.get("vi_active"),
                "vi_data_quality": ctx.get("vi_data_quality"),
                "orderbook_snapshot": ctx.get("orderbook_snapshot"),
                "orderbook_data_quality": ctx.get("orderbook_data_quality"),
                "orderbook_imbalance": ctx.get("orderbook_imbalance"),
                "orderbook_support": ctx.get("orderbook_support"),
            },
        }
        metadata.update(overrides)
        if path_name == "immediate":
            status = "TRIGGERED" if current_price_num else "PENDING"
            trigger_time = known if current_price_num else None
            trigger_price = current_price_num
            trigger_reason = "actual_immediate" if current_price_num else ""
            entry_price = current_price_num
        elif path_name == "no_entry":
            status = "BASELINE_NO_TRADE"
        elif path_name == "or_break" and (_boolish(ctx.get("opening_range_break")) or _boolish(ctx.get("or_break"))):
            status = "TRIGGERED"
            trigger_time = known
            trigger_price = current_price_num
            trigger_reason = "opening_range_break"
            entry_price = current_price_num
        elif path_name == "vwap_reclaim" and _boolish(ctx.get("vwap_reclaim")):
            status = "TRIGGERED"
            trigger_time = known
            trigger_price = current_price_num
            trigger_reason = "vwap_reclaim"
            entry_price = current_price_num
        elif path_name == "volume_surge" and volume_ratio is not None and volume_ratio >= 1.5:
            status = "TRIGGERED"
            trigger_time = known
            trigger_price = current_price_num
            trigger_reason = "volume_surge"
            entry_price = current_price_num
        elif path_name == "pullback_reclaim" and (
            _boolish(ctx.get("pullback_reclaim"))
            or (
                pullback_pct is not None
                and pullback_pct <= -1.0
                and (_boolish(ctx.get("vwap_reclaim")) or _boolish(ctx.get("opening_range_break")))
            )
        ):
            status = "TRIGGERED"
            trigger_time = known
            trigger_price = current_price_num
            trigger_reason = "pullback_reclaim"
            entry_price = current_price_num
        elif path_name in {"wait_30m", "wait_60m"}:
            minutes = 30 if path_name == "wait_30m" else 60
            trigger_time = _known_plus_minutes(known, minutes)
            trigger_reason = path_name
            entry_delay_min = float(minutes)
        if market_key == "KR" and path_name in {"vi_safe_reclaim", "orderbook_support"}:
            missing: list[str] = []
            vi_payload = ctx.get("vi_state")
            vi_quality = str((vi_payload or {}).get("data_quality") if isinstance(vi_payload, dict) else "").upper()
            orderbook_payload = ctx.get("orderbook_snapshot")
            orderbook_quality = str(
                (orderbook_payload or {}).get("data_quality") if isinstance(orderbook_payload, dict) else ""
            ).upper()
            if path_name == "vi_safe_reclaim" and vi_quality != "OK":
                missing.append("kr_vi")
            if path_name == "orderbook_support" and orderbook_quality != "OK":
                missing.append("kr_orderbook")
            if missing:
                status = "DATA_MISSING"
                metadata["missing_features"] = missing
        rows.append(
            {
                "runtime_mode": str(runtime_mode or "live"),
                "session_date": str(session_date or ""),
                "market": market_key,
                "ticker": ticker_key,
                "candidate_key": key,
                "call_id": call_id,
                "signal_time": str(signal_time or known),
                "known_at": known,
                "trade_ready_action": action,
                "actual_path": actual_path or ("immediate" if action in {"BUY_READY", "PROBE_READY", "ADD_READY"} else "no_entry"),
                "path_name": path_name,
                "entry_price": entry_price,
                "trigger_time": trigger_time,
                "trigger_price": trigger_price,
                "trigger_reason": trigger_reason,
                "entry_delay_min": entry_delay_min,
                "status": status,
                "metadata_quality": overrides.get("metadata_quality"),
                "label_source": overrides.get("label_source"),
                "metadata": metadata,
            }
        )
    return rows


def safe_write_counterfactual_paths(rows: list[dict[str, Any]], *, db_path: str | Path | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    if not rows:
        return {"ok": True, "count": 0, "duration_ms": 0.0, "errors": []}
    try:
        from audit.candidate_counterfactual_store import CandidateCounterfactualStore

        path = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
        store = CandidateCounterfactualStore(path)
        count = store.upsert_paths(rows)
        errors = list(getattr(store, "last_upsert_errors", []) or [])
        return {
            "ok": not errors,
            "count": count,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "errors": errors,
        }
    except Exception as exc:
        return {
            "ok": False,
            "count": 0,
            "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "errors": [{"error": str(exc)}],
            "error": str(exc),
        }
