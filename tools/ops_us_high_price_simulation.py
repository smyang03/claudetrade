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
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.rehearsal.context import RehearsalGuardError

KST = ZoneInfo("Asia/Seoul")
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_PERF_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_PRICE_ROOT = ROOT / "data" / "price"
DEFAULT_OUTPUT_ROOT = ROOT / ".runtime" / "ops_simulation_analysis"
ONE_SHARE_CAP_KRW = 700_000.0


def _ro_uri(path: Path) -> str:
    return f"file:{path.resolve().as_posix()}?mode=ro"


def _connect_ro(path: Path) -> sqlite3.Connection:
    source = Path(path)
    if not source.exists():
        raise RehearsalGuardError(f"read-only DB not found: {source}")
    conn = sqlite3.connect(_ro_uri(source), uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _now_stamp() -> str:
    return datetime.now(KST).strftime("%Y%m%d_%H%M%S")


def _now_text() -> str:
    return datetime.now(KST).replace(microsecond=0).isoformat()


def _output_dir(root: Path, output_dir: str) -> Path:
    base = Path(root).expanduser()
    target = Path(output_dir).expanduser() if output_dir else base / f"us_high_price_{_now_stamp()}"
    if not target.is_absolute():
        target = base / target
    resolved_base = base.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_base and resolved_base not in resolved_target.parents:
        raise RehearsalGuardError(f"output_dir must stay under output_root: {target}")
    target.mkdir(parents=True, exist_ok=True)
    return target


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


def _as_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return float(default)
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return int(default)


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
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def _pct(entry: float, exit_price: float) -> float:
    if entry <= 0:
        return 0.0
    return ((float(exit_price) / float(entry)) - 1.0) * 100.0


def _stats(values: list[float]) -> dict[str, Any]:
    vals = [float(value) for value in values if value is not None]
    if not vals:
        return {"count": 0, "avg": 0.0, "median": 0.0, "best": 0.0, "worst": 0.0, "win_rate_pct": 0.0}
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 4),
        "median": round(float(median(vals)), 4),
        "best": round(max(vals), 4),
        "worst": round(min(vals), 4),
        "win_rate_pct": round(sum(1 for value in vals if value > 0) / len(vals) * 100.0, 2),
    }


def _session_open(reference: datetime) -> datetime:
    ref = reference.astimezone(KST)
    if ref.time() <= time(5, 0):
        return datetime.combine(ref.date() - timedelta(days=1), time(22, 30), tzinfo=KST)
    return datetime.combine(ref.date(), time(22, 30), tzinfo=KST)


def _session_end(reference: datetime) -> datetime:
    opened = _session_open(reference)
    return datetime.combine(opened.date() + timedelta(days=1), time(5, 0), tzinfo=KST)


def _price_file(price_root: Path, ticker: str) -> Path | None:
    ticker_key = str(ticker or "").upper()
    for candidate in (
        price_root / "minute" / "us" / f"us_{ticker_key}.csv",
        price_root / "us" / f"us_{ticker_key}.csv",
    ):
        if candidate.exists():
            return candidate
    return None


def _read_prices(price_root: Path, ticker: str, start_at: datetime, end_at: datetime) -> list[dict[str, Any]]:
    source = _price_file(price_root, ticker)
    if source is None:
        return []
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as fp:
        for raw in csv.DictReader(fp):
            ts = raw.get("ts") or raw.get("date") or raw.get("datetime")
            dt = _parse_dt(ts)
            if dt is None or dt < start_at or dt > end_at:
                continue
            close = _as_float(raw.get("close") or raw.get("price"))
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


def _row_at_or_before(rows: list[dict[str, Any]], at: datetime) -> dict[str, Any] | None:
    previous: dict[str, Any] | None = None
    for row in rows:
        if row["dt"] > at:
            break
        previous = row
    return previous


