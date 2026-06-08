from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.rehearsal.context import RehearsalGuardError

KST = ZoneInfo("Asia/Seoul")
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_CANDIDATE_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price"
DEFAULT_RUNTIME_ROOT = ROOT / ".runtime" / "ops_simulation_batches"
MINUTE_BAR_TOLERANCE = timedelta(seconds=90)


@dataclass(frozen=True)
class TapeRows:
    rows: list[dict[str, Any]]
    price_file: Path
    start_at: str
    end_at: str
    coverage: dict[str, Any]


def _now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _utc_now_text() -> str:
    return datetime.now().astimezone().replace(microsecond=0).isoformat()


def _ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_ro(path: Path) -> sqlite3.Connection:
    source = Path(path)
    if not source.exists():
        raise RehearsalGuardError(f"read-only DB not found: {source}")
    conn = sqlite3.connect(_ro_uri(source), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_name(value: str, *, default: str = "case") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned[:140] or default


def _coerce_float(value: Any, default: float = 0.0) -> float:
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


def _parse_dt(value: Any, *, end_of_day: bool = False) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            base = datetime.fromisoformat(raw)
            local_time = time.max if end_of_day else time.min
            return datetime.combine(base.date(), local_time, tzinfo=KST)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _dt_text(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _min_dt(*values: Any) -> datetime | None:
    parsed = [dt for dt in (_parse_dt(value) for value in values) if dt is not None]
    return min(parsed) if parsed else None


def _max_dt(*values: Any) -> datetime | None:
    parsed = [dt for dt in (_parse_dt(value, end_of_day=True) for value in values) if dt is not None]
    return max(parsed) if parsed else None


def _market_session_end(market: str, reference: datetime | None) -> datetime | None:
    if reference is None:
        return None
    ref = reference.astimezone(KST)
    market_key = str(market or "").upper()
    if market_key == "KR":
        return datetime.combine(ref.date(), time(15, 30), tzinfo=KST)
    if market_key == "US":
        if ref.time() <= time(5, 0):
            return datetime.combine(ref.date(), time(5, 0), tzinfo=KST)
        return datetime.combine(ref.date() + timedelta(days=1), time(5, 0), tzinfo=KST)
    return datetime.combine(ref.date(), time.max, tzinfo=KST)


def _price_file(price_root: Path, market: str, ticker: str) -> Path | None:
    market_key = str(market or "").lower()
    ticker_key = str(ticker or "").upper() if market_key == "us" else str(ticker or "")
    candidates = [
        price_root / "minute" / market_key / f"{market_key}_{ticker_key}.csv",
        price_root / market_key / f"{market_key}_{ticker_key}.csv",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _price_file_text(source: Path | None) -> str:
    if source is None:
        return ""
    try:
        return str(source.relative_to(ROOT))
    except ValueError:
        return str(source)


def _price_coverage_payload(
    *,
    price_file: Path | None,
    requested_start_at: datetime | None,
    requested_end_at: datetime | None,
    actual_start_at: str = "",
    actual_end_at: str = "",
    matched_rows: int = 0,
    max_rows_limit_hit: bool = False,
    missing_reason: str = "",
) -> dict[str, Any]:
    flags: list[str] = []
    actual_start_dt = _parse_dt(actual_start_at)
    actual_end_dt = _parse_dt(actual_end_at)
    if missing_reason:
        flags.append(missing_reason)
    if matched_rows > 0:
        if (
            requested_start_at is not None
            and actual_start_dt is not None
            and actual_start_dt - requested_start_at > MINUTE_BAR_TOLERANCE
        ):
            flags.append("start_after_requested")
        if (
            requested_end_at is not None
            and actual_end_dt is not None
            and requested_end_at - actual_end_dt > MINUTE_BAR_TOLERANCE
        ):
            flags.append("end_before_requested")
        if max_rows_limit_hit:
            flags.append("max_rows_limit_hit")

    if missing_reason:
        status = missing_reason
    elif not flags:
        status = "complete"
    else:
        status = "partial"

    return {
        "coverage_status": status,
        "coverage_flags": flags,
        "requested_start_at": _dt_text(requested_start_at),
        "requested_end_at": _dt_text(requested_end_at),
        "actual_start_at": str(actual_start_at or ""),
        "actual_end_at": str(actual_end_at or ""),
        "matched_rows": int(matched_rows or 0),
        "price_file": _price_file_text(price_file),
    }


def _missing_price_coverage(
    *,
    price_root: Path,
    market: str,
    ticker: str,
    start_at: datetime | None,
    end_at: datetime | None,
) -> dict[str, Any]:
    source = _price_file(price_root, market, ticker)
    reason = "price_file_missing" if source is None else "price_rows_empty_after_filter"
    return _price_coverage_payload(
        price_file=source,
        requested_start_at=start_at,
        requested_end_at=end_at,
        missing_reason=reason,
    )


def _read_price_tape(
    *,
    price_root: Path,
    market: str,
    ticker: str,
    start_at: datetime | None,
    end_at: datetime | None,
    max_rows: int,
) -> TapeRows | None:
    source = _price_file(price_root, market, ticker)
    if source is None:
        return None
    rows: list[dict[str, Any]] = []
    max_rows_limit_hit = False
    with source.open("r", encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        for raw in reader:
            ts_value = raw.get("ts") or raw.get("date")
            price_value = raw.get("close") or raw.get("price")
            if not ts_value or price_value in (None, ""):
                continue
            row_dt = _parse_dt(ts_value)
            if start_at is not None and row_dt is not None and row_dt < start_at:
                continue
            if end_at is not None and row_dt is not None and row_dt > end_at:
                continue
            price = _coerce_float(price_value)
            if price <= 0:
                continue
            rows.append({"ts": str(ts_value), "price": price})
            if max_rows > 0 and len(rows) >= max_rows:
                max_rows_limit_hit = True
                break
    if not rows:
        return None
    coverage = _price_coverage_payload(
        price_file=source,
        requested_start_at=start_at,
        requested_end_at=end_at,
        actual_start_at=rows[0]["ts"],
        actual_end_at=rows[-1]["ts"],
        matched_rows=len(rows),
        max_rows_limit_hit=max_rows_limit_hit,
    )
    return TapeRows(
        rows=rows,
        price_file=source,
        start_at=rows[0]["ts"],
        end_at=rows[-1]["ts"],
        coverage=coverage,
    )


def _write_tape(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=["ts", "price"])
        writer.writeheader()
        writer.writerows(rows)


def _case_record(
    *,
    name: str,
    market: str,
    ticker: str,
    path_type: str,
    tape_rel: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "market": market,
        "ticker": ticker,
        "path_type": path_type,
        "tape_file": tape_rel,
        "params": params,
    }


def _pathb_rows(
    *,
    db_path: Path,
    market: str,
    start_date: str,
    end_date: str,
    limit: int,
) -> list[sqlite3.Row]:
    filters = ["runtime_mode = 'live'", "status = 'CLOSED'", "COALESCE(plan_json, '') != ''"]
    params: list[Any] = []
    if market.upper() in {"KR", "US"}:
        filters.append("market = ?")
        params.append(market.upper())
    if start_date:
        filters.append("session_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("session_date <= ?")
        params.append(end_date)
    params.append(int(limit))
    sql = f"""
        SELECT path_run_id, decision_id, market, session_date, ticker, status,
               plan_json, created_at, updated_at
        FROM v2_path_runs
        WHERE {' AND '.join(filters)}
        ORDER BY COALESCE(updated_at, created_at, session_date) DESC
        LIMIT ?
    """
    with _connect_ro(db_path) as conn:
        return list(conn.execute(sql, params))


def _candidate_rows(
    *,
    db_path: Path,
    market: str,
    start_date: str,
    end_date: str,
    limit: int,
    min_runup_pct: float,
) -> list[sqlite3.Row]:
    filters = [
        "status = 'CLOSE_OUTCOME_FILLED'",
        "COALESCE(actual_path, '') = 'no_entry'",
        "entry_price IS NOT NULL",
        "max_runup_60m_pct IS NOT NULL",
        "max_runup_60m_pct >= ?",
    ]
    params: list[Any] = [float(min_runup_pct)]
    if market.upper() in {"KR", "US"}:
        filters.append("market = ?")
        params.append(market.upper())
    if start_date:
        filters.append("session_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("session_date <= ?")
        params.append(end_date)
    params.append(int(limit))
    sql = f"""
        SELECT market, session_date, ticker, candidate_key, actual_path, path_name,
               signal_time, trigger_time, trigger_price, entry_price,
               outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
               max_runup_60m_pct, max_drawdown_60m_pct, status
        FROM candidate_counterfactual_paths
        WHERE {' AND '.join(filters)}
        ORDER BY max_runup_60m_pct DESC, session_date DESC
        LIMIT ?
    """
    with _connect_ro(db_path) as conn:
        return list(conn.execute(sql, params))


def _pathb_case_from_row(
    row: sqlite3.Row,
    *,
    price_root: Path,
    out_dir: Path,
    index: int,
    max_tape_rows: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    plan = _json_obj(row["plan_json"])
    market = str(row["market"] or plan.get("market") or "").upper()
    ticker = str(row["ticker"] or plan.get("ticker") or "")
    entry = _coerce_float(plan.get("actual_entry_price") or plan.get("entry_price") or plan.get("hit_price"))
    exit_price = _coerce_float(plan.get("actual_exit_price") or plan.get("exit_price"))
    buy_low = _coerce_float(plan.get("buy_zone_low"), entry * 0.98 if entry else 0.0)
    buy_high = _coerce_float(plan.get("buy_zone_high"), entry * 1.01 if entry else 0.0)
    target = _coerce_float(plan.get("sell_target") or plan.get("target_price"), exit_price or (entry * 1.04 if entry else 0.0))
    stop = _coerce_float(plan.get("stop_loss") or plan.get("hard_stop"), entry * 0.98 if entry else 0.0)
    if not market or not ticker or buy_low <= 0 or buy_high <= 0:
        return None, {"source": "pathb_historical", "reason": "missing_plan_core", "path_run_id": row["path_run_id"]}

    start_at = _min_dt(plan.get("created_at"), row["created_at"], plan.get("filled_at"), plan.get("entry_filled_at"))
    if start_at is None:
        start_at = _parse_dt(row["session_date"])
    end_at = _max_dt(
        plan.get("closed_at"),
        plan.get("exit_filled_at"),
        plan.get("auto_sell_reviewed_at"),
        row["updated_at"],
    )
    if end_at is None:
        end_at = _market_session_end(market, start_at or _parse_dt(row["session_date"]))
    tape = _read_price_tape(
        price_root=price_root,
        market=market,
        ticker=ticker,
        start_at=start_at,
        end_at=end_at,
        max_rows=max_tape_rows,
    )
    if tape is None:
        coverage = _missing_price_coverage(
            price_root=price_root,
            market=market,
            ticker=ticker,
            start_at=start_at,
            end_at=end_at,
        )
        return None, {
            "source": "pathb_historical",
            "reason": coverage["coverage_status"],
            "market": market,
            "ticker": ticker,
            "path_run_id": row["path_run_id"],
            "price_coverage": coverage,
        }

    qty = max(1.0, _coerce_float(plan.get("entry_qty") or plan.get("filled_qty"), 1.0))
    usd_krw = 1350.0
    budget_basis = max(entry or 0.0, buy_high or 0.0, tape.rows[0]["price"])
    native_krw = budget_basis * (usd_krw if market == "US" else 1.0)
    fixed_order_krw = max(450_000.0, native_krw * qty * 1.02)
    case_name = _safe_name(f"pathb_{row['session_date']}_{market}_{ticker}_{index}")
    tape_path = out_dir / "tapes" / f"{case_name}.csv"
    _write_tape(tape_path, tape.rows)

    params = {
        "source": "pathb_historical",
        "source_db": "v2_event_store",
        "path_run_id": row["path_run_id"],
        "decision_id": row["decision_id"],
        "session_date": row["session_date"],
        "buy_zone_low": round(buy_low, 6),
        "buy_zone_high": round(buy_high, 6),
        "target_price": round(target, 6),
        "stop_price": round(stop, 6),
        "confidence": _coerce_float(plan.get("confidence"), 0.72),
        "fixed_order_krw": round(fixed_order_krw, 2),
        "observed_entry_price": entry or None,
        "observed_exit_price": exit_price or None,
        "observed_close_reason": plan.get("close_reason") or plan.get("auto_sell_review_close_reason") or "",
        "observed_tape_start": tape.start_at,
        "observed_tape_end": tape.end_at,
        "observed_price_file": str(tape.price_file.relative_to(ROOT)) if tape.price_file.is_relative_to(ROOT) else str(tape.price_file),
        "price_coverage": tape.coverage,
    }
    return (
        _case_record(
            name=case_name,
            market=market,
            ticker=ticker,
            path_type="claude_price",
            tape_rel=str(tape_path.relative_to(out_dir)).replace("\\", "/"),
            params=params,
        ),
        None,
    )


def _candidate_case_from_row(
    row: sqlite3.Row,
    *,
    price_root: Path,
    out_dir: Path,
    index: int,
    max_tape_rows: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    market = str(row["market"] or "").upper()
    ticker = str(row["ticker"] or "")
    entry = _coerce_float(row["entry_price"] or row["trigger_price"])
    if not market or not ticker or entry <= 0:
        return None, {"source": "counterfactual_missed", "reason": "missing_candidate_core", "candidate_key": row["candidate_key"]}
    start_at = _parse_dt(row["trigger_time"] or row["signal_time"] or row["session_date"])
    end_at = _market_session_end(market, start_at or _parse_dt(row["signal_time"]) or _parse_dt(row["session_date"]))
    tape = _read_price_tape(
        price_root=price_root,
        market=market,
        ticker=ticker,
        start_at=start_at,
        end_at=end_at,
        max_rows=max_tape_rows,
    )
    if tape is None:
        coverage = _missing_price_coverage(
            price_root=price_root,
            market=market,
            ticker=ticker,
            start_at=start_at,
            end_at=end_at,
        )
        return None, {
            "source": "counterfactual_missed",
            "reason": coverage["coverage_status"],
            "market": market,
            "ticker": ticker,
            "candidate_key": row["candidate_key"],
            "price_coverage": coverage,
        }

    runup = _coerce_float(row["max_runup_60m_pct"])
    drawdown = abs(_coerce_float(row["max_drawdown_60m_pct"]))
    target_pct = min(max(runup * 0.5, 2.0), 8.0)
    stop_pct = min(max(drawdown + 0.5, 1.5), 5.0)
    case_name = _safe_name(f"missed_{row['session_date']}_{market}_{ticker}_{row['path_name']}_{index}")
    tape_path = out_dir / "tapes" / f"{case_name}.csv"
    _write_tape(tape_path, tape.rows)

    params = {
        "source": "counterfactual_missed",
        "source_db": "candidate_audit",
        "candidate_key": row["candidate_key"],
        "session_date": row["session_date"],
        "counterfactual_path": row["path_name"],
        "buy_zone_low": round(entry * 0.995, 6),
        "buy_zone_high": round(entry * 1.005, 6),
        "target_price": round(entry * (1.0 + target_pct / 100.0), 6),
        "stop_price": round(entry * (1.0 - stop_pct / 100.0), 6),
        "confidence": 0.72,
        "observed_entry_price": entry,
        "observed_outcome_30m_pct": row["outcome_30m_pct"],
        "observed_outcome_60m_pct": row["outcome_60m_pct"],
        "observed_outcome_close_pct": row["outcome_close_pct"],
        "observed_max_runup_60m_pct": row["max_runup_60m_pct"],
        "observed_max_drawdown_60m_pct": row["max_drawdown_60m_pct"],
        "observed_tape_start": tape.start_at,
        "observed_tape_end": tape.end_at,
        "observed_price_file": str(tape.price_file.relative_to(ROOT)) if tape.price_file.is_relative_to(ROOT) else str(tape.price_file),
        "price_coverage": tape.coverage,
    }
    return (
        _case_record(
            name=case_name,
            market=market,
            ticker=ticker,
            path_type="claude_price" if market == "US" else "path_a",
            tape_rel=str(tape_path.relative_to(out_dir)).replace("\\", "/"),
            params=params,
        ),
        None,
    )


def _parse_assignments(items: list[str]) -> dict[str, Any]:
    parsed: dict[str, Any] = {}
    for item in items:
        if "=" not in item:
            raise RehearsalGuardError(f"invalid assignment: {item}")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not key:
            raise RehearsalGuardError(f"empty assignment key: {item}")
        values = [part.strip() for part in raw.split(",") if part.strip()]
        parsed[key] = [_coerce_cli_value(value) for value in values] if len(values) > 1 else _coerce_cli_value(raw)
    return parsed


def _coerce_cli_value(value: str) -> Any:
    raw = str(value).strip()
    lower = raw.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in {"none", "null"}:
        return None
    try:
        if "." not in raw and "e" not in lower:
            return int(raw)
        return float(raw)
    except ValueError:
        return raw


def _normalize_sources(raw: str) -> set[str]:
    values = {part.strip().lower() for part in str(raw or "all").split(",") if part.strip()}
    if not values or "all" in values:
        return {"pathb", "counterfactual"}
    invalid = values - {"pathb", "counterfactual"}
    if invalid:
        raise RehearsalGuardError(f"unknown source(s): {', '.join(sorted(invalid))}")
    return values


def _coverage_summary(cases: list[dict[str, Any]], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    by_status: Counter[str] = Counter()
    by_flag: Counter[str] = Counter()
    incomplete: list[dict[str, Any]] = []

    def add(item: dict[str, Any], *, skipped_item: bool) -> None:
        coverage = item.get("price_coverage") if skipped_item else ((item.get("params") or {}).get("price_coverage") or {})
        if not isinstance(coverage, dict) or not coverage:
            return
        status = str(coverage.get("coverage_status") or "unknown")
        by_status[status] += 1
        for flag in coverage.get("coverage_flags") or []:
            by_flag[str(flag or "unknown")] += 1
        if status != "complete" or skipped_item:
            entry = {
                "market": item.get("market"),
                "ticker": item.get("ticker"),
                "source": (item.get("params") or {}).get("source") if not skipped_item else item.get("source"),
                "reason": item.get("reason") if skipped_item else "",
                "coverage_status": status,
                "coverage_flags": coverage.get("coverage_flags") or [],
                "requested_start_at": coverage.get("requested_start_at", ""),
                "requested_end_at": coverage.get("requested_end_at", ""),
                "actual_start_at": coverage.get("actual_start_at", ""),
                "actual_end_at": coverage.get("actual_end_at", ""),
                "matched_rows": coverage.get("matched_rows", 0),
            }
            incomplete.append(entry)

    for case in cases:
        add(case, skipped_item=False)
    for item in skipped:
        add(item, skipped_item=True)

    return {
        "case_count": len(cases),
        "skipped_count": len(skipped),
        "by_status": dict(sorted(by_status.items())),
        "by_flag": dict(sorted(by_flag.items())),
        "incomplete_examples": incomplete[:50],
    }


def _output_dir(runtime_root: Path, requested: str) -> Path:
    root = Path(runtime_root).expanduser().resolve()
    target = Path(requested).expanduser() if requested else root / _now_stamp()
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise RehearsalGuardError(f"batch output path must stay inside runtime root: {target}") from exc
    return target


def build_simulation_batch(
    *,
    sources: str = "all",
    market: str = "ALL",
    limit: int = 40,
    start_date: str = "",
    end_date: str = "",
    event_db: str | Path = DEFAULT_EVENT_DB,
    candidate_db: str | Path = DEFAULT_CANDIDATE_DB,
    price_root: str | Path = DEFAULT_PRICE_ROOT,
    runtime_root: str | Path = DEFAULT_RUNTIME_ROOT,
    output_dir: str = "",
    max_tape_rows: int = 2500,
    min_counterfactual_runup_pct: float = 3.0,
    overrides: dict[str, Any] | None = None,
    sweep: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active_sources = _normalize_sources(sources)
    out_dir = _output_dir(Path(runtime_root), output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    per_source_limit = max(1, int(limit))

    if "pathb" in active_sources:
        for idx, row in enumerate(
            _pathb_rows(
                db_path=Path(event_db),
                market=market,
                start_date=start_date,
                end_date=end_date,
                limit=per_source_limit,
            )
        ):
            case, skip = _pathb_case_from_row(
                row,
                price_root=Path(price_root),
                out_dir=out_dir,
                index=idx,
                max_tape_rows=max_tape_rows,
            )
            if case:
                cases.append(case)
            if skip:
                skipped.append(skip)

    if "counterfactual" in active_sources:
        for idx, row in enumerate(
            _candidate_rows(
                db_path=Path(candidate_db),
                market=market,
                start_date=start_date,
                end_date=end_date,
                limit=per_source_limit,
                min_runup_pct=min_counterfactual_runup_pct,
            )
        ):
            case, skip = _candidate_case_from_row(
                row,
                price_root=Path(price_root),
                out_dir=out_dir,
                index=idx,
                max_tape_rows=max_tape_rows,
            )
            if case:
                cases.append(case)
            if skip:
                skipped.append(skip)

    if not cases:
        raise RehearsalGuardError("no simulation cases could be built from existing DB/price data")

    batch = {
        "generated_at": _utc_now_text(),
        "runtime_context": "ops_db_simulation_batch",
        "live_writes_performed": False,
        "sources": sorted(active_sources),
        "source_paths": {
            "event_db": str(Path(event_db)),
            "candidate_db": str(Path(candidate_db)),
            "price_root": str(Path(price_root)),
        },
        "filters": {
            "market": market,
            "limit_per_source": per_source_limit,
            "start_date": start_date,
            "end_date": end_date,
            "min_counterfactual_runup_pct": min_counterfactual_runup_pct,
            "max_tape_rows": max_tape_rows,
        },
        "overrides": overrides or {},
        "sweep": sweep or {},
        "coverage_summary": _coverage_summary(cases, skipped),
        "cases": cases,
    }
    batch_path = out_dir / "simulation_batch.json"
    batch_path.write_text(json.dumps(batch, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    source_counts = Counter(str((case.get("params") or {}).get("source") or "unknown") for case in cases)
    skipped_counts = Counter(str(item.get("reason") or "unknown") for item in skipped)
    return {
        "ok": True,
        "batch_path": str(batch_path),
        "output_dir": str(out_dir),
        "case_count": len(cases),
        "source_counts": dict(sorted(source_counts.items())),
        "skipped_count": len(skipped),
        "skipped_reasons": dict(sorted(skipped_counts.items())),
        "skipped": skipped[:50],
        "coverage_summary": batch["coverage_summary"],
        "live_writes_performed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build ops_simulate batch files from existing read-only live DBs.")
    parser.add_argument("--sources", default="all", help="all, pathb, counterfactual, or comma-separated values")
    parser.add_argument("--market", default="ALL", choices=["ALL", "KR", "US"])
    parser.add_argument("--limit", type=int, default=40, help="maximum rows per source")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--candidate-db", default=str(DEFAULT_CANDIDATE_DB))
    parser.add_argument("--price-root", default=str(DEFAULT_PRICE_ROOT))
    parser.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--output-dir", default="", help="relative directory under runtime root; default is timestamped")
    parser.add_argument("--max-tape-rows", type=int, default=2500)
    parser.add_argument("--min-counterfactual-runup-pct", type=float, default=3.0)
    parser.add_argument("--set", dest="sets", action="append", default=[], help="batch override, e.g. confidence_threshold=0.55")
    parser.add_argument("--sweep", action="append", default=[], help="batch sweep, e.g. target_price_mult=1.03,1.05")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        overrides = _parse_assignments(args.sets)
        raw_sweep = _parse_assignments(args.sweep)
        sweep = {key: value if isinstance(value, list) else [value] for key, value in raw_sweep.items()}
        report = build_simulation_batch(
            sources=args.sources,
            market=args.market,
            limit=args.limit,
            start_date=args.start_date,
            end_date=args.end_date,
            event_db=args.event_db,
            candidate_db=args.candidate_db,
            price_root=args.price_root,
            runtime_root=args.runtime_root,
            output_dir=args.output_dir,
            max_tape_rows=args.max_tape_rows,
            min_counterfactual_runup_pct=args.min_counterfactual_runup_pct,
            overrides=overrides,
            sweep=sweep,
        )
    except RehearsalGuardError as exc:
        payload = {"ok": False, "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"ops_build_simulation_batch failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(
            "ok "
            f"cases={report['case_count']} skipped={report['skipped_count']} "
            f"batch={report['batch_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
