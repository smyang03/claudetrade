from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path


RECENT_START = "2026-06-01"
RECENT_END = "2026-06-05"
DEFAULT_WINDOWS = ("recent", "primary", "full_available")
ORP_STRATEGY = "opening_range_pullback"


def _connect_readonly(path: str | Path) -> sqlite3.Connection:
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")
    uri = f"file:{db_path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _connect_optional_readonly(path: str | Path) -> sqlite3.Connection | None:
    db_path = Path(path)
    if not db_path.exists():
        return None
    return _connect_readonly(db_path)


def _safe_text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _safe_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except Exception:
        return 0


def _safe_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _round(value: Any, digits: int = 4) -> float | None:
    number = _safe_float(value)
    return round(number, digits) if number is not None else None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


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


def _table_exists(conn: sqlite3.Connection | None, table: str) -> bool:
    if conn is None:
        return False
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
) -> tuple[str | None, str | None]:
    if not _table_exists(conn, "ticker_selection_log"):
        return None, None
    row = conn.execute(
        """
        SELECT MIN(date) AS min_date, MAX(date) AS max_date
        FROM ticker_selection_log
        WHERE market = ?
          AND COALESCE(NULLIF(bot_mode, ''), 'live') = ?
        """,
        (market, runtime_mode),
    ).fetchone()
    if not row:
        return None, None
    return row["min_date"], row["max_date"]


def _resolve_windows(
    conn: sqlite3.Connection,
    *,
    market: str,
    runtime_mode: str,
    lookback_days: int,
    date_from: str | None,
    date_to: str | None,
    windows: list[str],
) -> list[dict[str, Any]]:
    data_min, data_max = _data_date_bounds(conn, market=market, runtime_mode=runtime_mode)
    explicit_range = bool(date_from or date_to)
    resolved: list[dict[str, Any]] = []
    for window in windows:
        if window == "full_available":
            start, end = data_min, data_max
        elif explicit_range:
            start = date_from or data_min
            end = date_to or data_max
        elif window == "recent":
            start, end = RECENT_START, RECENT_END
        else:
            end = data_max
            start = _date_minus(end, max(lookback_days - 1, 0)) if end else None
        resolved.append({"window": window, "date_from": start, "date_to": end})
    return resolved


def _fetch_selection_rows(
    conn: sqlite3.Connection,
    *,
    market: str,
    runtime_mode: str,
    date_from: str | None,
    date_to: str | None,
) -> list[dict[str, Any]]:
    if not date_from or not date_to:
        return []
    if not _table_exists(conn, "ticker_selection_log"):
        return []
    rows = conn.execute(
        """
        SELECT
            date, market, ticker, source_type, selected_at, signal_at,
            trade_ready, signal_fired, traded, strategy_name,
            recommended_strategy, blocked_reason, selected_reason_tag,
            execution_reason, bot_mode
        FROM ticker_selection_log
        WHERE market = ?
          AND COALESCE(NULLIF(bot_mode, ''), 'live') = ?
          AND date BETWEEN ? AND ?
        ORDER BY date, selected_at, ticker
        """,
        (market, runtime_mode, date_from, date_to),
    ).fetchall()
    return [dict(row) for row in rows]