def _simulate_exit_policy(
    rows: list[dict[str, Any]],
    *,
    entry_price: float,
    target: float,
    stop: float,
    policy: dict[str, Any],
) -> tuple[float, str]:
    if not rows or entry_price <= 0:
        return 0.0, "NO_TAPE"
    kind = str(policy.get("kind") or "")
    peak = entry_price
    start_dt = rows[0]["dt"]
    for row in rows:
        close = float(row["close"])
        high = float(row.get("high") or close)
        low = float(row.get("low") or close)
        peak = max(peak, high)
        pnl = _pct(entry_price, close)
        peak_pnl = _pct(entry_price, peak)
        if kind in {"target_stop", "ladder"}:
            if stop > 0 and low <= stop:
                return _pct(entry_price, stop), "STOP"
            if target > 0 and high >= target:
                return _pct(entry_price, target), "TARGET"
        if kind == "ladder":
            trigger = float(policy.get("trigger_pct") or 4.0)
            giveback = float(policy.get("giveback_pct") or 1.5)
            if peak_pnl >= trigger and peak_pnl - pnl >= giveback:
                return pnl, "LADDER"
        if kind == "time_stop":
            minutes = float(policy.get("minutes") or 60)
            if (row["dt"] - start_dt).total_seconds() >= minutes * 60:
                return pnl, f"TIME_{int(minutes)}"
    return _pct(entry_price, float(rows[-1]["close"])), "END"


def _path_run_id(payload: dict[str, Any]) -> str:
    sizing = payload.get("pathb_sizing") if isinstance(payload.get("pathb_sizing"), dict) else {}
    return str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or sizing.get("path_run_id") or "")


def _collect_block_candidates(event_conn: sqlite3.Connection, perf_conn: sqlite3.Connection, price_root: Path) -> list[dict[str, Any]]:
    rows = event_conn.execute(
        """
        select event_id, event_type, occurred_at, session_date, ticker, reason_code, payload_json
        from lifecycle_events
        where runtime_mode='live'
          and market='US'
          and (
                reason_code in ('HIGH_PRICE_BUDGET_BLOCK','ORDER_SIZE_TOO_SMALL_GATE','PATHB_HIGH_PRICE_BUDGET_BLOCK')
                or payload_json like '%high_price_one_share_blocked%'
              )
        order by occurred_at, event_id
        """
    ).fetchall()
    by_path: dict[str, dict[str, Any]] = {}
    registration_blocks: list[dict[str, Any]] = []
    for row in rows:
        payload = _json_obj(row["payload_json"])
        sizing = payload.get("pathb_sizing") if isinstance(payload.get("pathb_sizing"), dict) else {}
        pid = _path_run_id(payload)
        price_krw = _as_float(payload.get("price_krw") or sizing.get("price_krw") or payload.get("buy_zone_low_krw"))
        blocker = str(sizing.get("blocker") or payload.get("blocker") or payload.get("reason") or row["reason_code"] or "")
        if not pid:
            registration_blocks.append(
                {
                    "session_date": row["session_date"],
                    "ticker": row["ticker"],
                    "reason_code": row["reason_code"],
                    "buy_zone_low_krw": _as_float(payload.get("buy_zone_low_krw")),
                    "buy_zone_high_krw": _as_float(payload.get("buy_zone_high_krw")),
                    "max_entry_krw": _as_float(payload.get("max_entry_krw")),
                    "candidate_type": "over_cap_registration_block",
                }
            )
            continue
        if pid in by_path:
            continue
        original_budget = _as_float(payload.get("original_budget_krw") or sizing.get("original_budget_krw") or 450_000)
        effective_budget = _as_float(payload.get("effective_budget_krw") or sizing.get("effective_budget_krw") or original_budget)
        if price_krw <= 0:
            continue
        by_path[pid] = {
            "path_run_id": pid,
            "session_date": row["session_date"],
            "ticker": row["ticker"],
            "first_block_at": row["occurred_at"],
            "reason_code": row["reason_code"],
            "blocker": blocker,
            "price_krw": round(price_krw, 2),
            "cash_krw": round(_as_float(payload.get("cash_krw")), 2),
            "original_budget_krw": round(original_budget, 2),
            "effective_budget_krw": round(effective_budget, 2),
            "early_gate_applied": bool(payload.get("early_gate_applied") or effective_budget < original_budget),
            "post_fix_class": (
                "post_fix_waiting_size_gate"
                if price_krw <= ONE_SHARE_CAP_KRW and effective_budget < original_budget
                else "post_fix_qty_one_allowed"
                if price_krw <= ONE_SHARE_CAP_KRW
                else "still_over_cap"
            ),
        }

    if not by_path:
        return []
    qmarks = ",".join("?" for _ in by_path)
    run_rows = event_conn.execute(
        f"""
        select path_run_id, status, plan_json
        from v2_path_runs
        where path_run_id in ({qmarks})
        """,
        list(by_path),
    ).fetchall()
    for row in run_rows:
        item = by_path[str(row["path_run_id"])]
        plan = _json_obj(row["plan_json"])
        item.update(
            {
                "status": str(row["status"] or ""),
                "cancel_reason": str(plan.get("cancel_reason") or plan.get("status_reason") or ""),
                "confidence": _as_float(plan.get("confidence")),
                "buy_zone_low": _as_float(plan.get("buy_zone_low")),
                "buy_zone_high": _as_float(plan.get("buy_zone_high")),
                "sell_target": _as_float(plan.get("sell_target") or plan.get("target_price")),
                "stop_loss": _as_float(plan.get("stop_loss") or plan.get("hard_stop")),
            }
        )
    perf_rows = perf_conn.execute(
        f"""
        select path_run_id, filled, closed, filled_at, closed_at, entry_price, exit_price, qty, pnl_pct, close_reason
        from v2_learning_performance
        where path_run_id in ({qmarks})
        """,
        list(by_path),
    ).fetchall()
    for row in perf_rows:
        item = by_path[str(row["path_run_id"])]
        item.update(
            {
                "perf_found": True,
                "filled": _as_int(row["filled"]),
                "closed": _as_int(row["closed"]),
                "filled_at": str(row["filled_at"] or ""),
                "closed_at": str(row["closed_at"] or ""),
                "actual_entry_price": _as_float(row["entry_price"]),
                "actual_pnl_pct": _as_float(row["pnl_pct"]) if row["pnl_pct"] not in (None, "") else None,
                "actual_close_reason": str(row["close_reason"] or ""),
                "qty": _as_int(row["qty"]),
            }
        )

    candidates = list(by_path.values())
    for item in candidates:
        item.setdefault("status", "MISSING")
        item.setdefault("cancel_reason", "")
        item.setdefault("perf_found", False)
        _add_price_replay(item, price_root)
    candidates.sort(key=lambda row: (str(row.get("session_date") or ""), str(row.get("ticker") or "")))
    return candidates


