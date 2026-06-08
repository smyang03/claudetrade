from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.rehearsal.context import RehearsalGuardError
from tools.ops_us_high_price_simulation import (
    DEFAULT_EVENT_DB,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PERF_DB,
    DEFAULT_PRICE_ROOT,
    KST,
    _as_float,
    _connect_ro,
    _json_obj,
    _output_dir,
    _pct,
    _read_prices,
    _session_end,
    _session_open,
    _stats,
)


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _parse_dt(value: Any, *, naive_tz: timezone | Any = KST) -> datetime | None:
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
        dt = dt.replace(tzinfo=naive_tz)
    return dt.astimezone(KST)


def _sum_stats(values: list[float]) -> dict[str, Any]:
    vals = [float(value) for value in values if value is not None]
    return {"sum": round(sum(vals), 4), **_stats(vals)}


def _kr_entry_bucket(filled_at: str) -> str:
    dt = _parse_dt(filled_at, naive_tz=timezone.utc)
    if dt is None:
        return "UNKNOWN"
    minutes = dt.hour * 60 + dt.minute - 9 * 60
    if minutes < 0:
        return "PREOPEN"
    if minutes < 30:
        return "OPEN_0_30"
    if minutes < 60:
        return "OPEN_30_60"
    if minutes < 90:
        return "OPEN_60_90"
    if minutes < 270:
        return "OPEN_90_270"
    return "LATE_AFTER_270"


