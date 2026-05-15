from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import json
import os

from interface.bucket_summary import build_bucket_summary
from config.v2 import DEFAULT_V2_CONFIG
from learning.approval_queue import BrainApprovalQueue
from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import load_broker_truth_snapshot
from runtime_paths import get_runtime_path
from bot.entry_timing import build_entry_timing_summary

try:
    from phase1_trainer.digest_builder import KR_TICKERS as _KR_TICKERS_STATIC
except Exception:
    _KR_TICKERS_STATIC = {}


V2_DASHBOARD_TABS: tuple[str, ...] = (
    "Account",
    "System Health",
    "Claude Picks",
    "B플랜 실시간",
    "Lifecycle",
    "Positions",
    "Brain",
    "Daily Review",
)

V2_TELEGRAM_COMMANDS: tuple[str, ...] = (
    "/status",
    "/health",
    "/picks",
    "/positions",
    "/errors",
    "/halt",
    "/resume",
    "/panic",
    "/brain_pending",
    "/pathb_status",
    "/pathb_on",
    "/pathb_off",
    "/pathb_kill",
    "/pathb_closeall",
)

RUNTIME_DRIFT_KEYS: tuple[str, ...] = (
    "ENABLED_MARKETS",
    "V2_MAX_DAILY_ENTRIES",
    "KR_DAILY_ENTRY_CAP",
    "US_DAILY_ENTRY_CAP",
    "PATHB_ENABLED",
    "PATHB_KR_LIVE_ENABLED",
    "PATHB_US_LIVE_ENABLED",
    "PATHB_MAX_POSITIONS",
    "PATHB_MAX_DAILY_ENTRIES",
    "PATHB_FIXED_ORDER_KRW",
)


def build_v2_ops_summary(
    *,
    bot: Any | None = None,
    store: EventStore | None = None,
    market: str | None = None,
    runtime_mode: str | None = None,
    session_date: str | None = None,
) -> dict[str, Any]:
    store = store or EventStore()
    market_key = str(market or "").upper() or None
    runtime_key = str(runtime_mode or "").lower() or None
    session_key = session_date or _session_date_from_bot(bot)
    events = store.events_for_session(
        market=market_key,
        runtime_mode=runtime_key,
        session_date=session_key,
    )
    counts = Counter(str(event.get("event_type") or "") for event in events)
    order_unknown = [event for event in events if event.get("event_type") == "ORDER_UNKNOWN"]
    safety_blocked = [event for event in events if event.get("event_type") == "SAFETY_BLOCKED"]
    timing_expired = [event for event in events if event.get("event_type") == "TIMING_EXPIRED"]
    timing_unsupported = [event for event in events if event.get("event_type") == "TIMING_UNSUPPORTED"]
    last_event = events[-1] if events else {}
    broker_truth = _broker_truth_summary(runtime_key)
    positions = _positions_from_bot(bot, runtime_key, broker_truth=broker_truth, market=market_key)
    pending_orders = _pending_orders_from_bot(bot)
    brain_pending = BrainApprovalQueue().read_all()
    latest_review = _latest_daily_review(runtime_key)

    return {
        "ok": True,
        "market": market_key or "ALL",
        "runtime_mode": runtime_key or "all",
        "session_date": session_key,
        "dashboard_tabs": list(V2_DASHBOARD_TABS),
        "telegram_commands": list(V2_TELEGRAM_COMMANDS),
        "account": _account_from_bot(bot),
        "system_health": {
            "bot_alive": bot is not None,
            "broker_status": _broker_status(bot),
            "websocket_status": getattr(bot, "websocket_status", "unknown") if bot is not None else "unknown",
            "rate_limit_state": _rate_limit_state(bot),
            "order_unknown_count": len(order_unknown),
            "unresolved_pending_orders": len(pending_orders),
            "last_market_data_time": getattr(bot, "last_market_data_time", "") if bot is not None else "",
            "last_claude_call_time": getattr(bot, "last_claude_call_time", "") if bot is not None else "",
            "last_lifecycle_event_time": last_event.get("occurred_at", ""),
            "broker_truth_generated_at": broker_truth.get("generated_at", ""),
            "broker_truth_stale": {
                "KR": bool(((broker_truth.get("markets") or {}).get("KR") or {}).get("stale")),
                "US": bool(((broker_truth.get("markets") or {}).get("US") or {}).get("stale")),
            },
        },
        "broker_truth": broker_truth,
        "claude_picks": _claude_picks(events),
        "entry_timing": build_entry_timing_summary(
            market=market_key,
            runtime_mode=runtime_key,
            session_date=session_key,
        ),
        "bucket_monitor": build_bucket_summary(market=market_key, session_date=session_key, runtime_mode=runtime_key),
        "path_b_live": _path_b_live_summary(
            store,
            market_key,
            runtime_key,
            session_key,
            events=events,
            broker_truth=broker_truth,
        ),
        "lifecycle": {
            "event_counts": dict(counts),
            "last_event": last_event,
            "order_unknown": _compact_events(order_unknown),
            "safety_blocked": _compact_events(safety_blocked),
            "timing_expired": _compact_events(timing_expired),
            "timing_unsupported": _compact_events(timing_unsupported),
        },
        "positions": positions,
        "performance": _performance_from_review(latest_review),
        "brain": {
            "pending_approval_count": len(brain_pending),
            "pending_approval": brain_pending[-10:],
        },
        "daily_review": latest_review,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }


def _session_date_from_bot(bot: Any | None) -> str:
    if bot is not None:
        current = getattr(bot, "current_session_date", "") or getattr(bot, "session_date", "")
        if current:
            return str(current)
    return datetime.now().date().isoformat()


def _broker_truth_summary(runtime_mode: str | None) -> dict[str, Any]:
    mode = str(runtime_mode or "live").lower()
    try:
        snapshot = load_broker_truth_snapshot(mode)
    except Exception as exc:
        return {
            "runtime_mode": mode,
            "missing": True,
            "broken": True,
            "error": str(exc),
            "markets": {
                "KR": {"missing": True, "stale": True, "error": str(exc), "positions": [], "open_orders": [], "today_fills": []},
                "US": {"missing": True, "stale": True, "error": str(exc), "positions": [], "open_orders": [], "today_fills": []},
            },
        }
    markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), dict) else {}
    return {
        "runtime_mode": snapshot.get("runtime_mode", mode),
        "generated_at": snapshot.get("generated_at", ""),
        "schema_version": snapshot.get("schema_version", 1),
        "broken": bool(snapshot.get("broken", False)),
        "error": str(snapshot.get("error", "") or ""),
        "markets": {
            market: {
                "missing": bool((markets.get(market) or {}).get("missing", True)),
                "stale": bool((markets.get(market) or {}).get("stale", True)),
                "last_success_at": (markets.get(market) or {}).get("last_success_at", ""),
                "last_attempt_at": (markets.get(market) or {}).get("last_attempt_at", ""),
                "ttl_sec": (markets.get(market) or {}).get("ttl_sec", 60),
                "error": (markets.get(market) or {}).get("error", ""),
                "account_summary": (markets.get(market) or {}).get("account_summary", {}),
                "positions": (markets.get(market) or {}).get("positions", []),
                "open_orders": (markets.get(market) or {}).get("open_orders", []),
                "today_fills": (markets.get(market) or {}).get("today_fills", []),
            }
            for market in ("KR", "US")
        },
    }


def _account_from_bot(bot: Any | None) -> dict[str, Any]:
    if bot is None or not getattr(bot, "risk", None):
        return {}
    risk = bot.risk
    return {
        "cash_krw": float(getattr(risk, "cash", 0) or 0),
        "equity_krw": float(risk.equity()) if hasattr(risk, "equity") else 0.0,
        "daily_pnl_krw": float(getattr(risk, "daily_pnl", 0) or 0),
        "daily_return_pct": float(risk.daily_return()) if hasattr(risk, "daily_return") else 0.0,
        "halted": bool(getattr(risk, "halted", False)),
        "halt_reason": str(getattr(risk, "halt_reason", "") or ""),
    }


