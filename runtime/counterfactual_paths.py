from __future__ import annotations

import hashlib
import time
from datetime import datetime
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
    rows: list[dict[str, Any]] = []
    for path_name in counterfactual_path_names(market_key):
        status = "PENDING"
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
        if path_name == "immediate":
            status = "TRIGGERED" if current_price else "PENDING"
        elif path_name == "no_entry":
            status = "BASELINE_NO_TRADE"
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
                "entry_price": current_price if path_name == "immediate" else None,
                "trigger_time": known if path_name == "immediate" and current_price else None,
                "trigger_reason": "actual_immediate" if path_name == "immediate" and current_price else "",
                "status": status,
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
