from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = ZoneInfo("Asia/Seoul")
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_CANDIDATE_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price"
DEFAULT_OUTPUT_ROOT = ROOT / ".runtime" / "ops_simulation_analysis"


def _ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_ro_uri(path), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _dt_text(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _json_obj(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(str(raw))
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pct(entry: float, exit_price: float) -> float:
    if entry <= 0:
        return 0.0
    return ((float(exit_price) / float(entry)) - 1.0) * 100.0


def _stats(values: list[float]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return {"count": 0, "avg": 0.0, "median": 0.0, "best": 0.0, "worst": 0.0, "win_rate_pct": 0.0}
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 4),
        "median": round(float(median(vals)), 4),
        "best": round(max(vals), 4),
        "worst": round(min(vals), 4),
        "win_rate_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100.0, 2),
    }


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


def _read_tape(
    *,
    price_root: Path,
    market: str,
    ticker: str,
    start_at: datetime | None,
    end_at: datetime | None,
    max_rows: int = 10_000,
) -> list[dict[str, Any]]:
    source = _price_file(price_root, market, ticker)
    if source is None:
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for raw in reader:
            ts = raw.get("ts") or raw.get("date") or raw.get("datetime")
            price = raw.get("close") or raw.get("price") or raw.get("last")
            if not ts or price in (None, ""):
                continue
            dt = _parse_dt(ts)
            if start_at is not None and dt is not None and dt < start_at:
                continue
            if end_at is not None and dt is not None and dt > end_at:
                continue
            px = _as_float(price)
            if px <= 0:
                continue
            rows.append({"ts": str(ts), "dt": dt, "price": px})
            if len(rows) >= max_rows:
                break
    return rows


def _session_end(market: str, reference: datetime | None) -> datetime | None:
    if reference is None:
        return None
    ref = reference.astimezone(KST)
    if str(market or "").upper() == "KR":
        return datetime.combine(ref.date(), time(15, 30), tzinfo=KST)
    if ref.time() <= time(5, 0):
        return datetime.combine(ref.date(), time(5, 0), tzinfo=KST)
    return datetime.combine(ref.date() + timedelta(days=1), time(5, 0), tzinfo=KST)


def _event_payload(row: sqlite3.Row) -> dict[str, Any]:
    return _json_obj(row["payload_json"] if "payload_json" in row.keys() else "")


def _payload_path_run_id(payload: dict[str, Any]) -> str:
    sizing = payload.get("pathb_sizing") if isinstance(payload.get("pathb_sizing"), dict) else {}
    return str(payload.get("path_run_id") or sizing.get("path_run_id") or "")


def _events_for_path(conn: sqlite3.Connection, path_run_id: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            select event_id, occurred_at, event_type, reason_code, payload_json
            from lifecycle_events
            where payload_json like ?
            order by occurred_at, event_id
            """,
            (f"%{path_run_id}%",),
        )
    )


def _first_buy_order_and_fill(events: list[sqlite3.Row]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    first_order: dict[str, Any] | None = None
    first_fill: dict[str, Any] | None = None
    for event in events:
        payload = _event_payload(event)
        if event["event_type"] == "ORDER_SENT" and first_fill is None and first_order is None:
            price = payload.get("price") or payload.get("order_price") or payload.get("limit_price")
            first_order = {
                "at": event["occurred_at"],
                "dt": _parse_dt(event["occurred_at"]),
                "price": _as_float(price),
                "qty": int(_as_float(payload.get("qty"), 0)),
            }
        if event["event_type"] == "FILLED" and first_fill is None:
            price = payload.get("price") or payload.get("fill_price") or payload.get("filled_price")
            if _as_float(price) > 0:
                first_fill = {
                    "at": event["occurred_at"],
                    "dt": _parse_dt(event["occurred_at"]),
                    "price": _as_float(price),
                    "qty": int(_as_float(payload.get("qty"), 0)),
                }
    return first_order, first_fill


def _last_close(events: list[sqlite3.Row]) -> dict[str, Any] | None:
    close: dict[str, Any] | None = None
    for event in events:
        if event["event_type"] != "CLOSED":
            continue
        payload = _event_payload(event)
        price = payload.get("price") or payload.get("exit_price") or payload.get("filled_price")
        close = {
            "at": event["occurred_at"],
            "dt": _parse_dt(event["occurred_at"]),
            "price": _as_float(price),
            "reason": str(event["reason_code"] or payload.get("reason") or ""),
            "pnl_pct": _as_float(payload.get("pnl_pct") or payload.get("realized_pnl_pct")),
        }
    return close


def _simulate_policy(
    rows: list[dict[str, Any]],
    *,
    entry_price: float,
    target: float,
    stop: float,
    policy: dict[str, Any],
) -> tuple[float, str]:
    if not rows or entry_price <= 0:
        return 0.0, "NO_TAPE"
    peak = entry_price
    kind = str(policy.get("kind") or "")
    for row in rows:
        px = float(row["price"])
        peak = max(peak, px)
        pnl = _pct(entry_price, px)
        peak_pnl = _pct(entry_price, peak)
        if kind in {"target_stop", "ladder"}:
            if stop > 0 and px <= stop:
                return _pct(entry_price, px), "STOP"
            if target > 0 and px >= target:
                return _pct(entry_price, px), "TARGET"
        if kind == "ladder":
            trigger = float(policy.get("trigger_pct") or 4.0)
            giveback = float(policy.get("giveback_pct") or 1.5)
            if peak_pnl >= trigger and peak_pnl - pnl >= giveback:
                return _pct(entry_price, px), "LADDER"
        if kind == "time_stop":
            limit_min = float(policy.get("minutes") or 60)
            first_dt = rows[0].get("dt")
            row_dt = row.get("dt")
            if first_dt is not None and row_dt is not None and (row_dt - first_dt).total_seconds() >= limit_min * 60:
                return _pct(entry_price, px), f"TIME_{int(limit_min)}"
    return _pct(entry_price, float(rows[-1]["price"])), "END"


def us_one_share_post_fix(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        select occurred_at, session_date, ticker, reason_code, payload_json
        from lifecycle_events
        where market='US' and runtime_mode='live'
          and (payload_json like '%high_price_one_share_blocked%'
               or reason_code in ('HIGH_PRICE_BUDGET_BLOCK','ORDER_SIZE_TOO_SMALL_GATE'))
        order by occurred_at
        """
    ).fetchall()
    events: list[dict[str, Any]] = []
    unique_paths: dict[str, dict[str, Any]] = {}
    counts = Counter()
    for row in rows:
        payload = _event_payload(row)
        sizing = payload.get("pathb_sizing") if isinstance(payload.get("pathb_sizing"), dict) else {}
        blocker = sizing.get("blocker") or payload.get("blocker")
        if blocker != "high_price_one_share_blocked":
            continue
        path_run_id = _payload_path_run_id(payload)
        price = _as_float(sizing.get("price_krw") or payload.get("price_krw") or payload.get("price"))
        original_budget = _as_float(sizing.get("original_budget_krw") or payload.get("original_budget_krw") or 450_000)
        effective_budget = _as_float(sizing.get("effective_budget_krw") or payload.get("effective_budget_krw") or original_budget)
        early_gate = bool(effective_budget and original_budget and effective_budget < original_budget)
        if price <= 0:
            continue
        if price <= 700_000 and early_gate:
            new_class = "post_fix_waiting_size_gate"
        elif price <= 700_000:
            new_class = "post_fix_qty_one_allowed"
        else:
            new_class = "still_high_price_cap_block"
        counts[new_class] += 1
        item = {
            "path_run_id": path_run_id,
            "ticker": row["ticker"],
            "session_date": row["session_date"],
            "occurred_at": row["occurred_at"],
            "old_reason": row["reason_code"],
            "price_krw": round(price, 2),
            "effective_budget_krw": round(effective_budget, 2),
            "original_budget_krw": round(original_budget, 2),
            "new_class": new_class,
        }
        events.append(item)
        if path_run_id and path_run_id not in unique_paths:
            unique_paths[path_run_id] = item

    path_status = Counter()
    path_examples: list[dict[str, Any]] = []
    for pid, item in unique_paths.items():
        run = conn.execute("select status, plan_json from v2_path_runs where path_run_id=?", (pid,)).fetchone()
        status = str(run["status"] if run else "MISSING")
        plan = _json_obj(run["plan_json"] if run else "")
        path_status[status] += 1
        path_examples.append({**item, "status": status, "confidence": plan.get("confidence"), "cancel_reason": plan.get("cancel_reason")})
    return {
        "event_count": len(events),
        "unique_path_count": len(unique_paths),
        "post_fix_class_counts": dict(counts),
        "path_status_counts": dict(path_status),
        "top_examples": sorted(path_examples, key=lambda x: (x["new_class"], -float(x["price_krw"])))[:20],
        "decision": "fixed_policy_gap_validated",
    }


def us_exit_replay(conn: sqlite3.Connection, *, price_root: Path, limit: int) -> dict[str, Any]:
    runs = conn.execute(
        """
        select path_run_id, ticker, market, session_date, status, plan_json, created_at, updated_at
        from v2_path_runs
        where runtime_mode='live' and market='US' and path_type='claude_price' and status='CLOSED'
        order by updated_at desc
        limit ?
        """,
        (int(limit),),
    ).fetchall()
    policies = [
        {"name": "actual"},
        {"name": "target_stop", "kind": "target_stop"},
        {"name": "hold_to_available_end", "kind": "hold"},
        {"name": "ladder_t3_g1_5", "kind": "ladder", "trigger_pct": 3.0, "giveback_pct": 1.5},
        {"name": "ladder_t4_g0_8", "kind": "ladder", "trigger_pct": 4.0, "giveback_pct": 0.8},
        {"name": "ladder_t4_g1_2", "kind": "ladder", "trigger_pct": 4.0, "giveback_pct": 1.2},
        {"name": "ladder_t4_g1_5", "kind": "ladder", "trigger_pct": 4.0, "giveback_pct": 1.5},
        {"name": "ladder_t4_g2_0", "kind": "ladder", "trigger_pct": 4.0, "giveback_pct": 2.0},
        {"name": "time_stop_60", "kind": "time_stop", "minutes": 60},
        {"name": "time_stop_120", "kind": "time_stop", "minutes": 120},
    ]
    by_policy: defaultdict[str, list[float]] = defaultdict(list)
    exit_reasons: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    rows_out: list[dict[str, Any]] = []
    for run in runs:
        plan = _json_obj(run["plan_json"])
        events = _events_for_path(conn, str(run["path_run_id"]))
        _order, fill = _first_buy_order_and_fill(events)
        close = _last_close(events)
        entry_price = _as_float((fill or {}).get("price") or plan.get("actual_entry_price") or plan.get("entry_price"))
        if entry_price <= 0 or close is None:
            skipped["missing_entry_or_close"] += 1
            continue
        start = (fill or {}).get("dt") or _parse_dt(run["created_at"])
        end = close.get("dt") or _parse_dt(run["updated_at"]) or _session_end("US", start)
        tape = _read_tape(price_root=price_root, market="US", ticker=str(run["ticker"]), start_at=start, end_at=end)
        if not tape:
            skipped["price_tape_missing"] += 1
            continue
        target = _as_float(plan.get("sell_target") or plan.get("target_price"))
        stop = _as_float(plan.get("stop_loss") or plan.get("hard_stop"))
        actual = _as_float(close.get("pnl_pct"), _pct(entry_price, _as_float(close.get("price"))))
        by_policy["actual"].append(actual)
        exit_reasons[str(close.get("reason") or "")] += 1
        row = {
            "path_run_id": run["path_run_id"],
            "ticker": run["ticker"],
            "session_date": run["session_date"],
            "actual_pnl_pct": round(actual, 4),
            "actual_reason": close.get("reason"),
            "entry_price": entry_price,
            "target": target,
            "stop": stop,
            "tape_rows": len(tape),
        }
        for policy in policies[1:]:
            sim_pnl, reason = _simulate_policy(tape, entry_price=entry_price, target=target, stop=stop, policy=policy)
            by_policy[str(policy["name"])].append(sim_pnl)
            row[str(policy["name"])] = round(sim_pnl, 4)
            row[str(policy["name"]) + "_reason"] = reason
        rows_out.append(row)
    policy_stats = {name: _stats(vals) for name, vals in sorted(by_policy.items())}
    ranked = sorted(policy_stats.items(), key=lambda kv: kv[1]["avg"], reverse=True)
    return {
        "evaluated_runs": len(rows_out),
        "skipped": dict(skipped),
        "actual_exit_reasons": dict(exit_reasons.most_common()),
        "policy_stats": policy_stats,
        "ranked_policies": [{"policy": name, **stats} for name, stats in ranked],
        "top_actual_vs_policy_examples": sorted(rows_out, key=lambda r: r.get("actual_pnl_pct", 0), reverse=True)[:20],
        "decision": "analysis_only_do_not_change_profit_ladder_without_targeted_review",
    }


def us_fill_quality(conn: sqlite3.Connection, *, limit: int) -> dict[str, Any]:
    runs = conn.execute(
        """
        select path_run_id, ticker, market, session_date, status, plan_json, updated_at
        from v2_path_runs
        where runtime_mode='live' and market='US' and path_type='claude_price'
        order by updated_at desc
        limit ?
        """,
        (int(limit),),
    ).fetchall()
    delays: list[float] = []
    statuses = Counter()
    unfilled: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    for run in runs:
        events = _events_for_path(conn, str(run["path_run_id"]))
        order, fill = _first_buy_order_and_fill(events)
        if not order:
            statuses["no_buy_order"] += 1
            continue
        if fill and fill.get("dt") and order.get("dt"):
            delay = (fill["dt"] - order["dt"]).total_seconds() / 60.0
            delays.append(delay)
            statuses["filled"] += 1
            fill_rows.append(
                {
                    "path_run_id": run["path_run_id"],
                    "ticker": run["ticker"],
                    "session_date": run["session_date"],
                    "order_price": order.get("price"),
                    "fill_price": fill.get("price"),
                    "fill_delay_min": round(delay, 3),
                    "status": run["status"],
                }
            )
        else:
            statuses["order_sent_not_filled"] += 1
            plan = _json_obj(run["plan_json"])
            unfilled.append(
                {
                    "path_run_id": run["path_run_id"],
                    "ticker": run["ticker"],
                    "session_date": run["session_date"],
                    "status": run["status"],
                    "order_price": order.get("price"),
                    "cancel_reason": plan.get("cancel_reason") or plan.get("status_reason"),
                }
            )
    return {
        "checked_runs": len(runs),
        "status_counts": dict(statuses),
        "fill_delay_stats_min": _stats(delays),
        "slowest_fills": sorted(fill_rows, key=lambda r: r["fill_delay_min"], reverse=True)[:20],
        "unfilled_orders": unfilled[:20],
        "decision": "monitor_unfilled_limit_orders_before_changing_slippage_caps",
    }


def kr_wait_filter_replay(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        select session_date, ticker, path_name, trade_ready_action,
               outcome_60m_pct, max_drawdown_60m_pct, metadata_json
        from candidate_counterfactual_paths
        where market='KR'
          and path_name in ('wait_30m','wait_60m')
          and status='CLOSE_OUTCOME_FILLED'
          and outcome_60m_pct is not null
        """
    ).fetchall()
    groups: defaultdict[tuple[str, str, str], list[float]] = defaultdict(list)
    all_vals: list[float] = []
    strong_vals: list[float] = []
    strong_examples: list[dict[str, Any]] = []
    for row in rows:
        outcome = _as_float(row["outcome_60m_pct"])
        drawdown = _as_float(row["max_drawdown_60m_pct"])
        meta = _json_obj(row["metadata_json"])
        ctx = meta.get("context") if isinstance(meta.get("context"), dict) else {}
        evidence_pack = ctx.get("evidence_pack") if isinstance(ctx.get("evidence_pack"), dict) else {}
        route_source = str(ctx.get("route_source") or meta.get("route_source") or "unknown")
        freshness = str(ctx.get("freshness_verdict") or meta.get("freshness_verdict") or "unknown")
        evidence_state = str(ctx.get("evidence_data_state") or evidence_pack.get("data_state") or "unknown")
        key = (str(row["path_name"]), route_source, f"{freshness}/{evidence_state}")
        groups[key].append(outcome)
        all_vals.append(outcome)
        if outcome >= 8.0 and drawdown >= -5.0:
            strong_vals.append(outcome)
            strong_examples.append(
                {
                    "session_date": row["session_date"],
                    "ticker": row["ticker"],
                    "path": row["path_name"],
                    "outcome_60m_pct": round(outcome, 4),
                    "max_drawdown_60m_pct": round(drawdown, 4),
                    "route_source": route_source,
                    "freshness": freshness,
                    "evidence_state": evidence_state,
                }
            )
    visible_groups = []
    for (path_name, route_source, freshness_state), vals in groups.items():
        if len(vals) < 20:
            continue
        stats = _stats(vals)
        visible_groups.append(
            {
                "path": path_name,
                "route_source": route_source,
                "freshness_state": freshness_state,
                **stats,
            }
        )
    visible_groups.sort(key=lambda r: (r["avg"], r["count"]), reverse=True)
    return {
        "all_wait_stats": _stats(all_vals),
        "strong_oracle_filter_stats": _stats(strong_vals),
        "strong_oracle_ratio_pct": round(len(strong_vals) / len(all_vals) * 100.0, 4) if all_vals else 0.0,
        "top_live_visible_groups": visible_groups[:20],
        "strong_examples": sorted(strong_examples, key=lambda r: r["outcome_60m_pct"], reverse=True)[:20],
        "decision": "restricted_re_evaluation_only_no_broad_live_wait",
    }


def operability_replay(conn: sqlite3.Connection) -> dict[str, Any]:
    reason_counts = Counter()
    order_unknown_rows = []
    for row in conn.execute(
        """
        select occurred_at, market, ticker, event_type, reason_code, payload_json
        from lifecycle_events
        where runtime_mode='live'
          and (event_type='ORDER_UNKNOWN' or reason_code like '%ORDER_UNKNOWN%')
        order by occurred_at desc
        limit 100
        """
    ):
        reason_counts[str(row["market"]) + ":" + str(row["event_type"] or row["reason_code"])] += 1
        order_unknown_rows.append(
            {
                "occurred_at": row["occurred_at"],
                "market": row["market"],
                "ticker": row["ticker"],
                "event_type": row["event_type"],
                "reason_code": row["reason_code"],
            }
        )

    full_events = conn.execute(
        """
        select event_type, market, runtime_mode, session_date, ticker, decision_id, payload_json
        from lifecycle_events
        where runtime_mode='live'
        order by event_id
        """
    ).fetchall()
    full_runs = conn.execute(
        """
        select path_run_id, market, runtime_mode, session_date, ticker, status, plan_json
        from v2_path_runs
        where runtime_mode='live' and path_type='claude_price'
        order by updated_at desc
        """
    ).fetchall()
    terminal_by_status = {"FILLED": "FILLED", "CLOSED": "CLOSED", "CANCELLED": "CLAUDE_PRICE_CANCELLED"}
    events_by_path_type = set()
    for event in full_events:
        payload = _event_payload(event)
        pid = _payload_path_run_id(payload)
        if pid:
            events_by_path_type.add((pid, str(event["event_type"])))
    missing_terminal = []
    cross_run_evidence = []
    for run in full_runs:
        expected = terminal_by_status.get(str(run["status"] or ""))
        if expected and (str(run["path_run_id"]), expected) not in events_by_path_type:
            missing_terminal.append(
                {
                    "path_run_id": run["path_run_id"],
                    "market": run["market"],
                    "ticker": run["ticker"],
                    "status": run["status"],
                    "missing_event": expected,
                }
            )
        plan = _json_obj(run["plan_json"])
        evidence = plan.get("pathb_closed_lifecycle_evidence")
        if isinstance(evidence, dict):
            evidence_pid = _payload_path_run_id(evidence)
            if evidence_pid and evidence_pid != str(run["path_run_id"]):
                cross_run_evidence.append(
                    {
                        "path_run_id": run["path_run_id"],
                        "evidence_path_run_id": evidence_pid,
                        "ticker": run["ticker"],
                        "status": run["status"],
                        "close_reason": plan.get("close_reason"),
                    }
                )
    return {
        "order_unknown_recent_count": len(order_unknown_rows),
        "order_unknown_counts": dict(reason_counts),
        "order_unknown_recent_examples": order_unknown_rows[:20],
        "pathb_terminal_missing_count": len(missing_terminal),
        "pathb_terminal_missing_examples": missing_terminal[:20],
        "pathb_cross_run_closed_evidence_count": len(cross_run_evidence),
        "pathb_cross_run_closed_evidence_examples": cross_run_evidence[:20],
        "decision": "operability_issues_are_data_audit_items_not_entry_policy_changes",
    }


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    event_db = Path(args.event_db)
    candidate_db = Path(args.candidate_db)
    price_root = Path(args.price_root)
    with _connect_ro(event_db) as event_conn, _connect_ro(candidate_db) as candidate_conn:
        payload = {
            "ok": True,
            "generated_at": datetime.now(KST).replace(microsecond=0).isoformat(),
            "live_writes_performed": False,
            "inputs": {
                "event_db": str(event_db),
                "candidate_db": str(candidate_db),
                "price_root": str(price_root),
            },
            "categories": {
                "US": {
                    "profitability": {
                        "one_share_post_fix": us_one_share_post_fix(event_conn),
                        "exit_policy_replay": us_exit_replay(event_conn, price_root=price_root, limit=args.limit),
                        "fill_quality": us_fill_quality(event_conn, limit=args.limit),
                    }
                },
                "KR": {
                    "profitability": {
                        "wait_filter_replay": kr_wait_filter_replay(candidate_conn),
                    }
                },
                "common": {
                    "operability": operability_replay(event_conn),
                },
            },
        }
    return payload


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return lines


def write_markdown(payload: dict[str, Any], path: Path) -> None:
    us = payload["categories"]["US"]["profitability"]
    kr = payload["categories"]["KR"]["profitability"]["wait_filter_replay"]
    op = payload["categories"]["common"]["operability"]
    one = us["one_share_post_fix"]
    exit_replay = us["exit_policy_replay"]
    fill = us["fill_quality"]
    lines = [
        "# Ops Extended Simulation Suite",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        "",
        "## US / profitability / one-share post-fix replay",
        "",
        f"- decision: {one['decision']}",
        f"- event_count: {one['event_count']}",
        f"- unique_path_count: {one['unique_path_count']}",
        f"- post_fix_class_counts: {json.dumps(one['post_fix_class_counts'], ensure_ascii=False, sort_keys=True)}",
        f"- path_status_counts: {json.dumps(one['path_status_counts'], ensure_ascii=False, sort_keys=True)}",
        "",
    ]
    lines.extend(_md_table(one["top_examples"], ["ticker", "session_date", "price_krw", "old_reason", "new_class", "status", "cancel_reason"]))
    lines.extend(
        [
            "",
            "## US / profitability / exit policy replay",
            "",
            f"- decision: {exit_replay['decision']}",
            f"- evaluated_runs: {exit_replay['evaluated_runs']}",
            f"- skipped: {json.dumps(exit_replay['skipped'], ensure_ascii=False, sort_keys=True)}",
            f"- actual_exit_reasons: {json.dumps(exit_replay['actual_exit_reasons'], ensure_ascii=False, sort_keys=True)}",
            "",
        ]
    )
    lines.extend(_md_table(exit_replay["ranked_policies"], ["policy", "count", "avg", "median", "win_rate_pct", "best", "worst"]))
    lines.extend(
        [
            "",
            "## US / operability_profitability / fill quality",
            "",
            f"- decision: {fill['decision']}",
            f"- checked_runs: {fill['checked_runs']}",
            f"- status_counts: {json.dumps(fill['status_counts'], ensure_ascii=False, sort_keys=True)}",
            f"- fill_delay_stats_min: {json.dumps(fill['fill_delay_stats_min'], ensure_ascii=False, sort_keys=True)}",
            "",
            "### Unfilled Orders",
            "",
        ]
    )
    lines.extend(_md_table(fill["unfilled_orders"], ["ticker", "session_date", "status", "order_price", "cancel_reason"]))
    lines.extend(
        [
            "",
            "## KR / profitability / wait filter replay",
            "",
            f"- decision: {kr['decision']}",
            f"- all_wait_stats: {json.dumps(kr['all_wait_stats'], ensure_ascii=False, sort_keys=True)}",
            f"- strong_oracle_filter_stats: {json.dumps(kr['strong_oracle_filter_stats'], ensure_ascii=False, sort_keys=True)}",
            f"- strong_oracle_ratio_pct: {kr['strong_oracle_ratio_pct']}",
            "",
            "### Top Live-visible Groups",
            "",
        ]
    )
    lines.extend(_md_table(kr["top_live_visible_groups"][:15], ["path", "route_source", "freshness_state", "count", "avg", "median", "win_rate_pct", "best", "worst"]))
    lines.extend(
        [
            "",
            "## Common / operability replay",
            "",
            f"- decision: {op['decision']}",
            f"- order_unknown_recent_count: {op['order_unknown_recent_count']}",
            f"- pathb_terminal_missing_count: {op['pathb_terminal_missing_count']}",
            f"- pathb_cross_run_closed_evidence_count: {op['pathb_cross_run_closed_evidence_count']}",
            "",
            "### PathB Terminal Missing Examples",
            "",
        ]
    )
    lines.extend(_md_table(op["pathb_terminal_missing_examples"], ["path_run_id", "market", "ticker", "status", "missing_event"]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run broad read-only ops simulation analysis.")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--candidate-db", default=str(DEFAULT_CANDIDATE_DB))
    parser.add_argument("--price-root", default=str(DEFAULT_PRICE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--limit", type=int, default=140)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    payload = build_report(args)
    root = Path(args.output_root).expanduser().resolve()
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else root / ("extended_" + datetime.now(KST).strftime("%Y%m%d_%H%M%S"))
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "ops_extended_simulation_suite.json"
    md_path = out_dir / "ops_extended_simulation_suite.md"
    payload["output_paths"] = {"json": str(json_path), "markdown": str(md_path)}
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    write_markdown(payload, md_path)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        us = payload["categories"]["US"]["profitability"]
        kr = payload["categories"]["KR"]["profitability"]["wait_filter_replay"]
        print(
            "ok "
            f"one_share_paths={us['one_share_post_fix']['unique_path_count']} "
            f"exit_runs={us['exit_policy_replay']['evaluated_runs']} "
            f"kr_wait_avg={kr['all_wait_stats']['avg']} "
            f"report={md_path}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
