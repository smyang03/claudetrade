from __future__ import annotations

"""Bounded live-order bridge for the 2026-07-15 profit strategy set.

The bridge is intentionally boring: exact-session signals, explicit live ACK,
KIS-only execution quotes, one shared kill switch, small cash caps, duplicate
guards and an append-only handoff ledger.  Research tools never call it.
"""

from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from bot.session_date import KST
from kis_api import get_price
from logger import get_trading_logger
from preopen.scheduler import regular_open_dt
from runtime_paths import get_runtime_path


log = get_trading_logger()
LIVE_ACK = "I_ACCEPT_LIVE_PROFIT_STRATEGIES"
CORE_LIVE_AUTHORITY = "MICRO_ENFORCE_OPERATOR_PROMOTED"
CORE_SOURCE_AUTHORITY = "SHADOW_ONLY_NO_ORDER_OR_LIVE_CONFIG_EFFECT"
CORE_IDS = {"US_SCHG_BIL_TREND_V1", "KR_FACTOR_TREND_V1"}
# Research challengers may still be materialized for forward observation, but
# absence of an explicit live allowlist must never promote them to orders.
DEFAULT_ENABLED = set(CORE_IDS)
TRANSIENT_SUBMIT_BLOCK_REASONS = {
    "ANALYST_NEW_BUY_BLOCK",
    "ENTRY_BLACKOUT",
    "BROKER_SYNC_QUARANTINE",
    "BROKER_TRUTH_UNTRUSTED",
    "BROKER_TRUTH_UNAVAILABLE",
    "GUARDIAN_MARKET_BLOCK",
    "GUARDIAN_MARKET_GATE_MISSING",
    "GUARDIAN_MARKET_GATE_INVALID",
}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _ticker_key(market: str, ticker: str) -> str:
    raw = str(ticker or "").strip()
    if str(market or "").upper() == "US":
        return raw.upper()
    return raw.split(".", 1)[0]


def _enabled_ids(bot: Any) -> set[str]:
    raw = str(bot._runtime_value("PROFIT_STRATEGY_ENABLED_IDS", ",".join(sorted(DEFAULT_ENABLED))) or "")
    return {item.strip().upper() for item in raw.split(",") if item.strip()}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_core_signals(*, market: str, session_date: str) -> list[dict[str, Any]]:
    market_key = str(market or "").upper()
    path = get_runtime_path(
        "state", f"profit_strategy_core_live_manifest_{market_key}.json", make_parents=False
    )
    payload = _read_json(path)
    if (
        payload.get("schema_version") != "profit_strategy_core_live_manifest_v1"
        or payload.get("authority") != CORE_LIVE_AUTHORITY
        or payload.get("status") != "healthy"
        or str(payload.get("market") or "").upper() != market_key
        or str(payload.get("session_date") or "") != session_date
        or str(payload.get("effective_month") or "") != str(session_date)[:7]
    ):
        return []
    source_path = Path(str(payload.get("source_artifact") or ""))
    if (
        not source_path.is_file()
        or str(payload.get("source_authority") or "") != CORE_SOURCE_AUTHORITY
        or not str(payload.get("source_sha256") or "")
        or _sha256(source_path) != str(payload.get("source_sha256") or "")
    ):
        return []
    expected = {
        "US": ("US_SCHG_BIL_TREND_V1", {"SCHG", "BIL"}),
        "KR": ("KR_FACTOR_TREND_V1", {"275280", "275300"}),
    }.get(market_key)
    if expected is None:
        return []
    output: list[dict[str, Any]] = []
    strategy_id, allowed_tickers = expected
    for raw in payload.get("signals") or []:
        row = dict(raw) if isinstance(raw, dict) else {}
        ticker = _ticker_key(market_key, str(row.get("ticker") or ""))
        try:
            weight = float(row.get("weight") or 0.0)
        except Exception:
            return []
        if (
            str(row.get("strategy_id") or "").upper() != strategy_id
            or str(row.get("market") or "").upper() != market_key
            or str(row.get("entry_session_date") or "") != session_date
            or ticker not in allowed_tickers
            or not 0.0 < weight <= 1.0
        ):
            return []
        row["ticker"] = ticker
        row["weight"] = weight
        evidence = dict(row.get("evidence") or {})
        evidence["live_manifest"] = str(path)
        evidence["source_sha256"] = str(payload.get("source_sha256") or "")
        row["evidence"] = evidence
        output.append(row)
    if not output or sum(float(row.get("weight") or 0.0) for row in output) > 1.000001:
        return []
    return output