def _positions_from_bot(
    bot: Any | None,
    runtime_mode: str | None = None,
    *,
    broker_truth: dict[str, Any] | None = None,
    market: str | None = None,
) -> list[dict[str, Any]]:
    if bot is None or not getattr(bot, "risk", None):
        raw_positions = _positions_from_state(runtime_mode)
    else:
        raw_positions = list(getattr(bot.risk, "positions", []) or [])
    local_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for pos in raw_positions:
        if not isinstance(pos, dict):
            continue
        pos_market = str(pos.get("market", "") or "")
        if not pos_market:
            pos_market = "US" if str(pos.get("ticker", "")).replace(".", "").isalpha() else "KR"
        pos_ticker = str(pos.get("ticker", "") or "")
        key = (pos_market.upper(), pos_ticker.upper() if pos_market.upper() == "US" else pos_ticker)
        local_by_key[key] = pos
    broker_positions = _positions_from_broker_truth(broker_truth, market, local_by_key, bot)
    if broker_positions is not None:
        return broker_positions
    if not raw_positions:
        return []
    positions = []
    for pos in raw_positions:
        if not isinstance(pos, dict):
            continue
        pathb_plan = pos.get("pathb_plan") if isinstance(pos, dict) else {}
        if not isinstance(pathb_plan, dict):
            pathb_plan = {}
        market = str(pos.get("market", "") or "")
        if not market:
            market = "US" if str(pos.get("ticker", "")).replace(".", "").isalpha() else "KR"
        ticker = str(pos.get("ticker", "") or "")
        name = str(pos.get("name", "") or pathb_plan.get("name", "") or "").strip()
        if not name and bot is not None and hasattr(bot, "_lookup_ticker_name"):
            try:
                name = str(bot._lookup_ticker_name(ticker, market) or "")
            except Exception:
                name = ""
        buy_path = _position_buy_path(pos)
        positions.append(
            {
                "market": market,
                "ticker": ticker,
                "name": name,
                "display_ticker": _display_ticker(ticker, name),
                "qty": pos.get("qty", 0),
                "entry": pos.get("entry", 0),
                "current_price": pos.get("current_price", pos.get("entry", 0)),
                "strategy": pos.get("strategy", ""),
                "position_id": pos.get("position_id", ""),
                "decision_id": pos.get("v2_decision_id", ""),
                "path_type": pos.get("path_type", ""),
                "path_run_id": pos.get("pathb_path_run_id", ""),
                "buy_path": buy_path,
                "buy_path_label": _buy_path_label(buy_path),
                "target": pathb_plan.get("sell_target", ""),
                "stop_loss": pathb_plan.get("stop_loss", ""),
                "intraday_only": bool(pathb_plan.get("intraday_only", False)),
                "source": "local_fallback",
                "broker_truth_stale": False,
            }
        )
    return positions


def _positions_from_broker_truth(
    broker_truth: dict[str, Any] | None,
    market: str | None,
    local_by_key: dict[tuple[str, str], dict[str, Any]],
    bot: Any | None,
) -> list[dict[str, Any]] | None:
    if not isinstance(broker_truth, dict):
        return None
    markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    target_markets = [str(market).upper()] if market else ["KR", "US"]
    usable = False
    positions: list[dict[str, Any]] = []
    for market_key in target_markets:
        data = markets.get(market_key) if isinstance(markets.get(market_key), dict) else {}
        if not data or bool(data.get("missing")) or not str(data.get("last_success_at", "") or ""):
            continue
        usable = True
        stale = bool(data.get("stale"))
        for row in list(data.get("positions") or []):
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker", "") or "")
            key = (market_key, ticker.upper() if market_key == "US" else ticker)
            local = local_by_key.get(key, {})
            pathb_plan = local.get("pathb_plan") if isinstance(local.get("pathb_plan"), dict) else {}
            name = str(row.get("name", "") or local.get("name", "") or "").strip()
            if not name and bot is not None and hasattr(bot, "_lookup_ticker_name"):
                try:
                    name = str(bot._lookup_ticker_name(ticker, market_key) or "")
                except Exception:
                    name = ""
            buy_path = _position_buy_path(local) if local else "manual_or_broker"
            positions.append(
                {
                    "market": market_key,
                    "ticker": ticker,
                    "name": name,
                    "display_ticker": _display_ticker(ticker, name),
                    "qty": row.get("qty", 0),
                    "entry": row.get("avg_price", 0),
                    "current_price": row.get("current_price", row.get("avg_price", 0)),
                    "strategy": local.get("strategy", "broker_account"),
                    "position_id": local.get("position_id", ""),
                    "decision_id": local.get("v2_decision_id", ""),
                    "path_type": local.get("path_type", ""),
                    "path_run_id": local.get("pathb_path_run_id", ""),
                    "buy_path": buy_path,
                    "buy_path_label": _buy_path_label(buy_path),
                    "target": pathb_plan.get("sell_target", ""),
                    "stop_loss": pathb_plan.get("stop_loss", ""),
                    "intraday_only": bool(pathb_plan.get("intraday_only", False)),
                    "pnl": row.get("pnl", 0),
                    "pnl_pct": row.get("pnl_pct", 0),
                    "eval_amount": row.get("eval_amount", 0),
                    "source": "broker_truth",
                    "broker_truth_stale": stale,
                    "broker_truth_last_success_at": data.get("last_success_at", ""),
                }
            )
    if usable:
        return positions
    return None


