from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


DEFAULT_WINDOWS = ("recent", "full_available")


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _safe_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _round(value: Any, digits: int = 4) -> float | None:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _parse_dt(value: Any) -> datetime | None:
    text = _safe_text(value)
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _date_minus(date_text: str, days: int) -> str:
    parsed = datetime.strptime(date_text, "%Y-%m-%d").date()
    return (parsed - timedelta(days=days)).isoformat()


def _split_windows(value: str) -> list[str]:
    windows = [_safe_text(item) for item in value.split(",")]
    windows = [item for item in windows if item]
    invalid = [item for item in windows if item not in DEFAULT_WINDOWS]
    if invalid:
        raise ValueError(f"invalid windows: {', '.join(invalid)}")
    return windows or list(DEFAULT_WINDOWS)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _data_date_bounds(
    conn: sqlite3.Connection,
    *,
    market: str,
    runtime_mode: str,
    reason: str,
) -> tuple[str | None, str | None]:
    if not _table_exists(conn, "pathb_miss_quality"):
        return None, None
    where = ["market = ?", "COALESCE(NULLIF(runtime_mode, ''), 'live') = ?"]
    params: list[Any] = [market, runtime_mode]
    if reason.upper() != "ALL":
        where.append("cancel_reason = ?")
        params.append(reason)
    row = conn.execute(
        f"""
        SELECT MIN(session_date) AS min_date, MAX(session_date) AS max_date
        FROM pathb_miss_quality
        WHERE {" AND ".join(where)}
        """,
        params,
    ).fetchone()
    if not row:
        return None, None
    return row["min_date"], row["max_date"]


def _resolve_windows(
    conn: sqlite3.Connection,
    *,
    market: str,
    runtime_mode: str,
    reason: str,
    lookback_days: int,
    date_from: str | None,
    date_to: str | None,
    windows: list[str],
) -> list[dict[str, Any]]:
    data_min, data_max = _data_date_bounds(
        conn,
        market=market,
        runtime_mode=runtime_mode,
        reason=reason,
    )
    explicit_range = bool(date_from or date_to)
    resolved: list[dict[str, Any]] = []
    for window in windows:
        if window == "full_available":
            start, end = data_min, data_max
        elif explicit_range:
            start = date_from or data_min
            end = date_to or data_max
        else:
            end = data_max
            start = _date_minus(end, max(lookback_days - 1, 0)) if end else None
        resolved.append({"window": window, "date_from": start, "date_to": end})
    return resolved


