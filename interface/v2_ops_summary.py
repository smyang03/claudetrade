from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json
import os

from interface.bucket_summary import build_bucket_summary
from bot.session_date import KST
from config.v2 import DEFAULT_V2_CONFIG
from learning.approval_queue import BrainApprovalQueue
from lifecycle.event_store import EventStore
from runtime.broker_truth_snapshot import age_seconds, load_broker_truth_snapshot
from runtime.market_resolver import resolve_position_market
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
    "/buy_capacity",
    "/capacity",
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

PATHB_ACTIVE_STATUSES: set[str] = {
    "WAITING",
    "HIT",
    "ORDER_SENT",
    "ORDER_ACKED",
    "PARTIAL_FILLED",
    "FILLED",
    "SELL_SENT",
    "SELL_ACKED",
    "SELL_PARTIAL_FILLED",
}

PATHB_ENTRY_WAITING_STATUSES: set[str] = {"WAITING", "HIT"}

PATHB_NO_PLAN_MESSAGES: dict[str, str] = {
    "NO_TRADE_READY": "Claude selection is all watch_only; no Path B live entry candidate is ready.",
    "PRICE_TARGETS_EMPTY": "trade_ready exists but price_targets are empty, so Path B cannot register plans.",
    "MISSING_PRICE_TARGETS": "some trade_ready tickers are missing price_targets.",
    "PATHB_OPERATOR_DISABLED": "Path B operator control is disabled.",
    "PATHB_CONFIG_DISABLED": "Path B config is disabled.",
    "PATHB_EMERGENCY_DISABLED": "Path B emergency disable is active.",
    "NO_SELECTION_FILE": "selection/judgment snapshot is missing for this market session.",
    "NO_WATCHLIST": "selection snapshot has no watchlist tickers.",
    "ALL_TRADE_READY_FILTERED": "raw trade_ready tickers were filtered before runtime application.",
    "NO_PATH_RUN_REGISTERED": "trade_ready and price_targets exist but no Path B run has been registered yet.",
    "MARKET_NOT_SELECTED": "select KR or US to inspect Path B selection state.",
}

PATHB_NO_PLAN_ACTION_REQUIRED: set[str] = {
    "PRICE_TARGETS_EMPTY",
    "MISSING_PRICE_TARGETS",
    "PATHB_OPERATOR_DISABLED",
    "PATHB_CONFIG_DISABLED",
    "PATHB_EMERGENCY_DISABLED",
    "NO_SELECTION_FILE",
    "NO_WATCHLIST",
    "ALL_TRADE_READY_FILTERED",
    "NO_PATH_RUN_REGISTERED",
    "CANDIDATE_ACTIONS_MISSING_CONTRACT",
    "SELECTION_TRUNCATED",
    "SELECTION_PARSE_FAILED",
}