def _add_price_replay(item: dict[str, Any], price_root: Path) -> None:
    block_at = _parse_dt(item.get("first_block_at"))
    if block_at is None:
        item["price_replay"] = {"coverage_status": "missing_block_time"}
        return
    start = max(block_at, _session_open(block_at) + timedelta(minutes=60))
    end = _session_end(block_at)
    rows = _read_prices(price_root, str(item.get("ticker") or ""), start - timedelta(minutes=2), end)
    entry = _first_at_or_after(rows, start)
    if entry is None:
        item["price_replay"] = {"coverage_status": "price_tape_missing", "post_gate_at": start.isoformat()}
        return
    future = [row for row in rows if row["dt"] >= entry["dt"]]
    p30 = _row_at_or_before(future, entry["dt"] + timedelta(minutes=30))
    p60 = _row_at_or_before(future, entry["dt"] + timedelta(minutes=60))
    eod = _row_at_or_before(future, end)
    entry_px = float(entry["close"])
    highs = [float(row.get("high") or row["close"]) for row in future]
    lows = [float(row.get("low") or row["close"]) for row in future]
    replay = {
        "coverage_status": "complete" if eod else "partial",
        "post_gate_at": start.isoformat(),
        "entry_at": entry["dt"].isoformat(),
        "entry_price": round(entry_px, 4),
        "ret_30m_pct": round(_pct(entry_px, float((p30 or entry)["close"])), 4) if p30 else None,
        "ret_60m_pct": round(_pct(entry_px, float((p60 or entry)["close"])), 4) if p60 else None,
        "ret_eod_pct": round(_pct(entry_px, float((eod or entry)["close"])), 4) if eod else None,
        "mfe_eod_pct": round(_pct(entry_px, max(highs)), 4) if highs else None,
        "mae_eod_pct": round(_pct(entry_px, min(lows)), 4) if lows else None,
        "tape_rows": len(future),
    }
    item["price_replay"] = replay
    item["features"] = _candidate_features(item, entry_px, future)
    item["exit_replay"] = _exit_replay_for_candidate(item, future, entry_px)