def _fetch_rows(
    conn: sqlite3.Connection,
    *,
    market: str,
    runtime_mode: str,
    reason: str,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    if not date_from or not date_to:
        return []
    if not _table_exists(conn, "pathb_miss_quality"):
        return []
    where = [
        "market = ?",
        "COALESCE(NULLIF(runtime_mode, ''), 'live') = ?",
        "session_date BETWEEN ? AND ?",
    ]
    params: list[Any] = [market, runtime_mode, date_from, date_to]
    if reason.upper() != "ALL":
        where.append("cancel_reason = ?")
        params.append(reason)
    rows = conn.execute(
        f"""
        SELECT
            id, path_run_id, decision_id, market, runtime_mode, session_date,
            ticker, cancelled_at, cancel_reason, current_at_plan, open_price,
            buy_zone_low, buy_zone_high, cancel_if_open_above,
            cancel_trigger_price, reference_price, baseline_price,
            baseline_source, market_close_at, followup_due_at,
            followup_filled_at, followup_status, zone_reentered_after_cancel,
            mfe_30m_pct, mae_30m_pct, observed_price_30m,
            quote_sample_count, payload_json, created_at, updated_at
        FROM pathb_miss_quality
        WHERE {" AND ".join(where)}
        ORDER BY session_date, cancelled_at, ticker, path_run_id
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def _load_path_run_status(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows or not _table_exists(conn, "v2_path_runs"):
        return {}
    path_ids = sorted({_safe_text(row.get("path_run_id")) for row in rows if row.get("path_run_id")})
    if not path_ids:
        return {}
    placeholders = ",".join("?" for _ in path_ids)
    try:
        meta_rows = conn.execute(
            f"""
            SELECT path_run_id, status, path_type, ticker, session_date, updated_at
            FROM v2_path_runs
            WHERE path_run_id IN ({placeholders})
            """,
            path_ids,
        ).fetchall()
    except sqlite3.Error:
        return {}
    return {row["path_run_id"]: dict(row) for row in meta_rows}


def _load_lifecycle_summary(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not rows or not _table_exists(conn, "lifecycle_events"):
        return {}
    decision_ids = sorted({_safe_text(row.get("decision_id")) for row in rows if row.get("decision_id")})
    if not decision_ids:
        return {}
    placeholders = ",".join("?" for _ in decision_ids)
    try:
        event_rows = conn.execute(
            f"""
            SELECT decision_id, event_type, occurred_at, reason_code, data_quality
            FROM lifecycle_events
            WHERE decision_id IN ({placeholders})
            ORDER BY decision_id, occurred_at
            """,
            decision_ids,
        ).fetchall()
    except sqlite3.Error:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in event_rows:
        grouped.setdefault(row["decision_id"], []).append(dict(row))
    summary: dict[str, dict[str, Any]] = {}
    for decision_id, events in grouped.items():
        latest = events[-1] if events else {}
        summary[decision_id] = {
            "lifecycle_event_count": len(events),
            "lifecycle_event_types": sorted(
                {_safe_text(event.get("event_type"), "unknown") for event in events}
            ),
            "latest_lifecycle_reason_code": latest.get("reason_code"),
            "latest_lifecycle_data_quality": latest.get("data_quality"),
        }
    return summary


def _payload(row: dict[str, Any]) -> dict[str, Any]:
    try:
        parsed = json.loads(row.get("payload_json") or "{}")
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _price_diagnostic_bucket(row: dict[str, Any], payload: dict[str, Any]) -> str:
    current = _safe_float(row.get("current_at_plan"))
    baseline = _safe_float(row.get("baseline_price"))
    reference = _safe_float(row.get("reference_price"))
    samples = _safe_int(row.get("quote_sample_count"))
    if current is None:
        if samples > 0 and (baseline is not None or reference is not None):
            return "current_missing_but_followup_quotes_available"
        return "current_price_missing"
    if current <= 0:
        return "current_price_non_positive"
    if baseline is None and reference is None:
        return "reference_price_missing"
    trigger_source = _safe_text(payload.get("cancel_trigger_source"))
    if trigger_source:
        return f"cancel_trigger_source:{trigger_source}"
    return "price_available_after_plan"


def _quote_age_bucket(row: dict[str, Any], payload: dict[str, Any]) -> str:
    samples = _safe_int(row.get("quote_sample_count"))
    sample_source = _safe_text(payload.get("sample_source"), "unknown")
    if samples <= 0:
        return f"no_samples:{sample_source}"
    if samples == 1:
        return f"single_sample:{sample_source}"
    if samples < 5:
        return f"sparse_samples_lt5:{sample_source}"
    return f"normal_samples:{sample_source}"


def _price_source_bucket(row: dict[str, Any], payload: dict[str, Any]) -> str:
    baseline_source = _safe_text(row.get("baseline_source"), "unknown_baseline")
    sample_source = _safe_text(payload.get("sample_source"), "unknown_sample")
    current = _safe_float(row.get("current_at_plan"))
    current_part = "plan_current_present" if current is not None and current > 0 else "plan_current_missing"
    return f"{current_part}|baseline:{baseline_source}|followup:{sample_source}"


def _tick_size_bucket(row: dict[str, Any]) -> str:
    market = _safe_text(row.get("market")).upper()
    prices = [
        _safe_float(row.get("current_at_plan")),
        _safe_float(row.get("buy_zone_low")),
        _safe_float(row.get("buy_zone_high")),
        _safe_float(row.get("reference_price")),
        _safe_float(row.get("baseline_price")),
    ]
    prices = [price for price in prices if price is not None and price > 0]
    if not prices:
        return "tick_size_unmeasured_no_positive_price"
    if market == "US":
        off_cent = any(abs((price * 100.0) - round(price * 100.0)) > 0.000001 for price in prices)
        return "sub_cent_precision_review" if off_cent else "cent_tick_plausible"
    if market == "KR":
        return "kr_tick_size_unverified_variable_band"
    return "tick_size_unclassified_market"


def _conversion_bucket(row: dict[str, Any]) -> str:
    market = _safe_text(row.get("market")).upper()
    prices = [
        _safe_float(row.get("current_at_plan")),
        _safe_float(row.get("buy_zone_low")),
        _safe_float(row.get("buy_zone_high")),
        _safe_float(row.get("reference_price")),
        _safe_float(row.get("baseline_price")),
    ]
    prices = [price for price in prices if price is not None and price > 0]
    if not prices:
        return "conversion_unmeasured_no_positive_price"
    max_price = max(prices)
    min_price = min(prices)
    if market == "US":
        if max_price >= 10000:
            return "possible_krw_scale_check"
        if max_price >= 1000:
            return "high_nominal_us_price_review"
        if min_price < 0.5:
            return "possible_fractional_or_bad_scale_check"
        return "native_usd_scale_plausible"
    if market == "KR":
        if max_price < 1000:
            return "possible_native_or_bad_krw_scale_check"
        return "native_krw_scale_plausible"
    return "market_scale_unclassified"


def _order_timing_bucket(row: dict[str, Any]) -> str:
    cancelled_at = _parse_dt(row.get("cancelled_at"))
    filled_at = _parse_dt(row.get("followup_filled_at"))
    followup_status = _safe_text(row.get("followup_status"), "unknown")
    if cancelled_at is None or filled_at is None:
        return f"followup_{followup_status}"
    minutes = (filled_at - cancelled_at).total_seconds() / 60.0
    if minutes <= 30:
        return "followup_filled_within_30m"
    if minutes <= 390:
        return "followup_filled_same_session"
    return "followup_filled_later"


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _metric_avg(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_safe_float(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return _round(mean(values), 4) if values else None


def _window_summary(
    *,
    window: str,
    date_from: str | None,
    date_to: str | None,
    market: str,
    reason: str,
    rows: list[dict[str, Any]],
    sample_limit: int,
) -> dict[str, Any]:
    zone_reentered = sum(1 for row in rows if _truthy(row.get("zone_reentered_after_cancel")))
    enriched_rows: list[dict[str, Any]] = []
    for row in rows:
        payload = _payload(row)
        enriched_rows.append(
            {
                "window": window,
                "market": row.get("market"),
                "cancel_reason": row.get("cancel_reason"),
                "ticker": row.get("ticker"),
                "path_run_id": row.get("path_run_id"),
                "decision_id": row.get("decision_id"),
                "session_date": row.get("session_date"),
                "cancelled_at": row.get("cancelled_at"),
                "current_at_plan": _round(row.get("current_at_plan")),
                "buy_zone_low": _round(row.get("buy_zone_low")),
                "buy_zone_high": _round(row.get("buy_zone_high")),
                "reference_price": _round(row.get("reference_price")),
                "baseline_price": _round(row.get("baseline_price")),
                "baseline_source": row.get("baseline_source"),
                "quote_sample_count": _safe_int(row.get("quote_sample_count")),
                "zone_reentered_after_cancel": _truthy(row.get("zone_reentered_after_cancel")),
                "mfe_30m_pct": _round(row.get("mfe_30m_pct")),
                "mae_30m_pct": _round(row.get("mae_30m_pct")),
                "followup_status": row.get("followup_status"),
                "followup_filled_at": row.get("followup_filled_at"),
                "sample_source": payload.get("sample_source"),
                "path_status": payload.get("path_status"),
                "path_run_status": row.get("path_run_status"),
                "path_type": row.get("path_type"),
                "lifecycle_event_count": row.get("lifecycle_event_count", 0),
                "lifecycle_event_types": row.get("lifecycle_event_types", []),
                "latest_lifecycle_reason_code": row.get("latest_lifecycle_reason_code"),
                "latest_lifecycle_data_quality": row.get("latest_lifecycle_data_quality"),
                "price_source_bucket": _price_source_bucket(row, payload),
                "price_diagnostic_bucket": _price_diagnostic_bucket(row, payload),
                "quote_age_bucket": _quote_age_bucket(row, payload),
                "tick_size_bucket": _tick_size_bucket(row),
                "conversion_bucket": _conversion_bucket(row),
                "order_timing_bucket": _order_timing_bucket(row),
            }
        )
    return {
        "window": window,
        "date_from": date_from,
        "date_to": date_to,
        "market": market,
        "cancel_reason": reason,
        "n": len(rows),
        "zone_reentered": zone_reentered,
        "zone_reentered_rate": _round((zone_reentered / len(rows)) * 100.0 if rows else 0.0, 2),
        "avg_mfe_30m_pct": _metric_avg(rows, "mfe_30m_pct"),
        "avg_mae_30m_pct": _metric_avg(rows, "mae_30m_pct"),
        "by_price_diagnostic_bucket": _counter_dict(Counter(row["price_diagnostic_bucket"] for row in enriched_rows)),
        "by_price_source_bucket": _counter_dict(Counter(row["price_source_bucket"] for row in enriched_rows)),
        "by_quote_age_bucket": _counter_dict(Counter(row["quote_age_bucket"] for row in enriched_rows)),
        "by_tick_size_bucket": _counter_dict(Counter(row["tick_size_bucket"] for row in enriched_rows)),
        "by_conversion_bucket": _counter_dict(Counter(row["conversion_bucket"] for row in enriched_rows)),
        "by_order_timing_bucket": _counter_dict(Counter(row["order_timing_bucket"] for row in enriched_rows)),
        "by_ticker": _counter_dict(Counter(_safe_text(row.get("ticker"), "unknown") for row in rows)),
        "rows_returned": min(len(enriched_rows), sample_limit),
        "rows": enriched_rows[:sample_limit],
    }


def analyze_pathb_invalid_price_miss(
    *,
    event_db: str | Path,
    market: str = "US",
    runtime_mode: str = "live",
    reason: str = "INVALID_PRICE",
    lookback_days: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
    windows: list[str] | tuple[str, ...] = DEFAULT_WINDOWS,
    sample_limit: int = 200,
) -> dict[str, Any]:
    conn = _connect_readonly(event_db)
    try:
        resolved_windows = _resolve_windows(
            conn,
            market=market,
            runtime_mode=runtime_mode,
            reason=reason,
            lookback_days=lookback_days,
            date_from=date_from,
            date_to=date_to,
            windows=list(windows),
        )
        window_reports: dict[str, Any] = {}
        path_run_join_available = _table_exists(conn, "v2_path_runs")
        for spec in resolved_windows:
            rows = _fetch_rows(
                conn,
                market=market,
                runtime_mode=runtime_mode,
                reason=reason,
                date_from=spec["date_from"],
                date_to=spec["date_to"],
            )
            meta_by_path_run = _load_path_run_status(conn, rows)
            lifecycle_by_decision = _load_lifecycle_summary(conn, rows)
            for row in rows:
                meta = meta_by_path_run.get(_safe_text(row.get("path_run_id")), {})
                if meta:
                    row["path_run_status"] = meta.get("status")
                    row["path_type"] = meta.get("path_type")
                lifecycle = lifecycle_by_decision.get(_safe_text(row.get("decision_id")), {})
                if lifecycle:
                    row.update(lifecycle)
            report = _window_summary(
                window=spec["window"],
                date_from=spec["date_from"],
                date_to=spec["date_to"],
                market=market,
                reason=reason,
                rows=rows,
                sample_limit=sample_limit,
            )
            if meta_by_path_run:
                report["by_path_run_status"] = _counter_dict(
                    Counter(_safe_text(row.get("path_run_status"), "unknown") for row in rows)
                )
            window_reports[spec["window"]] = report
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "report": "pathb_invalid_price_miss",
            "source": {
                "event_db": str(Path(event_db)),
                "table": "pathb_miss_quality",
                "table_available": _table_exists(conn, "pathb_miss_quality"),
                "optional_path_run_join": path_run_join_available,
                "optional_lifecycle_join": _table_exists(conn, "lifecycle_events"),
            },
            "scope": {
                "market": market,
                "runtime_mode": runtime_mode,
                "reason": reason,
                "lookback_days": lookback_days,
                "requested_date_from": date_from,
                "requested_date_to": date_to,
                "windows": list(windows),
                "sample_limit": sample_limit,
            },
            "windows": window_reports,
        }
    finally:
        conn.close()


def _render_text(report: dict[str, Any]) -> str:
    lines = ["PathB INVALID_PRICE miss diagnostics", ""]
    for name, window in report["windows"].items():
        lines.append(
            f"[{name}] {window['date_from']}..{window['date_to']} "
            f"market={window['market']} reason={window['cancel_reason']} "
            f"n={window['n']} zone_reentered={window['zone_reentered']} "
            f"zone_reentered_rate={window['zone_reentered_rate']}% "
            f"avg_mfe_30m={window['avg_mfe_30m_pct']} avg_mae_30m={window['avg_mae_30m_pct']}"
        )
        lines.append(f"  by_price_diagnostic_bucket={window['by_price_diagnostic_bucket']}")
        lines.append(f"  by_price_source_bucket={window['by_price_source_bucket']}")
        lines.append(f"  by_quote_age_bucket={window['by_quote_age_bucket']}")
        lines.append(f"  by_tick_size_bucket={window['by_tick_size_bucket']}")
        lines.append(f"  by_conversion_bucket={window['by_conversion_bucket']}")
        lines.append(f"  by_order_timing_bucket={window['by_order_timing_bucket']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only PathB INVALID_PRICE miss diagnostics")
    parser.add_argument("--event-db", default=str(get_runtime_path("data", "v2_event_store.db")))
    parser.add_argument("--market", default="US")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--reason", default="INVALID_PRICE")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--windows", default=",".join(DEFAULT_WINDOWS))
    parser.add_argument("--sample-limit", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    report = analyze_pathb_invalid_price_miss(
        event_db=args.event_db,
        market=args.market,
        runtime_mode=args.runtime_mode,
        reason=args.reason,
        lookback_days=args.lookback_days,
        date_from=args.date_from,
        date_to=args.date_to,
        windows=_split_windows(args.windows),
        sample_limit=max(args.sample_limit, 0),
    )
    output = json.dumps(report, ensure_ascii=False, indent=2) if args.json or args.output else _render_text(report)
    if args.output:
        Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
