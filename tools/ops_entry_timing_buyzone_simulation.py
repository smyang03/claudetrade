from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ops_us_high_price_simulation import (
    DEFAULT_EVENT_DB,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRICE_ROOT,
    KST,
    _as_float,
    _connect_ro,
    _json_obj,
    _output_dir,
    _parse_dt,
    _pct,
    _stats,
)

DEFAULT_CANDIDATE_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DELAY_MINUTES = (0, 5, 10, 15, 30, 60)
WAIT_PATHS = ("wait_30m", "wait_60m", "vwap_reclaim", "or_break", "pullback_reclaim", "volume_surge")


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _sum_stats(values: list[float]) -> dict[str, Any]:
    vals = [float(value) for value in values if value is not None]
    return {"sum": round(sum(vals), 4), **_stats(vals)}


def _first_number(mapping: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            value = _as_float(mapping[key])
            if value != 0.0 or str(mapping[key]).strip() in {"0", "0.0"}:
                return value
    return 0.0


def _session_open(market: str, reference: datetime) -> datetime:
    ref = reference.astimezone(KST)
    if str(market or "").upper() == "KR":
        return datetime.combine(ref.date(), time(9, 0), tzinfo=KST)
    if ref.time() <= time(5, 0):
        return datetime.combine(ref.date() - timedelta(days=1), time(22, 30), tzinfo=KST)
    return datetime.combine(ref.date(), time(22, 30), tzinfo=KST)


def _session_end(market: str, reference: datetime) -> datetime:
    opened = _session_open(market, reference)
    if str(market or "").upper() == "KR":
        return datetime.combine(opened.date(), time(15, 30), tzinfo=KST)
    return datetime.combine(opened.date() + timedelta(days=1), time(5, 0), tzinfo=KST)


def _price_file(price_root: Path, market: str, ticker: str) -> Path | None:
    market_key = str(market or "").lower()
    ticker_key = str(ticker or "").upper() if market_key == "us" else str(ticker or "")
    for candidate in (
        price_root / "minute" / market_key / f"{market_key}_{ticker_key}.csv",
        price_root / market_key / f"{market_key}_{ticker_key}.csv",
    ):
        if candidate.exists():
            return candidate
    return None


def _read_prices(
    *,
    price_root: Path,
    market: str,
    ticker: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    source = _price_file(price_root, market, ticker)
    if source is None:
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as fp:
        for raw in csv.DictReader(fp):
            ts = raw.get("ts") or raw.get("datetime") or raw.get("date")
            dt = _parse_dt(ts)
            if dt is None or dt < start_at or dt > end_at:
                continue
            close = _as_float(raw.get("close") or raw.get("price") or raw.get("last"))
            if close <= 0:
                continue
            rows.append(
                {
                    "dt": dt,
                    "ts": str(ts),
                    "open": _as_float(raw.get("open"), close),
                    "high": _as_float(raw.get("high"), close),
                    "low": _as_float(raw.get("low"), close),
                    "close": close,
                }
            )
    return rows


def _first_at_or_after(rows: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    for row in rows:
        if row["dt"] >= at:
            return row
    return None


def _path_matches(payload: dict[str, Any], path_run_id: str) -> bool:
    sizing = payload.get("pathb_sizing") if isinstance(payload.get("pathb_sizing"), dict) else {}
    return str(payload.get("path_run_id") or sizing.get("path_run_id") or "") == str(path_run_id)


def _path_events(conn: sqlite3.Connection, path_run_id: str, decision_id: str = "") -> list[sqlite3.Row]:
    if decision_id:
        return list(
            conn.execute(
                """
                select event_id, event_type, market, runtime_mode, session_date, ticker,
                       decision_id, occurred_at, reason_code, payload_json
                from lifecycle_events
                where payload_json like ?
                   or decision_id = ?
                order by occurred_at, event_id
                """,
                (f"%{path_run_id}%", decision_id),
            )
        )
    return list(
        conn.execute(
            """
            select event_id, event_type, market, runtime_mode, session_date, ticker,
                   decision_id, occurred_at, reason_code, payload_json
            from lifecycle_events
            where payload_json like ?
            order by occurred_at, event_id
            """,
            (f"%{path_run_id}%",),
        )
    )


def _extract_closed_trade(run: sqlite3.Row, events: list[sqlite3.Row]) -> dict[str, Any] | None:
    path_run_id = str(run["path_run_id"])
    plan = _json_obj(run["plan_json"])
    hit: dict[str, Any] | None = None
    first_order: dict[str, Any] | None = None
    first_fill: dict[str, Any] | None = None
    last_close: dict[str, Any] | None = None

    for event in events:
        payload = _json_obj(event["payload_json"])
        if not _path_matches(payload, path_run_id):
            continue
        event_type = str(event["event_type"] or "")
        side = str(payload.get("side") or "").lower()
        if event_type == "CLAUDE_PRICE_HIT" and hit is None:
            hit = {
                "dt": _parse_dt(event["occurred_at"]),
                "price": _first_number(payload, ("price", "hit_price", "current_price")),
            }
        elif event_type == "ORDER_SENT" and first_order is None and side in {"", "buy"}:
            first_order = {
                "dt": _parse_dt(event["occurred_at"]),
                "price": _first_number(payload, ("price", "order_price", "limit_price")),
            }
        elif event_type == "FILLED" and first_fill is None and side in {"", "buy"}:
            fill_price = _first_number(payload, ("price", "fill_price", "filled_price", "actual_entry_price"))
            if fill_price > 0:
                first_fill = {"dt": _parse_dt(event["occurred_at"]), "price": fill_price}
        elif event_type == "CLOSED":
            close_price = _first_number(payload, ("price", "exit_price", "filled_price", "actual_exit_price"))
            close_pnl = _first_number(payload, ("pnl_pct", "realized_pnl_pct"))
            last_close = {
                "dt": _parse_dt(event["occurred_at"]),
                "price": close_price,
                "pnl_pct": close_pnl,
                "reason": str(event["reason_code"] or payload.get("close_reason") or payload.get("reason") or ""),
            }

    entry_dt = (first_fill or {}).get("dt") or _parse_dt(plan.get("entry_filled_at") or plan.get("filled_at"))
    entry_price = (first_fill or {}).get("price") or _as_float(plan.get("actual_entry_price"))
    if entry_price <= 0 and first_order is not None:
        entry_price = _as_float(first_order.get("price"))

    close_dt = (last_close or {}).get("dt") or _parse_dt(plan.get("closed_at") or plan.get("sell_order_sent_at"))
    close_price = (last_close or {}).get("price") or _as_float(plan.get("actual_exit_price"))
    actual_pnl = (last_close or {}).get("pnl_pct")
    if actual_pnl in (None, 0.0) and plan.get("pnl_pct") not in (None, ""):
        actual_pnl = _as_float(plan.get("pnl_pct"))
    if close_price <= 0 and entry_price > 0 and actual_pnl not in (None, 0.0):
        close_price = entry_price * (1.0 + float(actual_pnl) / 100.0)
    if (actual_pnl in (None, 0.0)) and entry_price > 0 and close_price > 0:
        actual_pnl = _pct(entry_price, close_price)

    reference_dt = (hit or {}).get("dt") or (first_order or {}).get("dt") or entry_dt
    if entry_dt is None or close_dt is None or reference_dt is None or entry_price <= 0 or close_price <= 0:
        return None

    buy_zone_low = _as_float(plan.get("buy_zone_low"))
    buy_zone_high = _as_float(plan.get("buy_zone_high"))
    reference_price = _as_float(plan.get("reference_price")) or (hit or {}).get("price") or entry_price
    width_pct = 0.0
    zone_position: float | None = None
    if buy_zone_high > buy_zone_low > 0:
        width_pct = (buy_zone_high - buy_zone_low) / max(reference_price, buy_zone_high) * 100.0
        zone_position = (entry_price - buy_zone_low) / (buy_zone_high - buy_zone_low)

    return {
        "path_run_id": path_run_id,
        "decision_id": str(run["decision_id"] or ""),
        "market": str(run["market"] or "").upper(),
        "session_date": str(run["session_date"] or ""),
        "ticker": str(run["ticker"] or ""),
        "status": str(run["status"] or ""),
        "created_dt": _parse_dt(run["created_at"]) or reference_dt,
        "origin_action": str(plan.get("origin_action") or ""),
        "origin_reason": str(plan.get("origin_reason") or ""),
        "confidence": _as_float(plan.get("confidence")),
        "close_reason": (last_close or {}).get("reason") or str(plan.get("close_reason") or ""),
        "reference_dt": reference_dt,
        "entry_dt": entry_dt,
        "close_dt": close_dt,
        "entry_price": float(entry_price),
        "close_price": float(close_price),
        "actual_pnl_pct": float(actual_pnl or 0.0),
        "buy_zone_low": buy_zone_low,
        "buy_zone_high": buy_zone_high,
        "zone_width_pct": float(width_pct),
        "zone_position": zone_position,
        "zone_bucket": _zone_bucket(zone_position),
    }


def _zone_bucket(position: float | None) -> str:
    if position is None:
        return "unknown"
    if position < 0:
        return "below_zone"
    if position <= 0.25:
        return "lower_0_25"
    if position <= 0.50:
        return "middle_25_50"
    if position <= 0.75:
        return "middle_50_75"
    if position <= 1.0:
        return "upper_75_100"
    return "above_zone"


def _confidence_bucket(confidence: float) -> str:
    if confidence >= 0.7:
        return "conf_ge_0_70"
    if confidence >= 0.6:
        return "conf_0_60_0_69"
    if confidence >= 0.5:
        return "conf_0_50_0_59"
    return "conf_lt_0_50"


def _closed_trades(event_conn: sqlite3.Connection, *, limit: int = 0) -> list[dict[str, Any]]:
    sql = """
        select path_run_id, decision_id, path_type, market, runtime_mode, session_date,
               ticker, status, plan_json, created_at, updated_at
        from v2_path_runs
        where runtime_mode='live'
          and path_type='claude_price'
          and upper(status) like 'CLOSED%'
        order by created_at, path_run_id
    """
    if limit > 0:
        sql += " limit ?"
        rows = event_conn.execute(sql, (limit,)).fetchall()
    else:
        rows = event_conn.execute(sql).fetchall()
    trades: list[dict[str, Any]] = []
    for run in rows:
        events = _path_events(event_conn, str(run["path_run_id"]), str(run["decision_id"] or ""))
        trade = _extract_closed_trade(run, events)
        if trade is not None:
            trades.append(trade)
    return trades


def _group_stats(rows: list[dict[str, Any]], key_fn: Callable[[dict[str, Any]], str]) -> dict[str, Any]:
    groups: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        groups[key_fn(row)].append(float(row["actual_pnl_pct"]))
    return {key: _sum_stats(values) for key, values in sorted(groups.items())}


def _policy_eval(
    rows: list[dict[str, Any]],
    predicate: Callable[[dict[str, Any]], bool],
) -> dict[str, Any]:
    kept = [row for row in rows if predicate(row)]
    removed = [row for row in rows if not predicate(row)]
    baseline = _sum_stats([row["actual_pnl_pct"] for row in rows])
    kept_stats = _sum_stats([row["actual_pnl_pct"] for row in kept])
    removed_stats = _sum_stats([row["actual_pnl_pct"] for row in removed])
    removed_positive = [row for row in removed if row["actual_pnl_pct"] > 0]
    removed_negative = [row for row in removed if row["actual_pnl_pct"] < 0]
    return {
        "kept": kept_stats,
        "removed": removed_stats,
        "delta_avg_vs_base": round(kept_stats["avg"] - baseline["avg"], 4) if kept else 0.0,
        "removed_positive_count": len(removed_positive),
        "removed_negative_count": len(removed_negative),
        "opportunity_cost_proxy": round(sum(row["actual_pnl_pct"] for row in removed_positive), 4),
        "loss_avoided_proxy": round(abs(sum(row["actual_pnl_pct"] for row in removed_negative)), 4),
        "dropped_examples": [
            {
                "market": row["market"],
                "session_date": row["session_date"],
                "ticker": row["ticker"],
                "pnl_pct": round(row["actual_pnl_pct"], 4),
                "zone_position": None if row["zone_position"] is None else round(float(row["zone_position"]), 4),
                "zone_width_pct": round(row["zone_width_pct"], 4),
                "close_reason": row["close_reason"],
            }
            for row in sorted(removed, key=lambda item: item["actual_pnl_pct"])[:12]
        ],
    }


def _zone_policy_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    policies: dict[str, Callable[[dict[str, Any]], bool]] = {
        "inside_zone_only": lambda row: row["zone_position"] is not None and 0.0 <= row["zone_position"] <= 1.0,
        "block_upper_25pct_of_zone": lambda row: row["zone_position"] is not None and row["zone_position"] <= 0.75,
        "lower_half_only": lambda row: row["zone_position"] is not None and row["zone_position"] <= 0.50,
        "lower_quarter_only": lambda row: row["zone_position"] is not None and row["zone_position"] <= 0.25,
        "zone_width_lte_2pct": lambda row: row["zone_width_pct"] > 0 and row["zone_width_pct"] <= 2.0,
        "zone_width_lte_3pct": lambda row: row["zone_width_pct"] > 0 and row["zone_width_pct"] <= 3.0,
        "zone_width_lte_4pct": lambda row: row["zone_width_pct"] > 0 and row["zone_width_pct"] <= 4.0,
    }
    by_market: dict[str, Any] = {}
    for market in sorted({row["market"] for row in trades}):
        market_rows = [row for row in trades if row["market"] == market]
        by_market[market] = {
            "baseline": _sum_stats([row["actual_pnl_pct"] for row in market_rows]),
            "policies": [
                {"policy": name, **_policy_eval(market_rows, predicate)} for name, predicate in policies.items()
            ],
            "by_zone_bucket": _group_stats(market_rows, lambda row: row["zone_bucket"]),
            "by_width_bucket": _group_stats(market_rows, _zone_width_bucket),
        }
    return by_market


def _zone_width_bucket(row: dict[str, Any]) -> str:
    width = float(row.get("zone_width_pct") or 0.0)
    if width <= 0:
        return "unknown"
    if width <= 2.0:
        return "width_lte_2pct"
    if width <= 3.0:
        return "width_2_3pct"
    if width <= 4.0:
        return "width_3_4pct"
    return "width_gt_4pct"


def _delay_replay(
    trades: list[dict[str, Any]],
    *,
    price_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    replay_rows: list[dict[str, Any]] = []
    skipped: defaultdict[str, int] = defaultdict(int)

    for trade in trades:
        market = trade["market"]
        replay_rows.append(
            {
                "market": market,
                "session_date": trade["session_date"],
                "ticker": trade["ticker"],
                "path_run_id": trade["path_run_id"],
                "delay_min": 0,
                "reference_at": trade["entry_dt"].isoformat(),
                "simulated_entry_at": trade["entry_dt"].isoformat(),
                "simulated_entry_price": round(trade["entry_price"], 6),
                "actual_entry_price": round(trade["entry_price"], 6),
                "close_price": round(trade["close_price"], 6),
                "actual_pnl_pct": round(trade["actual_pnl_pct"], 6),
                "simulated_pnl_pct": round(trade["actual_pnl_pct"], 6),
                "delta_vs_actual_pct": 0.0,
                "zone_bucket": trade["zone_bucket"],
                "zone_width_pct": round(trade["zone_width_pct"], 6),
                "close_reason": trade["close_reason"],
                "confidence_bucket": _confidence_bucket(trade["confidence"]),
                "origin_action": trade["origin_action"],
            }
        )

        start_at = _session_open(market, trade["entry_dt"])
        end_at = trade["close_dt"] if trade["close_dt"] is not None else _session_end(market, trade["reference_dt"])
        prices = _read_prices(
            price_root=price_root,
            market=market,
            ticker=trade["ticker"],
            start_at=start_at,
            end_at=end_at,
        )
        if not prices:
            skipped[f"{market}:missing_or_daily_price_tape"] += 1
            continue
        for delay_min in DELAY_MINUTES:
            if delay_min == 0:
                continue
            target_at = trade["entry_dt"] + timedelta(minutes=delay_min)
            if trade["close_dt"] is not None and target_at >= trade["close_dt"]:
                skipped[f"{market}:delay_after_close"] += 1
                continue
            row = _first_at_or_after(prices, target_at)
            if row is None:
                skipped[f"{market}:no_price_after_delay"] += 1
                continue
            simulated_pnl = _pct(float(row["close"]), trade["close_price"])
            replay_rows.append(
                {
                    "market": market,
                    "session_date": trade["session_date"],
                    "ticker": trade["ticker"],
                    "path_run_id": trade["path_run_id"],
                    "delay_min": delay_min,
                    "reference_at": trade["entry_dt"].isoformat(),
                    "simulated_entry_at": row["dt"].isoformat(),
                    "simulated_entry_price": round(float(row["close"]), 6),
                    "actual_entry_price": round(trade["entry_price"], 6),
                    "close_price": round(trade["close_price"], 6),
                    "actual_pnl_pct": round(trade["actual_pnl_pct"], 6),
                    "simulated_pnl_pct": round(simulated_pnl, 6),
                    "delta_vs_actual_pct": round(simulated_pnl - trade["actual_pnl_pct"], 6),
                    "zone_bucket": trade["zone_bucket"],
                    "zone_width_pct": round(trade["zone_width_pct"], 6),
                    "close_reason": trade["close_reason"],
                    "confidence_bucket": _confidence_bucket(trade["confidence"]),
                    "origin_action": trade["origin_action"],
                }
            )

    summary: dict[str, Any] = {"skipped": dict(sorted(skipped.items())), "by_market_delay": {}}
    for market in sorted({row["market"] for row in replay_rows}):
        market_rows = [row for row in replay_rows if row["market"] == market]
        by_delay: dict[str, Any] = {}
        for delay_min in DELAY_MINUTES:
            delay_rows = [row for row in market_rows if row["delay_min"] == delay_min]
            deltas = [row["delta_vs_actual_pct"] for row in delay_rows]
            by_delay[str(delay_min)] = {
                "actual": _stats([row["actual_pnl_pct"] for row in delay_rows]),
                "simulated": _stats([row["simulated_pnl_pct"] for row in delay_rows]),
                "delta": _stats(deltas),
                "improved_count": sum(1 for value in deltas if value > 0),
                "harmed_count": sum(1 for value in deltas if value < 0),
            }
        summary["by_market_delay"][market] = by_delay

    summary["best_delay_by_market"] = {}
    summary["best_positive_delay_by_market"] = {}
    for market, delay_map in summary["by_market_delay"].items():
        best_name, best_payload = max(
            delay_map.items(),
            key=lambda item: (item[1]["delta"]["avg"], item[1]["simulated"]["avg"], item[1]["simulated"]["count"]),
        )
        summary["best_delay_by_market"][market] = {"delay_min": int(best_name), **best_payload}
        positive_candidates = {name: item for name, item in delay_map.items() if int(name) > 0 and item["simulated"]["count"] > 0}
        if positive_candidates:
            best_pos_name, best_pos_payload = max(
                positive_candidates.items(),
                key=lambda item: (item[1]["delta"]["avg"], item[1]["simulated"]["avg"], item[1]["simulated"]["count"]),
            )
            summary["best_positive_delay_by_market"][market] = {"delay_min": int(best_pos_name), **best_pos_payload}
    return summary, replay_rows


def _first_zone_touch(rows: list[dict[str, Any]], *, low: float, high: float) -> dict[str, Any] | None:
    if low <= 0 or high <= low:
        return None
    for row in rows:
        row_high = float(row.get("high") or row["close"])
        row_low = float(row.get("low") or row["close"])
        if row_low <= high and row_high >= low:
            close = float(row["close"])
            entry_price = min(high, max(low, close))
            out = dict(row)
            out["simulated_entry_price"] = entry_price
            return out
    return None


def _early_zone_entry_replay(
    trades: list[dict[str, Any]],
    *,
    price_root: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows_out: list[dict[str, Any]] = []
    skipped: defaultdict[str, int] = defaultdict(int)
    for trade in trades:
        if trade["buy_zone_low"] <= 0 or trade["buy_zone_high"] <= trade["buy_zone_low"]:
            skipped[f"{trade['market']}:missing_buy_zone"] += 1
            continue
        start_at = max(_session_open(trade["market"], trade["entry_dt"]), trade["created_dt"])
        end_at = trade["entry_dt"]
        if start_at >= end_at:
            skipped[f"{trade['market']}:created_after_or_at_entry"] += 1
            continue
        prices = _read_prices(
            price_root=price_root,
            market=trade["market"],
            ticker=trade["ticker"],
            start_at=start_at,
            end_at=end_at,
        )
        if not prices:
            skipped[f"{trade['market']}:missing_or_daily_price_tape"] += 1
            continue
        touch = _first_zone_touch(prices, low=trade["buy_zone_low"], high=trade["buy_zone_high"])
        if touch is None:
            skipped[f"{trade['market']}:no_prior_zone_touch"] += 1
            continue
        simulated_entry_price = float(touch["simulated_entry_price"])
        simulated_pnl = _pct(simulated_entry_price, trade["close_price"])
        lag_min = (trade["entry_dt"] - touch["dt"]).total_seconds() / 60.0
        rows_out.append(
            {
                "market": trade["market"],
                "session_date": trade["session_date"],
                "ticker": trade["ticker"],
                "path_run_id": trade["path_run_id"],
                "created_at": trade["created_dt"].isoformat(),
                "actual_entry_at": trade["entry_dt"].isoformat(),
                "early_entry_at": touch["dt"].isoformat(),
                "entry_lag_min": round(lag_min, 4),
                "early_entry_price": round(simulated_entry_price, 6),
                "actual_entry_price": round(trade["entry_price"], 6),
                "close_price": round(trade["close_price"], 6),
                "actual_pnl_pct": round(trade["actual_pnl_pct"], 6),
                "simulated_pnl_pct": round(simulated_pnl, 6),
                "delta_vs_actual_pct": round(simulated_pnl - trade["actual_pnl_pct"], 6),
                "zone_bucket": trade["zone_bucket"],
                "zone_width_pct": round(trade["zone_width_pct"], 6),
                "close_reason": trade["close_reason"],
                "origin_action": trade["origin_action"],
            }
        )

    summary: dict[str, Any] = {"skipped": dict(sorted(skipped.items())), "by_market": {}}
    for market in sorted({row["market"] for row in rows_out}):
        market_rows = [row for row in rows_out if row["market"] == market]
        deltas = [row["delta_vs_actual_pct"] for row in market_rows]
        lags = [row["entry_lag_min"] for row in market_rows]
        summary["by_market"][market] = {
            "actual": _stats([row["actual_pnl_pct"] for row in market_rows]),
            "simulated": _stats([row["simulated_pnl_pct"] for row in market_rows]),
            "delta": _stats(deltas),
            "improved_count": sum(1 for value in deltas if value > 0),
            "harmed_count": sum(1 for value in deltas if value < 0),
            "entry_lag_min": {
                "avg": round(sum(lags) / len(lags), 4) if lags else 0.0,
                "median": round(float(median(lags)), 4) if lags else 0.0,
                "best": round(max(lags), 4) if lags else 0.0,
                "worst": round(min(lags), 4) if lags else 0.0,
            },
        }
    return summary, rows_out


def _counterfactual_wait_summary(candidate_conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = candidate_conn.execute(
        """
        select market, ticker, candidate_key, call_id, signal_time, trade_ready_action,
               path_name, status, outcome_close_pct, outcome_60m_pct,
               max_runup_60m_pct, max_drawdown_60m_pct
        from candidate_counterfactual_paths
        where runtime_mode='live'
          and status='CLOSE_OUTCOME_FILLED'
          and path_name in ('immediate', 'wait_30m', 'wait_60m', 'vwap_reclaim',
                            'or_break', 'pullback_reclaim', 'volume_surge')
        """
    ).fetchall()
    grouped: defaultdict[tuple[str, str, str, str, str, str], dict[str, sqlite3.Row]] = defaultdict(dict)
    for row in rows:
        key = (
            str(row["market"] or "").upper(),
            str(row["ticker"] or ""),
            str(row["candidate_key"] or ""),
            str(row["call_id"] or ""),
            str(row["signal_time"] or ""),
            str(row["trade_ready_action"] or ""),
        )
        grouped[key][str(row["path_name"] or "")] = row

    pair_rows: list[dict[str, Any]] = []
    for key, paths in grouped.items():
        immediate = paths.get("immediate")
        if immediate is None:
            continue
        immediate_close = _as_float(immediate["outcome_close_pct"])
        for path_name in WAIT_PATHS:
            alt = paths.get(path_name)
            if alt is None:
                continue
            alt_close = _as_float(alt["outcome_close_pct"])
            pair_rows.append(
                {
                    "market": key[0],
                    "ticker": key[1],
                    "candidate_key": key[2],
                    "call_id": key[3],
                    "signal_time": key[4],
                    "trade_ready_action": key[5],
                    "path_name": path_name,
                    "immediate_close_pct": round(immediate_close, 6),
                    "alternative_close_pct": round(alt_close, 6),
                    "delta_vs_immediate_pct": round(alt_close - immediate_close, 6),
                    "alternative_60m_pct": None
                    if alt["outcome_60m_pct"] is None
                    else round(_as_float(alt["outcome_60m_pct"]), 6),
                    "max_runup_60m_pct": None
                    if alt["max_runup_60m_pct"] is None
                    else round(_as_float(alt["max_runup_60m_pct"]), 6),
                    "max_drawdown_60m_pct": None
                    if alt["max_drawdown_60m_pct"] is None
                    else round(_as_float(alt["max_drawdown_60m_pct"]), 6),
                }
            )

    summary: dict[str, Any] = {"pair_count": len(pair_rows), "by_market_action_path": {}}
    groups2: defaultdict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        groups2[(row["market"], row["trade_ready_action"], row["path_name"])].append(row)
    for (market, action, path_name), group in sorted(groups2.items()):
        market_payload = summary["by_market_action_path"].setdefault(market, {})
        action_payload = market_payload.setdefault(action or "UNKNOWN", {})
        deltas = [row["delta_vs_immediate_pct"] for row in group]
        action_payload[path_name] = {
            "immediate": _stats([row["immediate_close_pct"] for row in group]),
            "alternative": _stats([row["alternative_close_pct"] for row in group]),
            "delta": _stats(deltas),
            "improved_count": sum(1 for value in deltas if value > 0),
            "harmed_count": sum(1 for value in deltas if value < 0),
        }

    summary["best_by_market_action"] = {}
    summary["best_wait_only_by_market_action"] = {}
    for market, actions in summary["by_market_action_path"].items():
        summary["best_by_market_action"][market] = {}
        summary["best_wait_only_by_market_action"][market] = {}
        for action, path_map in actions.items():
            best_name, best_payload = max(
                path_map.items(),
                key=lambda item: (item[1]["delta"]["avg"], item[1]["alternative"]["avg"], item[1]["alternative"]["count"]),
            )
            summary["best_by_market_action"][market][action] = {"path_name": best_name, **best_payload}
            wait_map = {name: item for name, item in path_map.items() if name in {"wait_30m", "wait_60m"}}
            if wait_map:
                best_wait_name, best_wait_payload = max(
                    wait_map.items(),
                    key=lambda item: (item[1]["delta"]["avg"], item[1]["alternative"]["avg"], item[1]["alternative"]["count"]),
                )
                summary["best_wait_only_by_market_action"][market][action] = {
                    "path_name": best_wait_name,
                    **best_wait_payload,
                }
    return summary, pair_rows


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# Entry Timing and Buy-Zone Simulation",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        f"- closed_trade_count: {payload['summary']['closed_trade_count']}",
        "",
        "## Delay Replay",
    ]
    delay = payload["summary"]["delay_replay"]
    for market, best in sorted(delay.get("best_delay_by_market", {}).items()):
        lines.append(
            f"- {market}: best_delay={best['delay_min']}m, "
            f"sim_avg={best['simulated']['avg']}%, delta_avg={best['delta']['avg']}%, "
            f"count={best['simulated']['count']}"
        )
    for market, best in sorted(delay.get("best_positive_delay_by_market", {}).items()):
        lines.append(
            f"- {market}: best_positive_delay={best['delay_min']}m, "
            f"sim_avg={best['simulated']['avg']}%, delta_avg={best['delta']['avg']}%, "
            f"count={best['simulated']['count']}"
        )
    if delay.get("skipped"):
        lines.append(f"- skipped: {json.dumps(delay['skipped'], ensure_ascii=False, sort_keys=True)}")

    lines.extend(["", "## Early Zone Entry"])
    early = payload["summary"]["early_zone_entry"]
    for market, item in sorted(early.get("by_market", {}).items()):
        lines.append(
            f"- {market}: count={item['simulated']['count']}, "
            f"sim_avg={item['simulated']['avg']}%, delta_avg={item['delta']['avg']}%, "
            f"improved={item['improved_count']}, harmed={item['harmed_count']}, "
            f"median_lag_min={item['entry_lag_min']['median']}"
        )
    if early.get("skipped"):
        lines.append(f"- skipped: {json.dumps(early['skipped'], ensure_ascii=False, sort_keys=True)}")

    lines.extend(["", "## Buy-Zone Policies"])
    for market, market_payload in sorted(payload["summary"]["zone_policy"].items()):
        base = market_payload["baseline"]
        lines.append(f"- {market} baseline: count={base['count']}, avg={base['avg']}%, win={base['win_rate_pct']}%")
        for policy in market_payload["policies"]:
            lines.append(
                f"  - {policy['policy']}: kept={policy['kept']['count']}, "
                f"kept_avg={policy['kept']['avg']}%, removed_pos={policy['removed_positive_count']}, "
                f"removed_neg={policy['removed_negative_count']}, "
                f"opp_cost={policy['opportunity_cost_proxy']}%, loss_avoided={policy['loss_avoided_proxy']}%"
            )

    lines.extend(["", "## Counterfactual Wait"])
    wait = payload["summary"]["counterfactual_wait"]
    lines.append(f"- pair_count: {wait['pair_count']}")
    for market, actions in sorted(wait.get("best_wait_only_by_market_action", {}).items()):
        for action, best in sorted(actions.items()):
            lines.append(
                f"- {market}/{action}: best_wait={best['path_name']}, "
                f"alt_avg={best['alternative']['avg']}%, delta_avg={best['delta']['avg']}%, "
                f"count={best['alternative']['count']}"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_entry_timing_buyzone_simulation(
    *,
    event_db: Path = DEFAULT_EVENT_DB,
    candidate_db: Path = DEFAULT_CANDIDATE_DB,
    price_root: Path = DEFAULT_PRICE_ROOT,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    output_dir: str = "",
    limit: int = 0,
) -> dict[str, Any]:
    out_dir = _output_dir(output_root, output_dir or f"entry_timing_buyzone_{_now_stamp()}")
    with _connect_ro(event_db) as event_conn, _connect_ro(candidate_db) as candidate_conn:
        trades = _closed_trades(event_conn, limit=limit)
        delay_summary, delay_rows = _delay_replay(trades, price_root=price_root)
        early_summary, early_rows = _early_zone_entry_replay(trades, price_root=price_root)
        wait_summary, wait_rows = _counterfactual_wait_summary(candidate_conn)

    trade_rows = [
        {
            "market": row["market"],
            "session_date": row["session_date"],
            "ticker": row["ticker"],
            "path_run_id": row["path_run_id"],
            "origin_action": row["origin_action"],
            "confidence": round(row["confidence"], 4),
            "close_reason": row["close_reason"],
            "entry_at": row["entry_dt"].isoformat(),
            "close_at": row["close_dt"].isoformat(),
            "entry_price": round(row["entry_price"], 6),
            "close_price": round(row["close_price"], 6),
            "actual_pnl_pct": round(row["actual_pnl_pct"], 6),
            "buy_zone_low": row["buy_zone_low"],
            "buy_zone_high": row["buy_zone_high"],
            "zone_width_pct": round(row["zone_width_pct"], 6),
            "zone_position": "" if row["zone_position"] is None else round(float(row["zone_position"]), 6),
            "zone_bucket": row["zone_bucket"],
        }
        for row in trades
    ]

    payload = {
        "generated_at": _now_text(),
        "live_writes_performed": False,
        "inputs": {
            "event_db": str(Path(event_db).resolve()),
            "candidate_db": str(Path(candidate_db).resolve()),
            "price_root": str(Path(price_root).resolve()),
            "output_dir": str(out_dir.resolve()),
            "limit": limit,
        },
        "summary": {
            "closed_trade_count": len(trades),
            "closed_trade_baseline": _sum_stats([row["actual_pnl_pct"] for row in trades]),
            "by_market": _group_stats(trades, lambda row: row["market"]),
            "by_close_reason": _group_stats(trades, lambda row: row["close_reason"] or "UNKNOWN"),
            "by_confidence_bucket": _group_stats(trades, lambda row: _confidence_bucket(row["confidence"])),
            "zone_policy": _zone_policy_summary(trades),
            "delay_replay": delay_summary,
            "early_zone_entry": early_summary,
            "counterfactual_wait": wait_summary,
        },
        "output_paths": {},
    }

    summary_path = out_dir / "summary.json"
    trades_path = out_dir / "closed_trades.csv"
    delay_path = out_dir / "delay_replay.csv"
    early_path = out_dir / "early_zone_entry_replay.csv"
    wait_path = out_dir / "counterfactual_wait_pairs.csv"
    report_path = out_dir / "summary.md"
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_csv(
        trades_path,
        trade_rows,
        [
            "market",
            "session_date",
            "ticker",
            "path_run_id",
            "origin_action",
            "confidence",
            "close_reason",
            "entry_at",
            "close_at",
            "entry_price",
            "close_price",
            "actual_pnl_pct",
            "buy_zone_low",
            "buy_zone_high",
            "zone_width_pct",
            "zone_position",
            "zone_bucket",
        ],
    )
    _write_csv(
        delay_path,
        delay_rows,
        [
            "market",
            "session_date",
            "ticker",
            "path_run_id",
            "delay_min",
            "reference_at",
            "simulated_entry_at",
            "simulated_entry_price",
            "actual_entry_price",
            "close_price",
            "actual_pnl_pct",
            "simulated_pnl_pct",
            "delta_vs_actual_pct",
            "zone_bucket",
            "zone_width_pct",
            "close_reason",
            "confidence_bucket",
            "origin_action",
        ],
    )
    _write_csv(
        early_path,
        early_rows,
        [
            "market",
            "session_date",
            "ticker",
            "path_run_id",
            "created_at",
            "actual_entry_at",
            "early_entry_at",
            "entry_lag_min",
            "early_entry_price",
            "actual_entry_price",
            "close_price",
            "actual_pnl_pct",
            "simulated_pnl_pct",
            "delta_vs_actual_pct",
            "zone_bucket",
            "zone_width_pct",
            "close_reason",
            "origin_action",
        ],
    )
    _write_csv(
        wait_path,
        wait_rows,
        [
            "market",
            "ticker",
            "candidate_key",
            "call_id",
            "signal_time",
            "trade_ready_action",
            "path_name",
            "immediate_close_pct",
            "alternative_close_pct",
            "delta_vs_immediate_pct",
            "alternative_60m_pct",
            "max_runup_60m_pct",
            "max_drawdown_60m_pct",
        ],
    )
    payload["output_paths"] = {
        "summary_json": str(summary_path.resolve()),
        "summary_md": str(report_path.resolve()),
        "closed_trades_csv": str(trades_path.resolve()),
        "delay_replay_csv": str(delay_path.resolve()),
        "early_zone_entry_replay_csv": str(early_path.resolve()),
        "counterfactual_wait_pairs_csv": str(wait_path.resolve()),
    }
    summary_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    _write_report(report_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay live PathB entry timing and buy-zone policy variants.")
    parser.add_argument("--event-db", type=Path, default=DEFAULT_EVENT_DB)
    parser.add_argument("--candidate-db", type=Path, default=DEFAULT_CANDIDATE_DB)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    payload = build_entry_timing_buyzone_simulation(
        event_db=args.event_db,
        candidate_db=args.candidate_db,
        price_root=args.price_root,
        output_root=args.output_root,
        output_dir=args.output_dir,
        limit=args.limit,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2, sort_keys=True))
    print(f"output_dir={payload['inputs']['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