def _fetch_orp_rows(
    conn: sqlite3.Connection | None,
    *,
    market: str,
    runtime_mode: str,
    date_from: str | None,
    date_to: str | None,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    if conn is None or not date_from or not date_to:
        return {}
    if not _table_exists(conn, "intraday_strategy_log"):
        return {}
    rows = conn.execute(
        """
        SELECT
            ts, session_date, market, ticker, strategy_name, stage,
            or_formed, entry_window_elapsed_min, signal_fired, traded,
            blocked_reason, note, bot_mode
        FROM intraday_strategy_log
        WHERE market = ?
          AND COALESCE(NULLIF(bot_mode, ''), 'live') = ?
          AND session_date BETWEEN ? AND ?
          AND strategy_name = ?
        ORDER BY session_date, ticker, ts
        """,
        (market, runtime_mode, date_from, date_to, ORP_STRATEGY),
    ).fetchall()
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        item = dict(row)
        indexed[(item["session_date"], item["ticker"])].append(item)
    return dict(indexed)


def _strategy(row: dict[str, Any]) -> str:
    return _safe_text(row.get("strategy_name") or row.get("recommended_strategy"), "unknown")


def _choose_nearest_orp_row(
    selection_row: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not candidates:
        return None
    anchor = _parse_dt(selection_row.get("selected_at") or selection_row.get("signal_at"))
    if anchor is None:
        return candidates[-1]

    def distance(row: dict[str, Any]) -> tuple[int, float]:
        ts = _parse_dt(row.get("ts"))
        if ts is None:
            return (1, float("inf"))
        return (0, abs((ts - anchor).total_seconds()))

    return min(candidates, key=distance)


def _window_phase(orp_row: dict[str, Any] | None) -> str:
    if orp_row is None:
        return "not_applicable"
    elapsed = _safe_float(orp_row.get("entry_window_elapsed_min"))
    or_formed = _truthy(orp_row.get("or_formed"))
    if not or_formed:
        return "before_or_formed"
    if elapsed is None:
        return "unknown"
    if elapsed < 0:
        return "before_entry_window"
    if elapsed < 50:
        return "inside_entry_window"
    if elapsed <= 60:
        return "near_expiry"
    return "after_expiry"


def _risk_order_bucket(selection_row: dict[str, Any], orp_row: dict[str, Any] | None) -> str:
    parts = [
        selection_row.get("blocked_reason"),
        selection_row.get("execution_reason"),
        selection_row.get("selected_reason_tag"),
        orp_row.get("blocked_reason") if orp_row else None,
    ]
    text = " ".join(_safe_text(part).lower() for part in parts if part)
    if not text:
        return "not_observed"
    if any(token in text for token in ("broker", "truth", "quarantine", "kis")):
        return "broker_truth_or_visibility"
    if any(token in text for token in ("risk", "blackout", "halt", "loss", "cap", "reentry")):
        return "risk_or_session_gate"
    if any(token in text for token in ("cash", "afford", "budget", "order", "size", "qty")):
        return "order_or_affordability_gate"
    return "not_observed"


def _evidence_bucket(strategy: str, orp_row: dict[str, Any] | None) -> str:
    if strategy != ORP_STRATEGY:
        return "not_applicable"
    if orp_row is None:
        return "intraday_orp_evidence_missing"
    reason = _safe_text(orp_row.get("blocked_reason")).lower()
    if reason in {"orp_forming", "orp_not_formed"}:
        return "orp_not_ready"
    if reason:
        return "intraday_orp_evidence_present"
    return "intraday_orp_evidence_without_reason"


def _nosignal_bucket(strategy: str, orp_row: dict[str, Any] | None) -> str:
    if strategy == ORP_STRATEGY:
        reason = _safe_text(orp_row.get("blocked_reason") if orp_row else None, "orp_evidence_missing")
        return f"strategy_signal:orp:{reason}"
    if strategy == "gap_pullback":
        return "strategy_signal:gap_pullback_no_signal"
    if strategy == "momentum":
        return "strategy_signal:momentum_no_signal"
    if strategy == "mean_reversion":
        return "strategy_signal:mean_reversion_no_signal"
    return f"strategy_signal:{strategy}_no_signal"


def _counter_dict(counter: Counter[str]) -> dict[str, int]:
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _window_summary(
    *,
    window: str,
    date_from: str | None,
    date_to: str | None,
    market: str,
    rows: list[dict[str, Any]],
    no_signal_rows: list[dict[str, Any]],
    sample_limit: int,
) -> dict[str, Any]:
    trade_ready_rows = [row for row in rows if _truthy(row.get("trade_ready"))]
    signal_rows = [row for row in rows if _truthy(row.get("signal_fired"))]
    traded_rows = [row for row in rows if _truthy(row.get("traded"))]
    trade_ready_signal_rows = [row for row in trade_ready_rows if _truthy(row.get("signal_fired"))]
    trade_ready_traded_rows = [row for row in trade_ready_rows if _truthy(row.get("traded"))]
    strategy_counts = Counter(_strategy(row) for row in trade_ready_rows)
    nosignal_counts = Counter(row["nosignal_bucket"] for row in no_signal_rows)
    evidence_counts = Counter(row["evidence_bucket"] for row in no_signal_rows)
    risk_order_counts = Counter(row["risk_order_bucket"] for row in no_signal_rows)
    orp_reason_counts = Counter(
        _safe_text(row.get("orp_block_reason"), "none") for row in no_signal_rows
        if row.get("strategy") == ORP_STRATEGY
    )
    elapsed_values = [
        _safe_float(row.get("entry_window_elapsed_min")) for row in no_signal_rows
        if _safe_float(row.get("entry_window_elapsed_min")) is not None
    ]
    return {
        "window": window,
        "date_from": date_from,
        "date_to": date_to,
        "market": market,
        "total_selection_rows": len(rows),
        "trade_ready": len(trade_ready_rows),
        "signal_fired": len(trade_ready_signal_rows),
        "traded": len(trade_ready_traded_rows),
        "no_signal": len(no_signal_rows),
        "total_signal_fired_rows": len(signal_rows),
        "total_traded_rows": len(traded_rows),
        "by_strategy": _counter_dict(strategy_counts),
        "by_nosignal_bucket": _counter_dict(nosignal_counts),
        "by_evidence_bucket": _counter_dict(evidence_counts),
        "by_risk_order_bucket": _counter_dict(risk_order_counts),
        "by_orp_block_reason": _counter_dict(orp_reason_counts),
        "by_window_phase": _counter_dict(Counter(row["window_phase"] for row in no_signal_rows)),
        "avg_entry_window_elapsed_min": _round(mean(elapsed_values), 2) if elapsed_values else None,
        "rows_returned": min(len(no_signal_rows), sample_limit),
        "rows": no_signal_rows[:sample_limit],
    }


def analyze_kr_nosignal_orp(
    *,
    selection_db: str | Path,
    intraday_db: str | Path,
    market: str = "KR",
    runtime_mode: str = "live",
    lookback_days: int = 30,
    date_from: str | None = None,
    date_to: str | None = None,
    windows: list[str] | tuple[str, ...] = DEFAULT_WINDOWS,
    sample_limit: int = 200,
) -> dict[str, Any]:
    selection_conn = _connect_readonly(selection_db)
    intraday_conn = _connect_optional_readonly(intraday_db)
    try:
        resolved_windows = _resolve_windows(
            selection_conn,
            market=market,
            runtime_mode=runtime_mode,
            lookback_days=lookback_days,
            date_from=date_from,
            date_to=date_to,
            windows=list(windows),
        )
        window_reports: dict[str, Any] = {}
        for spec in resolved_windows:
            selection_rows = _fetch_selection_rows(
                selection_conn,
                market=market,
                runtime_mode=runtime_mode,
                date_from=spec["date_from"],
                date_to=spec["date_to"],
            )
            orp_rows = _fetch_orp_rows(
                intraday_conn,
                market=market,
                runtime_mode=runtime_mode,
                date_from=spec["date_from"],
                date_to=spec["date_to"],
            )
            no_signal_rows: list[dict[str, Any]] = []
            for selection_row in selection_rows:
                if not _truthy(selection_row.get("trade_ready")):
                    continue
                if _truthy(selection_row.get("signal_fired")):
                    continue
                strategy = _strategy(selection_row)
                candidates = orp_rows.get((selection_row["date"], selection_row["ticker"]), [])
                orp_row = _choose_nearest_orp_row(selection_row, candidates) if strategy == ORP_STRATEGY else None
                no_signal_rows.append(
                    {
                        "window": spec["window"],
                        "date_from": spec["date_from"],
                        "date_to": spec["date_to"],
                        "market": market,
                        "strategy": strategy,
                        "ticker": selection_row.get("ticker"),
                        "selected_at": selection_row.get("selected_at"),
                        "signal_at": selection_row.get("signal_at"),
                        "source_type": selection_row.get("source_type"),
                        "recommended_strategy": selection_row.get("recommended_strategy"),
                        "orp_block_reason": orp_row.get("blocked_reason") if orp_row else None,
                        "orp_stage": orp_row.get("stage") if orp_row else None,
                        "orp_ts": orp_row.get("ts") if orp_row else None,
                        "entry_window_elapsed_min": _round(
                            orp_row.get("entry_window_elapsed_min") if orp_row else None,
                            2,
                        ),
                        "window_phase": _window_phase(orp_row),
                        "nosignal_bucket": _nosignal_bucket(strategy, orp_row),
                        "evidence_bucket": _evidence_bucket(strategy, orp_row),
                        "risk_order_bucket": _risk_order_bucket(selection_row, orp_row),
                    }
                )
            window_reports[spec["window"]] = _window_summary(
                window=spec["window"],
                date_from=spec["date_from"],
                date_to=spec["date_to"],
                market=market,
                rows=selection_rows,
                no_signal_rows=no_signal_rows,
                sample_limit=sample_limit,
            )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "report": "kr_nosignal_orp",
            "source": {
                "selection_db": str(Path(selection_db)),
                "intraday_db": str(Path(intraday_db)),
                "selection_table": "ticker_selection_log",
                "intraday_table": "intraday_strategy_log",
                "selection_table_available": _table_exists(selection_conn, "ticker_selection_log"),
                "intraday_table_available": _table_exists(intraday_conn, "intraday_strategy_log"),
            },
            "scope": {
                "market": market,
                "runtime_mode": runtime_mode,
                "lookback_days": lookback_days,
                "requested_date_from": date_from,
                "requested_date_to": date_to,
                "windows": list(windows),
                "sample_limit": sample_limit,
            },
            "windows": window_reports,
        }
    finally:
        selection_conn.close()
        if intraday_conn is not None:
            intraday_conn.close()


def _render_text(report: dict[str, Any]) -> str:
    lines = ["KR no-signal / ORP timing report", ""]
    for name, window in report["windows"].items():
        lines.append(
            f"[{name}] {window['date_from']}..{window['date_to']} "
            f"selection={window['total_selection_rows']} trade_ready={window['trade_ready']} "
            f"signal_fired={window['signal_fired']} traded={window['traded']} "
            f"no_signal={window['no_signal']}"
        )
        lines.append(f"  by_strategy={window['by_strategy']}")
        lines.append(f"  by_nosignal_bucket={window['by_nosignal_bucket']}")
        lines.append(f"  by_window_phase={window['by_window_phase']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only KR trade_ready -> NO_SIGNAL / ORP timing report")
    parser.add_argument("--selection-db", default=str(get_runtime_path("data", "ticker_selection_log.db")))
    parser.add_argument("--intraday-db", default=str(get_runtime_path("data", "intraday_strategy_log.db")))
    parser.add_argument("--market", default="KR")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--date-from")
    parser.add_argument("--date-to")
    parser.add_argument("--windows", default=",".join(DEFAULT_WINDOWS))
    parser.add_argument("--sample-limit", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    report = analyze_kr_nosignal_orp(
        selection_db=args.selection_db,
        intraday_db=args.intraday_db,
        market=args.market,
        runtime_mode=args.runtime_mode,
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