def _path_b_live_summary(
    store: EventStore,
    market: str | None,
    runtime_mode: str | None,
    session_date: str,
    events: list[dict[str, Any]] | None = None,
    broker_truth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markets = [market] if market else ["KR", "US"]
    modes = [runtime_mode] if runtime_mode else ["live", "paper"]
    runs: list[dict[str, Any]] = []
    for market_key in markets:
        for mode_key in modes:
            try:
                runs.extend(
                    store.path_runs_for_session(
                        market=market_key,
                        runtime_mode=mode_key,
                        session_date=session_date,
                    )
                )
            except Exception:
                continue
    status_overrides = _path_status_overrides(events or [])
    pathb_runs = [
        _apply_lifecycle_status(run, status_overrides)
        for run in runs
        if str(run.get("path_type", "")) == "claude_price"
    ]
    name_map = _path_b_name_map(markets, runtime_mode, session_date)
    status_counts = Counter(str(run.get("status", "") or "UNKNOWN") for run in pathb_runs)
    active_statuses = {"WAITING", "HIT", "ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "SELL_SENT", "SELL_ACKED"}
    active = [run for run in pathb_runs if str(run.get("status", "")) in active_statuses]
    unknown = [run for run in pathb_runs if str(run.get("status", "")) == "ORDER_UNKNOWN"]
    metrics = _path_b_metrics(pathb_runs)
    comparison = _path_performance_comparison(events or [], pathb_runs)
    consistency_health = _path_b_consistency_health(pathb_runs, events or [], broker_truth)
    config = _path_b_config(runtime_mode)
    control = _path_b_control_state(runtime_mode)
    return {
        "config": config,
        "control": control,
        "runs": len(pathb_runs),
        "metrics": metrics,
        "path_comparison": comparison,
        "consistency_health": consistency_health,
        "charts": _path_b_charts(pathb_runs, metrics),
        "selection": _path_b_selection_snapshot(
            market=market,
            runtime_mode=runtime_mode,
            session_date=session_date,
            pathb_runs=pathb_runs,
            config=config,
            control=control,
            name_map=name_map,
        ),
        "active": _compact_path_runs(active, name_map=name_map, broker_truth=broker_truth),
        "recent": _compact_path_runs(pathb_runs, name_map=name_map, broker_truth=broker_truth),
        "order_unknown": _compact_path_runs(unknown, name_map=name_map, broker_truth=broker_truth),
        "status_counts": dict(status_counts),
        "waiting": status_counts.get("WAITING", 0),
        "filled": status_counts.get("FILLED", 0),
        "closed": status_counts.get("CLOSED", 0),
        "expired": status_counts.get("EXPIRED", 0),
        "cancelled": status_counts.get("CANCELLED", 0),
    }


def _path_b_selection_snapshot(
    *,
    market: str | None,
    runtime_mode: str | None,
    session_date: str,
    pathb_runs: list[dict[str, Any]],
    config: dict[str, Any],
    control: dict[str, Any],
    name_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    if not market_key:
        return {
            "market": "ALL",
            "counts": {},
            "watch_rows": [],
            "no_plan_reasons": ["MARKET_NOT_SELECTED"],
            "message": "Select KR or US to inspect Path B selection state.",
        }

    rec = _load_judgment_record(market_key, runtime_mode, session_date)
    meta = rec.get("selection_meta") if isinstance(rec.get("selection_meta"), dict) else {}
    stages = rec.get("selection_stages") if isinstance(rec.get("selection_stages"), dict) else {}
    raw_stage = stages.get("raw") if isinstance(stages.get("raw"), dict) else {}
    normalized_stage = stages.get("normalized") if isinstance(stages.get("normalized"), dict) else {}

    watchlist = _unique_list(meta.get("watchlist") or rec.get("tickers") or raw_stage.get("watchlist") or [])
    raw_trade_ready = _unique_list(raw_stage.get("trade_ready") or meta.get("_raw_trade_ready") or [])
    applied_trade_ready = _unique_list(meta.get("trade_ready") or rec.get("trade_ready_tickers") or [])
    runtime_filtered = meta.get("_runtime_filtered_trade_ready") or normalized_stage.get("runtime_filtered") or {}
    runtime_filtered = runtime_filtered if isinstance(runtime_filtered, dict) else {}
    price_targets = meta.get("price_targets") if isinstance(meta.get("price_targets"), dict) else {}
    reasons = meta.get("reasons") if isinstance(meta.get("reasons"), dict) else {}
    recommended = meta.get("recommended_strategy") if isinstance(meta.get("recommended_strategy"), dict) else {}
    adaptive = meta.get("_adaptive_live_condition") if isinstance(meta.get("_adaptive_live_condition"), dict) else {}
    adaptive_decisions = adaptive.get("decisions") if isinstance(adaptive.get("decisions"), dict) else {}
    live_evidence = meta.get("_live_evidence") if isinstance(meta.get("_live_evidence"), dict) else {}
    live_evidence_packs = live_evidence.get("packs") if isinstance(live_evidence.get("packs"), dict) else {}
    compact_validation = meta.get("_compact_validation") if isinstance(meta.get("_compact_validation"), dict) else {}
    candidate_actions = meta.get("candidate_actions") if isinstance(meta.get("candidate_actions"), list) else []
    missing_strategy_count = sum(1 for ticker in watchlist if not _selection_lookup(recommended, ticker, market_key))

    run_by_ticker = {str(run.get("ticker", "") or "").upper() if market_key == "US" else str(run.get("ticker", "") or ""): run for run in pathb_runs}
    missing_applied = [ticker for ticker in applied_trade_ready if not _selection_lookup(price_targets, ticker, market_key)]
    missing_raw = [ticker for ticker in raw_trade_ready if not _selection_lookup(price_targets, ticker, market_key)]
    filtered_tickers = _unique_list(runtime_filtered.keys())
    no_plan_reasons: list[str] = []

    if not bool(config.get("enabled", False)):
        no_plan_reasons.append("PATHB_CONFIG_DISABLED")
    if not bool(control.get("enabled", True)):
        no_plan_reasons.append("PATHB_OPERATOR_DISABLED")
    if bool(config.get("emergency_disable", False)) or bool(control.get("emergency_disabled", False)):
        no_plan_reasons.append("PATHB_EMERGENCY_DISABLED")
    if not rec:
        no_plan_reasons.append("NO_SELECTION_FILE")
    elif not watchlist:
        no_plan_reasons.append("NO_WATCHLIST")
    elif raw_trade_ready and not applied_trade_ready:
        no_plan_reasons.append("ALL_TRADE_READY_FILTERED")
    elif applied_trade_ready and not price_targets:
        no_plan_reasons.append("PRICE_TARGETS_EMPTY")
    elif missing_applied:
        no_plan_reasons.append("MISSING_PRICE_TARGETS")
    elif applied_trade_ready and not pathb_runs:
        no_plan_reasons.append("NO_PATH_RUN_REGISTERED")
    elif not applied_trade_ready:
        no_plan_reasons.append("NO_TRADE_READY")
    if meta.get("_candidate_actions_missing_contract"):
        no_plan_reasons.append("CANDIDATE_ACTIONS_MISSING_CONTRACT")
    if str(meta.get("_fallback_mode") or "") in {"selection_truncated", "selection_parse_failed"}:
        no_plan_reasons.append(str(meta.get("_fallback_mode")).upper())

    ordered = _unique_list(list(applied_trade_ready) + list(filtered_tickers) + list(raw_trade_ready) + list(watchlist))
    watch_rows: list[dict[str, Any]] = []
    for ticker in ordered[:30]:
        name = _name_for_ticker(ticker, market_key, name_map)
        target = _selection_lookup(price_targets, ticker, market_key) or {}
        run = run_by_ticker.get(ticker.upper() if market_key == "US" else ticker)
        filtered_reason = _selection_lookup(runtime_filtered, ticker, market_key)
        adaptive_decision = _selection_lookup(adaptive_decisions, ticker, market_key) or {}
        evidence_pack = _selection_lookup(live_evidence_packs, ticker, market_key) or {}
        evidence_trace = evidence_pack.get("decision_trace") if isinstance(evidence_pack.get("decision_trace"), dict) else {}
        if run:
            state = str(run.get("status") or "REGISTERED")
        elif ticker in applied_trade_ready and target:
            state = "READY_NO_PATH_RUN"
        elif ticker in applied_trade_ready:
            state = "MISSING_PRICE_TARGETS"
        elif filtered_reason:
            state = str(filtered_reason)
        else:
            state = "WATCH_ONLY"
        watch_rows.append(
            {
                "ticker": ticker,
                "name": name,
                "display_ticker": _display_ticker(ticker, name),
                "category": (
                    "applied_trade_ready" if ticker in applied_trade_ready
                    else "filtered_trade_ready" if filtered_reason
                    else "raw_trade_ready" if ticker in raw_trade_ready
                    else "watch_only"
                ),
                "state": state,
                "reason": _selection_lookup(reasons, ticker, market_key) or "",
                "recommended_strategy": _selection_lookup(recommended, ticker, market_key) or "",
                "filter_reason": filtered_reason or "",
                "buy_zone_low": target.get("buy_zone_low", "") if isinstance(target, dict) else "",
                "buy_zone_high": target.get("buy_zone_high", "") if isinstance(target, dict) else "",
                "sell_target": target.get("sell_target", "") if isinstance(target, dict) else "",
                "stop_loss": target.get("stop_loss", "") if isinstance(target, dict) else "",
                "confidence": target.get("confidence", "") if isinstance(target, dict) else "",
                "entry_rationale": target.get("entry_rationale", "") if isinstance(target, dict) else "",
                "exit_rationale": target.get("exit_rationale", "") if isinstance(target, dict) else "",
                "adaptive_action": adaptive_decision.get("action", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_score": adaptive_decision.get("score", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_size_intent": adaptive_decision.get("size_intent", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_suggested_claude_action": adaptive_decision.get("suggested_claude_action", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_suggested_size_intent": adaptive_decision.get("suggested_size_intent", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_claude_reask": bool(adaptive_decision.get("claude_reask")) if isinstance(adaptive_decision, dict) else False,
                "adaptive_non_executable": bool(adaptive_decision.get("non_executable")) if isinstance(adaptive_decision, dict) else False,
                "adaptive_action_ceiling": adaptive_decision.get("action_ceiling", "") if isinstance(adaptive_decision, dict) else "",
                "adaptive_reasons": adaptive_decision.get("reason_codes", []) if isinstance(adaptive_decision, dict) else [],
                "adaptive_blockers": adaptive_decision.get("blockers", []) if isinstance(adaptive_decision, dict) else [],
                "live_evidence_state": evidence_pack.get("data_state", "") if isinstance(evidence_pack, dict) else "",
                "live_evidence_quality": evidence_pack.get("data_quality", "") if isinstance(evidence_pack, dict) else "",
                "live_evidence_missing_fields": evidence_pack.get("missing_fields", []) if isinstance(evidence_pack, dict) else [],
                "live_evidence_action_ceiling": evidence_pack.get("action_ceiling", "") if isinstance(evidence_pack, dict) else "",
                "execution_state": evidence_trace.get("execution_state", "") if isinstance(evidence_trace, dict) else "",
                "execution_block_reason": evidence_trace.get("block_reason", "") if isinstance(evidence_trace, dict) else "",
            }
        )

    return {
        "market": market_key,
        "judgment_file": rec.get("_source_file", ""),
        "fallback_mode": str(meta.get("_fallback_mode", "") or ""),
        "parse_recovered": bool(meta.get("_parse_recovered", False)),
        "counts": {
            "universe": len(rec.get("universe_tickers") or []),
            "watchlist": len(watchlist),
            "raw_trade_ready": len(raw_trade_ready),
            "applied_trade_ready": len(applied_trade_ready),
            "runtime_filtered": len(runtime_filtered),
            "price_targets": len(price_targets),
            "registered_plans": len(pathb_runs),
            "candidate_actions": len(candidate_actions),
            "missing_strategy": missing_strategy_count,
            "compact_validation_errors": len(compact_validation.get("errors") or []) if isinstance(compact_validation, dict) else 0,
            "compact_validation_warnings": len(compact_validation.get("warnings") or []) if isinstance(compact_validation, dict) else 0,
            "adaptive_reask_claude_shadow": len(adaptive.get("reask_claude_shadow") or []),
            "adaptive_suggested_probe_ready_shadow": len(adaptive.get("suggested_probe_ready_shadow") or []),
            "adaptive_suggested_micro_probe_shadow": len(adaptive.get("suggested_micro_probe_shadow") or []),
            "adaptive_probe_ready_shadow": len(adaptive.get("probe_ready_shadow") or []),
            "adaptive_micro_probe_shadow": len(adaptive.get("micro_probe_shadow") or []),
            "live_evidence_missing": ((live_evidence.get("counts") or {}).get("data_state") or {}).get("missing", 0)
            if isinstance(live_evidence.get("counts"), dict) else 0,
            "live_evidence_partial": ((live_evidence.get("counts") or {}).get("data_state") or {}).get("partial", 0)
            if isinstance(live_evidence.get("counts"), dict) else 0,
            "live_evidence_confirmed": ((live_evidence.get("counts") or {}).get("data_state") or {}).get("confirmed", 0)
            if isinstance(live_evidence.get("counts"), dict) else 0,
        },
        "watchlist": watchlist,
        "raw_trade_ready": raw_trade_ready,
        "applied_trade_ready": applied_trade_ready,
        "runtime_filtered": runtime_filtered,
        "missing_price_targets": missing_applied,
        "missing_price_targets_raw": missing_raw,
        "no_plan_reasons": _unique_list(no_plan_reasons),
        "selection_raw_schema": str(meta.get("_selection_raw_schema") or ""),
        "selection_schema_version": str(meta.get("_selection_schema_version") or ""),
        "selection_stop_reason": str(meta.get("_selection_stop_reason") or ""),
        "candidate_actions_source": str(meta.get("_candidate_actions_source") or ""),
        "candidate_actions_missing_contract": bool(meta.get("_candidate_actions_missing_contract")),
        "compact_validation": compact_validation,
        "adaptive_live_condition": adaptive,
        "live_evidence": {key: value for key, value in live_evidence.items() if key != "packs"},
        "watch_rows": watch_rows,
    }


def _load_judgment_record(market: str, runtime_mode: str | None, session_date: str) -> dict[str, Any]:
    day = str(session_date or "").replace("-", "")
    mode = str(runtime_mode or "live").lower()
    candidates = [
        get_runtime_path("logs", "daily_judgment", f"{mode}_{day}_{market}.json", make_parents=False),
        get_runtime_path("logs", "daily_judgment", f"live_{day}_{market}.json", make_parents=False),
        get_runtime_path("logs", "daily_judgment", f"{day}_{market}.json", make_parents=False),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
            if isinstance(data, dict):
                data["_source_file"] = str(path)
                return data
        except Exception:
            continue
    return {}


def _unique_list(values: Any) -> list[str]:
    out: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _selection_lookup(mapping: dict[str, Any], ticker: str, market: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    key = str(ticker or "").upper() if market == "US" else str(ticker or "")
    if key in mapping:
        return mapping.get(key)
    if ticker in mapping:
        return mapping.get(ticker)
    if market == "US":
        for raw_key, value in mapping.items():
            if str(raw_key).upper() == key:
                return value
    return None


def _positions_from_state(runtime_mode: str | None) -> list[dict[str, Any]]:
    mode = str(runtime_mode or "live").lower()
    candidates = [
        get_runtime_path("state", f"{mode}_open_positions.json", make_parents=False),
        get_runtime_path("state", "live_open_positions.json", make_parents=False),
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "[]")
        except Exception:
            continue
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
    return []


def _position_buy_path(pos: dict[str, Any]) -> str:
    path_type = str(pos.get("path_type", "") or "").strip()
    path_run = str(pos.get("pathb_path_run_id", "") or "").strip()
    if path_type == "claude_price" or path_run:
        return "path_b"
    if str(pos.get("v2_decision_id", "") or pos.get("decision_id", "") or "").strip():
        return "path_a"
    strategy = str(pos.get("strategy", "") or "").strip()
    if strategy and strategy not in {"broker_sync", "broker_balance"}:
        return "path_a"
    return "manual_or_broker"


def _buy_path_label(value: str) -> str:
    return {
        "path_a": "Path A - Timing Adapter",
        "path_b": "Path B - Claude Price",
        "manual_or_broker": "수동/브로커 동기화",
    }.get(str(value or ""), str(value or ""))


def _path_b_name_map(markets: list[str], runtime_mode: str | None, session_date: str) -> dict[str, str]:
    name_map: dict[str, str] = {}
    if any(str(m).upper() == "KR" for m in markets):
        for ticker, name in (_KR_TICKERS_STATIC or {}).items():
            _put_name(name_map, ticker, name)
    for market in markets:
        rec = _load_judgment_record(str(market).upper(), runtime_mode, session_date)
        _merge_names_from_record(name_map, rec)
        _merge_names_from_state_files(name_map, str(market).upper(), runtime_mode)
    for pos in _positions_from_state(runtime_mode):
        _put_name(name_map, pos.get("ticker", ""), pos.get("name", ""))
    return name_map


def _merge_names_from_record(name_map: dict[str, str], rec: dict[str, Any]) -> None:
    digest_raw = rec.get("digest_raw") if isinstance(rec.get("digest_raw"), dict) else {}
    technicals = digest_raw.get("technicals") if isinstance(digest_raw.get("technicals"), dict) else {}
    for ticker, info in technicals.items():
        if isinstance(info, dict):
            _put_name(name_map, ticker, info.get("name", ""))
    for bucket in ("candidates", "merged_candidates", "filtered_candidates", "final_candidates"):
        rows = digest_raw.get(bucket) if isinstance(digest_raw.get(bucket), list) else []
        for row in rows:
            if isinstance(row, dict):
                _put_name(name_map, row.get("ticker", ""), row.get("name", ""))


def _merge_names_from_state_files(name_map: dict[str, str], market: str, runtime_mode: str | None) -> None:
    mode = str(runtime_mode or "live").lower()
    candidates = [
        get_runtime_path("state", "kr_screen_cache.json", make_parents=False) if market == "KR" else None,
        get_runtime_path("state", "us_screen_cache.json", make_parents=False) if market == "US" else None,
        get_runtime_path("state", f"{mode}_live_status_{market}.json", make_parents=False),
        get_runtime_path("state", f"{mode}_open_positions.json", make_parents=False),
    ]
    for path in [p for p in candidates if p is not None and p.exists()]:
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace") or "{}")
        except Exception:
            continue
        if isinstance(data, dict):
            for key in ("candidates", "positions", "pending_orders"):
                rows = data.get(key)
                if isinstance(rows, list):
                    for row in rows:
                        if isinstance(row, dict):
                            _put_name(name_map, row.get("ticker", ""), row.get("name", ""))
        elif isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    _put_name(name_map, row.get("ticker", ""), row.get("name", ""))


def _put_name(name_map: dict[str, str], ticker: Any, name: Any) -> None:
    key = str(ticker or "").strip().upper()
    value = str(name or "").strip()
    if key and value and value.upper() != key:
        name_map.setdefault(key, value)


def _name_for_ticker(ticker: Any, market: str, name_map: dict[str, str] | None) -> str:
    key = str(ticker or "").strip().upper()
    return str((name_map or {}).get(key, "") or "").strip()


def _display_ticker(ticker: Any, name: Any = "") -> str:
    raw_ticker = str(ticker or "").strip().upper()
    raw_name = str(name or "").strip()
    if raw_ticker and raw_name and raw_name.upper() != raw_ticker:
        return f"{raw_name} ({raw_ticker})"
    return raw_ticker or raw_name or "-"


def _ticker_key(market: str, ticker: Any) -> str:
    raw = str(ticker or "").strip()
    return raw.upper() if str(market or "").upper() == "US" else raw


def _path_status_overrides(events: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    overrides: dict[str, dict[str, str]] = {}
    event_status = {
        "CLAUDE_PRICE_WAITING": "WAITING",
        "CLAUDE_PRICE_HIT": "HIT",
        "CLAUDE_PRICE_CANCELLED": "CANCELLED",
        "CLAUDE_PRICE_EXPIRED": "EXPIRED",
        "ORDER_SENT": "ORDER_SENT",
        "ORDER_ACKED": "ORDER_ACKED",
        "PARTIAL_FILLED": "PARTIAL_FILLED",
        "FILLED": "FILLED",
        "CLOSED": "CLOSED",
        "ORDER_UNKNOWN": "ORDER_UNKNOWN",
    }
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id", "") or "").strip()
        if not path_run_id:
            continue
        status = str(payload.get("path_status", "") or "").strip()
        if not status:
            status = event_status.get(str(event.get("event_type", "") or ""), "")
        if not status:
            continue
        overrides[path_run_id] = {
            "status": status,
            "event_type": str(event.get("event_type", "") or ""),
            "occurred_at": str(event.get("occurred_at", "") or ""),
        }
    return overrides


def _apply_lifecycle_status(run: dict[str, Any], overrides: dict[str, dict[str, str]]) -> dict[str, Any]:
    path_run_id = str(run.get("path_run_id", "") or "")
    override = overrides.get(path_run_id)
    if not override:
        return run
    stored_status = str(run.get("status", "") or "")
    effective_status = str(override.get("status", "") or stored_status)
    if not effective_status or effective_status == stored_status:
        return run
    out = dict(run)
    out["stored_status"] = stored_status
    out["status"] = effective_status
    out["status_from_lifecycle"] = True
    out["status_lifecycle_event_type"] = override.get("event_type", "")
    out["status_lifecycle_at"] = override.get("occurred_at", "")
    return out


def _path_b_consistency_health(
    runs: list[dict[str, Any]],
    events: list[dict[str, Any]],
    broker_truth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    runs_by_id = {str(run.get("path_run_id", "") or ""): run for run in runs}
    pathb_ids = {path_run_id for path_run_id in runs_by_id if path_run_id}
    pathb_keys = {
        (
            str(run.get("decision_id", "") or ""),
            _ticker_key(str(run.get("market", "") or ""), run.get("ticker", "")),
        )
        for run in runs
    }
    status_event_types = {
        "CLAUDE_PRICE_PLAN_CREATED",
        "CLAUDE_PRICE_WAITING",
        "CLAUDE_PRICE_HIT",
        "CLAUDE_PRICE_CANCELLED",
        "CLAUDE_PRICE_EXPIRED",
        "ORDER_SENT",
        "ORDER_ACKED",
        "PARTIAL_FILLED",
        "FILLED",
        "SELL_SENT",
        "SELL_ACKED",
        "SELL_PARTIAL_FILLED",
        "CLOSED",
        "ORDER_UNKNOWN",
    }
    execution_required_types = {
        "ORDER_SENT",
        "ORDER_ACKED",
        "PARTIAL_FILLED",
        "FILLED",
        "SELL_SENT",
        "SELL_ACKED",
        "SELL_PARTIAL_FILLED",
        "CLOSED",
    }
    for run in runs:
        status = str(run.get("status", "") or "")
        stored_status = str(run.get("stored_status", status) or status)
        if bool(run.get("status_from_lifecycle", False)) and stored_status != status:
            issues.append(
                {
                    "code": "raw_status_differs_from_lifecycle",
                    "path_run_id": run.get("path_run_id", ""),
                    "ticker": run.get("ticker", ""),
                    "stored_status": stored_status,
                    "effective_status": status,
                    "lifecycle_event_type": run.get("status_lifecycle_event_type", ""),
                    "lifecycle_at": run.get("status_lifecycle_at", ""),
                }
            )
        if status == "ORDER_UNKNOWN" or stored_status == "ORDER_UNKNOWN":
            evidence = _broker_evidence_for_path_run(run, broker_truth)
            plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
            issues.append(
                {
                    "code": "active_order_unknown",
                    "path_run_id": run.get("path_run_id", ""),
                    "ticker": run.get("ticker", ""),
                    "stored_status": stored_status,
                    "effective_status": status,
                    "resolution": str(plan.get("order_unknown_resolution", "") or ""),
                    "broker_position_evidence": bool(
                        plan.get("broker_position_evidence", False)
                        or evidence.get("broker_position_evidence", False)
                    ),
                    "broker_open_order_evidence": bool(
                        plan.get("broker_open_order_evidence", False)
                        or evidence.get("broker_open_order_evidence", False)
                    ),
                    "broker_today_fill_evidence": bool(
                        plan.get("broker_today_fill_evidence", False)
                        or evidence.get("broker_today_fill_evidence", False)
                    ),
                }
            )
    for event in events:
        event_type = str(event.get("event_type", "") or "")
        if event_type not in status_event_types:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id", "") or "")
        path_type = str(payload.get("path_type", "") or "")
        market = str(event.get("market", "") or "")
        ticker = _ticker_key(market, event.get("ticker", ""))
        decision_id = str(event.get("decision_id", "") or "")
        decision_match = (decision_id, ticker) in pathb_keys
        pathb_related = path_type == "claude_price" or path_run_id in pathb_ids or decision_match
        if not pathb_related:
            continue
        if not decision_id:
            issues.append(
                {
                    "code": "pathb_lifecycle_missing_decision_id",
                    "event_id": event.get("event_id", 0),
                    "event_type": event_type,
                    "path_run_id": path_run_id,
                    "ticker": ticker,
                }
            )
        if not path_run_id:
            issues.append(
                {
                    "code": "pathb_lifecycle_missing_path_run_id",
                    "event_id": event.get("event_id", 0),
                    "event_type": event_type,
                    "decision_id": decision_id,
                    "ticker": ticker,
                }
            )
        elif path_run_id not in pathb_ids:
            issues.append(
                {
                    "code": "lifecycle_path_run_missing",
                    "event_id": event.get("event_id", 0),
                    "event_type": event_type,
                    "path_run_id": path_run_id,
                    "ticker": ticker,
                }
            )
        elif path_type != "claude_price":
            issues.append(
                {
                    "code": "pathb_lifecycle_missing_path_type",
                    "event_id": event.get("event_id", 0),
                    "event_type": event_type,
                    "path_run_id": path_run_id,
                    "ticker": ticker,
                }
            )
        if event_type in execution_required_types and not str(event.get("execution_id", "") or ""):
            issues.append(
                {
                    "code": "pathb_lifecycle_missing_execution_id",
                    "event_id": event.get("event_id", 0),
                    "event_type": event_type,
                    "path_run_id": path_run_id,
                    "ticker": ticker,
                }
            )
    return {
        "ok": len(issues) == 0,
        "issue_count": len(issues),
        "checked_runs": len(runs),
        "checked_events": len(events),
        "issues": issues[:20],
    }


def _broker_evidence_for_path_run(run: dict[str, Any], broker_truth: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(broker_truth, dict):
        return {}
    market = str(run.get("market", "") or "").upper()
    ticker = _ticker_key(market, run.get("ticker", ""))
    if not market or not ticker:
        return {}
    markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    data = markets.get(market) if isinstance(markets.get(market), dict) else {}
    if not data:
        return {}
    plan = run.get("plan") or run.get("plan_json") or {}
    if not isinstance(plan, dict):
        plan = {}
    execution_ids = {
        str(plan.get("entry_execution_id", "") or "").strip(),
        str(plan.get("exit_execution_id", "") or "").strip(),
    }
    execution_ids = {value for value in execution_ids if value}
    status = str(run.get("status", "") or "")
    openish_statuses = {
        "ORDER_SENT",
        "ORDER_ACKED",
        "PARTIAL_FILLED",
        "FILLED",
        "SELL_SENT",
        "SELL_ACKED",
        "SELL_PARTIAL_FILLED",
        "ORDER_UNKNOWN",
    }

    def matching(rows: Any) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for row in list(rows or []):
            if not isinstance(row, dict):
                continue
            if _ticker_key(market, row.get("ticker", "")) == ticker:
                out.append(row)
        return out

    def matching_orders(rows: Any, *, fallback_to_ticker: bool) -> list[dict[str, Any]]:
        ticker_matches = matching(rows)
        if not execution_ids:
            return ticker_matches if fallback_to_ticker else []
        exact = [
            row for row in ticker_matches
            if str(row.get("order_no", "") or "").strip() in execution_ids
        ]
        return exact if exact or not fallback_to_ticker else ticker_matches

    positions = matching(data.get("positions", [])) if status in openish_statuses else []
    open_orders = matching_orders(data.get("open_orders", []), fallback_to_ticker=status in openish_statuses)
    fills = matching_orders(data.get("today_fills", []), fallback_to_ticker=status in openish_statuses)
    return {
        "broker_position_evidence": bool(positions),
        "broker_open_order_evidence": bool(open_orders),
        "broker_today_fill_evidence": bool(fills),
        "broker_truth_last_success_at": data.get("last_success_at", ""),
        "broker_truth_stale": bool(data.get("stale")),
        "broker_truth_error": str(data.get("error", "") or ""),
        "broker_position_count": len(positions),
        "broker_open_order_count": len(open_orders),
        "broker_today_fill_count": len(fills),
    }


def _path_b_metrics(runs: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(runs)
    currencies = {
        "USD" if str(run.get("market", "") or "").upper() == "US" else "KRW"
        for run in runs
        if str(run.get("market", "") or "").upper() in {"KR", "US"}
    }
    currency = next(iter(currencies)) if len(currencies) == 1 else ("mixed" if currencies else "native")
    entered_statuses = {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED", "CLOSED"}
    entered = [run for run in runs if str(run.get("status", "")) in entered_statuses]
    closed = [run for run in runs if str(run.get("status", "")) == "CLOSED"]
    expired = [run for run in runs if str(run.get("status", "")) == "EXPIRED"]
    cancelled = [run for run in runs if str(run.get("status", "")) == "CANCELLED"]
    target_hits = [run for run in closed if str((run.get("plan") or {}).get("close_reason") or "") == "CLOSED_CLAUDE_PRICE_TARGET"]
    stop_hits = [
        run for run in closed
        if str((run.get("plan") or {}).get("close_reason") or "") in {"CLOSED_CLAUDE_PRICE_STOP", "CLOSED_HARD_STOP"}
    ]
    pnl_values = [_to_float((run.get("plan") or {}).get("pnl_pct")) for run in closed]
    pnl_values = [value for value in pnl_values if value is not None]
    wins = [value for value in pnl_values if value > 0]
    deployed = 0.0
    realized = 0.0
    for run in runs:
        plan = run.get("plan") or {}
        qty = _to_float(plan.get("filled_qty") or plan.get("entry_qty")) or 0.0
        entry = _to_float(plan.get("actual_entry_price") or plan.get("entry_order_price")) or 0.0
        pnl_pct = _to_float(plan.get("pnl_pct")) or 0.0
        if qty > 0 and entry > 0:
            amount = qty * entry
            deployed += amount
            if str(run.get("status", "")) == "CLOSED":
                realized += amount * pnl_pct / 100.0
    return {
        "total_plans": total,
        "entered": len(entered),
        "closed": len(closed),
        "expired": len(expired),
        "cancelled": len(cancelled),
        "target_hits": len(target_hits),
        "stop_hits": len(stop_hits),
        "entry_rate_pct": _pct(len(entered), total),
        "win_rate_pct": _pct(len(wins), len(pnl_values)),
        "target_hit_rate_pct": _pct(len(target_hits), len(closed)),
        "stop_hit_rate_pct": _pct(len(stop_hits), len(closed)),
        "expired_rate_pct": _pct(len(expired), total),
        "avg_pnl_pct": round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else 0.0,
        "realized_pnl_value": round(realized, 4),
        "deployed_value": round(deployed, 4),
        "currency": currency,
    }


def _path_b_charts(runs: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
    status_counts = Counter(str(run.get("status", "") or "UNKNOWN") for run in runs)
    closed = [run for run in runs if str(run.get("status", "")) == "CLOSED"]
    pnl_labels: list[str] = []
    pnl_series: list[float] = []
    point_pnl_pct: list[float] = []
    point_close_reasons: list[str] = []
    cumulative = 0.0
    for idx, run in enumerate(closed, 1):
        plan = run.get("plan") or {}
        pnl_pct = float(_to_float(plan.get("pnl_pct")) or 0.0)
        cumulative += pnl_pct
        pnl_labels.append(str(run.get("ticker") or idx))
        pnl_series.append(round(cumulative, 4))
        point_pnl_pct.append(round(pnl_pct, 4))
        point_close_reasons.append(str(plan.get("close_reason") or ""))
    return {
        "status": {
            "labels": list(status_counts.keys()),
            "data": list(status_counts.values()),
        },
        "outcomes": {
            "labels": ["진입", "목표가", "손절", "미진입"],
            "data": [
                metrics.get("entry_rate_pct", 0),
                metrics.get("target_hit_rate_pct", 0),
                metrics.get("stop_hit_rate_pct", 0),
                metrics.get("expired_rate_pct", 0),
            ],
        },
        "pnl": {
            "labels": pnl_labels,
            "data": pnl_series,
            "point_pnl_pct": point_pnl_pct,
            "point_close_reasons": point_close_reasons,
            "basis": "cumulative_sum_of_closed_trade_pnl_pct",
        },
    }


def _closed_trade_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl_values = [float(row.get("pnl_pct", 0) or 0) for row in rows if row.get("pnl_pct") is not None]
    pnl_amounts = [float(row.get("pnl_value", 0) or 0) for row in rows if row.get("pnl_value") is not None]
    wins = [value for value in pnl_values if value > 0]
    return {
        "closed": len(rows),
        "wins": len(wins),
        "win_rate_pct": _pct(len(wins), len(pnl_values)),
        "avg_pnl_pct": round(sum(pnl_values) / len(pnl_values), 4) if pnl_values else 0.0,
        "realized_pnl_value": round(sum(pnl_amounts), 4) if pnl_amounts else 0.0,
    }


def _path_performance_comparison(events: list[dict[str, Any]], pathb_runs: list[dict[str, Any]]) -> dict[str, Any]:
    path_a_rows: list[dict[str, Any]] = []
    for event in events:
        if event.get("event_type") != "CLOSED":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        close_reason = str(payload.get("close_reason") or event.get("reason_code") or "")
        if close_reason == "CLOSED_BROKER_SYNC":
            continue
        if str(payload.get("path_type") or "") == "claude_price" or str(payload.get("path_run_id") or ""):
            continue
        path_a_rows.append(
            {
                "ticker": event.get("ticker", ""),
                "pnl_pct": _to_float(payload.get("pnl_pct")),
                "pnl_value": _to_float(payload.get("pnl_krw")),
            }
        )

    path_b_rows: list[dict[str, Any]] = []
    counted_path_run_ids: set[str] = set()
    for run in pathb_runs:
        if str(run.get("status", "")) != "CLOSED":
            continue
        plan = run.get("plan") or {}
        close_reason = str(plan.get("close_reason") or "")
        if close_reason == "CLOSED_BROKER_SYNC":
            continue
        path_run_id = str(run.get("path_run_id", "") or "").strip()
        if path_run_id:
            counted_path_run_ids.add(path_run_id)
        path_b_rows.append(
            {
                "ticker": run.get("ticker", ""),
                "pnl_pct": _to_float(plan.get("pnl_pct")),
                "pnl_value": _path_b_realized_value(run),
            }
        )
    for event in events:
        if event.get("event_type") != "CLOSED":
            continue
        payload = event.get("payload") or {}
        if not isinstance(payload, dict):
            payload = {}
        close_reason = str(payload.get("close_reason") or event.get("reason_code") or "")
        if close_reason == "CLOSED_BROKER_SYNC":
            continue
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        path_type = str(payload.get("path_type") or "").strip()
        buy_path = str(payload.get("buy_path") or payload.get("entry_route") or "").strip()
        if path_type != "claude_price" and buy_path != "path_b":
            continue
        if path_run_id and path_run_id in counted_path_run_ids:
            continue
        if path_run_id:
            counted_path_run_ids.add(path_run_id)
        path_b_rows.append(
            {
                "ticker": event.get("ticker", ""),
                "pnl_pct": _to_float(payload.get("pnl_pct")),
                "pnl_value": _to_float(payload.get("pnl_krw") or payload.get("realized_pnl_value")),
            }
        )

    path_a = _closed_trade_metrics(path_a_rows)
    path_b = _closed_trade_metrics(path_b_rows)
    return {
        "basis": "실현 청산 기준",
        "path_a": {"label": _buy_path_label("path_a"), **path_a},
        "path_b": {"label": _buy_path_label("path_b"), **path_b},
        "delta": {
            "avg_pnl_pct": round(float(path_b.get("avg_pnl_pct", 0) or 0) - float(path_a.get("avg_pnl_pct", 0) or 0), 4),
            "realized_pnl_value": round(float(path_b.get("realized_pnl_value", 0) or 0) - float(path_a.get("realized_pnl_value", 0) or 0), 4),
        },
        "chart": {
            "labels": ["Path A", "Path B"],
            "data": [path_a.get("avg_pnl_pct", 0), path_b.get("avg_pnl_pct", 0)],
        },
    }


def _path_b_realized_value(run: dict[str, Any]) -> float | None:
    plan = run.get("plan") or {}
    explicit = _to_float(plan.get("pnl_krw") or plan.get("realized_pnl_value"))
    if explicit is not None:
        return explicit
    pnl_pct = _to_float(plan.get("pnl_pct"))
    qty = _to_float(plan.get("filled_qty") or plan.get("entry_qty"))
    entry = _to_float(plan.get("actual_entry_price") or plan.get("entry_order_price"))
    if pnl_pct is None or qty is None or entry is None or qty <= 0 or entry <= 0:
        return None
    return float(qty) * float(entry) * float(pnl_pct) / 100.0


def _pct(numerator: int, denominator: int) -> float:
    return round((float(numerator) / float(denominator)) * 100.0, 2) if denominator else 0.0


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _path_b_config(runtime_mode: str | None = None) -> dict[str, Any]:
    env, source = _effective_runtime_env(runtime_mode)
    cfg = DEFAULT_V2_CONFIG
    return {
        "enabled": _env_bool(env, "PATHB_ENABLED", cfg.pathb_enabled),
        "mode": str(env.get("PATHB_MODE", cfg.pathb_mode) or cfg.pathb_mode),
        "fixed_order_krw": _env_int(env, "PATHB_FIXED_ORDER_KRW", cfg.pathb_fixed_order_krw),
        "max_positions": _env_int(env, "PATHB_MAX_POSITIONS", cfg.pathb_max_positions),
        "max_daily_entries": _env_int(env, "PATHB_MAX_DAILY_ENTRIES", cfg.pathb_max_daily_entries),
        "min_confidence": _env_float(env, "PATHB_MIN_CONFIDENCE", cfg.pathb_min_confidence),
        "intraday_only": _env_bool(env, "PATHB_INTRADAY_ONLY", cfg.pathb_intraday_only),
        "emergency_disable": _env_bool(env, "PATHB_EMERGENCY_DISABLE", cfg.pathb_emergency_disable),
        "source": source,
    }


def _norm_config_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _latest_runtime_config_snapshot(mode: str) -> tuple[str, dict[str, Any]]:
    try:
        config_dir = get_runtime_path("logs", "config", "_probe", make_parents=False).parent
    except Exception:
        config_dir = Path(__file__).resolve().parent.parent / "logs" / "config"
    if not config_dir.exists():
        return "", {}
    candidates = sorted(
        config_dir.glob(f"effective_config_*_{mode}.redacted.json"),
        key=lambda path: path.stat().st_mtime if path.exists() else 0,
        reverse=True,
    )
    for path in candidates:
        try:
            return str(path), json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return "", {}


def _runtime_config_drift(effective: dict[str, str], mode: str) -> dict[str, Any]:
    snapshot_path, payload = _latest_runtime_config_snapshot(mode)
    runtime_effective = dict((payload or {}).get("effective") or {})
    drift: dict[str, dict[str, str]] = {}
    for key in RUNTIME_DRIFT_KEYS:
        if key not in effective and key not in runtime_effective:
            continue
        file_value = _norm_config_value(effective.get(key, ""))
        runtime_value = _norm_config_value(runtime_effective.get(key, ""))
        if file_value != runtime_value:
            drift[key] = {"file_effective": file_value, "runtime_snapshot": runtime_value}
    return {
        "snapshot_path": snapshot_path,
        "written_at": (payload or {}).get("written_at", ""),
        "drift": drift,
    }


def _effective_runtime_env(runtime_mode: str | None) -> tuple[dict[str, str], dict[str, Any]]:
    root = Path(__file__).resolve().parent.parent
    mode = str(runtime_mode or "live").lower()
    env_path = root / f".env.{mode}"
    if not env_path.exists():
        env_path = root / ".env"
    base_env = _read_env_file(env_path)
    effective = dict(base_env)
    disabled = str(base_env.get("V2_START_CONFIG_DISABLED", "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}
    start_config_raw = str(base_env.get("V2_START_CONFIG_PATH", "") or os.getenv("V2_START_CONFIG_PATH", "config/v2_start_config.json"))
    start_config_path = Path(start_config_raw)
    if not start_config_path.is_absolute():
        start_config_path = root / start_config_path
    overrides: dict[str, str] = {}
    applied = False
    if mode == "live" and not disabled and start_config_path.exists():
        try:
            data = json.loads(start_config_path.read_text(encoding="utf-8"))
            raw = data.get("env_overrides") or {}
            if isinstance(raw, dict):
                overrides = {str(k): str(v).lower() if isinstance(v, bool) else str(v) for k, v in raw.items() if v is not None}
                effective.update(overrides)
                applied = True
        except Exception:
            applied = False
    watched = [
        "ENABLED_MARKETS",
        "PATHB_FIXED_ORDER_KRW",
        "PATHB_MAX_POSITIONS",
        "PATHB_MAX_DAILY_ENTRIES",
        "PATHB_ENABLED",
        "PATHB_KR_LIVE_ENABLED",
        "PATHB_US_LIVE_ENABLED",
        "PATHB_INTRADAY_ONLY",
        "PATHB_MIN_CONFIDENCE",
        "V2_MAX_DAILY_ENTRIES",
        "KR_DAILY_ENTRY_CAP",
        "US_DAILY_ENTRY_CAP",
        "KR_MAX_POSITIONS",
        "US_MAX_POSITIONS",
    ]
    conflicts = {
        key: {"runtime_env": base_env.get(key), "start_config": overrides.get(key)}
        for key in watched
        if key in base_env and key in overrides and str(base_env.get(key)) != str(overrides.get(key))
    }
    runtime_snapshot = _runtime_config_drift(effective, mode)
    return effective, {
        "runtime_env": str(env_path),
        "start_config": str(start_config_path) if start_config_path.exists() else "",
        "start_config_applied": applied,
        "conflicts": conflicts,
        "runtime_snapshot": runtime_snapshot,
        "runtime_drift": runtime_snapshot.get("drift", {}),
    }


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    data: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        return int(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        return int(default)


def _env_float(env: dict[str, str], key: str, default: float) -> float:
    try:
        return float(str(env.get(key, default)).strip())
    except (TypeError, ValueError):
        return float(default)


def _env_bool(env: dict[str, str], key: str, default: bool) -> bool:
    raw = str(env.get(key, default)).strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _path_b_control_state(runtime_mode: str | None) -> dict[str, Any]:
    mode = str(runtime_mode or "live").lower()
    path = get_runtime_path("state", f"{mode}_pathb_control.json", make_parents=False)
    if not path.exists():
        return {"enabled": True, "emergency_disabled": False, "updated_at": "", "updated_by": "default", "reason": ""}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {"enabled": True, "emergency_disabled": False, "updated_at": "", "updated_by": "unreadable", "reason": "control_read_failed"}
    return {
        "enabled": bool(data.get("enabled", True)),
        "emergency_disabled": bool(data.get("emergency_disabled", False)),
        "updated_at": str(data.get("updated_at", "") or ""),
        "updated_by": str(data.get("updated_by", "") or ""),
        "reason": str(data.get("reason", "") or ""),
    }


def _compact_path_runs(
    runs: list[dict[str, Any]],
    *,
    name_map: dict[str, str] | None = None,
    broker_truth: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    compact = []
    for run in runs[-20:]:
        plan = run.get("plan") or run.get("plan_json") or {}
        if not isinstance(plan, dict):
            plan = {}
        ticker = str(run.get("ticker", "") or "")
        market = str(run.get("market", "") or "")
        name = str(plan.get("name", "") or _name_for_ticker(ticker, market, name_map) or "")
        broker_evidence = _broker_evidence_for_path_run(run, broker_truth)
        compact.append(
            {
                "market": market,
                "runtime_mode": run.get("runtime_mode", ""),
                "ticker": ticker,
                "name": name,
                "display_ticker": _display_ticker(ticker, name),
                "buy_path": "path_b",
                "buy_path_label": _buy_path_label("path_b"),
                "decision_id": run.get("decision_id", ""),
                "path_run_id": run.get("path_run_id", ""),
                "status": run.get("status", ""),
                "stored_status": run.get("stored_status", run.get("status", "")),
                "status_from_lifecycle": bool(run.get("status_from_lifecycle", False)),
                "status_lifecycle_event_type": run.get("status_lifecycle_event_type", ""),
                "status_lifecycle_at": run.get("status_lifecycle_at", ""),
                "buy_zone_low": plan.get("buy_zone_low", ""),
                "buy_zone_high": plan.get("buy_zone_high", ""),
                "sell_target": plan.get("sell_target", ""),
                "stop_loss": plan.get("stop_loss", ""),
                "confidence": plan.get("confidence", ""),
                "entry_rationale": plan.get("entry_rationale", ""),
                "exit_rationale": plan.get("exit_rationale", ""),
                "rationale": plan.get("rationale", ""),
                "entry_basis_tags": plan.get("entry_basis_tags", []),
                "exit_basis_tags": plan.get("exit_basis_tags", []),
                "invalidation_conditions": plan.get("invalidation_conditions", []),
                "entry_order_price": plan.get("entry_order_price", ""),
                "actual_entry_price": plan.get("actual_entry_price", ""),
                "actual_exit_price": plan.get("actual_exit_price", ""),
                "filled_qty": plan.get("filled_qty", ""),
                "pnl_pct": plan.get("pnl_pct", ""),
                "cancel_reason": plan.get("cancel_reason", ""),
                "close_reason": plan.get("close_reason", ""),
                "order_unknown_resolution": plan.get("order_unknown_resolution", ""),
                "order_unknown_resolution_at": plan.get("order_unknown_resolution_at", ""),
                "broker_position_evidence": bool(
                    plan.get("broker_position_evidence", False)
                    or broker_evidence.get("broker_position_evidence", False)
                ),
                "broker_open_order_evidence": bool(
                    plan.get("broker_open_order_evidence", False)
                    or broker_evidence.get("broker_open_order_evidence", False)
                ),
                "broker_today_fill_evidence": bool(
                    plan.get("broker_today_fill_evidence", False)
                    or broker_evidence.get("broker_today_fill_evidence", False)
                ),
                "path_a_origin_possible": bool(plan.get("path_a_lifecycle_evidence") or plan.get("path_a_pending_evidence")),
                "broker_truth_last_success_at": plan.get("broker_truth_last_success_at", "")
                or broker_evidence.get("broker_truth_last_success_at", ""),
                "broker_truth_stale": bool(broker_evidence.get("broker_truth_stale", False)),
                "broker_truth_error": broker_evidence.get("broker_truth_error", ""),
                "broker_position_count": broker_evidence.get("broker_position_count", 0),
                "broker_open_order_count": broker_evidence.get("broker_open_order_count", 0),
                "broker_today_fill_count": broker_evidence.get("broker_today_fill_count", 0),
                "session_end_unresolved": bool(plan.get("session_end_unresolved", False)),
                "created_at": run.get("created_at", ""),
                "updated_at": run.get("updated_at", ""),
            }
        )
    return compact


def _pending_orders_from_bot(bot: Any | None) -> list[dict[str, Any]]:
    if bot is None:
        return []
    pending = []
    for attr in ("pending_orders", "pending_order_queue"):
        value = getattr(bot, attr, None)
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            pending.extend(value.values())
    return pending


def _broker_status(bot: Any | None) -> dict[str, str]:
    if bot is None:
        return {"KR": "unknown", "US": "unknown"}
    status = getattr(bot, "broker_status", None)
    if isinstance(status, dict):
        return {
            "KR": str(status.get("KR", "unknown")),
            "US": str(status.get("US", "unknown")),
        }
    trust = getattr(bot, "broker_trust_level", "unknown")
    return {"KR": str(trust), "US": str(trust)}


def _rate_limit_state(bot: Any | None) -> dict[str, Any]:
    limiter = getattr(bot, "v2_order_rate_limiter", None) if bot is not None else None
    if limiter is None:
        return {"enabled": False}
    return {"enabled": True, "class": limiter.__class__.__name__}


def _claude_picks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    picks = []
    for event in events:
        if event.get("event_type") != "CLAUDE_TRADE_READY":
            continue
        payload = event.get("payload") or {}
        selection_meta = payload.get("selection_meta") if isinstance(payload.get("selection_meta"), dict) else {}
        price_targets = selection_meta.get("price_targets") if isinstance(selection_meta.get("price_targets"), dict) else {}
        ticker = str(event.get("ticker", "") or "")
        market = str(event.get("market", "") or "")
        key = ticker.upper() if market == "US" else ticker
        target = price_targets.get(key) or price_targets.get(ticker) or {}
        if not isinstance(target, dict):
            target = {}
        picks.append(
            {
                "market": market,
                "ticker": ticker,
                "decision_id": event.get("decision_id", ""),
                "path_a": "timing_adapter",
                "path_b": "claude_price" if target else "",
                "timing_style": payload.get("timing_style", ""),
                "buy_zone_low": target.get("buy_zone_low", ""),
                "buy_zone_high": target.get("buy_zone_high", ""),
                "sell_target": target.get("sell_target", ""),
                "stop_loss": target.get("stop_loss", ""),
                "confidence": target.get("confidence", ""),
                "created_at": event.get("occurred_at", ""),
            }
        )
    return picks


def _compact_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact = []
    for event in events[-20:]:
        compact.append(
            {
                "market": event.get("market", ""),
                "ticker": event.get("ticker", ""),
                "decision_id": event.get("decision_id", ""),
                "execution_id": event.get("execution_id", ""),
                "reason_code": event.get("reason_code", ""),
                "occurred_at": event.get("occurred_at", ""),
            }
        )
    return compact


def _latest_daily_review(runtime_mode: str | None) -> dict[str, Any]:
    review_dir = get_runtime_path("logs", "daily_review")
    if not review_dir.exists():
        return {}
    prefix = f"{runtime_mode}_" if runtime_mode else ""
    files = sorted(review_dir.glob(f"{prefix}*_summary.json"))
    if not files and runtime_mode:
        files = sorted(review_dir.glob("*_summary.json"))
    if not files:
        return {}
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception:
        return {"path": str(files[-1]), "load_error": True}


def _performance_from_review(review: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(review, dict):
        return {}
    return {
        "net_pnl": review.get("net_pnl", review.get("today_net_pnl", {})),
        "net_pnl_after_claude": (review.get("performance") or {}).get("net_pnl_after_claude", {}),
        "selection_alpha": (review.get("performance") or {}).get("selection_alpha", {}),
        "entry_delay": (review.get("performance") or {}).get("entry_delay", {}),
        "exit_efficiency": (review.get("performance") or {}).get("exit_efficiency", {}),
        "mdd": {
            "since_inception_mdd": review.get("since_inception_mdd"),
            "rolling_20d_mdd": review.get("rolling_20d_mdd"),
            "rolling_60d_mdd": review.get("rolling_60d_mdd"),
        },
    }