def _kr_closed_rows(perf_conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = perf_conn.execute(
        """
        select market, session_date, ticker, path_type, strategy, filled_at, closed_at,
               pnl_pct, close_reason, quality_grade, candidate_pool_role
        from v2_learning_performance
        where runtime_mode='live'
          and market='KR'
          and filled=1
          and closed=1
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["pnl_pct"] = _as_float(row["pnl_pct"])
        item["entry_bucket"] = _kr_entry_bucket(str(row["filled_at"] or ""))
        out.append(item)
    return out


def _policy_eval(rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    kept = [row for row in rows if predicate(row)]
    removed = [row for row in rows if not predicate(row)]
    baseline = _sum_stats([row["pnl_pct"] for row in rows])
    kept_stats = _sum_stats([row["pnl_pct"] for row in kept])
    removed_stats = _sum_stats([row["pnl_pct"] for row in removed])
    removed_positive = [row["pnl_pct"] for row in removed if row["pnl_pct"] > 0]
    removed_negative = [row["pnl_pct"] for row in removed if row["pnl_pct"] < 0]
    return {
        "kept": kept_stats,
        "removed": removed_stats,
        "delta_avg_vs_base": round(kept_stats["avg"] - baseline["avg"], 4) if kept else 0.0,
        "loss_avoided_proxy": round(abs(sum(removed_negative)), 4),
        "opportunity_cost_proxy": round(sum(removed_positive), 4),
        "removed_positive_count": len(removed_positive),
        "removed_negative_count": len(removed_negative),
        "removed_examples": sorted(
            [
                {
                    "session_date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "strategy": row.get("strategy"),
                    "entry_bucket": row.get("entry_bucket"),
                    "pnl_pct": round(float(row.get("pnl_pct") or 0), 4),
                    "close_reason": row.get("close_reason"),
                }
                for row in removed
            ],
            key=lambda item: item["pnl_pct"],
        )[:12],
    }


def _kr_policy_simulation(perf_conn: sqlite3.Connection) -> dict[str, Any]:
    rows = _kr_closed_rows(perf_conn)
    policies: dict[str, Callable[[dict[str, Any]], bool]] = {
        "exclude_open_0_30": lambda row: row["entry_bucket"] != "OPEN_0_30",
        "exclude_late_after_270": lambda row: row["entry_bucket"] != "LATE_AFTER_270",
        "exclude_open_0_30_and_late": lambda row: row["entry_bucket"]
        not in {"OPEN_0_30", "LATE_AFTER_270"},
        "exclude_momentum": lambda row: str(row.get("strategy") or "") != "momentum",
        "exclude_opening_range_pullback": lambda row: str(row.get("strategy") or "") != "opening_range_pullback",
        "exclude_momentum_and_bad_buckets": lambda row: str(row.get("strategy") or "") != "momentum"
        and row["entry_bucket"] not in {"OPEN_0_30", "LATE_AFTER_270"},
        "keep_claude_price_strategy_only": lambda row: str(row.get("strategy") or "") == "claude_price",
    }
    by_bucket: defaultdict[str, list[float]] = defaultdict(list)
    by_strategy: defaultdict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_bucket[str(row["entry_bucket"])].append(row["pnl_pct"])
        by_strategy[str(row.get("strategy") or "")].append(row["pnl_pct"])
    policy_rows = [{"policy": name, **_policy_eval(rows, predicate)} for name, predicate in policies.items()]
    policy_rows.sort(key=lambda row: (row["kept"]["avg"], row["kept"]["count"]), reverse=True)
    return {
        "baseline": _sum_stats([row["pnl_pct"] for row in rows]),
        "by_bucket": {key: _sum_stats(vals) for key, vals in sorted(by_bucket.items())},
        "by_strategy": {key: _sum_stats(vals) for key, vals in sorted(by_strategy.items())},
        "policies": policy_rows,
        "worst_trades": sorted(
            [
                {
                    "session_date": row.get("session_date"),
                    "ticker": row.get("ticker"),
                    "strategy": row.get("strategy"),
                    "entry_bucket": row.get("entry_bucket"),
                    "pnl_pct": round(float(row.get("pnl_pct") or 0), 4),
                    "close_reason": row.get("close_reason"),
                }
                for row in rows
            ],
            key=lambda item: item["pnl_pct"],
        )[:20],
    }


def _path_events(conn: sqlite3.Connection, path_run_id: str, decision_id: str = "") -> list[sqlite3.Row]:
    if decision_id:
        return list(
            conn.execute(
                """
                select event_id, event_type, occurred_at, reason_code, payload_json
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
            select event_id, event_type, occurred_at, reason_code, payload_json
            from lifecycle_events
            where payload_json like ?
            order by occurred_at, event_id
            """,
            (f"%{path_run_id}%",),
        )
    )


def _first_order_fill_close(
    events: list[sqlite3.Row],
    path_run_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    first_order: dict[str, Any] | None = None
    first_fill: dict[str, Any] | None = None
    first_close: dict[str, Any] | None = None
    for event in events:
        payload = _json_obj(event["payload_json"])
        payload_path_run_id = str(payload.get("path_run_id") or "")
        path_matches = payload_path_run_id == str(path_run_id or "")
        side = str(payload.get("side") or "").lower()
        if event["event_type"] == "ORDER_SENT" and first_order is None and path_matches and side in {"", "buy"}:
            price = _as_float(payload.get("price") or payload.get("order_price") or payload.get("limit_price"))
            first_order = {"at": event["occurred_at"], "dt": _parse_dt(event["occurred_at"]), "price": price}
        if event["event_type"] == "FILLED" and first_fill is None and path_matches and side in {"", "buy"}:
            price = _as_float(payload.get("price") or payload.get("fill_price") or payload.get("filled_price"))
            first_fill = {"at": event["occurred_at"], "dt": _parse_dt(event["occurred_at"]), "price": price}
        if event["event_type"] == "CLOSED" and first_close is None and (path_matches or not payload_path_run_id):
            price = _as_float(payload.get("price") or payload.get("exit_price") or payload.get("actual_exit_price"))
            first_close = {
                "at": event["occurred_at"],
                "dt": _parse_dt(event["occurred_at"]),
                "price": price,
                "reason_code": str(event["reason_code"] or payload.get("close_reason") or ""),
                "path_run_linked": path_matches,
            }
    return first_order, first_fill, first_close


def _us_unfilled_audit(event_conn: sqlite3.Connection, price_root: Path) -> dict[str, Any]:
    runs = event_conn.execute(
        """
        select path_run_id, decision_id, ticker, session_date, status, plan_json, updated_at
        from v2_path_runs
        where runtime_mode='live'
          and market='US'
          and path_type='claude_price'
        order by updated_at desc
        """
    ).fetchall()
    bp_values = [0, 5, 10, 20, 30]
    rows: list[dict[str, Any]] = []
    for run in runs:
        path_run_id = str(run["path_run_id"])
        order, fill, close = _first_order_fill_close(
            _path_events(event_conn, path_run_id, str(run["decision_id"] or "")),
            path_run_id,
        )
        if not order or fill:
            continue
        order_dt = order.get("dt")
        limit = _as_float(order.get("price"))
        plan = _json_obj(run["plan_json"])
        item = {
            "path_run_id": path_run_id,
            "ticker": run["ticker"],
            "session_date": run["session_date"],
            "status": run["status"],
            "order_at": order.get("at"),
            "limit": round(limit, 6),
            "cancel_reason": str(plan.get("cancel_reason") or plan.get("status_reason") or ""),
        }
        if close is not None:
            item["closed_at"] = close.get("at")
            item["close_reason"] = close.get("reason_code")
            item["classification"] = (
                "closed_without_fill_event"
                if close.get("path_run_linked")
                else "closed_event_unlinked_to_path_run"
            )
            rows.append(item)
            continue
        if str(run["status"] or "").upper() == "CLOSED":
            item["classification"] = "closed_path_run_without_fill_event"
            rows.append(item)
            continue
        if order_dt is None or limit <= 0:
            item["classification"] = "invalid_limit_or_order_time"
            rows.append(item)
            continue
        tape = _read_prices(price_root, str(run["ticker"]), order_dt, _session_end(order_dt))
        if not tape:
            item["classification"] = "price_tape_missing"
            rows.append(item)
            continue
        filled_any = False
        for bp in bp_values:
            test_limit = limit * (1.0 + bp / 10_000.0)
            hit = next((bar for bar in tape if float(bar.get("low") or bar["close"]) <= test_limit), None)
            if hit is not None:
                filled_any = True
                eod = tape[-1]
                item[f"fill_bp_{bp}"] = True
                item[f"eod_ret_bp_{bp}"] = round(_pct(test_limit, float(eod["close"])), 4)
            else:
                item[f"fill_bp_{bp}"] = False
                item[f"eod_ret_bp_{bp}"] = None
        item["classification"] = "tape_fill_possible_without_fill_event" if item.get("fill_bp_0") else (
            "fill_possible_only_with_slippage" if filled_any else "limit_never_touched"
        )
        rows.append(item)

    by_bp: dict[str, Any] = {}
    for bp in bp_values:
        filled = [row for row in rows if row.get(f"fill_bp_{bp}") is True]
        by_bp[str(bp)] = {
            "fill_count": len(filled),
            "eod_return": _stats([_as_float(row.get(f"eod_ret_bp_{bp}")) for row in filled]),
        }
    unfilled_rows = [
        row
        for row in rows
        if str(row.get("classification") or "")
        not in {
            "closed_without_fill_event",
            "closed_event_unlinked_to_path_run",
            "closed_path_run_without_fill_event",
        }
    ]
    return {
        "audit_row_count": len(rows),
        "unfilled_order_count": len(unfilled_rows),
        "classification_counts": dict(sorted(Counter(str(row.get("classification") or "") for row in rows).items())),
        "by_slippage_bp": by_bp,
        "rows": rows,
    }


def _first_at_or_after(rows: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    for row in rows:
        if row["dt"] >= at:
            return row
    return None


def _row_at_or_before(rows: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    previous: dict[str, Any] | None = None
    for row in rows:
        if row["dt"] > at:
            break
        previous = row
    return previous


def _us_overcap_rows(event_conn: sqlite3.Connection, price_root: Path) -> list[dict[str, Any]]:
    events = event_conn.execute(
        """
        select event_id, occurred_at, session_date, ticker, reason_code, payload_json
        from lifecycle_events
        where runtime_mode='live'
          and market='US'
          and reason_code='HIGH_PRICE_BUDGET_BLOCK'
          and payload_json like '%pathb_plan_registration%'
        order by occurred_at, event_id
        """
    ).fetchall()
    out: list[dict[str, Any]] = []
    ticker_day_counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        payload = _json_obj(event["payload_json"])
        low_krw = _as_float(payload.get("buy_zone_low_krw"))
        if low_krw <= 700_000:
            continue
        at = _parse_dt(event["occurred_at"])
        if at is None:
            continue
        ticker_day = (str(event["session_date"] or ""), str(event["ticker"] or ""))
        ticker_day_counts[ticker_day] += 1
        start = max(at, _session_open(at) + timedelta(minutes=60))
        end = _session_end(at)
        tape = _read_prices(price_root, str(event["ticker"]), start - timedelta(minutes=2), end)
        entry = _first_at_or_after(tape, start)
        item = {
            "event_id": event["event_id"],
            "session_date": event["session_date"],
            "ticker": event["ticker"],
            "occurred_at": event["occurred_at"],
            "ticker_day_rank": ticker_day_counts[ticker_day],
            "buy_zone_low": _as_float(payload.get("buy_zone_low")),
            "buy_zone_high": _as_float(payload.get("buy_zone_high")),
            "buy_zone_low_krw": round(low_krw, 2),
            "buy_zone_high_krw": round(_as_float(payload.get("buy_zone_high_krw")), 2),
            "max_entry_krw": round(_as_float(payload.get("max_entry_krw")), 2),
            "coverage_status": "price_tape_missing",
        }
        if entry is not None:
            future = [row for row in tape if row["dt"] >= entry["dt"]]
            row60 = _row_at_or_before(future, entry["dt"] + timedelta(minutes=60))
            eod = _row_at_or_before(future, end)
            highs = [float(row.get("high") or row["close"]) for row in future]
            lows = [float(row.get("low") or row["close"]) for row in future]
            entry_price = float(entry["close"])
            item.update(
                {
                    "coverage_status": "complete" if eod is not None else "partial",
                    "post_gate_entry": round(entry_price, 4),
                    "ret_60m_pct": round(_pct(entry_price, float((row60 or entry)["close"])), 4)
                    if row60 is not None
                    else None,
                    "ret_eod_pct": round(_pct(entry_price, float((eod or entry)["close"])), 4) if eod else None,
                    "mfe_eod_pct": round(_pct(entry_price, max(highs)), 4) if highs else None,
                    "mae_eod_pct": round(_pct(entry_price, min(lows)), 4) if lows else None,
                }
            )
        out.append(item)
    return out


def _overcap_policy_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    complete = [row for row in rows if row.get("coverage_status") == "complete"]

    policies: dict[str, Callable[[dict[str, Any]], bool]] = {
        "all": lambda row: True,
        "first_per_ticker_day": lambda row: int(row.get("ticker_day_rank") or 0) == 1,
        "cap_le_900k": lambda row: _as_float(row.get("buy_zone_low_krw")) <= 900_000,
        "cap_le_1_100k": lambda row: _as_float(row.get("buy_zone_low_krw")) <= 1_100_000,
        "cap_le_1_300k": lambda row: _as_float(row.get("buy_zone_low_krw")) <= 1_300_000,
        "first_and_cap_le_1_100k": lambda row: int(row.get("ticker_day_rank") or 0) == 1
        and _as_float(row.get("buy_zone_low_krw")) <= 1_100_000,
    }
    result: dict[str, Any] = {}
    for name, predicate in policies.items():
        selected = [row for row in complete if predicate(row)]
        result[name] = {
            "count": len(selected),
            "unique_ticker_days": len({(row.get("session_date"), row.get("ticker")) for row in selected}),
            "ret_60m": _stats([_as_float(row.get("ret_60m_pct")) for row in selected]),
            "ret_eod": _stats([_as_float(row.get("ret_eod_pct")) for row in selected]),
            "mae_eod": _stats([_as_float(row.get("mae_eod_pct")) for row in selected]),
        }
    return dict(sorted(result.items(), key=lambda item: (item[1]["ret_eod"]["avg"], item[1]["count"]), reverse=True))


def _us_overcap_simulation(event_conn: sqlite3.Connection, price_root: Path) -> dict[str, Any]:
    rows = _us_overcap_rows(event_conn, price_root)
    complete = [row for row in rows if row.get("coverage_status") == "complete"]
    return {
        "raw_count": len(rows),
        "complete_count": len(complete),
        "unique_ticker_days": len({(row.get("session_date"), row.get("ticker")) for row in rows}),
        "ret_60m": _stats([_as_float(row.get("ret_60m_pct")) for row in complete]),
        "ret_eod": _stats([_as_float(row.get("ret_eod_pct")) for row in complete]),
        "policy_stats": _overcap_policy_stats(rows),
        "rows": rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return lines


def _write_md(payload: dict[str, Any], path: Path) -> None:
    kr = payload["summary"]["kr_policy"]
    unfilled = payload["summary"]["us_unfilled"]
    overcap = payload["summary"]["us_overcap"]
    lines = [
        "# Ops Next Wave Policy Simulation",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        "",
        "## KR Policy Simulation",
        "",
        f"- baseline: {json.dumps(kr['baseline'], ensure_ascii=False, sort_keys=True)}",
        "",
        "### KR Policy Ranking",
        "",
    ]
    policy_rows = [
        {
            "policy": row["policy"],
            "kept_count": row["kept"]["count"],
            "kept_avg": row["kept"]["avg"],
            "kept_win": row["kept"]["win_rate_pct"],
            "removed_count": row["removed"]["count"],
            "removed_avg": row["removed"]["avg"],
            "loss_avoided": row["loss_avoided_proxy"],
            "opportunity_cost": row["opportunity_cost_proxy"],
        }
        for row in kr["policies"]
    ]
    lines.extend(
        _md_table(
            policy_rows,
            [
                "policy",
                "kept_count",
                "kept_avg",
                "kept_win",
                "removed_count",
                "removed_avg",
                "loss_avoided",
                "opportunity_cost",
            ],
        )
    )
    lines.extend(
        [
            "",
            "## US Unfilled/Event Audit",
            "",
            f"- unfilled_order_count: {unfilled['unfilled_order_count']}",
            f"- classification_counts: {json.dumps(unfilled['classification_counts'], ensure_ascii=False, sort_keys=True)}",
            "",
            "## US Over-cap Tier Simulation",
            "",
            f"- raw_count: {overcap['raw_count']}",
            f"- complete_count: {overcap['complete_count']}",
            f"- unique_ticker_days: {overcap['unique_ticker_days']}",
            f"- ret_eod: {json.dumps(overcap['ret_eod'], ensure_ascii=False, sort_keys=True)}",
            "",
            "### Over-cap Policy Ranking",
            "",
        ]
    )
    overcap_rows = [
        {
            "policy": name,
            "count": stats["count"],
            "unique": stats["unique_ticker_days"],
            "eod_avg": stats["ret_eod"]["avg"],
            "eod_worst": stats["ret_eod"]["worst"],
            "win": stats["ret_eod"]["win_rate_pct"],
        }
        for name, stats in overcap["policy_stats"].items()
    ]
    lines.extend(_md_table(overcap_rows, ["policy", "count", "unique", "eod_avg", "eod_worst", "win"]))
    lines.extend(
        [
            "",
            "## Safety Notes",
            "",
            "- This report is read-only and does not submit orders.",
            "- KR policy rows are historical counterfactual policy cuts, not live gate changes.",
            "- US over-cap tier remains approval-only because notional is outside current PathB cap.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_next_wave_policy_simulation(
    *,
    event_db: str | Path = DEFAULT_EVENT_DB,
    perf_db: str | Path = DEFAULT_PERF_DB,
    price_root: str | Path = DEFAULT_PRICE_ROOT,
    output_root: str | Path = DEFAULT_OUTPUT_ROOT,
    output_dir: str = "",
) -> dict[str, Any]:
    out_dir = _output_dir(Path(output_root), output_dir)
    event_conn = _connect_ro(Path(event_db))
    perf_conn = _connect_ro(Path(perf_db))
    try:
        kr = _kr_policy_simulation(perf_conn)
        unfilled = _us_unfilled_audit(event_conn, Path(price_root))
        overcap = _us_overcap_simulation(event_conn, Path(price_root))
    finally:
        event_conn.close()
        perf_conn.close()

    json_path = out_dir / "ops_next_wave_policy_simulation.json"
    md_path = out_dir / "ops_next_wave_policy_simulation.md"
    kr_csv = out_dir / "kr_worst_trades.csv"
    unfilled_csv = out_dir / "us_unfilled_audit.csv"
    overcap_csv = out_dir / "us_overcap_tier.csv"
    payload = {
        "ok": True,
        "generated_at": _now_text(),
        "live_writes_performed": False,
        "inputs": {"event_db": str(event_db), "perf_db": str(perf_db), "price_root": str(price_root)},
        "summary": {"kr_policy": kr, "us_unfilled": unfilled, "us_overcap": overcap},
        "apply_prohibitions": [
            "do not submit orders from this report",
            "do not change live KR gates from historical cuts without forward monitoring",
            "do not enable over-cap tier without operator approval",
        ],
        "output_paths": {
            "json": str(json_path),
            "md": str(md_path),
            "kr_worst_csv": str(kr_csv),
            "us_unfilled_csv": str(unfilled_csv),
            "us_overcap_csv": str(overcap_csv),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(payload, md_path)
    _write_csv(
        kr_csv,
        kr["worst_trades"],
        ["session_date", "ticker", "strategy", "entry_bucket", "pnl_pct", "close_reason"],
    )
    _write_csv(
        unfilled_csv,
        unfilled["rows"],
        ["session_date", "ticker", "status", "cancel_reason", "limit", "classification"],
    )
    _write_csv(
        overcap_csv,
        overcap["rows"],
        [
            "session_date",
            "ticker",
            "ticker_day_rank",
            "buy_zone_low_krw",
            "buy_zone_high_krw",
            "ret_60m_pct",
            "ret_eod_pct",
            "mae_eod_pct",
        ],
    )
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only next-wave KR/US policy simulations.")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--perf-db", default=str(DEFAULT_PERF_DB))
    parser.add_argument("--price-root", default=str(DEFAULT_PRICE_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = build_next_wave_policy_simulation(
            event_db=args.event_db,
            perf_db=args.perf_db,
            price_root=args.price_root,
            output_root=args.output_root,
            output_dir=args.output_dir,
        )
    except RehearsalGuardError as exc:
        error = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(error, ensure_ascii=False, indent=2))
        else:
            print(f"ops_next_wave_policy_simulation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, "summary": payload["summary"], "output_paths": payload["output_paths"]}, ensure_ascii=False, indent=2))
    else:
        print(
            "ok "
            f"kr_trades={payload['summary']['kr_policy']['baseline']['count']} "
            f"unfilled={payload['summary']['us_unfilled']['unfilled_order_count']} "
            f"overcap={payload['summary']['us_overcap']['raw_count']} "
            f"md={payload['output_paths']['md']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