def load_materialized_signals(*, market: str, session_date: str) -> list[dict[str, Any]]:
    path = get_runtime_path("state", f"profit_strategy_signals_{market}.json", make_parents=False)
    payload = _read_json(path)
    if str(payload.get("session_date") or "") != session_date:
        return []
    if str(payload.get("status") or "") not in {"healthy", "degraded"}:
        return []
    return [
        dict(row)
        for row in payload.get("signals") or []
        if str((row or {}).get("market") or "").upper() == market
        and str((row or {}).get("entry_session_date") or "") == session_date
    ]


def load_signals(bot: Any, *, market: str, session_date: str) -> list[dict[str, Any]]:
    enabled = _enabled_ids(bot)
    rows = [
        *load_core_signals(market=market, session_date=session_date),
        *load_materialized_signals(market=market, session_date=session_date),
    ]
    rows = [row for row in rows if str(row.get("strategy_id") or "").upper() in enabled]
    rows.sort(key=lambda row: (-float(row.get("priority") or 0.0), int(row.get("rank") or 999999)))
    return rows


def _ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    output: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines()[-2000:]:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            output.append(row)
    return output


def _is_transient_submit_block(row: dict[str, Any]) -> bool:
    return (
        str((row or {}).get("status") or "").upper() in {"SUBMIT_BLOCKED", "SUBMIT_DEFERRED"}
        and str((row or {}).get("reason") or "").upper() in TRANSIENT_SUBMIT_BLOCK_REASONS
    )


def _recorded_at(row: dict[str, Any]) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str((row or {}).get("recorded_at") or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=KST)
    except Exception:
        return None


def _position_sources(bot: Any, market: str) -> list[dict[str, Any]]:
    return [
        row for row in [
            *(getattr(getattr(bot, "risk", None), "positions", []) or []),
            *(getattr(bot, "pending_orders", []) or []),
        ]
        if str(row.get("market") or bot._ticker_market(str(row.get("ticker") or ""))).upper() == market
    ]


def _is_open_for_source(bot: Any, market: str, ticker: str, source: str) -> bool:
    ticker_key = _ticker_key(market, ticker)
    source_key = str(source or "").lower()
    return any(
        _ticker_key(market, str(row.get("ticker") or "")) == ticker_key
        and str(row.get("source_strategy") or row.get("strategy_used") or "").lower() == source_key
        for row in _position_sources(bot, market)
    )


def _core_rebalance_exits(bot: Any, *, market: str, desired: list[dict[str, Any]]) -> list[dict[str, Any]]:
    desired_by_source = {
        str(row.get("source_strategy") or "").lower(): {
            _ticker_key(market, str(item.get("ticker") or ""))
            for item in desired
            if str(item.get("source_strategy") or "").lower() == str(row.get("source_strategy") or "").lower()
        }
        for row in desired
        if str(row.get("strategy_id") or "").upper() in CORE_IDS
    }
    results: list[dict[str, Any]] = []
    for pos in list(getattr(getattr(bot, "risk", None), "positions", []) or []):
        source = str(pos.get("source_strategy") or "").lower()
        if source not in desired_by_source:
            continue
        ticker = _ticker_key(market, str(pos.get("ticker") or ""))
        if ticker in desired_by_source[source] or bot._has_active_pending_sell_confirmation(pos):
            continue
        try:
            quote = get_price(ticker, bot._token_for_market(market), market=market, allow_fallback=False)
            raw = float((quote or {}).get("price") or 0.0)
            if raw <= 0:
                raise ValueError("fresh_quote_missing")
            bot.price_cache_raw[ticker] = raw
            exit_krw = raw * float(bot.usd_krw_rate or 0.0) if market == "US" else raw
            cand = {**pos, "ticker": ticker, "exit_price": exit_krw}
            sold = bool(bot._execute_sell(cand, market, reason="core_monthly_rebalance"))
            results.append({"ticker": ticker, "source_strategy": source, "status": "SELL_SENT" if sold else "SELL_BLOCKED"})
        except Exception as exc:
            results.append({"ticker": ticker, "source_strategy": source, "status": "SELL_ERROR", "reason": str(exc)[:240]})
        break
    return results