def build_v2_ops_summary(
    *,
    bot: Any | None = None,
    store: EventStore | None = None,
    market: str | None = None,
    runtime_mode: str | None = None,
    session_date: str | None = None,
) -> dict[str, Any]:
    store = store or _default_read_event_store()
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
    live_truth_verdict = _broker_truth_verdict(broker_truth)
    entry_timing = build_entry_timing_summary(
        market=market_key,
        runtime_mode=runtime_key,
        session_date=session_key,
    )
    path_b_live = _path_b_live_summary(
        store,
        market_key,
        runtime_key,
        session_key,
        events=events,
        broker_truth=broker_truth,
        live_truth_verdict=live_truth_verdict,
        bot=bot,
    )

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
            "order_unknown_event_history_count": len(order_unknown),
            "current_order_unknown_count": len(path_b_live.get("order_unknown") or []),
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
        "live_truth_verdict": live_truth_verdict,
        "claude_picks": _claude_picks(events),
        "entry_timing": entry_timing,
        "bucket_monitor": build_bucket_summary(market=market_key, session_date=session_key, runtime_mode=runtime_key),
        "buy_readiness": path_b_live.get("buy_readiness", {}),
        "path_b_live": path_b_live,
        "lifecycle": {
            "event_counts": dict(counts),
            "last_event": last_event,
            "order_unknown": _compact_events(order_unknown),
            "order_unknown_event_history": _compact_events(order_unknown),
            "order_unknown_event_history_count": len(order_unknown),
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


def _default_read_event_store() -> EventStore:
    store = EventStore(read_only=True, initialize=False)
    try:
        with store.connect():
            pass
        return store
    except Exception:
        return EventStore()


def _session_date_from_bot(bot: Any | None) -> str:
    if bot is not None:
        current = getattr(bot, "current_session_date", "") or getattr(bot, "session_date", "")
        if current:
            return str(current)
    return datetime.now().date().isoformat()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds").replace("+00:00", "Z")


def _broker_truth_stale_reason(
    *,
    missing: bool,
    stale: bool,
    error: str,
    last_success_at: str,
    age_sec: float | None,
    ttl_sec: int,
) -> str:
    if missing:
        return "missing"
    if error:
        return "error"
    if not last_success_at:
        return "last_success_missing"
    if age_sec is None:
        return "last_success_unparseable"
    if stale or age_sec > ttl_sec:
        return "age_gt_ttl"
    return ""


def _broker_truth_freshness_fields(data: dict[str, Any], *, evaluated_at: datetime) -> dict[str, Any]:
    ttl_sec = int(_safe_int(data.get("ttl_sec")) or 60)
    last_success_at = str(data.get("last_success_at", "") or "")
    age = age_seconds(last_success_at, now=evaluated_at)
    age_value = round(age, 3) if age is not None else None
    ttl_margin = round(float(ttl_sec) - age, 3) if age is not None else None
    missing = bool(data.get("missing", True))
    stale = bool(data.get("stale", True))
    error = str(data.get("error", "") or "")
    return {
        "evaluated_at": _utc_iso(evaluated_at),
        "age_sec": age_value,
        "ttl_sec": ttl_sec,
        "ttl_margin_sec": ttl_margin,
        "stale_reason": _broker_truth_stale_reason(
            missing=missing,
            stale=stale,
            error=error,
            last_success_at=last_success_at,
            age_sec=age,
            ttl_sec=ttl_sec,
        ),
    }


def _broker_truth_summary(runtime_mode: str | None) -> dict[str, Any]:
    mode = str(runtime_mode or "live").lower()
    evaluated_at = _utc_now()
    try:
        snapshot = load_broker_truth_snapshot(mode)
    except Exception as exc:
        error_payload = {
            "missing": True,
            "stale": True,
            "error": str(exc),
            "positions": [],
            "open_orders": [],
            "today_fills": [],
            "evaluated_at": _utc_iso(evaluated_at),
            "age_sec": None,
            "ttl_sec": 60,
            "ttl_margin_sec": None,
            "stale_reason": "error",
        }
        return {
            "runtime_mode": mode,
            "missing": True,
            "broken": True,
            "error": str(exc),
            "evaluated_at": _utc_iso(evaluated_at),
            "markets": {"KR": dict(error_payload), "US": dict(error_payload)},
        }
    markets = snapshot.get("markets") if isinstance(snapshot.get("markets"), dict) else {}
    summarized_markets: dict[str, dict[str, Any]] = {}
    for market in ("KR", "US"):
        item = markets.get(market) if isinstance(markets.get(market), dict) else {}
        summarized_markets[market] = {
            "missing": bool(item.get("missing", True)),
            "stale": bool(item.get("stale", True)),
            "last_success_at": item.get("last_success_at", ""),
            "last_attempt_at": item.get("last_attempt_at", ""),
            "error": item.get("error", ""),
            "account_summary": item.get("account_summary", {}),
            "positions": item.get("positions", []),
            "open_orders": item.get("open_orders", []),
            "today_fills": item.get("today_fills", []),
            **_broker_truth_freshness_fields(item, evaluated_at=evaluated_at),
        }
    return {
        "runtime_mode": snapshot.get("runtime_mode", mode),
        "generated_at": snapshot.get("generated_at", ""),
        "evaluated_at": _utc_iso(evaluated_at),
        "schema_version": snapshot.get("schema_version", 1),
        "broken": bool(snapshot.get("broken", False)),
        "error": str(snapshot.get("error", "") or ""),
        "markets": summarized_markets,
    }


def _broker_truth_verdict(broker_truth: dict[str, Any] | None) -> dict[str, Any]:
    markets = broker_truth.get("markets") if isinstance(broker_truth, dict) and isinstance(broker_truth.get("markets"), dict) else {}
    verdict: dict[str, Any] = {}
    for market in ("KR", "US"):
        data = markets.get(market) if isinstance(markets.get(market), dict) else {}
        missing = bool(data.get("missing", True))
        stale = bool(data.get("stale", True))
        error = str(data.get("error", "") or "")
        positions = data.get("positions") if isinstance(data.get("positions"), list) else []
        open_orders = data.get("open_orders") if isinstance(data.get("open_orders"), list) else []
        fills = data.get("today_fills") if isinstance(data.get("today_fills"), list) else []
        fresh = not missing and not stale and not error
        trusted = fresh
        stale_reason = str(data.get("stale_reason", "") or "")
        if fresh:
            message = f"broker truth: positions={len(positions)}, open_orders={len(open_orders)}"
        else:
            reason_suffix = f" ({stale_reason})" if stale_reason else ""
            message = f"broker truth needs refresh/reconcile before treating local state as current{reason_suffix}"
        verdict[market] = {
            "trusted": trusted,
            "fresh": fresh,
            "missing": missing,
            "stale": stale,
            "error": error,
            "stale_reason": stale_reason,
            "evaluated_at": data.get("evaluated_at", ""),
            "age_sec": data.get("age_sec"),
            "ttl_sec": data.get("ttl_sec", 60),
            "ttl_margin_sec": data.get("ttl_margin_sec"),
            "positions": len(positions),
            "open_orders": len(open_orders),
            "today_fills": len(fills),
            "last_success_at": data.get("last_success_at", ""),
            "last_attempt_at": data.get("last_attempt_at", ""),
            "operator_message": message,
        }
    return verdict


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
        pos_market = resolve_position_market(pos, unknown="KR")
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
        market = resolve_position_market(pos, unknown="KR")
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
    live_truth_verdict: dict[str, Any] | None = None,
    bot: Any | None = None,
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
    ops_context = _path_b_ops_context(pathb_runs, events or [])
    pathb_runs = [_merge_path_b_ops_context(run, ops_context["by_path_run_id"]) for run in pathb_runs]
    name_map = _path_b_name_map(markets, runtime_mode, session_date)
    status_counts = Counter(str(run.get("status", "") or "UNKNOWN") for run in pathb_runs)
    active = [run for run in pathb_runs if str(run.get("status", "")).upper() in PATHB_ACTIVE_STATUSES]
    unknown = [run for run in pathb_runs if str(run.get("status", "")) == "ORDER_UNKNOWN"]
    metrics = _path_b_metrics(pathb_runs)
    comparison = _path_performance_comparison(events or [], pathb_runs)
    consistency_health = _path_b_consistency_health(pathb_runs, events or [], broker_truth)
    config = _path_b_config(runtime_mode)
    control = _path_b_control_state(runtime_mode)
    run_counts = _path_b_run_counts(store, markets, modes, session_date)
    consensus_by_market = _path_b_consensus_by_market(markets, runtime_mode, session_date, bot=bot)
    equity_context_by_market = _path_b_equity_context_by_market(
        markets,
        broker_truth or {},
        config,
        bot=bot,
    )
    capacity = _path_b_execution_capacity(
        broker_truth or {},
        config,
        pathb_runs,
        markets=markets,
        session_date=session_date,
        consensus_by_market=consensus_by_market,
        equity_context_by_market=equity_context_by_market,
    )
    selection = _path_b_selection_snapshot(
        market=market,
        runtime_mode=runtime_mode,
        session_date=session_date,
        pathb_runs=pathb_runs,
        config=config,
        control=control,
        name_map=name_map,
        broker_truth=broker_truth or {},
    )
    readiness = _path_b_execution_readiness(
        market=market,
        session_date=session_date,
        selection=selection,
        config=config,
        control=control,
        broker_truth=broker_truth or {},
        live_truth_verdict=live_truth_verdict or _broker_truth_verdict(broker_truth or {}),
        execution_capacity=capacity,
        pathb_runs=pathb_runs,
        runtime_mode=runtime_mode,
    )
    selection_by_market: dict[str, dict[str, Any]] = {}
    readiness_by_market: dict[str, dict[str, Any]] = {}
    for market_item in markets:
        market_key_item = str(market_item or "").upper()
        if not market_key_item:
            continue
        if market and market_key_item == str(market or "").upper():
            market_selection = selection
            market_readiness = readiness
        else:
            market_selection = _path_b_selection_snapshot(
                market=market_key_item,
                runtime_mode=runtime_mode,
                session_date=session_date,
                pathb_runs=pathb_runs,
                config=config,
                control=control,
                name_map=name_map,
                broker_truth=broker_truth or {},
            )
            market_readiness = _path_b_execution_readiness(
                market=market_key_item,
                session_date=session_date,
                selection=market_selection,
                config=config,
                control=control,
                broker_truth=broker_truth or {},
                live_truth_verdict=live_truth_verdict or _broker_truth_verdict(broker_truth or {}),
                execution_capacity=capacity,
                pathb_runs=pathb_runs,
                runtime_mode=runtime_mode,
            )
        selection_by_market[market_key_item] = market_selection
        readiness_by_market[market_key_item] = market_readiness
    buy_readiness = _path_buy_readiness_summary(
        markets=markets,
        selections_by_market=selection_by_market,
        readiness_by_market=readiness_by_market,
        execution_capacity=capacity,
    )
    return {
        "config": config,
        "control": control,
        "live_truth_verdict": live_truth_verdict or _broker_truth_verdict(broker_truth or {}),
        "execution_capacity": capacity,
        "run_counts": run_counts,
        "readiness": readiness,
        "readiness_by_market": readiness_by_market,
        "buy_readiness": buy_readiness,
        "runs": len(pathb_runs),
        "metrics": metrics,
        "ops_summary": ops_context["summary"],
        "path_comparison": comparison,
        "consistency_health": consistency_health,
        "charts": _path_b_charts(pathb_runs, metrics),
        "selection": selection,
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
    broker_truth: dict[str, Any] | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    if not market_key:
        no_plan = _pathb_no_plan_summary(["MARKET_NOT_SELECTED"])
        return {
            "market": "ALL",
            "counts": {},
            "watch_rows": [],
            "no_plan_reasons": ["MARKET_NOT_SELECTED"],
            "no_plan_primary_reason": no_plan["primary_reason"],
            "no_plan_messages": no_plan["messages"],
            "no_plan_summary": no_plan["summary"],
            "no_plan_action_required": no_plan["action_required"],
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
    candidate_actions = meta.get("candidate_actions") if isinstance(meta.get("candidate_actions"), list) else []
    candidate_action_routes = meta.get("_candidate_action_routes") if isinstance(meta.get("_candidate_action_routes"), list) else []
    route_by_ticker: dict[str, dict[str, Any]] = {}
    for route_row in candidate_action_routes:
        if not isinstance(route_row, dict):
            continue
        raw_ticker = str(route_row.get("ticker") or "").strip()
        if not raw_ticker:
            continue
        key = raw_ticker.upper() if market_key == "US" else raw_ticker
        route_by_ticker[key] = route_row
    price_targets = {
        **_candidate_action_price_targets(candidate_actions, market_key),
        **(meta.get("price_targets") if isinstance(meta.get("price_targets"), dict) else {}),
        **(meta.get("_pathb_price_targets") if isinstance(meta.get("_pathb_price_targets"), dict) else {}),
    }
    reasons = meta.get("reasons") if isinstance(meta.get("reasons"), dict) else {}
    recommended = meta.get("recommended_strategy") if isinstance(meta.get("recommended_strategy"), dict) else {}
    adaptive = meta.get("_adaptive_live_condition") if isinstance(meta.get("_adaptive_live_condition"), dict) else {}
    adaptive_decisions = adaptive.get("decisions") if isinstance(adaptive.get("decisions"), dict) else {}
    live_evidence = meta.get("_live_evidence") if isinstance(meta.get("_live_evidence"), dict) else {}
    live_evidence_packs = live_evidence.get("packs") if isinstance(live_evidence.get("packs"), dict) else {}
    compact_validation = meta.get("_compact_validation") if isinstance(meta.get("_compact_validation"), dict) else {}
    missing_strategy_count = sum(1 for ticker in watchlist if not _selection_lookup(recommended, ticker, market_key))

    run_by_ticker = {str(run.get("ticker", "") or "").upper() if market_key == "US" else str(run.get("ticker", "") or ""): run for run in pathb_runs}
    held_by_ticker = _held_positions_by_ticker_from_broker_truth(broker_truth, market_key)
    held_tickers = set(held_by_ticker)
    applied_not_held = [ticker for ticker in applied_trade_ready if _ticker_key(market_key, ticker) not in held_tickers]
    missing_applied = [
        ticker for ticker in applied_trade_ready
        if _ticker_key(market_key, ticker) not in held_tickers and not _selection_lookup(price_targets, ticker, market_key)
    ]
    missing_raw = [
        ticker for ticker in raw_trade_ready
        if _ticker_key(market_key, ticker) not in held_tickers and not _selection_lookup(price_targets, ticker, market_key)
    ]
    filtered_tickers = _unique_list(runtime_filtered.keys())
    plan_a_routed = [
        ticker
        for ticker in applied_trade_ready
        if str((route_by_ticker.get(ticker.upper() if market_key == "US" else ticker) or {}).get("route") or "").startswith("PlanA.")
    ]
    pathb_expected_trade_ready = [
        ticker for ticker in applied_trade_ready
        if ticker not in set(plan_a_routed) and _ticker_key(market_key, ticker) not in held_tickers
    ]
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
    elif applied_not_held and not price_targets:
        no_plan_reasons.append("PRICE_TARGETS_EMPTY")
    elif missing_applied:
        no_plan_reasons.append("MISSING_PRICE_TARGETS")
    elif pathb_expected_trade_ready and not pathb_runs:
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
        ticker_key = _ticker_key(market_key, ticker)
        name = _name_for_ticker(ticker, market_key, name_map)
        target = _selection_lookup(price_targets, ticker, market_key) or {}
        run = run_by_ticker.get(ticker_key)
        held = held_by_ticker.get(ticker_key) or {}
        if not name and held:
            name = str(held.get("name", "") or "")
        route_info = route_by_ticker.get(ticker_key) or {}
        execution_route = str(route_info.get("route") or "")
        route_is_plan_a = execution_route.startswith("PlanA.")
        route_is_pathb = execution_route.startswith("PathB.")
        buy_path = "path_a" if route_is_plan_a else "path_b" if (run or route_is_pathb) else "manual_or_broker" if held else ""
        filtered_reason = _selection_lookup(runtime_filtered, ticker, market_key)
        adaptive_decision = _selection_lookup(adaptive_decisions, ticker, market_key) or {}
        evidence_pack = _selection_lookup(live_evidence_packs, ticker, market_key) or {}
        evidence_trace = evidence_pack.get("decision_trace") if isinstance(evidence_pack.get("decision_trace"), dict) else {}
        if run:
            state = str(run.get("status") or "REGISTERED")
        elif held:
            state = "LIVE_POSITION_HELD_STALE" if bool(held.get("broker_truth_stale")) else "LIVE_POSITION_HELD"
        elif route_is_plan_a and ticker in applied_trade_ready:
            state = "PLAN_A_ROUTED"
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
                "buy_path": buy_path,
                "buy_path_label": _buy_path_label(buy_path),
                "execution_route": execution_route,
                "route_final_action": str(route_info.get("final_action") or ""),
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
                "broker_position_qty": held.get("qty", "") if held else "",
                "broker_position_source": held.get("source", "") if held else "",
                "broker_truth_stale": bool(held.get("broker_truth_stale")) if held else False,
                "broker_truth_last_success_at": held.get("broker_truth_last_success_at", "") if held else "",
            }
        )

    no_plan = _pathb_no_plan_summary(_unique_list(no_plan_reasons))
    return {
        "market": market_key,
        "judgment_file": rec.get("_source_file", ""),
        "judgment_snapshot": _judgment_snapshot(market_key, session_date, rec),
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
            "held_positions": len(held_by_ticker),
            "held_trade_ready": sum(1 for ticker in applied_trade_ready if _ticker_key(market_key, ticker) in held_tickers),
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
        "no_plan_primary_reason": no_plan["primary_reason"],
        "no_plan_messages": no_plan["messages"],
        "no_plan_summary": no_plan["summary"],
        "no_plan_action_required": no_plan["action_required"],
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


def _pathb_no_plan_summary(reasons: list[str]) -> dict[str, Any]:
    unique = _unique_list(reasons)
    primary = unique[0] if unique else ""
    messages = [PATHB_NO_PLAN_MESSAGES.get(reason, reason) for reason in unique]
    return {
        "primary_reason": primary,
        "messages": messages,
        "summary": messages[0] if messages else "",
        "action_required": any(reason in PATHB_NO_PLAN_ACTION_REQUIRED for reason in unique),
    }


def _judgment_snapshot(market: str, session_date: str, rec: dict[str, Any]) -> dict[str, Any]:
    source_file = str(rec.get("_source_file", "") or "")
    consensus = rec.get("consensus") if isinstance(rec.get("consensus"), dict) else {}
    meta = rec.get("selection_meta") if isinstance(rec.get("selection_meta"), dict) else {}
    file_session = str(rec.get("date") or rec.get("session_date") or session_date or "")[:10]
    generated_at = (
        str(rec.get("generated_at") or rec.get("created_at") or rec.get("updated_at") or "")
        or str(meta.get("generated_at") or meta.get("created_at") or meta.get("updated_at") or "")
    )
    if not generated_at and source_file:
        try:
            generated_at = datetime.fromtimestamp(Path(source_file).stat().st_mtime, tz=KST).isoformat(timespec="seconds")
        except Exception:
            generated_at = ""
    market_mode = str(consensus.get("mode") or rec.get("market_mode") or "")
    new_buy_permission = str(consensus.get("new_buy_permission") or rec.get("new_buy_permission") or "")
    session_match = bool(file_session == str(session_date or "")[:10])
    stale_reason = "" if session_match else "session_date_mismatch"
    return {
        "market": str(market or "").upper(),
        "session_date": str(session_date or "")[:10],
        "source_file": source_file,
        "generated_at": generated_at,
        "market_mode": market_mode,
        "new_buy_permission": new_buy_permission,
        "selection_snapshot_fresh": bool(rec) and session_match,
        "session_match": session_match,
        "stale_reason": stale_reason,
    }


def _candidate_action_price_targets(candidate_actions: list[Any], market: str) -> dict[str, dict[str, Any]]:
    targets: dict[str, dict[str, Any]] = {}
    market_key = str(market or "").upper()
    for action in candidate_actions or []:
        if not isinstance(action, dict):
            continue
        action_market = str(action.get("market", "") or "").upper()
        if action_market and market_key and action_market != market_key:
            continue
        ticker = str(action.get("ticker", "") or "").strip()
        if not ticker:
            continue
        key = ticker.upper() if market_key == "US" else ticker
        target = action.get("price_targets") if isinstance(action.get("price_targets"), dict) else {}
        if target:
            targets[key] = dict(target)
    return targets


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


def _held_positions_by_ticker_from_broker_truth(
    broker_truth: dict[str, Any] | None,
    market: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(broker_truth, dict):
        return {}
    markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    market_key = str(market or "").upper()
    data = markets.get(market_key) if isinstance(markets.get(market_key), dict) else {}
    if not data or bool(data.get("missing")) or str(data.get("error", "") or ""):
        return {}
    out: dict[str, dict[str, Any]] = {}
    stale = bool(data.get("stale"))
    for row in list(data.get("positions") or []):
        if not isinstance(row, dict):
            continue
        ticker = _ticker_key(market_key, row.get("ticker", ""))
        if not ticker:
            continue
        try:
            qty = int(float(row.get("qty", 0) or 0))
        except Exception:
            qty = 0
        if qty <= 0:
            continue
        out[ticker] = {
            "ticker": ticker,
            "name": str(row.get("name", "") or ""),
            "qty": qty,
            "source": "broker_truth",
            "broker_truth_stale": stale,
            "broker_truth_last_success_at": data.get("last_success_at", ""),
        }
    return out


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


def _path_b_ops_context(runs: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    by_path, by_decision = _path_b_event_indexes(events)
    by_run: dict[str, dict[str, Any]] = {}
    reason_counts: Counter[str] = Counter()
    expired_reason_counts: Counter[str] = Counter()
    order_unknown_reason_counts: Counter[str] = Counter()
    unknown_reason_count = 0
    runs_with_lifecycle_reason = 0
    runs_with_lifecycle_events = 0

    for run in runs:
        path_run_id = str(run.get("path_run_id", "") or "").strip()
        related = _path_b_related_events(run, by_path, by_decision)
        if related:
            runs_with_lifecycle_events += 1
        ops = _derive_path_b_ops(run, related)
        if ops.get("reason_codes"):
            runs_with_lifecycle_reason += 1
        reason = str(ops.get("ops_reason") or "")
        if reason:
            reason_counts[reason] += 1
            status = str(run.get("status") or "").upper()
            if status == "EXPIRED":
                expired_reason_counts[reason] += 1
            if status == "ORDER_UNKNOWN":
                order_unknown_reason_counts[reason] += 1
        else:
            unknown_reason_count += 1
        if path_run_id:
            by_run[path_run_id] = ops

    return {
        "by_path_run_id": by_run,
        "summary": {
            "checked_runs": len(runs),
            "runs_with_lifecycle_events": runs_with_lifecycle_events,
            "runs_with_lifecycle_reason": runs_with_lifecycle_reason,
            "unknown_reason_count": unknown_reason_count,
            "reason_counts": dict(reason_counts),
            "expired_reason_counts": dict(expired_reason_counts),
            "order_unknown_reason_counts": dict(order_unknown_reason_counts),
            "join_contract": "path_run_id_then_decision_market_ticker",
        },
    }


def _path_b_event_indexes(events: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[tuple[str, str, str], list[dict[str, Any]]]]:
    by_path: dict[str, list[dict[str, Any]]] = {}
    by_decision: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            by_path.setdefault(path_run_id, []).append(event)
        decision_id = str(event.get("decision_id") or "").strip()
        market = str(event.get("market") or "").upper()
        ticker = _ticker_key(market, event.get("ticker", ""))
        if decision_id and market and ticker:
            by_decision.setdefault((decision_id, market, ticker), []).append(event)
    return by_path, by_decision


def _path_b_related_events(
    run: dict[str, Any],
    by_path: dict[str, list[dict[str, Any]]],
    by_decision: dict[tuple[str, str, str], list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    path_run_id = str(run.get("path_run_id") or "").strip()
    decision_id = str(run.get("decision_id") or "").strip()
    market = str(run.get("market") or "").upper()
    ticker = _ticker_key(market, run.get("ticker", ""))
    raw_events = list(by_path.get(path_run_id, []))
    raw_events.extend(by_decision.get((decision_id, market, ticker), []))
    seen: set[str] = set()
    related: list[dict[str, Any]] = []
    for event in raw_events:
        key = str(event.get("event_id") or "")
        if not key:
            key = "|".join(
                [
                    str(event.get("event_type") or ""),
                    str(event.get("occurred_at") or ""),
                    str(event.get("reason_code") or ""),
                    str(event.get("execution_id") or ""),
                ]
            )
        if key in seen:
            continue
        seen.add(key)
        related.append(event)
    return sorted(related, key=lambda item: int(item.get("event_id") or 0))


def _derive_path_b_ops(run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    plan = run.get("plan") or run.get("plan_json") or {}
    if not isinstance(plan, dict):
        plan = {}
    status = str(run.get("status") or "").upper()
    reason_items = [_event_reason_item(event) for event in events]
    reason_items = [item for item in reason_items if item.get("reason")]
    reason_codes = _unique_list([str(item.get("reason") or "") for item in reason_items])
    latest_gate = _latest_event_reason(
        events,
        {"CLAUDE_PRICE_PLAN_GATE_WARNING", "SAFETY_BLOCKED", "ORDER_UNKNOWN", "CLAUDE_PRICE_INVALID"},
    )
    expired_event = _latest_event_reason(events, {"CLAUDE_PRICE_EXPIRED"})
    sent_at = str(plan.get("entry_order_sent_at") or _first_event_at(events, {"ORDER_SENT"}) or "")
    acked_at = str(plan.get("entry_order_acked_at") or _first_event_at(events, {"ORDER_ACKED"}) or "")
    filled_at = str(plan.get("filled_at") or plan.get("entry_filled_at") or _first_entry_fill_at(events) or "")
    closed_at = str(plan.get("closed_at") or _last_event_at(events, {"CLOSED"}) or "")
    expired_at = str(_last_event_at(events, {"CLAUDE_PRICE_EXPIRED"}) or "")

    plan_reason, plan_reason_key = _first_plan_reason(
        plan,
        (
            "expired_reason",
            "cancel_reason",
            "block_reason",
            "order_unknown_resolution",
            "order_unknown_detail",
            "pending_buy_ttl_deferred_reason",
            "last_error",
            "close_reason",
        ),
    )
    ops_reason = plan_reason
    reason_source = f"plan_json.{plan_reason_key}" if plan_reason else ""
    if status == "EXPIRED":
        candidate = expired_event.get("reason") or latest_gate.get("reason") or plan_reason
        if candidate:
            ops_reason = str(candidate)
            reason_source = str(expired_event.get("source") or latest_gate.get("source") or reason_source or "derived")
    elif status == "ORDER_UNKNOWN":
        candidate = plan_reason or latest_gate.get("reason")
        if candidate:
            ops_reason = str(candidate)
            reason_source = reason_source or str(latest_gate.get("source") or "derived")
    elif not ops_reason and latest_gate.get("reason"):
        ops_reason = str(latest_gate.get("reason"))
        reason_source = str(latest_gate.get("source") or "lifecycle_reason_code")

    return {
        "ops_reason": ops_reason,
        "reason_source": reason_source,
        "reason_codes": reason_codes,
        "latest_gate_reason": latest_gate.get("reason") or "",
        "latest_gate_reason_source": latest_gate.get("source") or "",
        "expired_reason": expired_event.get("reason") or (ops_reason if status == "EXPIRED" else ""),
        "expired_reason_source": expired_event.get("source") or (reason_source if status == "EXPIRED" else ""),
        "event_types": _unique_list([str(event.get("event_type") or "") for event in events]),
        "event_count": len(events),
        "sent_at": sent_at,
        "acked_at": acked_at,
        "filled_at": filled_at,
        "closed_at": closed_at,
        "expired_at": expired_at,
        "sent_to_ack_latency_sec": _seconds_between(sent_at, acked_at),
        "sent_to_fill_latency_sec": _seconds_between(sent_at, filled_at),
        "sent_to_expired_latency_sec": _seconds_between(sent_at, expired_at),
        "order_unknown_reconcile_attempts": _safe_int(plan.get("order_unknown_reconcile_attempts")),
        "order_unknown_phase": str(plan.get("order_unknown_phase") or ""),
        "order_unknown_age_sec": _safe_int(plan.get("order_unknown_age_sec")),
        "order_unknown_soft_timeout_sec": _safe_int(plan.get("order_unknown_soft_timeout_sec")),
        "order_unknown_hard_timeout_sec": _safe_int(plan.get("order_unknown_hard_timeout_sec")),
        "entry_slippage_bps": _bps(plan.get("entry_order_price"), plan.get("actual_entry_price")),
        "exit_slippage_bps": _bps(plan.get("exit_order_price"), plan.get("actual_exit_price")),
    }


def _merge_path_b_ops_context(run: dict[str, Any], by_run: dict[str, dict[str, Any]]) -> dict[str, Any]:
    path_run_id = str(run.get("path_run_id") or "").strip()
    ops = by_run.get(path_run_id) if path_run_id else None
    if not ops:
        return run
    out = dict(run)
    out["ops"] = dict(ops)
    return out


def _event_reason_item(event: dict[str, Any]) -> dict[str, str]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    reason = (
        str(event.get("reason_code") or "").strip()
        or str(payload.get("reason_code") or "").strip()
        or str(payload.get("reason") or "").strip()
        or str(payload.get("block_reason") or "").strip()
        or str(payload.get("cancel_reason") or "").strip()
        or str(payload.get("order_unknown_detail") or "").strip()
        or str(payload.get("last_error") or "").strip()
    )
    source = "lifecycle_reason_code" if str(event.get("reason_code") or "").strip() else "lifecycle_payload"
    return {
        "event_type": str(event.get("event_type") or ""),
        "occurred_at": str(event.get("occurred_at") or ""),
        "reason": reason,
        "source": source if reason else "",
    }


def _latest_event_reason(events: list[dict[str, Any]], event_types: set[str]) -> dict[str, str]:
    latest: dict[str, str] = {}
    for event in events:
        if str(event.get("event_type") or "") not in event_types:
            continue
        item = _event_reason_item(event)
        if item.get("reason"):
            latest = item
    return latest


def _first_plan_reason(plan: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str]:
    for key in keys:
        value = str(plan.get(key) or "").strip()
        if value:
            return value, key
    return "", ""


def _first_event_at(events: list[dict[str, Any]], event_types: set[str]) -> str:
    for event in events:
        if str(event.get("event_type") or "") in event_types:
            return str(event.get("occurred_at") or "")
    return ""


def _last_event_at(events: list[dict[str, Any]], event_types: set[str]) -> str:
    value = ""
    for event in events:
        if str(event.get("event_type") or "") in event_types:
            value = str(event.get("occurred_at") or "")
    return value


def _first_entry_fill_at(events: list[dict[str, Any]]) -> str:
    for event in events:
        if str(event.get("event_type") or "") not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("side") or "").lower() == "sell":
            continue
        return str(event.get("occurred_at") or "")
    return ""


def _seconds_between(start: Any, end: Any) -> int | None:
    left = _parse_dt(start)
    right = _parse_dt(end)
    if left is None or right is None:
        return None
    return max(0, int((right - left).total_seconds()))


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _bps(reference: Any, actual: Any) -> float | None:
    ref = _to_float(reference)
    val = _to_float(actual)
    if ref is None or val is None or ref <= 0:
        return None
    return round((val / ref - 1.0) * 10000.0, 4)


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
    broker_position_qty = sum(float(_to_float(row.get("qty")) or 0.0) for row in positions)
    return {
        "broker_position_evidence": bool(positions),
        "broker_open_order_evidence": bool(open_orders),
        "broker_today_fill_evidence": bool(fills),
        "broker_truth_last_success_at": data.get("last_success_at", ""),
        "broker_truth_stale": bool(data.get("stale")),
        "broker_truth_error": str(data.get("error", "") or ""),
        "broker_position_count": len(positions),
        "broker_position_qty": broker_position_qty,
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


def _env_bool_default(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_config_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _pathb_market_live_gate_detail(env: dict[str, str], market: str, runtime_mode: str | None) -> dict[str, Any]:
    market_key = str(market or "").upper()
    primary = f"PATHB_{market_key}_LIVE_ENABLED"
    legacy = f"{market_key}_CLAUDE_PRICE_LIVE_ENABLED"
    legacy_present = legacy in env
    if str(runtime_mode or "").lower() == "paper":
        return {
            "effective": True,
            "source_key": "paper_runtime",
            "source_value": "",
            "legacy_key": legacy,
            "legacy_value": str(env.get(legacy, "")),
            "legacy_shadowed": False,
        }
    if primary in env:
        return {
            "effective": _env_bool_default(env, primary, True),
            "source_key": primary,
            "source_value": str(env.get(primary, "")),
            "legacy_key": legacy,
            "legacy_value": str(env.get(legacy, "")),
            "legacy_shadowed": legacy_present,
        }
    return {
        "effective": _env_bool_default(env, legacy, True),
        "source_key": legacy,
        "source_value": str(env.get(legacy, "")),
        "legacy_key": legacy,
        "legacy_value": str(env.get(legacy, "")),
        "legacy_shadowed": False,
    }


def _gross_cap_mode_from_env(env: dict[str, str], market: str) -> str:
    market_key = str(market or "").upper()
    raw = str(
        env.get(f"{market_key}_ANALYST_GROSS_EXPOSURE_CAP_MODE")
        or env.get("ANALYST_GROSS_EXPOSURE_CAP_MODE")
        or "auto"
    ).strip().lower()
    return "manual" if raw in {"manual", "fixed", "operator", "override"} else "auto"


def _gross_cap_pct_from_env(env: dict[str, str], market: str) -> float:
    market_key = str(market or "").upper()
    raw = env.get(f"{market_key}_ANALYST_GROSS_EXPOSURE_CAP_PCT")
    if raw is None or str(raw).strip() == "":
        raw = env.get("ANALYST_GROSS_EXPOSURE_CAP_PCT", "")
    value = _to_float(raw)
    return max(0.0, min(100.0, float(value or 0.0)))


def _resolve_gross_exposure_cap_policy(
    market: str,
    consensus: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    analyst_cap = max(0.0, min(100.0, float(_to_float(consensus.get("max_gross_exposure_pct")) or 0.0)))
    mode_by_market = config.get("analyst_gross_exposure_cap_mode_by_market")
    mode_by_market = mode_by_market if isinstance(mode_by_market, dict) else {}
    raw_mode = str(mode_by_market.get(market_key) or config.get("analyst_gross_exposure_cap_mode") or "auto")
    mode_key = raw_mode.strip().lower()
    mode = "manual" if mode_key in {"manual", "fixed", "operator", "override"} else "auto"
    manual_by_market = config.get("analyst_gross_exposure_cap_pct_by_market")
    manual_by_market = manual_by_market if isinstance(manual_by_market, dict) else {}
    manual_cap = max(
        0.0,
        min(
            100.0,
            float(_to_float(manual_by_market.get(market_key)) or _to_float(config.get("analyst_gross_exposure_cap_pct")) or 0.0),
        ),
    )
    config_error = ""
    if mode == "manual":
        if manual_cap > 0:
            effective_cap = manual_cap
            source = "manual_config"
        else:
            effective_cap = analyst_cap
            source = "analyst_consensus"
            config_error = "manual_cap_missing_or_invalid"
    else:
        effective_cap = analyst_cap
        source = "analyst_consensus"
        if mode_key and mode_key not in {"auto", "claude", "analyst", "consensus"}:
            config_error = f"unknown_mode:{raw_mode}"
    return {
        "max_gross_exposure_pct": effective_cap,
        "analyst_max_gross_exposure_pct": analyst_cap,
        "manual_max_gross_exposure_pct": manual_cap,
        "gross_cap_mode": mode,
        "gross_cap_requested_mode": raw_mode,
        "gross_cap_source": source,
        "gross_cap_config_error": config_error,
    }


def _path_b_config(runtime_mode: str | None = None) -> dict[str, Any]:
    env, source = _effective_runtime_env(runtime_mode)
    cfg = DEFAULT_V2_CONFIG
    usd_krw = _env_float(env, "USD_KRW_RATE", 1350.0)
    us_fixed_default = cfg.us_fixed_order_krw or int(float(cfg.us_fixed_order_usd) * usd_krw)
    us_min_default = cfg.us_min_order_krw or int(float(cfg.us_min_order_usd) * usd_krw)
    fixed_order_krw = _env_int(env, "PATHB_FIXED_ORDER_KRW", cfg.pathb_fixed_order_krw)
    market_live_gate_source = {
        market: _pathb_market_live_gate_detail(env, market, runtime_mode)
        for market in ("KR", "US")
    }
    return {
        "enabled": _env_bool(env, "PATHB_ENABLED", cfg.pathb_enabled),
        "mode": str(env.get("PATHB_MODE", cfg.pathb_mode) or cfg.pathb_mode),
        "fixed_order_krw": fixed_order_krw,
        "fixed_order_krw_by_market": {
            "KR": _env_int(env, "KR_FIXED_ORDER_KRW", fixed_order_krw),
            "US": _env_int(env, "US_FIXED_ORDER_KRW", us_fixed_default) or fixed_order_krw,
        },
        "min_order_krw_by_market": {
            "KR": _env_int(env, "KR_MIN_ORDER_KRW", cfg.kr_min_order_krw),
            "US": _env_int(env, "US_MIN_ORDER_KRW", us_min_default) or us_min_default,
        },
        "max_positions": _env_int(env, "PATHB_MAX_POSITIONS", cfg.pathb_max_positions),
        "max_daily_entries": _env_int(env, "PATHB_MAX_DAILY_ENTRIES", cfg.pathb_max_daily_entries),
        "daily_entry_cap_by_market": {
            "KR": _env_int(env, "KR_DAILY_ENTRY_CAP", _env_int(env, "PATHB_MAX_DAILY_ENTRIES", cfg.pathb_max_daily_entries)),
            "US": _env_int(env, "US_DAILY_ENTRY_CAP", _env_int(env, "PATHB_MAX_DAILY_ENTRIES", cfg.pathb_max_daily_entries)),
        },
        "analyst_gross_exposure_cap_mode": str(env.get("ANALYST_GROSS_EXPOSURE_CAP_MODE", "auto") or "auto"),
        "analyst_gross_exposure_cap_mode_by_market": {
            "KR": _gross_cap_mode_from_env(env, "KR"),
            "US": _gross_cap_mode_from_env(env, "US"),
        },
        "analyst_gross_exposure_cap_pct_by_market": {
            "KR": _gross_cap_pct_from_env(env, "KR"),
            "US": _gross_cap_pct_from_env(env, "US"),
        },
        "min_confidence": _env_float(env, "PATHB_MIN_CONFIDENCE", cfg.pathb_min_confidence),
        "intraday_only": _env_bool(env, "PATHB_INTRADAY_ONLY", cfg.pathb_intraday_only),
        "emergency_disable": _env_bool(env, "PATHB_EMERGENCY_DISABLE", cfg.pathb_emergency_disable),
        "usd_krw": usd_krw,
        "market_live_enabled": {market: bool(detail.get("effective")) for market, detail in market_live_gate_source.items()},
        "market_live_gate_source": market_live_gate_source,
        "source": source,
    }


def _is_shadow_pathb_run(run: dict[str, Any]) -> bool:
    status = str(run.get("status", "") or "").upper()
    plan = run.get("plan") if isinstance(run.get("plan"), dict) else {}
    return status.startswith("SHADOW") or bool(plan.get("shadow") or plan.get("shadow_only") or plan.get("dry_run"))


def _path_b_run_counts(store: EventStore, markets: list[str], modes: list[str], session_date: str) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "current_live": [],
        "current_shadow": [],
        "historical_live": [],
        "historical_shadow": [],
        "current_paper": [],
    }
    for market in markets:
        for mode in modes:
            try:
                rows = store.path_runs_for_session(market=market, runtime_mode=mode, path_type="claude_price")
            except Exception:
                rows = []
            for row in rows:
                current = str(row.get("session_date", "") or "") == str(session_date or "")
                shadow = _is_shadow_pathb_run(row)
                mode_key = str(row.get("runtime_mode", mode) or mode).lower()
                if mode_key == "paper" and current:
                    buckets["current_paper"].append(row)
                elif current and shadow:
                    buckets["current_shadow"].append(row)
                elif current:
                    buckets["current_live"].append(row)
                elif shadow:
                    buckets["historical_shadow"].append(row)
                else:
                    buckets["historical_live"].append(row)

    def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
        status_counts = Counter(str(row.get("status", "") or "UNKNOWN") for row in rows)
        return {"total": len(rows), "status_counts": dict(status_counts)}

    return {key: compact(value) for key, value in buckets.items()}


def _path_b_consensus_by_market(
    markets: list[str],
    runtime_mode: str | None,
    session_date: str,
    *,
    bot: Any | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    bot_judgment = getattr(bot, "today_judgment", None) if bot is not None else None
    bot_judgment = bot_judgment if isinstance(bot_judgment, dict) else {}
    bot_market = str(bot_judgment.get("market", "") or "").upper()
    bot_day = "".join(ch for ch in str(bot_judgment.get("date") or bot_judgment.get("session_date") or session_date or "") if ch.isdigit())[:8]
    requested_day = "".join(ch for ch in str(session_date or "") if ch.isdigit())[:8]
    for market in markets:
        market_key = str(market or "").upper()
        consensus: dict[str, Any] = {}
        if market_key and bot_market == market_key and (not requested_day or bot_day == requested_day):
            bot_consensus = bot_judgment.get("consensus")
            if isinstance(bot_consensus, dict):
                consensus = dict(bot_consensus)
        if not consensus:
            rec = _load_judgment_record(market_key, runtime_mode, session_date)
            rec_consensus = rec.get("consensus") if isinstance(rec.get("consensus"), dict) else {}
            consensus = dict(rec_consensus)
        out[market_key] = consensus
    return out


def _path_b_equity_context_by_market(
    markets: list[str],
    broker_truth: dict[str, Any],
    config: dict[str, Any],
    *,
    bot: Any | None = None,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for market in markets:
        market_key = str(market or "").upper()
        context: dict[str, Any] = {}
        provider = getattr(bot, "_market_equity_reference_context", None) if bot is not None else None
        if callable(provider):
            try:
                candidate = provider(market_key)
                if isinstance(candidate, dict):
                    total = _to_float(candidate.get("total_krw")) or 0.0
                    cash = _to_float(candidate.get("cash_krw")) or 0.0
                    position = _to_float(candidate.get("position_krw")) or 0.0
                    if total > 0 or cash > 0 or position > 0:
                        context = dict(candidate)
                        context.setdefault("source", "bot_equity_context")
            except Exception:
                context = {}
        if not context:
            context = _path_b_equity_context_from_broker(market_key, broker_truth, config)
        out[market_key] = context
    return out


def _path_b_equity_context_from_broker(
    market: str,
    broker_truth: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    market_payload = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    data = market_payload.get(market_key) if isinstance(market_payload.get(market_key), dict) else {}
    account = data.get("account_summary") if isinstance(data.get("account_summary"), dict) else {}
    positions = data.get("positions") if isinstance(data.get("positions"), list) else []
    usd_krw = float(config.get("usd_krw") or 1350.0)

    def account_float(key: str) -> float:
        return float(_to_float(account.get(key)) or 0.0)

    if market_key == "US":
        cash_krw = account_float("asset_cash_krw") or account_float("cash") * usd_krw
        position_krw = account_float("total_eval_krw")
        if position_krw <= 0:
            position_krw = sum(float(_to_float(row.get("eval_amount")) or 0.0) for row in positions) * usd_krw
        total_krw = (
            account_float("market_asset_krw")
            or account_float("asset_cash_krw") + account_float("total_eval_krw")
            or cash_krw + position_krw
        )
    else:
        cash_krw = account_float("cash")
        position_krw = account_float("total_eval")
        if position_krw <= 0:
            position_krw = sum(float(_to_float(row.get("eval_amount")) or 0.0) for row in positions)
        total_krw = account_float("market_asset_krw") or cash_krw + position_krw

    source = "broker_truth_stale" if bool(data.get("stale") or data.get("missing")) else "broker_truth"
    return {
        "market": market_key,
        "total_krw": float(total_krw),
        "cash_krw": float(cash_krw),
        "position_krw": float(position_krw),
        "source": source,
        "broker_total_krw": float(total_krw),
        "internal_krw": 0.0,
        "adjustment_krw": 0.0,
        "lag_suspected": False,
        "fallback_reason": "" if total_krw > 0 else "no_positive_equity_reference",
    }


def _path_b_execution_capacity(
    broker_truth: dict[str, Any],
    config: dict[str, Any],
    pathb_runs: list[dict[str, Any]],
    *,
    markets: list[str],
    session_date: str,
    consensus_by_market: dict[str, dict[str, Any]] | None = None,
    equity_context_by_market: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    market_payload = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    fixed_by_market = config.get("fixed_order_krw_by_market") if isinstance(config.get("fixed_order_krw_by_market"), dict) else {}
    min_by_market = config.get("min_order_krw_by_market") if isinstance(config.get("min_order_krw_by_market"), dict) else {}
    cap_by_market = config.get("daily_entry_cap_by_market") if isinstance(config.get("daily_entry_cap_by_market"), dict) else {}
    usd_krw = float(config.get("usd_krw") or 1350.0)
    entered_statuses = {"ORDER_SENT", "ORDER_ACKED", "PARTIAL_FILLED", "FILLED", "SELL_SENT", "SELL_ACKED", "SELL_PARTIAL_FILLED", "CLOSED"}
    out: dict[str, Any] = {}
    for market in markets:
        market_key = str(market or "").upper()
        data = market_payload.get(market_key) if isinstance(market_payload.get(market_key), dict) else {}
        account = data.get("account_summary") if isinstance(data.get("account_summary"), dict) else {}
        orderable_native = float(_to_float(account.get("orderable_cash")) or 0.0)
        orderable_krw = orderable_native * usd_krw if market_key == "US" else orderable_native
        fixed_order = int(_to_float(fixed_by_market.get(market_key)) or _to_float(config.get("fixed_order_krw")) or 0)
        min_order = int(_to_float(min_by_market.get(market_key)) or 0)
        current_positions = len(data.get("positions") if isinstance(data.get("positions"), list) else [])
        open_orders = len(data.get("open_orders") if isinstance(data.get("open_orders"), list) else [])
        max_positions = int(config.get("max_positions") or 0)
        daily_cap = int(_to_float(cap_by_market.get(market_key)) or _to_float(config.get("max_daily_entries")) or 0)
        current_daily_entries = sum(
            1
            for run in pathb_runs
            if str(run.get("market", "") or "").upper() == market_key
            and str(run.get("session_date", "") or "") == str(session_date or "")
            and str(run.get("status", "") or "") in entered_statuses
        )
        max_fixed = int(orderable_krw // fixed_order) if fixed_order > 0 else 0
        remaining_daily = max(0, daily_cap - current_daily_entries)
        open_slots = max(0, max_positions - current_positions)
        consensus = (
            consensus_by_market.get(market_key)
            if isinstance(consensus_by_market, dict) and isinstance(consensus_by_market.get(market_key), dict)
            else {}
        )
        equity_context = (
            equity_context_by_market.get(market_key)
            if isinstance(equity_context_by_market, dict) and isinstance(equity_context_by_market.get(market_key), dict)
            else {}
        )
        permission = str(consensus.get("new_buy_permission", "") or "").strip().lower()
        cap_policy = _resolve_gross_exposure_cap_policy(market_key, consensus, config)
        max_gross_pct = float(cap_policy.get("max_gross_exposure_pct", 0.0) or 0.0)
        total_krw = float(_to_float(equity_context.get("total_krw")) or 0.0)
        position_krw = float(_to_float(equity_context.get("position_krw")) or 0.0)
        if total_krw <= 0 and position_krw + orderable_krw > 0:
            total_krw = position_krw + orderable_krw
        gross_pct = position_krw / total_krw * 100.0 if total_krw > 0 else 0.0
        gross_cap_krw = total_krw * max_gross_pct / 100.0 if total_krw > 0 and max_gross_pct > 0 else 0.0
        gross_remaining_raw = gross_cap_krw - position_krw if gross_cap_krw > 0 else 0.0
        gross_remaining = max(0.0, gross_remaining_raw) if gross_cap_krw > 0 else orderable_krw
        cash_exposure_room = min(orderable_krw, gross_remaining) if gross_cap_krw > 0 else orderable_krw
        if permission == "block":
            cash_exposure_room = 0.0
        slot_limit = min(remaining_daily if daily_cap > 0 else 10**9, open_slots if max_positions > 0 else 10**9)
        slot_limit = max(0, int(slot_limit if slot_limit < 10**9 else max_fixed))
        today_fixed_cash_orders = int(cash_exposure_room // fixed_order) if fixed_order > 0 else 0
        today_entry_capacity_orders = min(today_fixed_cash_orders, slot_limit)
        today_fixed_order_capacity = today_entry_capacity_orders * fixed_order
        today_min_order_possible = bool(
            min_order > 0
            and cash_exposure_room >= min_order
            and slot_limit > 0
            and permission != "block"
        )
        block_reasons: list[str] = []
        if permission == "block":
            block_reasons.append("ANALYST_NEW_BUY_BLOCK")
        if max_gross_pct > 0 and total_krw <= 0:
            block_reasons.append("GROSS_EXPOSURE_REFERENCE_MISSING")
        if max_gross_pct > 0 and total_krw > 0 and gross_pct >= max_gross_pct:
            block_reasons.append("ANALYST_MAX_GROSS_EXPOSURE_REACHED")
        elif max_gross_pct > 0 and gross_cap_krw > 0 and gross_remaining < min_order:
            block_reasons.append("GROSS_EXPOSURE_REMAINING_BELOW_MIN_ORDER")
        if min_order > 0 and orderable_krw < min_order:
            block_reasons.append("CASH_BELOW_MIN_ORDER")
        if max_positions > 0 and open_slots <= 0:
            block_reasons.append("POSITION_CAP_REACHED")
        if daily_cap > 0 and remaining_daily <= 0:
            block_reasons.append("DAILY_ENTRY_CAP_REACHED")
        if (
            fixed_order > 0
            and today_fixed_cash_orders <= 0
            and today_min_order_possible
            and "ANALYST_MAX_GROSS_EXPOSURE_REACHED" not in block_reasons
        ):
            block_reasons.append("FIXED_ORDER_SIZE_EXCEEDS_TODAY_CAPACITY")
        out[market_key] = {
            "currency": "USD" if market_key == "US" else "KRW",
            "orderable_cash_native": round(orderable_native, 4),
            "orderable_cash_krw": round(orderable_krw, 2),
            "fixed_order_krw": fixed_order,
            "min_order_krw": min_order,
            "max_affordable_fixed_orders": max_fixed,
            "min_order_possible": bool(min_order > 0 and orderable_krw >= min_order),
            "current_positions": current_positions,
            "broker_open_orders": open_orders,
            "max_positions": max_positions,
            "open_position_slots": open_slots,
            "current_daily_entries": current_daily_entries,
            "remaining_daily_entries": remaining_daily,
            "daily_entry_cap": daily_cap,
            "daily_cap_cash_feasible": bool(daily_cap > 0 and max_fixed >= daily_cap),
            "new_buy_permission": permission,
            "consensus_quality": str(consensus.get("consensus_quality", "") or ""),
            "max_gross_exposure_pct": round(max_gross_pct, 3),
            "analyst_max_gross_exposure_pct": round(float(cap_policy.get("analyst_max_gross_exposure_pct", 0.0) or 0.0), 3),
            "manual_max_gross_exposure_pct": round(float(cap_policy.get("manual_max_gross_exposure_pct", 0.0) or 0.0), 3),
            "gross_cap_mode": str(cap_policy.get("gross_cap_mode", "auto") or "auto"),
            "gross_cap_requested_mode": str(cap_policy.get("gross_cap_requested_mode", "auto") or "auto"),
            "gross_cap_source": str(cap_policy.get("gross_cap_source", "analyst_consensus") or "analyst_consensus"),
            "gross_cap_config_error": str(cap_policy.get("gross_cap_config_error", "") or ""),
            "gross_exposure_pct": round(gross_pct, 3),
            "gross_exposure_cap_krw": round(gross_cap_krw, 2),
            "gross_exposure_remaining_krw": round(gross_remaining, 2) if gross_cap_krw > 0 else None,
            "gross_exposure_remaining_raw_krw": round(gross_remaining_raw, 2) if gross_cap_krw > 0 else None,
            "position_exposure_krw": round(position_krw, 2),
            "equity_reference_krw": round(total_krw, 2),
            "equity_source": str(equity_context.get("source", "") or ""),
            "equity_lag_suspected": bool(equity_context.get("lag_suspected", False)),
            "equity_fallback_reason": str(equity_context.get("fallback_reason", "") or ""),
            "today_buy_capacity_krw": round(cash_exposure_room, 2),
            "today_affordable_fixed_orders": today_fixed_cash_orders,
            "today_entry_capacity_orders": today_entry_capacity_orders,
            "today_fixed_order_capacity_krw": round(today_fixed_order_capacity, 2),
            "today_min_order_possible": today_min_order_possible,
            "capacity_block_reasons": block_reasons,
            "capacity_primary_block_reason": block_reasons[0] if block_reasons else "",
            "operator_message": (
                f"orderable cash allows fixed orders={max_fixed}; today capacity after exposure/slots={today_entry_capacity_orders}; "
                f"daily cap={daily_cap} is an upper limit, not cash capacity"
            ),
        }
    return out


def _path_b_market_session_state(market: str, session_date: str) -> dict[str, Any]:
    market_key = str(market or "").upper()
    now_dt = datetime.now(KST)
    try:
        from preopen.scheduler import is_trading_day, regular_close_dt, regular_open_dt

        trading_day = bool(is_trading_day(market_key, session_date))
        opened = regular_open_dt(market_key, session_date) if trading_day else None
        closed = regular_close_dt(market_key, session_date) if trading_day else None
        active = bool(opened is not None and closed is not None and opened <= now_dt <= closed)
        if not trading_day:
            state = "inactive"
            reason = "not_trading_day"
        elif active:
            state = "active"
            reason = ""
        elif opened is not None and now_dt < opened:
            state = "inactive"
            reason = "before_open"
        else:
            state = "inactive"
            reason = "after_close"
        return {
            "state": state,
            "reason": reason,
            "now": now_dt.isoformat(timespec="seconds"),
            "regular_open_at": opened.isoformat(timespec="seconds") if opened else "",
            "regular_close_at": closed.isoformat(timespec="seconds") if closed else "",
            "calendar_source": "exchange_calendars",
        }
    except Exception:
        return {
            "state": "unknown",
            "reason": "calendar_unavailable",
            "now": now_dt.isoformat(timespec="seconds"),
            "regular_open_at": "",
            "regular_close_at": "",
            "calendar_source": "fallback_failed",
        }


def _kr_confirmation_hard_veto_ready(config_source: dict[str, Any]) -> bool:
    values = ((config_source or {}).get("source") or {}).get("runtime_snapshot") or {}
    effective = values.get("effective") if isinstance(values.get("effective"), dict) else {}
    if not effective:
        return True
    cap = int(_to_float(effective.get("KR_DAILY_ENTRY_CAP")) or 0)
    enabled = str(effective.get("KR_CONFIRMATION_GATE_ENABLED", "")).lower() in {"1", "true", "yes", "y", "on"}
    shadow = str(effective.get("KR_CONFIRMATION_GATE_SHADOW", "")).lower() in {"1", "true", "yes", "y", "on"}
    mode = str(effective.get("KR_CONFIRMATION_GATE_MODE", "") or "").upper()
    return not (cap >= 40 and (not enabled or shadow or mode != "FAST_TRIGGER_WITH_HARD_VETO"))


def _path_b_intraday_only_from_config(config: dict[str, Any], runtime_mode: str | None) -> bool:
    raw = config.get("intraday_only") if isinstance(config, dict) else None
    if raw is not None:
        return _coerce_config_bool(raw, DEFAULT_V2_CONFIG.pathb_intraday_only)
    try:
        effective_config = _path_b_config(runtime_mode)
    except Exception:
        effective_config = {}
    return _coerce_config_bool(
        effective_config.get("intraday_only") if isinstance(effective_config, dict) else None,
        DEFAULT_V2_CONFIG.pathb_intraday_only,
    )


def _path_b_execution_readiness(
    *,
    market: str | None,
    session_date: str,
    selection: dict[str, Any],
    config: dict[str, Any],
    control: dict[str, Any],
    broker_truth: dict[str, Any],
    live_truth_verdict: dict[str, Any],
    execution_capacity: dict[str, Any],
    pathb_runs: list[dict[str, Any]],
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    if market_key not in {"KR", "US"}:
        return {"state": "MARKET_NOT_SELECTED", "operator_action_required": False}
    counts = selection.get("counts") if isinstance(selection.get("counts"), dict) else {}
    truth = live_truth_verdict.get(market_key) if isinstance(live_truth_verdict.get(market_key), dict) else {}
    capacity = execution_capacity.get(market_key) if isinstance(execution_capacity.get(market_key), dict) else {}
    session = _path_b_market_session_state(market_key, session_date)
    trade_ready_count = int(counts.get("applied_trade_ready") or 0)
    price_targets_count = int(counts.get("price_targets") or 0)
    watchlist_count = int(counts.get("watchlist") or 0)
    registered_live_plans = sum(1 for run in pathb_runs if str(run.get("market", "") or "").upper() == market_key and not _is_shadow_pathb_run(run))
    active_live_plans = sum(
        1
        for run in pathb_runs
        if str(run.get("market", "") or "").upper() == market_key
        and not _is_shadow_pathb_run(run)
        and str(run.get("status", "") or "").upper() in PATHB_ACTIVE_STATUSES
    )
    entry_waiting_plans = sum(
        1
        for run in pathb_runs
        if str(run.get("market", "") or "").upper() == market_key
        and not _is_shadow_pathb_run(run)
        and str(run.get("status", "") or "").upper() in PATHB_ENTRY_WAITING_STATUSES
    )
    broker_open_orders = int(truth.get("open_orders") or 0)
    broker_position_count = int(truth.get("positions") or 0)
    intraday_only = _path_b_intraday_only_from_config(config, runtime_mode)
    live_gate = ((config.get("market_live_enabled") or {}).get(market_key) if isinstance(config.get("market_live_enabled"), dict) else True)
    known_blockers: list[str] = []
    next_gate_checks = ["price_in_buy_zone", "affordability", "risk", "confirmation_gate"]
    state = "READY_WAITING_BUY_ZONE"
    quote_state = "quote_not_checked_read_only"

    broker_truth_unfresh = not bool(truth.get("trusted")) or not bool(truth.get("fresh"))
    capacity_known = bool(capacity)
    capacity_reasons = capacity.get("capacity_block_reasons") if isinstance(capacity.get("capacity_block_reasons"), list) else []
    capacity_ok = capacity_known and bool(capacity.get("min_order_possible", True)) and not capacity_reasons

    if not bool(config.get("enabled", False)) or not bool(control.get("enabled", True)) or bool(config.get("emergency_disable", False)) or bool(control.get("emergency_disabled", False)) or not bool(live_gate):
        state = "BLOCKED_CONFIG_OR_CONTROL"
        known_blockers.append(state)
    elif str(session.get("state")) != "active":
        if intraday_only:
            state = "IDLE_MARKET_CLOSED"
        elif broker_position_count > 0 or broker_open_orders > 0:
            state = "HOLDING_OVERNIGHT"
        else:
            state = "IDLE_MARKET_CLOSED_OVERNIGHT_ALLOWED"
        quote_state = "not_checked_market_inactive"
        if broker_truth_unfresh:
            known_blockers.append("BROKER_TRUTH_STALE_WARNING")
    elif broker_truth_unfresh:
        state = "BLOCKED_BROKER_TRUTH"
        known_blockers.append(state)
    elif active_live_plans > 0:
        if entry_waiting_plans > 0:
            if not bool(capacity.get("min_order_possible", True)):
                state = "BLOCKED_AFFORDABILITY"
                known_blockers.append(state)
            elif market_key == "KR" and not _kr_confirmation_hard_veto_ready(config):
                state = "BLOCKED_CONFIRMATION_GATE"
                known_blockers.append(state)
            else:
                state = "WAITING_QUOTE_OR_BUY_ZONE"
        else:
            state = "WAITING_QUOTE_OR_BUY_ZONE"
    elif trade_ready_count <= 0:
        state = "IDLE_NO_TRADE_READY"
        quote_state = "not_required_no_trade_ready"
    elif price_targets_count <= 0 or selection.get("missing_price_targets"):
        state = "BLOCKED_MISSING_PRICE_TARGETS"
        known_blockers.append(state)
    elif not bool(capacity.get("min_order_possible", True)):
        state = "BLOCKED_AFFORDABILITY"
        known_blockers.append(state)
    elif market_key == "KR" and not _kr_confirmation_hard_veto_ready(config):
        state = "BLOCKED_CONFIRMATION_GATE"
        known_blockers.append(state)
    elif active_live_plans <= 0:
        state = "READY_WAITING_BUY_ZONE"
    else:
        state = "WAITING_QUOTE_OR_BUY_ZONE"

    return {
        "state": state,
        "watchlist_count": watchlist_count,
        "trade_ready_count": trade_ready_count,
        "price_targets_count": price_targets_count,
        "registered_live_plans": registered_live_plans,
        "active_live_plans": active_live_plans,
        "entry_waiting_plans": entry_waiting_plans,
        "broker_open_orders": broker_open_orders,
        "market_session_state": session.get("state", "unknown"),
        "market_session": session,
        "pathb_intraday_only": intraday_only,
        "broker_position_count": broker_position_count,
        "quote_state": quote_state,
        "known_blockers": known_blockers,
        "next_gate_checks": next_gate_checks,
        "operator_action_required": state.startswith("BLOCKED_"),
        "broker_truth": truth,
        "broker_truth_warning": "stale_or_untrusted" if broker_truth_unfresh else "",
        "broker_truth_freshness": {
            "fresh": bool(truth.get("fresh")),
            "trusted": bool(truth.get("trusted")),
            "missing": bool(truth.get("missing", True)),
            "stale": bool(truth.get("stale", True)),
            "error": str(truth.get("error", "") or ""),
            "stale_reason": str(truth.get("stale_reason", "") or ""),
            "last_success_at": str(truth.get("last_success_at", "") or ""),
            "last_attempt_at": str(truth.get("last_attempt_at", "") or ""),
            "evaluated_at": str(truth.get("evaluated_at", "") or ""),
            "age_sec": truth.get("age_sec"),
            "ttl_sec": truth.get("ttl_sec"),
            "ttl_margin_sec": truth.get("ttl_margin_sec"),
        },
        "capacity_known": capacity_known,
        "capacity_ok": capacity_ok,
        "capacity_ok_but_broker_truth_blocked": state == "BLOCKED_BROKER_TRUTH" and capacity_ok,
    }


HARD_BUY_CAPACITY_BLOCKERS: set[str] = {
    "ANALYST_NEW_BUY_BLOCK",
    "ANALYST_MAX_GROSS_EXPOSURE_REACHED",
    "GROSS_EXPOSURE_REFERENCE_MISSING",
    "GROSS_EXPOSURE_REMAINING_BELOW_MIN_ORDER",
    "CASH_BELOW_MIN_ORDER",
    "POSITION_CAP_REACHED",
    "DAILY_ENTRY_CAP_REACHED",
}


def _path_buy_readiness_summary(
    *,
    markets: list[str],
    selections_by_market: dict[str, dict[str, Any]],
    readiness_by_market: dict[str, dict[str, Any]],
    execution_capacity: dict[str, Any],
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for market in markets:
        market_key = str(market or "").upper()
        if market_key not in {"KR", "US"}:
            continue
        selection = selections_by_market.get(market_key) if isinstance(selections_by_market.get(market_key), dict) else {}
        readiness = readiness_by_market.get(market_key) if isinstance(readiness_by_market.get(market_key), dict) else {}
        capacity = execution_capacity.get(market_key) if isinstance(execution_capacity.get(market_key), dict) else {}
        out[market_key] = {
            "path_a": _path_a_buy_readiness(market_key, selection, readiness, capacity),
            "path_b": _path_b_buy_readiness(market_key, selection, readiness, capacity),
        }
    return out


def _capacity_hard_blockers(capacity: dict[str, Any]) -> list[str]:
    reasons = capacity.get("capacity_block_reasons") if isinstance(capacity.get("capacity_block_reasons"), list) else []
    return [str(reason) for reason in reasons if str(reason) in HARD_BUY_CAPACITY_BLOCKERS]


def _path_a_ready_rows(selection: dict[str, Any]) -> list[dict[str, Any]]:
    rows = selection.get("watch_rows") if isinstance(selection.get("watch_rows"), list) else []
    ready_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        route = str(row.get("execution_route") or "")
        buy_path = str(row.get("buy_path") or "")
        category = str(row.get("category") or "")
        if buy_path == "path_a" and (category == "applied_trade_ready" or route.startswith("PlanA.")):
            ready_rows.append(row)
    return ready_rows


def _readiness_primary_reason(status: str, blockers: list[str], fallback: str = "") -> str:
    if blockers:
        return blockers[0]
    if fallback:
        return fallback
    return status


def _path_a_buy_readiness(
    market: str,
    selection: dict[str, Any],
    readiness: dict[str, Any],
    capacity: dict[str, Any],
) -> dict[str, Any]:
    ready_rows = _path_a_ready_rows(selection)
    session_state = str(readiness.get("market_session_state") or "unknown")
    truth_warning = str(readiness.get("broker_truth_warning") or "")
    hard_blockers = _capacity_hard_blockers(capacity)
    blockers: list[str] = []
    status = "unknown"
    state = "UNKNOWN"
    if session_state != "active":
        status = "closed"
        state = "MARKET_CLOSED"
        blockers.append("MARKET_CLOSED")
    elif truth_warning:
        status = "blocked"
        state = "BLOCKED_BROKER_TRUTH"
        blockers.append("BROKER_TRUTH_STALE_OR_UNTRUSTED")
    elif hard_blockers:
        status = "blocked"
        state = hard_blockers[0]
        blockers.extend(hard_blockers)
    elif not bool(capacity.get("min_order_possible", True)):
        status = "blocked"
        state = "BLOCKED_AFFORDABILITY"
        blockers.append("CASH_BELOW_MIN_ORDER")
    elif not ready_rows:
        status = "idle"
        state = "IDLE_NO_PATHA_TRADE_READY"
    else:
        status = "available"
        state = "READY_WAITING_SIGNAL"

    return {
        "market": market,
        "path": "path_a",
        "label": "PathA",
        "status": status,
        "state": state,
        "can_buy": status == "available",
        "primary_reason": _readiness_primary_reason(state, blockers),
        "blockers": blockers,
        "ready_count": len(ready_rows),
        "trade_ready_count": len(ready_rows),
        "market_session_state": session_state,
        "orderable_cash_krw": capacity.get("orderable_cash_krw", 0),
        "today_buy_capacity_krw": capacity.get("today_buy_capacity_krw", 0),
        "today_entry_capacity_orders": capacity.get("today_entry_capacity_orders", 0),
        "remaining_daily_entries": capacity.get("remaining_daily_entries", 0),
        "open_position_slots": capacity.get("open_position_slots", 0),
        "summary": (
            f"PathA {status}: ready={len(ready_rows)} "
            f"slots={capacity.get('open_position_slots', 0)} daily={capacity.get('remaining_daily_entries', 0)}"
        ),
    }


def _path_b_buy_readiness(
    market: str,
    selection: dict[str, Any],
    readiness: dict[str, Any],
    capacity: dict[str, Any],
) -> dict[str, Any]:
    state = str(readiness.get("state") or "UNKNOWN")
    session_state = str(readiness.get("market_session_state") or "unknown")
    known_blockers = [str(item) for item in (readiness.get("known_blockers") or [])]
    hard_blockers = _capacity_hard_blockers(capacity)
    blockers = list(dict.fromkeys(known_blockers + hard_blockers))
    entry_waiting = int(readiness.get("entry_waiting_plans") or 0)
    trade_ready = int(readiness.get("trade_ready_count") or 0)
    if state.startswith("BLOCKED_") or hard_blockers:
        status = "blocked"
    elif state in {"IDLE_MARKET_CLOSED", "IDLE_MARKET_CLOSED_OVERNIGHT_ALLOWED", "HOLDING_OVERNIGHT"}:
        status = "closed"
    elif state == "READY_WAITING_BUY_ZONE" or (state == "WAITING_QUOTE_OR_BUY_ZONE" and entry_waiting > 0):
        status = "available"
    elif state == "WAITING_QUOTE_OR_BUY_ZONE":
        status = "idle"
        if "NO_ENTRY_WAITING_PLAN" not in blockers:
            blockers.append("NO_ENTRY_WAITING_PLAN")
    elif state == "IDLE_NO_TRADE_READY":
        status = "idle"
    else:
        status = "unknown"

    no_plan = selection.get("no_plan_reasons") if isinstance(selection.get("no_plan_reasons"), list) else []
    if status == "idle" and not blockers and no_plan:
        blockers.append(str(no_plan[0]))

    return {
        "market": market,
        "path": "path_b",
        "label": "PathB",
        "status": status,
        "state": state,
        "can_buy": status == "available",
        "primary_reason": _readiness_primary_reason(state, blockers),
        "blockers": blockers,
        "ready_count": trade_ready,
        "trade_ready_count": trade_ready,
        "registered_live_plans": int(readiness.get("registered_live_plans") or 0),
        "active_live_plans": int(readiness.get("active_live_plans") or 0),
        "entry_waiting_plans": entry_waiting,
        "market_session_state": session_state,
        "orderable_cash_krw": capacity.get("orderable_cash_krw", 0),
        "today_buy_capacity_krw": capacity.get("today_buy_capacity_krw", 0),
        "today_entry_capacity_orders": capacity.get("today_entry_capacity_orders", 0),
        "remaining_daily_entries": capacity.get("remaining_daily_entries", 0),
        "open_position_slots": capacity.get("open_position_slots", 0),
        "summary": (
            f"PathB {status}: state={state} entry_waiting={entry_waiting} "
            f"slots={capacity.get('open_position_slots', 0)} daily={capacity.get('remaining_daily_entries', 0)}"
        ),
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
        "KR_CLAUDE_PRICE_LIVE_ENABLED",
        "US_CLAUDE_PRICE_LIVE_ENABLED",
        "PATHB_INTRADAY_ONLY",
        "PATHB_MIN_CONFIDENCE",
        "KR_FIXED_ORDER_KRW",
        "US_FIXED_ORDER_KRW",
        "KR_MIN_ORDER_KRW",
        "US_MIN_ORDER_KRW",
        "USD_KRW_RATE",
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
        ops = run.get("ops") if isinstance(run.get("ops"), dict) else {}
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
                "preopen_exit_policy_mode": plan.get("preopen_exit_policy_mode", ""),
                "preopen_exit_policy_decision": plan.get("preopen_exit_policy_decision", ""),
                "preopen_exit_policy_status": plan.get("preopen_exit_policy_status", ""),
                "preopen_exit_policy_severity": plan.get("preopen_exit_policy_severity", ""),
                "preopen_exit_policy_pnl_pct": plan.get("preopen_exit_policy_pnl_pct", ""),
                "preopen_exit_policy_stop_distance_pct": plan.get("preopen_exit_policy_stop_distance_pct", ""),
                "preopen_exit_policy_recorded_at": plan.get("preopen_exit_policy_recorded_at", ""),
                "preopen_exit_policy_recheck_earliest_at": plan.get("preopen_exit_policy_recheck_earliest_at", ""),
                "preopen_exit_defer_active": bool(plan.get("preopen_exit_defer_active", False)),
                "preopen_exit_defer_status": plan.get("preopen_exit_defer_status", ""),
                "open_confirm_recheck_result": plan.get("open_confirm_recheck_result", ""),
                "open_confirm_recheck_at": plan.get("open_confirm_recheck_at", ""),
                "skip_stale_or_closed_review": bool(plan.get("skip_stale_or_closed_review", False)),
                "skip_stale_or_closed_review_reason": plan.get("skip_stale_or_closed_review_reason", ""),
                "skip_stale_or_closed_review_at": plan.get("skip_stale_or_closed_review_at", ""),
                "ops_reason": ops.get("ops_reason", ""),
                "ops_reason_source": ops.get("reason_source", ""),
                "reason_codes": ops.get("reason_codes", []),
                "latest_gate_reason": ops.get("latest_gate_reason", ""),
                "latest_gate_reason_source": ops.get("latest_gate_reason_source", ""),
                "expired_reason": ops.get("expired_reason", ""),
                "expired_reason_source": ops.get("expired_reason_source", ""),
                "sent_at": ops.get("sent_at", ""),
                "acked_at": ops.get("acked_at", ""),
                "filled_at": ops.get("filled_at", ""),
                "closed_at": ops.get("closed_at", ""),
                "expired_at": ops.get("expired_at", ""),
                "sent_to_ack_latency_sec": ops.get("sent_to_ack_latency_sec"),
                "sent_to_fill_latency_sec": ops.get("sent_to_fill_latency_sec"),
                "sent_to_expired_latency_sec": ops.get("sent_to_expired_latency_sec"),
                "entry_slippage_bps": ops.get("entry_slippage_bps"),
                "exit_slippage_bps": ops.get("exit_slippage_bps"),
                "order_unknown_phase": ops.get("order_unknown_phase", ""),
                "order_unknown_age_sec": ops.get("order_unknown_age_sec"),
                "order_unknown_reconcile_attempts": ops.get("order_unknown_reconcile_attempts"),
                "order_unknown_soft_timeout_sec": ops.get("order_unknown_soft_timeout_sec"),
                "order_unknown_hard_timeout_sec": ops.get("order_unknown_hard_timeout_sec"),
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
                "broker_position_qty": plan.get("broker_position_qty", broker_evidence.get("broker_position_qty", 0)),
                "broker_open_order_count": broker_evidence.get("broker_open_order_count", 0),
                "broker_today_fill_count": broker_evidence.get("broker_today_fill_count", 0),
                "local_exposure": bool(plan.get("local_exposure", False)),
                "local_position_qty": plan.get("local_position_qty", 0),
                "remediation_allowed": bool(
                    plan.get("remediation_allowed", False)
                    or plan.get("order_unknown_remediation_allowed", False)
                ),
                "remediation_blockers": plan.get("remediation_blockers", []),
                "audited_remediation": bool(plan.get("audited_remediation", False)),
                "session_end_unresolved": bool(plan.get("session_end_unresolved", False)),
                "manual_reconciliation_required": bool(plan.get("manual_reconciliation_required", False)),
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
