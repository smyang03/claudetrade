from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_audit_store import CandidateAuditStore
from runtime_paths import get_runtime_path


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue
            if isinstance(item, dict):
                rows.append(item)
    return rows


def _second(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).replace(microsecond=0).isoformat()
    except Exception:
        return raw[:19]


def _compact_ts(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or ""))[:32]


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _first_match_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    return _to_float(match.group(1)) if match else None


def _first_match_text(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return str(match.group(1)).strip() if match else ""


def _as_ticker_set(values: Any) -> set[str]:
    out: set[str] = set()
    if not isinstance(values, list):
        return out
    for item in values:
        if isinstance(item, dict):
            ticker = item.get("ticker")
        else:
            ticker = item
        ticker_s = str(ticker or "").strip().upper()
        if ticker_s:
            out.add(ticker_s)
    return out


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return parsed
        except Exception:
            pass
        return [part.strip() for part in text.split(",") if part.strip()]
    return [value]


def _value_for_ticker(mapping: Any, ticker: str) -> Any:
    if isinstance(mapping, dict):
        return mapping.get(ticker) or mapping.get(str(ticker).upper()) or mapping.get(str(ticker).lower())
    return None


def _action_map(actions: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(actions, list):
        return out
    for item in actions:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker") or "").strip().upper()
        if ticker:
            out[ticker] = item
    return out


def _candidate_row_value(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _trainer_audit_fields(
    row: dict[str, Any],
    meta: dict[str, Any],
    *,
    included: bool,
    excluded_reason: str = "",
) -> dict[str, Any]:
    components = row.get("trainer_score_components")
    if not isinstance(components, dict):
        components = row.get("trainer_score_components_json") if isinstance(row.get("trainer_score_components_json"), dict) else {}
    return {
        "final_prompt_included": bool(included),
        "raw_rank": row.get("raw_rank"),
        "trainer_score_rank": row.get("trainer_score_rank"),
        "prompt_excluded_reason": excluded_reason or row.get("prompt_excluded_reason") or "",
        "trainer_prompt_score": row.get("trainer_prompt_score"),
        "trainer_plan_a_score": row.get("trainer_plan_a_score"),
        "trainer_pathb_wait_score": row.get("trainer_pathb_wait_score"),
        "trainer_risk_score": row.get("trainer_risk_score"),
        "trainer_score_components_json": components,
        "trainer_candidate_state": row.get("trainer_candidate_state"),
        "source_tags_json": row.get("source_tags") or [],
        "data_quality_flags_json": row.get("data_quality_flags") or row.get("quality_data_gaps") or [],
        "candidate_pool_version": row.get("candidate_pool_version") or meta.get("_candidate_quality_trainer_version", ""),
        "prompt_pool_version": row.get("prompt_pool_version") or meta.get("_prompt_pool_version", ""),
        "candidate_source": row.get("candidate_source") or row.get("source") or "",
        "candidate_age_min": row.get("candidate_age_min"),
        "price_change_since_first_seen_pct": row.get("price_change_since_first_seen_pct"),
        "freshness_verdict": row.get("freshness_verdict"),
        "trainer_tier": row.get("trainer_tier"),
    }


def _parse_prompt_candidates(prompt: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    in_block = False
    for line in str(prompt or "").splitlines():
        if "후보 종목:" in line:
            in_block = True
            continue
        if line.strip().lower() == "candidates:":
            in_block = True
            continue
        if not in_block:
            continue
        match = re.match(r"\s*(?:\d+\.\s*)?([0-9A-Z][0-9A-Z.\-]{0,9})\s+(.*)$", line)
        if not match:
            if rows:
                break
            continue
        ticker = match.group(1).strip().upper()
        text = match.group(2)
        if "chg=" not in text:
            if rows:
                break
            continue
        rows.append(
            {
                "ticker": ticker,
                "price": _first_match_float(r"\bp=([0-9,.]+)", text),
                "change_pct": _first_match_float(r"\bchg=([+-]?[0-9.]+)%", text),
                "volume_ratio": _first_match_float(r"\bvol=([0-9.]+)x", text),
                "turnover": _first_match_float(r"\bturn=([0-9,.]+)", text),
                "market_type": _first_match_text(r"\bboard=([A-Za-z0-9_]+)", text),
                "liquidity_bucket": _first_match_text(r"\bliq=([A-Za-z0-9_-]+)", text),
                "primary_bucket": _first_match_text(r"\b(?:fit|category)=([A-Za-z0-9_-]+)", text),
                "payload": {
                    "prompt_line": line,
                    "from_high_tag": _first_match_text(r"from_high=[^()]*\(([^)]+)\)", text),
                    "entry_price_tag": _first_match_text(r"\bep=([A-Za-z0-9_-]+)", text),
                    "ma60": _first_match_text(r"\bma60=([A-Za-z0-9_-]+)", text),
                },
            }
        )
    return rows


def _raw_select_files(root: Path, session_date: str, market: str) -> list[Path]:
    day = session_date.replace("-", "")
    return sorted((root / "logs" / "raw_calls").glob(f"{day}_{market}_select_tickers*.json"))


def _call_id_for_event(
    *,
    store: CandidateAuditStore,
    call_by_second: dict[str, str],
    session_date: str,
    market: str,
    runtime_mode: str,
    occurred_at: str,
    source: str,
) -> str:
    sec = _second(occurred_at)
    if sec in call_by_second:
        return call_by_second[sec]
    call_id = f"{source}_{market}_{_compact_ts(sec)}"
    store.upsert_call(
        {
            "call_id": call_id,
            "runtime_mode": runtime_mode,
            "market": market,
            "session_date": session_date,
            "called_at": occurred_at,
            "label": source,
            "source_file": source,
        }
    )
    call_by_second[sec] = call_id
    return call_id


def _ensure_source_candidate(
    *,
    store: CandidateAuditStore,
    call_by_second: dict[str, str],
    session_date: str,
    market: str,
    runtime_mode: str,
    ticker: str,
    occurred_at: str,
    source: str,
    source_file: str,
    payload: dict[str, Any] | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    ticker_key = str(ticker or "").strip().upper()
    if not ticker_key:
        return
    call_id = _call_id_for_event(
        store=store,
        call_by_second=call_by_second,
        session_date=session_date,
        market=market,
        runtime_mode=runtime_mode,
        occurred_at=occurred_at,
        source=source,
    )
    row = {
        "call_id": call_id,
        "runtime_mode": runtime_mode,
        "market": market,
        "session_date": session_date,
        "known_at": occurred_at,
        "ticker": ticker_key,
        "source_file": source_file,
        "payload": payload or {},
    }
    row.update(fields or {})
    store.upsert_candidate(row)


def _backfill_raw_calls(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
) -> dict[str, str]:
    call_by_second: dict[str, str] = {}
    for path in _raw_select_files(root, session_date, market):
        data = _read_json(path)
        called_at = str(data.get("timestamp") or "")
        parsed_raw = data.get("parsed") if isinstance(data.get("parsed"), dict) else {}
        parsed_normalized = parsed_raw.get("_normalized") if isinstance(parsed_raw.get("_normalized"), dict) else None
        parsed = parsed_normalized if isinstance(parsed_normalized, dict) else parsed_raw
        tokens = data.get("tokens") if isinstance(data.get("tokens"), dict) else {}
        prompt_candidates = _parse_prompt_candidates(str(data.get("prompt") or ""))
        call_id = str(data.get("call_id") or path.stem)
        call_by_second[_second(called_at)] = call_id
        watch_set = _as_ticker_set(parsed.get("watchlist"))
        ready_set = _as_ticker_set(parsed.get("trade_ready"))
        actions = _action_map(parsed.get("candidate_actions"))
        prompt_pool_rows = [
            dict(row or {})
            for row in list(parsed.get("_final_prompt_pool") or [])
            if isinstance(row, dict) and str(row.get("ticker") or "").strip()
        ]
        prompt_pool_by_ticker = {
            str(row.get("ticker") or "").strip().upper(): row
            for row in prompt_pool_rows
        }
        prompt_tickers = {str(candidate.get("ticker") or "").strip().upper() for candidate in prompt_candidates}
        store.upsert_call(
            {
                "call_id": call_id,
                "runtime_mode": runtime_mode,
                "market": market,
                "session_date": session_date,
                "called_at": called_at,
                "label": data.get("label", ""),
                "model": data.get("model", ""),
                "prompt_version": data.get("prompt_version", ""),
                "source_file": str(path.relative_to(root)),
                "input_tokens": tokens.get("input", 0),
                "output_tokens": tokens.get("output", 0),
                "prompt_candidate_count": len(prompt_candidates),
                "watchlist_count": len(watch_set),
                "trade_ready_count": len(ready_set),
                "candidate_action_count": len(actions),
                "payload": {"extra": data.get("extra") or {}},
            }
        )
        for rank, candidate in enumerate(prompt_candidates, start=1):
            ticker = str(candidate.get("ticker") or "").upper()
            trainer_row = prompt_pool_by_ticker.get(ticker, {})
            action = actions.get(ticker, {})
            action_name = str(action.get("action") or action.get("decision") or "").strip().upper()
            if not action_name:
                action_name = "TRADE_READY" if ticker in ready_set else ("WATCH" if ticker in watch_set else "")
            reason_value = (
                action.get("reason")
                or _value_for_ticker(parsed.get("reasons"), ticker)
                or _value_for_ticker(parsed.get("veto"), ticker)
                or ""
            )
            store.upsert_candidate(
                {
                    "call_id": call_id,
                    "runtime_mode": runtime_mode,
                    "market": market,
                    "session_date": session_date,
                    "known_at": called_at,
                    "ticker": ticker,
                    "source_file": str(path.relative_to(root)),
                    "prompt_rank": trainer_row.get("prompt_rank") or rank,
                    "in_prompt": True,
                    "price": candidate.get("price"),
                    "change_pct": candidate.get("change_pct"),
                    "volume_ratio": candidate.get("volume_ratio"),
                    "turnover": candidate.get("turnover"),
                    "market_type": candidate.get("market_type", ""),
                    "liquidity_bucket": candidate.get("liquidity_bucket", ""),
                    "primary_bucket": candidate.get("primary_bucket", ""),
                    "claude_action": action_name,
                    "claude_reason": reason_value,
                    "claude_veto_reason": _value_for_ticker(parsed.get("veto"), ticker) or "",
                    "claude_watchlist": ticker in watch_set,
                    "claude_trade_ready": ticker in ready_set,
                    "recommended_strategy": _value_for_ticker(parsed.get("recommended_strategy"), ticker) or "",
                    "risk_tags": _value_for_ticker(parsed.get("risk_tags"), ticker) or [],
                    "max_position_pct": _value_for_ticker(parsed.get("max_position_pct"), ticker),
                    **(_trainer_audit_fields(trainer_row, parsed, included=True) if trainer_row else {}),
                    "payload": {
                        "prompt": candidate.get("payload") or {},
                        "candidate_action": action,
                    },
                }
            )
        for item in list(parsed.get("_excluded_from_prompt") or []):
            if not isinstance(item, dict):
                continue
            row = dict(item.get("candidate") or item)
            ticker = str(row.get("ticker") or item.get("ticker") or "").strip().upper()
            if not ticker or ticker in prompt_tickers:
                continue
            excluded_reason = str(item.get("prompt_excluded_reason") or item.get("reason") or "not_in_prompt")
            store.upsert_candidate(
                {
                    "call_id": call_id,
                    "runtime_mode": runtime_mode,
                    "market": market,
                    "session_date": session_date,
                    "known_at": called_at,
                    "ticker": ticker,
                    "source_file": str(path.relative_to(root)),
                    "prompt_rank": None,
                    "in_prompt": False,
                    "screener_seen": True,
                    "input_to_claude_reported": False,
                    "name": _candidate_row_value(row, "name", default=""),
                    "price": _candidate_row_value(row, "price", "current_price"),
                    "change_pct": _candidate_row_value(row, "change_pct", "change_rate"),
                    "volume_ratio": _candidate_row_value(row, "volume_ratio", "vol_ratio"),
                    "turnover": _candidate_row_value(row, "turnover"),
                    "market_type": _candidate_row_value(row, "market_type", default=""),
                    "liquidity_bucket": _candidate_row_value(row, "liquidity_bucket", default=""),
                    "primary_bucket": _candidate_row_value(row, "primary_bucket", default=""),
                    "secondary_buckets": list(row.get("secondary_buckets") or []),
                    "classification": "not_in_prompt",
                    **_trainer_audit_fields(row, parsed, included=False, excluded_reason=excluded_reason),
                    "payload": {
                        "selection_stage": "trainer_prompt_pool_excluded",
                        "prompt_pool_audit": True,
                        "excluded_reason": excluded_reason,
                    },
                }
            )
    return call_by_second


def _backfill_screener_quality(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    day = session_date.replace("-", "")
    path = root / "logs" / "screener_quality" / f"{day}_{market}_candidates.jsonl"
    for item in _iter_jsonl(path):
        ticker = str(item.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        occurred_at = str(item.get("timestamp") or "")
        call_id = _call_id_for_event(
            store=store,
            call_by_second=call_by_second,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            occurred_at=occurred_at,
            source="screener_quality",
        )
        store.upsert_candidate(
            {
                "call_id": call_id,
                "runtime_mode": runtime_mode,
                "market": market,
                "session_date": session_date,
                "known_at": occurred_at,
                "ticker": ticker,
                "source_file": str(path.relative_to(root)) if path.exists() else "",
                "screener_seen": True,
                "input_to_claude_reported": bool(item.get("input_to_claude")),
                "name": item.get("name", ""),
                "price": item.get("price"),
                "change_pct": item.get("change_rate"),
                "volume_ratio": item.get("volume_ratio"),
                "turnover": item.get("turnover"),
                "market_type": item.get("market_type", ""),
                "liquidity_bucket": item.get("liquidity_bucket", ""),
                "primary_bucket": item.get("primary_bucket", ""),
                "secondary_buckets": item.get("secondary_buckets") or [],
                "payload": {
                    "bucket": item.get("bucket", ""),
                    "bucket_reasons": item.get("bucket_reasons") or {},
                    "status": item.get("status", ""),
                    "reported_input_to_claude": item.get("input_to_claude"),
                    "excluded_reason": item.get("excluded_reason", ""),
                    "screen_quality": item.get("screen_quality", ""),
                },
            }
        )


def _backfill_routes(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    day = session_date.replace("-", "")
    files = [
        root / "logs" / "funnel" / f"action_routing_shadow_{day}_{market}.jsonl",
        root / "logs" / "funnel" / f"candidate_funnel_snapshot_{day}_{market}.jsonl",
    ]
    for path in files:
        for item in _iter_jsonl(path):
            occurred_at = str(item.get("written_at") or "")
            call_id = _call_id_for_event(
                store=store,
                call_by_second=call_by_second,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                occurred_at=occurred_at,
                source="route",
            )
            routes = item.get("routes") or item.get("candidate_action_routes") or []
            if not isinstance(routes, list):
                continue
            for route in routes:
                if not isinstance(route, dict):
                    continue
                ticker = str(route.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                runtime_gate = route.get("runtime_gate") if isinstance(route.get("runtime_gate"), dict) else {}
                store.upsert_candidate(
                    {
                        "call_id": call_id,
                        "runtime_mode": runtime_mode,
                        "market": market,
                        "session_date": session_date,
                        "known_at": occurred_at,
                        "ticker": ticker,
                        "source_file": str(path.relative_to(root)),
                        "claude_action": route.get("original_action") or route.get("requested_action") or "",
                        "route_original_action": route.get("original_action") or route.get("requested_action") or "",
                        "route_final_action": route.get("final_action", ""),
                        "route_route": route.get("route", ""),
                        "route_reason": route.get("reason", ""),
                        "route_demoted_to": route.get("demoted_to", ""),
                        "route_runtime_gate_reason": route.get("runtime_gate_reason", ""),
                        "route_overextended": bool(runtime_gate.get("overextended")),
                        "route_cancel_pathb": bool(route.get("cancel_pathb")),
                        "route_suspend_pathb": bool(route.get("suspend_pathb")),
                        "route_warnings": route.get("warnings") or [],
                        "payload": {"route": route},
                    }
                )


def _connect_existing(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _backfill_decisions(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    conn = _connect_existing(root / "data" / "ml" / "decisions.db")
    if conn is None:
        return
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM decisions
                WHERE session_date=? AND market=?
                ORDER BY ts, id
                """,
                (session_date, market),
            )
        ]
    finally:
        conn.close()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("ticker") or "").strip().upper()].append(row)
    for ticker, items in grouped.items():
        if not ticker:
            continue
        buy_rows = [r for r in items if str(r.get("decision") or "") == "BUY_SIGNAL"]
        filled_rows = [r for r in items if int(r.get("filled") or 0) == 1]
        pnl_rows = [r for r in items if r.get("pnl_pct") is not None]
        last_pnl = pnl_rows[-1] if pnl_rows else {}
        linked_row = last_pnl or (filled_rows[-1] if filled_rows else (buy_rows[-1] if buy_rows else items[-1]))
        values = {
            "decision_count": len(items),
            "buy_signal_count": len(buy_rows),
            "no_signal_count": sum(1 for r in items if str(r.get("decision") or "") == "NO_SIGNAL"),
            "watch_only_count": sum(1 for r in items if str(r.get("block_reason") or "") == "watch_only"),
            "filled_count": len(filled_rows),
            "first_signal_at": buy_rows[0].get("ts") if buy_rows else "",
            "first_fill_at": filled_rows[0].get("ts") if filled_rows else "",
            "entry_price": last_pnl.get("entry_price") if last_pnl else (filled_rows[-1].get("entry_price") if filled_rows else None),
            "exit_price": last_pnl.get("exit_price") if last_pnl else None,
            "pnl_pct": last_pnl.get("pnl_pct") if last_pnl else None,
            "exit_reason": last_pnl.get("exit_reason") if last_pnl else "",
            "strategy_used": last_pnl.get("strategy_used") if last_pnl else (items[-1].get("strategy_used") if items else ""),
            "execution_link_source": "decisions_db",
            "execution_decision_id": str(linked_row.get("id") or ""),
        }
        updated = store.update_execution_by_ticker(
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            ticker=ticker,
            values=values,
            latest_only=True,
        )
        if updated == 0:
            first = items[0]
            _ensure_source_candidate(
                store=store,
                call_by_second=call_by_second,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                occurred_at=str(first.get("ts") or ""),
                source="decisions_db",
                source_file="data/ml/decisions.db",
                fields={
                    "claude_action": first.get("decision") or "",
                    "claude_reason": first.get("block_reason") or "",
                    "price": first.get("price"),
                    "change_pct": first.get("change_pct"),
                    "volume_ratio": first.get("vol_ratio"),
                    "recommended_strategy": first.get("strategy_used") or "",
                },
                payload={
                    "source": "decisions.db",
                    "row_count": len(items),
                    "first_id": first.get("id"),
                    "last_id": items[-1].get("id"),
                },
            )
            store.update_execution_by_ticker(
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                values=values,
                latest_only=True,
            )


def _backfill_selection_log(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    conn = _connect_existing(root / "data" / "ticker_selection_log.db")
    if conn is None:
        return
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM ticker_selection_log
                WHERE date=? AND market=? AND bot_mode=?
                ORDER BY selected_at, id
                """,
                (session_date, market, runtime_mode),
            )
        ]
    finally:
        conn.close()
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        selected_at = str(row.get("selected_at") or row.get("created_at") or "")
        call_id = _call_id_for_event(
            store=store,
            call_by_second=call_by_second,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            occurred_at=selected_at,
            source="selection_log",
        )
        trade_ready = bool(row.get("trade_ready"))
        watchlist_rank = row.get("watchlist_rank")
        store.upsert_candidate(
            {
                "call_id": call_id,
                "runtime_mode": runtime_mode,
                "market": market,
                "session_date": session_date,
                "known_at": selected_at,
                "ticker": ticker,
                "source_file": "data/ticker_selection_log.db",
                "claude_watchlist": watchlist_rank is not None or not trade_ready,
                "claude_trade_ready": trade_ready,
                "claude_reason": row.get("selected_reason", ""),
                "claude_veto_reason": row.get("veto_reason", ""),
                "recommended_strategy": row.get("recommended_strategy", ""),
                "risk_tags": _as_list(row.get("risk_tags")),
                "max_position_pct": row.get("max_position_pct"),
                "payload": {
                    "selection_log_id": row.get("id"),
                    "selection_rank": row.get("selection_rank"),
                    "source_type": row.get("source_type"),
                    "selection_batch_id": row.get("selection_batch_id"),
                    "selected_reason_tag": row.get("selected_reason_tag"),
                    "signal_fired": row.get("signal_fired"),
                    "strategy_name": row.get("strategy_name"),
                    "blocked_reason": row.get("blocked_reason"),
                    "traded": row.get("traded"),
                    "exit_reason": row.get("exit_reason"),
                },
            }
        )


def _backfill_lifecycle(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    conn = _connect_existing(root / "data" / "v2_event_store.db")
    if conn is None:
        return
    try:
        events = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM lifecycle_events
                WHERE session_date=? AND market=? AND runtime_mode=?
                ORDER BY occurred_at, event_id
                """,
                (session_date, market, runtime_mode),
            )
        ]
        path_counts = {
            str(row["ticker"] or "").strip().upper(): int(row["rows"] or 0)
            for row in conn.execute(
                """
                SELECT ticker, COUNT(*) AS rows
                FROM v2_path_runs
                WHERE session_date=? AND market=? AND runtime_mode=?
                GROUP BY ticker
                """,
                (session_date, market, runtime_mode),
            )
        }
    finally:
        conn.close()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get("ticker") or "").strip().upper()].append(event)
    for ticker, items in grouped.items():
        close_events = [e for e in items if str(e.get("event_type") or "").upper() == "CLOSED"]
        fill_events = [e for e in items if str(e.get("event_type") or "").upper() == "FILLED"]
        last = items[-1] if items else {}
        close = close_events[-1] if close_events else {}
        linked_event = close or (fill_events[-1] if fill_events else last)
        first_fill_payload: dict[str, Any] = {}
        if fill_events:
            try:
                first_fill_payload = json.loads(str(fill_events[0].get("payload_json") or "{}"))
            except Exception:
                first_fill_payload = {}
        close_payload: dict[str, Any] = {}
        if close:
            try:
                close_payload = json.loads(str(close.get("payload_json") or "{}"))
            except Exception:
                close_payload = {}
        values: dict[str, Any] = {
            "lifecycle_event_count": len(items),
            "last_lifecycle_event": last.get("event_type", ""),
            "close_reason": close.get("reason_code", ""),
            "path_run_count": path_counts.get(ticker, 0),
            "execution_link_source": "v2_event_store.lifecycle_events",
            "execution_decision_id": str(linked_event.get("decision_id") or ""),
            "execution_event_id": linked_event.get("event_id"),
        }
        if fill_events:
            values.update(
                {
                    "filled_count": 1,
                    "first_fill_at": fill_events[0].get("occurred_at", ""),
                    "entry_price": first_fill_payload.get("fill_price_native") or first_fill_payload.get("price"),
                }
            )
        if close_payload:
            values.update(
                {
                    "exit_price": close_payload.get("price"),
                    "pnl_pct": close_payload.get("pnl_pct"),
                    "exit_reason": close_payload.get("close_reason") or close.get("reason_code", ""),
                }
            )
        updated = store.update_execution_by_ticker(
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            ticker=ticker,
            values=values,
            latest_only=True,
        )
        if updated == 0:
            _ensure_source_candidate(
                store=store,
                call_by_second=call_by_second,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                occurred_at=str(items[0].get("occurred_at") or ""),
                source="lifecycle_db",
                source_file="data/v2_event_store.db:lifecycle_events",
                fields={"claude_action": str(items[0].get("event_type") or "")},
                payload={
                    "source": "lifecycle_events",
                    "event_count": len(items),
                    "first_event_id": items[0].get("event_id"),
                    "last_event_id": items[-1].get("event_id"),
                },
            )
            store.update_execution_by_ticker(
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                values=values,
                latest_only=True,
            )


def _backfill_v2_decisions(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    conn = _connect_existing(root / "data" / "v2_event_store.db")
    if conn is None:
        return
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT * FROM v2_decisions
                WHERE session_date=? AND market=? AND runtime_mode=?
                ORDER BY created_at, decision_id
                """,
                (session_date, market, runtime_mode),
            )
        ]
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        payload: dict[str, Any] = {}
        try:
            payload = json.loads(str(row.get("payload_json") or "{}"))
        except Exception:
            payload = {}
        _ensure_source_candidate(
            store=store,
            call_by_second=call_by_second,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            ticker=ticker,
            occurred_at=str(row.get("created_at") or ""),
            source="v2_decisions_db",
            source_file="data/v2_event_store.db:v2_decisions",
            fields={
                "claude_action": row.get("status") or "",
                "recommended_strategy": row.get("strategy_hint") or "",
            },
            payload={
                "source": "v2_decisions",
                "decision_id": row.get("decision_id"),
                "strategy_hint": row.get("strategy_hint"),
                "timing_style": row.get("timing_style"),
                "status": row.get("status"),
                "payload": payload,
            },
        )


def _backfill_intraday(
    *,
    root: Path,
    store: CandidateAuditStore,
    session_date: str,
    market: str,
    runtime_mode: str,
    call_by_second: dict[str, str],
) -> None:
    conn = _connect_existing(root / "data" / "intraday_strategy_log.db")
    if conn is None:
        return
    try:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT ticker,
                       SUM(CASE WHEN signal_fired=1 THEN 1 ELSE 0 END) AS signals,
                       SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) AS traded
                FROM intraday_strategy_log
                WHERE session_date=? AND market=? AND bot_mode=?
                GROUP BY ticker
                """,
                (session_date, market, runtime_mode),
            )
        ]
    finally:
        conn.close()
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        if not ticker:
            continue
        updated = store.update_execution_by_ticker(
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
            ticker=ticker,
            values={
                "intraday_signal_count": int(row.get("signals") or 0),
                "intraday_traded_count": int(row.get("traded") or 0),
            },
            latest_only=True,
        )
        if updated == 0:
            _ensure_source_candidate(
                store=store,
                call_by_second=call_by_second,
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                occurred_at=session_date,
                source="intraday_db",
                source_file="data/intraday_strategy_log.db",
                fields={"claude_action": "INTRADAY_OBSERVED"},
                payload={
                    "source": "intraday_strategy_log",
                    "signals": int(row.get("signals") or 0),
                    "traded": int(row.get("traded") or 0),
                },
            )
            store.update_execution_by_ticker(
                session_date=session_date,
                market=market,
                runtime_mode=runtime_mode,
                ticker=ticker,
                values={
                    "intraday_signal_count": int(row.get("signals") or 0),
                    "intraday_traded_count": int(row.get("traded") or 0),
                },
                latest_only=True,
            )


def backfill_candidate_audit(
    *,
    root: Path = ROOT,
    db_path: Path | None = None,
    session_date: str,
    market: str,
    runtime_mode: str = "live",
    reset_session: bool = True,
) -> dict[str, Any]:
    market_key = str(market or "").upper()
    mode = str(runtime_mode or "live").lower()
    target = db_path or get_runtime_path("data", "audit", "candidate_audit.db")
    store = CandidateAuditStore(target)
    if reset_session:
        store.clear_session(session_date=session_date, market=market_key, runtime_mode=mode)
    call_by_second = _backfill_raw_calls(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
    )
    _backfill_screener_quality(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_routes(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_selection_log(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_decisions(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_v2_decisions(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_lifecycle(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    _backfill_intraday(
        root=root,
        store=store,
        session_date=session_date,
        market=market_key,
        runtime_mode=mode,
        call_by_second=call_by_second,
    )
    changed = store.refresh_classifications(session_date=session_date, market=market_key, runtime_mode=mode)
    summary = store.summary(session_date=session_date, market=market_key, runtime_mode=mode)
    summary["classification_rows_refreshed"] = changed
    try:
        from tools.analyze_candidate_audit import watch_trigger_funnel_summary

        summary["watch_trigger_shadow_summary"] = watch_trigger_funnel_summary(
            session_date=session_date,
            market=market_key,
        )
    except Exception:
        summary["watch_trigger_shadow_summary"] = {}
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill analysis-only candidate audit DB from existing DBs/logs.")
    parser.add_argument("--date", required=True, help="session date YYYY-MM-DD")
    parser.add_argument("--market", default="KR", choices=["KR", "US"])
    parser.add_argument("--mode", default="live", choices=["live", "paper"])
    parser.add_argument("--db", default="", help="output DB path; defaults to data/audit/candidate_audit.db")
    parser.add_argument("--no-reset", action="store_true", help="do not clear the target session before backfill")
    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None
    summary = backfill_candidate_audit(
        root=ROOT,
        db_path=db_path,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.mode,
        reset_session=not args.no_reset,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