def _candidate_features(item: dict[str, Any], entry_px: float, rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy_low = _as_float(item.get("buy_zone_low"))
    buy_high = _as_float(item.get("buy_zone_high"))
    target = _as_float(item.get("sell_target"))
    stop = _as_float(item.get("stop_loss"))
    open_px = float(rows[0]["open"]) if rows else entry_px
    zone_width = max(0.0, buy_high - buy_low)
    return {
        "post_gate_open_return_pct": round(_pct(open_px, entry_px), 4) if open_px > 0 else 0.0,
        "entry_vs_buy_zone_high_pct": round(_pct(buy_high, entry_px), 4) if buy_high > 0 else 0.0,
        "entry_zone_position": round((entry_px - buy_low) / zone_width, 4) if zone_width > 0 else 0.0,
        "target_distance_pct": round(_pct(entry_px, target), 4) if target > 0 else 0.0,
        "stop_distance_pct": round(_pct(entry_px, stop), 4) if stop > 0 else 0.0,
        "rr_ratio": round(abs(_pct(entry_px, target) / _pct(entry_px, stop)), 4)
        if target > 0 and stop > 0 and _pct(entry_px, stop) != 0
        else 0.0,
    }


def _exit_replay_for_candidate(item: dict[str, Any], rows: list[dict[str, Any]], entry_px: float) -> dict[str, Any]:
    target = _as_float(item.get("sell_target"))
    stop = _as_float(item.get("stop_loss"))
    policies = [
        {"name": "target_stop", "kind": "target_stop"},
        {"name": "hold_to_eod", "kind": "hold"},
        {"name": "time_stop_60", "kind": "time_stop", "minutes": 60},
        {"name": "time_stop_120", "kind": "time_stop", "minutes": 120},
        {"name": "ladder_t3_g1_0", "kind": "ladder", "trigger_pct": 3.0, "giveback_pct": 1.0},
        {"name": "ladder_t4_g1_5", "kind": "ladder", "trigger_pct": 4.0, "giveback_pct": 1.5},
    ]
    result: dict[str, Any] = {}
    for policy in policies:
        pnl, reason = _simulate_exit_policy(rows, entry_price=entry_px, target=target, stop=stop, policy=policy)
        result[str(policy["name"])] = {"pnl_pct": round(pnl, 4), "reason": reason}
    return result


def _actual_us_pathb_pressure(perf_conn: sqlite3.Connection) -> dict[str, Any]:
    rows = perf_conn.execute(
        """
        select session_date, ticker, path_run_id, filled_at, closed_at, entry_price, qty
        from v2_learning_performance
        where runtime_mode='live'
          and market='US'
          and path_type='claude_price'
          and filled=1
        """
    ).fetchall()
    by_day: defaultdict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_day[str(row["session_date"] or "")].append(row)
    days: dict[str, Any] = {}
    for day, day_rows in by_day.items():
        points: list[tuple[datetime, int]] = []
        for row in day_rows:
            start = _parse_dt(row["filled_at"])
            end = _parse_dt(row["closed_at"]) or (start + timedelta(hours=6) if start else None)
            if start is None or end is None:
                continue
            points.append((start, 1))
            points.append((end, -1))
        current = 0
        max_concurrent = 0
        for _dt, delta in sorted(points, key=lambda item: (item[0], -item[1])):
            current += delta
            max_concurrent = max(max_concurrent, current)
        days[day] = {"actual_entry_count": len(day_rows), "actual_max_concurrent": max_concurrent}
    return days


def _displacement_summary(candidates: list[dict[str, Any]], perf_conn: sqlite3.Connection) -> dict[str, Any]:
    pressure = _actual_us_pathb_pressure(perf_conn)
    addable: list[dict[str, Any]] = []
    non_addable: list[dict[str, Any]] = []
    for item in candidates:
        status = str(item.get("status") or "")
        cancel_reason = str(item.get("cancel_reason") or "")
        replay = item.get("price_replay") or {}
        if status != "CANCELLED":
            continue
        if cancel_reason == "ALREADY_HOLDING":
            non_addable.append({**item, "non_addable_reason": "already_holding"})
            continue
        if "operator_cancelled" in cancel_reason:
            non_addable.append({**item, "non_addable_reason": "operator_cancelled_unfilled"})
            continue
        if replay.get("coverage_status") != "complete":
            non_addable.append({**item, "non_addable_reason": "price_replay_incomplete"})
            continue
        addable.append(item)

    day_rows: list[dict[str, Any]] = []
    for item in addable:
        day = str(item.get("session_date") or "")
        day_pressure = pressure.get(day, {"actual_entry_count": 0, "actual_max_concurrent": 0})
        cash = _as_float(item.get("cash_krw"))
        price = _as_float(item.get("price_krw"))
        day_rows.append(
            {
                "session_date": day,
                "ticker": item.get("ticker"),
                "actual_entry_count": day_pressure["actual_entry_count"],
                "entry_count_after_add": int(day_pressure["actual_entry_count"]) + 1,
                "actual_max_concurrent": day_pressure["actual_max_concurrent"],
                "max_concurrent_after_add": int(day_pressure["actual_max_concurrent"]) + 1,
                "daily_cap_pressure": int(day_pressure["actual_entry_count"]) + 1 > 40,
                "position_cap_pressure": int(day_pressure["actual_max_concurrent"]) + 1 > 15,
                "cash_observed_ok": bool(cash <= 0 or cash >= price),
                "post_gate_ret_60m_pct": (item.get("price_replay") or {}).get("ret_60m_pct"),
                "post_gate_ret_eod_pct": (item.get("price_replay") or {}).get("ret_eod_pct"),
            }
        )
    return {
        "addable_cancelled_count": len(addable),
        "non_addable_cancelled_count": len(non_addable),
        "daily_or_position_pressure_count": sum(
            1 for row in day_rows if row["daily_cap_pressure"] or row["position_cap_pressure"]
        ),
        "cash_pressure_count": sum(1 for row in day_rows if not row["cash_observed_ok"]),
        "addable_return_60m": _stats([_as_float((row.get("price_replay") or {}).get("ret_60m_pct")) for row in addable]),
        "addable_return_eod": _stats([_as_float((row.get("price_replay") or {}).get("ret_eod_pct")) for row in addable]),
        "day_rows": day_rows,
        "non_addable_examples": [
            {
                "session_date": row.get("session_date"),
                "ticker": row.get("ticker"),
                "cancel_reason": row.get("cancel_reason"),
                "non_addable_reason": row.get("non_addable_reason"),
            }
            for row in non_addable[:20]
        ],
    }


def _policy_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    policies = ["target_stop", "hold_to_eod", "time_stop_60", "time_stop_120", "ladder_t3_g1_0", "ladder_t4_g1_5"]
    out: dict[str, Any] = {}
    for policy in policies:
        vals = [
            _as_float(((row.get("exit_replay") or {}).get(policy) or {}).get("pnl_pct"))
            for row in candidates
            if (row.get("price_replay") or {}).get("coverage_status") == "complete"
        ]
        out[policy] = _stats(vals)
    return dict(sorted(out.items(), key=lambda item: item[1]["avg"], reverse=True))


def _loss_filter_summary(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in candidates if (row.get("price_replay") or {}).get("coverage_status") == "complete"]

    def eod(row: dict[str, Any]) -> float:
        return _as_float((row.get("price_replay") or {}).get("ret_eod_pct"))

    def feature(row: dict[str, Any], key: str) -> float:
        return _as_float((row.get("features") or {}).get(key))

    rules: list[tuple[str, Callable[[dict[str, Any]], bool]]] = [
        ("confidence_ge_0_58", lambda row: _as_float(row.get("confidence")) >= 0.58),
        ("confidence_ge_0_60", lambda row: _as_float(row.get("confidence")) >= 0.60),
        ("entry_not_above_buy_zone_high", lambda row: feature(row, "entry_vs_buy_zone_high_pct") <= 0.0),
        ("entry_within_1pct_above_zone", lambda row: feature(row, "entry_vs_buy_zone_high_pct") <= 1.0),
        ("rr_ratio_ge_1_3", lambda row: feature(row, "rr_ratio") >= 1.3),
        ("not_overheated_post_gate", lambda row: feature(row, "post_gate_open_return_pct") <= 1.0),
        (
            "confidence_ge_0_58_and_not_overheated",
            lambda row: _as_float(row.get("confidence")) >= 0.58 and feature(row, "post_gate_open_return_pct") <= 1.0,
        ),
        (
            "zone_or_close_plus_confidence",
            lambda row: _as_float(row.get("confidence")) >= 0.58
            and feature(row, "entry_vs_buy_zone_high_pct") <= 1.0,
        ),
    ]
    base = _stats([eod(row) for row in rows])
    evaluated = []
    for name, predicate in rules:
        kept = [row for row in rows if predicate(row)]
        excluded = [row for row in rows if not predicate(row)]
        if not kept:
            continue
        kept_stats = _stats([eod(row) for row in kept])
        excluded_stats = _stats([eod(row) for row in excluded])
        evaluated.append(
            {
                "rule": name,
                "kept": kept_stats,
                "excluded": excluded_stats,
                "delta_avg_vs_base": round(kept_stats["avg"] - base["avg"], 4),
                "kept_examples": [
                    {
                        "ticker": row.get("ticker"),
                        "session_date": row.get("session_date"),
                        "ret_eod_pct": eod(row),
                        "confidence": row.get("confidence"),
                        "features": row.get("features"),
                    }
                    for row in kept[:5]
                ],
            }
        )
    evaluated.sort(key=lambda row: (row["kept"]["avg"], row["kept"]["count"]), reverse=True)
    return {"base_eod": base, "rules": evaluated}


def _write_csv(path: Path, candidates: list[dict[str, Any]]) -> None:
    fields = [
        "session_date",
        "ticker",
        "path_run_id",
        "status",
        "cancel_reason",
        "reason_code",
        "post_fix_class",
        "price_krw",
        "cash_krw",
        "confidence",
        "ret_60m_pct",
        "ret_eod_pct",
        "mfe_eod_pct",
        "mae_eod_pct",
        "entry_vs_buy_zone_high_pct",
        "post_gate_open_return_pct",
        "rr_ratio",
    ]
    with path.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        for row in candidates:
            replay = row.get("price_replay") or {}
            features = row.get("features") or {}
            writer.writerow(
                {
                    **{key: row.get(key, "") for key in fields},
                    "ret_60m_pct": replay.get("ret_60m_pct", ""),
                    "ret_eod_pct": replay.get("ret_eod_pct", ""),
                    "mfe_eod_pct": replay.get("mfe_eod_pct", ""),
                    "mae_eod_pct": replay.get("mae_eod_pct", ""),
                    "entry_vs_buy_zone_high_pct": features.get("entry_vs_buy_zone_high_pct", ""),
                    "post_gate_open_return_pct": features.get("post_gate_open_return_pct", ""),
                    "rr_ratio": features.get("rr_ratio", ""),
                }
            )


def _md_table(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    if not rows:
        return ["_no rows_"]
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(column, "")) for column in columns) + " |")
    return lines