def run_profit_strategy_handoff(bot: Any, market: str) -> dict[str, Any]:
    market_key = str(market or "").upper()
    if market_key not in {"KR", "US"}:
        return {"status": "SKIPPED", "reason": "unsupported_market"}
    if not bot._runtime_bool("PROFIT_STRATEGY_ORDER_HANDOFF_ENABLED", False):
        return {"status": "DISABLED", "reason": "handoff_disabled"}
    if bot._runtime_bool("PROFIT_STRATEGY_KILL_SWITCH", False):
        return {"status": "BLOCKED", "reason": "operator_kill_switch"}
    mode = str(bot._runtime_value("PROFIT_STRATEGY_AUTHORITY_MODE", "shadow") or "shadow").lower()
    submit = bot._runtime_bool("PROFIT_STRATEGY_ORDER_SUBMIT_ENABLED", False)
    ack = str(bot._runtime_value("PROFIT_STRATEGY_ORDER_LIVE_ACK", "") or "")
    if mode != "micro" or not submit or (not bot.is_paper and ack != LIVE_ACK):
        return {"status": "BLOCKED", "reason": "micro_authority_or_live_ack_missing", "mode": mode}

    session_date = bot._current_session_date_str(market_key)
    opened = regular_open_dt(market_key, session_date)
    now = datetime.now(KST)
    elapsed_min = (now - opened).total_seconds() / 60.0
    min_open = bot._runtime_int("PROFIT_STRATEGY_ORDER_MIN_OPEN_MIN", 5)
    max_open = bot._runtime_int("PROFIT_STRATEGY_ORDER_MAX_OPEN_MIN", 45)
    if elapsed_min < min_open or elapsed_min > max_open:
        return {"status": "WAIT", "reason": "outside_entry_window", "elapsed_min": round(elapsed_min, 2)}

    signals = load_signals(bot, market=market_key, session_date=session_date)
    if not signals:
        return {"status": "SKIPPED", "reason": "no_exact_session_signal"}
    ledger_path = get_runtime_path("state", "profit_strategy_handoff.jsonl")
    ledger = _ledger_rows(ledger_path)
    attempted_today = [
        row for row in ledger
        if row.get("session_date") == session_date and row.get("market") == market_key
        and row.get("status") in {"SUBMITTED", "ORDER_UNKNOWN", "SUBMIT_BLOCKED"}
        and not _is_transient_submit_block(row)
    ]
    unresolved_unknown = [row for row in attempted_today if row.get("status") == "ORDER_UNKNOWN"]
    if unresolved_unknown:
        return {
            "status": "BLOCKED",
            "reason": "unresolved_strategy_order_unknown",
            "order_unknown": len(unresolved_unknown),
        }
    max_new = bot._runtime_int(f"PROFIT_STRATEGY_MAX_NEW_PER_DAY_{market_key}", 1)
    if len(attempted_today) >= max_new:
        return {
            "status": "BLOCKED",
            "reason": "daily_strategy_order_cap",
            "submitted": len(attempted_today),
            "attempted": len(attempted_today),
        }

    bot._sync_runtime_with_broker()
    desired_core = [row for row in signals if str(row.get("strategy_id") or "").upper() in CORE_IDS]
    sell_results = _core_rebalance_exits(bot, market=market_key, desired=desired_core)
    if any(row.get("status") == "SELL_SENT" for row in sell_results):
        return {"status": "REBALANCE_SELL_SENT", "sells": sell_results}

    results: list[dict[str, Any]] = []
    for signal in signals:
        strategy_id = str(signal.get("strategy_id") or "").upper()
        source = str(signal.get("source_strategy") or strategy_id.lower()).lower()
        ticker = _ticker_key(market_key, str(signal.get("ticker") or ""))
        identity = (session_date, market_key, strategy_id, ticker)
        if any(
            (row.get("session_date"), row.get("market"), row.get("strategy_id"), row.get("ticker")) == identity
            and row.get("status") in {"SUBMITTED", "ORDER_UNKNOWN", "SUBMIT_BLOCKED"}
            and not _is_transient_submit_block(row)
            for row in ledger
        ):
            continue
        retry_min = max(1, bot._runtime_int("PROFIT_STRATEGY_TRANSIENT_RETRY_MIN", 5))
        recent_transient = next(
            (
                row
                for row in reversed(ledger)
                if (row.get("session_date"), row.get("market"), row.get("strategy_id"), row.get("ticker")) == identity
                and _is_transient_submit_block(row)
                and _recorded_at(row) is not None
                and (now - _recorded_at(row)).total_seconds() < retry_min * 60
            ),
            None,
        )
        if recent_transient is not None:
            results.append(
                {
                    "strategy_id": strategy_id,
                    "ticker": ticker,
                    "status": "WAIT",
                    "reason": "transient_retry_cooldown",
                    "previous_reason": str(recent_transient.get("reason") or ""),
                    "retry_min": retry_min,
                }
            )
            continue
        if _is_open_for_source(bot, market_key, ticker, source):
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "SKIPPED", "reason": "already_open_for_strategy"})
            continue
        if bot._has_open_position(ticker, market_key) or bot._has_pending_order(ticker, market_key):
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "BLOCKED", "reason": "cross_strategy_ticker_overlap"})
            continue
        if len(_position_sources(bot, market_key)) >= bot._runtime_int("PROFIT_STRATEGY_MAX_OPEN_SLOTS", 4):
            return {"status": "BLOCKED", "reason": "profit_strategy_slot_cap", "results": results}
        try:
            quote = get_price(ticker, bot._token_for_market(market_key), market=market_key, allow_fallback=False)
        except Exception as exc:
            quote = {}
            log.warning(f"[profit strategy handoff] fresh quote failed {market_key} {ticker}: {exc}")
        raw = float((quote or {}).get("price") or 0.0)
        open_price = float((quote or {}).get("open") or raw or 0.0)
        if raw <= 0 or open_price <= 0:
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "BLOCKED", "reason": "fresh_quote_missing"})
            continue
        chase_pct = (raw / open_price - 1.0) * 100.0
        max_chase = bot._runtime_float("PROFIT_STRATEGY_MAX_CHASE_PCT", 0.75)
        if chase_pct > max_chase:
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "BLOCKED", "reason": "price_chase", "chase_pct": chase_pct})
            continue
        fx = float(getattr(bot, "usd_krw_rate", 0.0) or 0.0)
        risk_price = raw * fx if market_key == "US" else raw
        if risk_price <= 0:
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "BLOCKED", "reason": "fx_or_price_missing"})
            continue
        configured_cap = bot._runtime_float(f"PROFIT_STRATEGY_MAX_ORDER_KRW_{market_key}", 100000.0)
        available = float(bot._market_budget_available(market_key) or 0.0)
        cash = float(bot._broker_orderable_cash_krw(market_key) or 0.0)
        # Every budget source is an independent hard ceiling.  Missing/zero
        # broker cash must fail closed instead of being silently ignored by a
        # positive configured cap.
        if configured_cap <= 0 or available <= 0 or cash <= 0:
            results.append(
                {
                    "strategy_id": strategy_id,
                    "ticker": ticker,
                    "status": "BLOCKED",
                    "reason": "micro_budget_unavailable",
                    "configured_cap_krw": configured_cap,
                    "market_budget_available_krw": available,
                    "broker_orderable_cash_krw": cash,
                }
            )
            continue
        spend_cap = min(configured_cap, available, cash)
        weighted_cap = spend_cap * max(0.0, min(1.0, float(signal.get("weight") or 1.0)))
        qty = int(weighted_cap // risk_price)
        if qty <= 0:
            results.append({"strategy_id": strategy_id, "ticker": ticker, "status": "BLOCKED", "reason": "micro_budget_cannot_buy_one_share"})
            continue
        hold_sessions = int(signal.get("hold_sessions") or 1)
        is_core = strategy_id in CORE_IDS
        # Core sleeves exit only through their monthly strategy owner.  Zero is
        # the honest contract here; extreme sentinel percentages contaminated
        # persisted positions and dashboards even though generic exits were
        # correctly isolated.
        tp_pct = 0.0 if is_core or source != "us_swing_5d" else 0.12
        sl_pct = 0.0 if is_core else 0.25
        mode_name = str((getattr(bot, "today_judgment", {}) or {}).get("consensus", {}).get("mode", "CAUTIOUS"))
        order_ok = bot._submit_micro_probe_buy_order(
            market=market_key,
            ticker=ticker,
            name=str((quote or {}).get("name") or ticker),
            qty=qty,
            raw_price=raw,
            risk_price_krw=risk_price,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            max_hold=hold_sessions,
            mode=mode_name,
            selected_reason=f"{strategy_id}:micro_enforce",
            source_strategy=source,
            entry_priority_score=float(signal.get("priority") or 0.0),
            tsdb_id=-1,
            isdb_id=0,
            signal_at=str(signal.get("known_at") or ""),
            signal_row={**signal, "authority_mode": "micro", "order_cap_krw": configured_cap},
            probe_meta={
                "reason": f"profit_strategy_micro:{strategy_id}",
                "original_qty": qty,
                "adjusted_qty": qty,
                "original_order_cost_krw": qty * risk_price,
                "adjusted_order_cost_krw": qty * risk_price,
                "order_budget_krw": configured_cap,
                "min_effective_order_krw": 0.0,
                "oversize_ratio": 1.0,
            },
        )
        outcome = dict(getattr(bot, "_last_micro_probe_submit_result", {}) or {})
        if order_ok and outcome.get("order_no"):
            status = "SUBMITTED"
        elif outcome.get("status") == "UNKNOWN":
            status = "ORDER_UNKNOWN"
        elif str(outcome.get("reason") or "").upper() in TRANSIENT_SUBMIT_BLOCK_REASONS:
            status = "SUBMIT_DEFERRED"
        else:
            status = "SUBMIT_BLOCKED"
        if status == "ORDER_UNKNOWN":
            try:
                bot._v2_record_order_unknown(
                    market_key,
                    ticker,
                    {
                        "ticker": ticker,
                        "market": market_key,
                        "qty": qty,
                        "order_no": str(outcome.get("order_no") or ""),
                        "source_strategy": source,
                        "strategy_id": strategy_id,
                    },
                    "profit strategy broker submission outcome unknown",
                )
            except Exception as exc:
                log.error(
                    f"[profit strategy handoff] ORDER_UNKNOWN registry failed "
                    f"{market_key} {ticker}: {exc}"
                )
        record = {
            "schema_version": "profit_strategy_handoff_v1",
            "recorded_at": datetime.now(KST).isoformat(timespec="seconds"),
            "session_date": session_date,
            "market": market_key,
            "strategy_id": strategy_id,
            "source_strategy": source,
            "ticker": ticker,
            "authority_mode": "micro",
            "status": status,
            "order_no": str(outcome.get("order_no") or ""),
            "qty": qty,
            "quote_price": raw,
            "risk_price_krw": risk_price,
            "order_cost_krw": qty * risk_price,
            "hold_sessions": hold_sessions,
            "reason": str(outcome.get("reason") or ""),
            "broker_outcome_status": str(outcome.get("status") or ""),
            "broker_detail": str(outcome.get("detail") or "")[:240],
            "core_analyst_entry_isolation_applied": bool(
                outcome.get("core_analyst_entry_isolation_applied")
            ),
            "analyst_direction_block_observed": bool(
                outcome.get("analyst_direction_block_observed")
            ),
            "analyst_gross_cap_source": str(outcome.get("analyst_gross_cap_source") or ""),
            "signal_known_at": signal.get("known_at"),
        }
        _append_jsonl(ledger_path, record)
        results.append(record)
        if status in {"SUBMITTED", "ORDER_UNKNOWN"}:
            break
    return {"status": "PROCESSED", "market": market_key, "session_date": session_date, "results": results, "rebalance": sell_results}