def _write_md(payload: dict[str, Any], path: Path) -> None:
    summary = payload["summary"]
    lines = [
        "# US High-price Simulation",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- live_writes_performed: {payload['live_writes_performed']}",
        f"- candidate_count: {summary['candidate_count']}",
        f"- complete_price_replay_count: {summary['complete_price_replay_count']}",
        "",
        "## Displacement",
        "",
        f"- addable_cancelled_count: {summary['displacement']['addable_cancelled_count']}",
        f"- daily_or_position_pressure_count: {summary['displacement']['daily_or_position_pressure_count']}",
        f"- cash_pressure_count: {summary['displacement']['cash_pressure_count']}",
        f"- addable_return_60m: {json.dumps(summary['displacement']['addable_return_60m'], ensure_ascii=False, sort_keys=True)}",
        f"- addable_return_eod: {json.dumps(summary['displacement']['addable_return_eod'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Exit Policy Ranking",
        "",
    ]
    policy_rows = [{"policy": name, **stats} for name, stats in summary["exit_policy_stats"].items()]
    lines.extend(_md_table(policy_rows, ["policy", "count", "avg", "median", "best", "worst", "win_rate_pct"]))
    lines.extend(["", "## Loss Filter Rules", ""])
    rule_rows = [
        {
            "rule": row["rule"],
            "kept_count": row["kept"]["count"],
            "kept_avg": row["kept"]["avg"],
            "kept_worst": row["kept"]["worst"],
            "kept_win": row["kept"]["win_rate_pct"],
            "delta_avg_vs_base": row["delta_avg_vs_base"],
        }
        for row in summary["loss_filters"]["rules"][:12]
    ]
    lines.extend(_md_table(rule_rows, ["rule", "kept_count", "kept_avg", "kept_worst", "kept_win", "delta_avg_vs_base"]))
    lines.extend(["", "## Candidate Replay", ""])
    candidate_rows = []
    for row in payload["candidates"][:30]:
        replay = row.get("price_replay") or {}
        features = row.get("features") or {}
        candidate_rows.append(
            {
                "date": row.get("session_date"),
                "ticker": row.get("ticker"),
                "status": row.get("status"),
                "cancel_reason": row.get("cancel_reason"),
                "ret60": replay.get("ret_60m_pct"),
                "eod": replay.get("ret_eod_pct"),
                "mae": replay.get("mae_eod_pct"),
                "conf": row.get("confidence"),
                "zone_hi_pct": features.get("entry_vs_buy_zone_high_pct"),
            }
        )
    lines.extend(_md_table(candidate_rows, ["date", "ticker", "status", "cancel_reason", "ret60", "eod", "mae", "conf", "zone_hi_pct"]))
    lines.extend(
        [
            "",
            "## Safety Notes",
            "",
            "- This report is read-only and does not submit orders.",
            "- Exit policy ranking is analysis-only; profit ladder, pre-close, target, stop, and hold advisor are protected live paths.",
            "- Displacement is a proxy based on actual filled intervals, daily entry cap, position cap, and observed cash in block events.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def build_us_high_price_simulation(
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
        candidates = _collect_block_candidates(event_conn, perf_conn, Path(price_root))
        complete = [row for row in candidates if (row.get("price_replay") or {}).get("coverage_status") == "complete"]
        summary = {
            "candidate_count": len(candidates),
            "complete_price_replay_count": len(complete),
            "by_status": dict(sorted(Counter(str(row.get("status") or "") for row in candidates).items())),
            "by_post_fix_class": dict(sorted(Counter(str(row.get("post_fix_class") or "") for row in candidates).items())),
            "price_replay_60m": _stats([_as_float((row.get("price_replay") or {}).get("ret_60m_pct")) for row in complete]),
            "price_replay_eod": _stats([_as_float((row.get("price_replay") or {}).get("ret_eod_pct")) for row in complete]),
            "displacement": _displacement_summary(candidates, perf_conn),
            "exit_policy_stats": _policy_summary(candidates),
            "loss_filters": _loss_filter_summary(candidates),
        }
    finally:
        event_conn.close()
        perf_conn.close()

    json_path = out_dir / "us_high_price_simulation.json"
    md_path = out_dir / "us_high_price_simulation.md"
    csv_path = out_dir / "us_high_price_candidates.csv"
    payload = {
        "ok": True,
        "generated_at": _now_text(),
        "live_writes_performed": False,
        "inputs": {
            "event_db": str(event_db),
            "perf_db": str(perf_db),
            "price_root": str(price_root),
        },
        "summary": summary,
        "candidates": candidates,
        "apply_prohibitions": [
            "do not submit orders from this report",
            "do not change protected exit policies from this small sample",
            "do not bypass broker truth, risk, cash, daily cap, or max position gates",
        ],
        "output_paths": {"json": str(json_path), "md": str(md_path), "csv": str(csv_path)},
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _write_md(payload, md_path)
    _write_csv(csv_path, candidates)
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run read-only US high-price improvement simulations.")
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
        payload = build_us_high_price_simulation(
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
            print(f"ops_us_high_price_simulation failed: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps({"ok": True, "summary": payload["summary"], "output_paths": payload["output_paths"]}, ensure_ascii=False, indent=2))
    else:
        print(
            "ok "
            f"candidates={payload['summary']['candidate_count']} "
            f"complete={payload['summary']['complete_price_replay_count']} "
            f"md={payload['output_paths']['md']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
