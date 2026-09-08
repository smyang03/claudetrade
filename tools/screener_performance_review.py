"""Build a point-in-time screener performance review without external API calls.

The review deliberately separates:

1. provider/collector health,
2. candidate opportunity after first observation,
3. prompt and execution funnel conversion,
4. sub-screener operating activity.

Forward returns use the first local minute bar at or after a candidate's first
logged ``known_at``. They are gross opportunity labels, not executable PnL.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
DEFAULT_PERFORMANCE_DB = ROOT / "data" / "ml" / "decisions.db"
READY_ACTIONS = {"BUY_READY", "PROBE_READY", "ADD_READY", "PULLBACK_WAIT"}


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _round(value: float | None, digits: int = 4) -> float | None:
    return round(value, digits) if value is not None and math.isfinite(value) else None


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.mean(clean) if clean else None


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    return statistics.median(clean) if clean else None


def _first_candidate_rows(
    audit_db: Path,
    *,
    start_date: str,
    end_date: str,
    runtime_mode: str,
) -> list[dict[str, Any]]:
    con = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """
            WITH ranked AS (
                SELECT r.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY market, session_date, ticker
                           ORDER BY known_at, candidate_key
                       ) AS observation_rank
                FROM audit_candidate_rows r
                WHERE runtime_mode=?
                  AND screener_seen=1
                  AND session_date BETWEEN ? AND ?
            )
            SELECT candidate_key, market, session_date, ticker, known_at,
                   candidate_source, source_file, raw_rank, raw_score_current,
                   trainer_score_rank, trainer_prompt_score,
                   candidate_quality_score, primary_bucket,
                   classification, final_prompt_included,
                   actual_prompt_included, data_quality, history_status,
                   consensus_mode, candidate_pool_role,
                   discovery_signal_family
            FROM ranked
            WHERE observation_rank=1
            ORDER BY market, session_date, known_at, ticker
            """,
            (runtime_mode, start_date, end_date),
        ).fetchall()
    finally:
        con.close()
    return [dict(row) for row in rows]


_MINUTE_CACHE: dict[tuple[str, str], list[tuple[datetime, float, float, float, float]]] = {}


def _minute_rows(market: str, ticker: str) -> list[tuple[datetime, float, float, float, float]]:
    key = (market, ticker)
    if key in _MINUTE_CACHE:
        return _MINUTE_CACHE[key]
    path = ROOT / "data" / "price" / "minute" / market.lower() / f"{market.lower()}_{ticker}.csv"
    rows: list[tuple[datetime, float, float, float, float]] = []
    if path.exists():
        try:
            with path.open(encoding="utf-8-sig") as handle:
                for raw in csv.DictReader(handle):
                    ts = _parse_dt(raw.get("ts"))
                    open_price = _num(raw.get("open"))
                    high = _num(raw.get("high"))
                    low = _num(raw.get("low"))
                    close = _num(raw.get("close"))
                    if ts is None or None in (open_price, high, low, close):
                        continue
                    rows.append((ts, open_price, high, low, close))
        except OSError:
            rows = []
    rows.sort(key=lambda row: row[0])
    _MINUTE_CACHE[key] = rows
    return rows


def _session_end(market: str, known_at: datetime) -> datetime:
    if market == "KR":
        return known_at.replace(hour=15, minute=30, second=0, microsecond=0)
    close_day = known_at if known_at.hour < 7 else known_at + timedelta(days=1)
    return close_day.replace(hour=5, minute=0, second=0, microsecond=0)


def attach_local_forward(rows: list[dict[str, Any]], horizon_min: int = 60) -> None:
    for row in rows:
        row["local_entry_at"] = None
        row["local_entry_price"] = None
        row[f"local_return_{horizon_min}m_pct"] = None
        row[f"local_mfe_{horizon_min}m_pct"] = None
        row[f"local_mae_{horizon_min}m_pct"] = None
        known_at = _parse_dt(row.get("known_at"))
        if known_at is None:
            continue
        bars = _minute_rows(str(row.get("market") or ""), str(row.get("ticker") or ""))
        if not bars:
            continue
        timestamps = [bar[0] for bar in bars]
        start_idx = bisect.bisect_left(timestamps, known_at)
        if start_idx >= len(bars):
            continue
        session_end = _session_end(str(row.get("market") or ""), known_at)
        if bars[start_idx][0] > session_end:
            continue
        target_at = known_at + timedelta(minutes=horizon_min)
        target_idx = bisect.bisect_left(timestamps, target_at)
        if target_idx >= len(bars) or bars[target_idx][0] > session_end:
            continue
        entry = bars[start_idx][1] or bars[start_idx][4]
        if entry <= 0:
            continue
        window = bars[start_idx : target_idx + 1]
        row["local_entry_at"] = bars[start_idx][0].isoformat(timespec="seconds")
        row["local_entry_price"] = entry
        row[f"local_return_{horizon_min}m_pct"] = (bars[target_idx][4] / entry - 1.0) * 100.0
        row[f"local_mfe_{horizon_min}m_pct"] = (max(bar[2] for bar in window) / entry - 1.0) * 100.0
        row[f"local_mae_{horizon_min}m_pct"] = (min(bar[3] for bar in window) / entry - 1.0) * 100.0


def _metric(rows: list[dict[str, Any]], horizon_min: int = 60) -> dict[str, Any]:
    return_key = f"local_return_{horizon_min}m_pct"
    mfe_key = f"local_mfe_{horizon_min}m_pct"
    mae_key = f"local_mae_{horizon_min}m_pct"
    returns = [_num(row.get(return_key)) for row in rows]
    returns = [value for value in returns if value is not None]
    mfe = [_num(row.get(mfe_key)) for row in rows]
    mae = [_num(row.get(mae_key)) for row in rows]
    return {
        "rows": len(rows),
        "matched": len(returns),
        "coverage": _round(len(returns) / len(rows) if rows else None),
        "mean_gross_pct": _round(_mean(returns)),
        "median_gross_pct": _round(_median(returns)),
        "positive_rate": _round(sum(value > 0 for value in returns) / len(returns) if returns else None),
        "gt_1pct_rate": _round(sum(value > 1.0 for value in returns) / len(returns) if returns else None),
        "mean_mfe_pct": _round(_mean(mfe)),
        "mean_mae_pct": _round(_mean(mae)),
        "label_contract": (
            f"first local minute bar at/after first known_at to {horizon_min}m; "
            "gross opportunity, no fill/cost/slippage"
        ),
    }


def _group_metrics(
    rows: list[dict[str, Any]],
    field: str,
    *,
    horizon_min: int = 60,
    min_rows: int = 1,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(field)
        key = str(value if value not in (None, "") else "(missing)")
        grouped[key].append(row)
    return {
        key: _metric(group, horizon_min)
        for key, group in sorted(grouped.items())
        if len(group) >= min_rows
    }


def _rank_bin(value: Any) -> str:
    rank = _num(value)
    if rank is None:
        return "missing"
    if rank <= 5:
        return "01-05"
    if rank <= 10:
        return "06-10"
    if rank <= 20:
        return "11-20"
    if rank <= 40:
        return "21-40"
    return "41+"


def _rank_bin_metrics(
    rows: list[dict[str, Any]],
    field: str,
    *,
    horizon_min: int = 60,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[_rank_bin(row.get(field))].append(row)
    order = ("01-05", "06-10", "11-20", "21-40", "41+", "missing")
    return {key: _metric(grouped[key], horizon_min) for key in order if grouped.get(key)}


def _quintile_metrics(
    rows: list[dict[str, Any]],
    field: str,
    *,
    higher_is_better: bool,
    horizon_min: int = 60,
) -> dict[str, Any]:
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if _num(row.get(field)) is not None:
            by_session[str(row.get("session_date") or "")].append(row)
    quintiles: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for session_rows in by_session.values():
        ordered = sorted(
            session_rows,
            key=lambda row: _num(row.get(field)) or 0.0,
            reverse=higher_is_better,
        )
        count = len(ordered)
        for index, row in enumerate(ordered):
            quintile = min(5, int(index * 5 / count) + 1)
            quintiles[quintile].append(row)
    return {
        f"Q{quintile}_{'best' if quintile == 1 else 'worst' if quintile == 5 else ''}".rstrip("_"): _metric(
            quintiles[quintile], horizon_min
        )
        for quintile in range(1, 6)
        if quintiles.get(quintile)
    }


def _rankdata(values: list[float]) -> list[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(indexed):
        end = cursor + 1
        while end < len(indexed) and indexed[end][1] == indexed[cursor][1]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in range(cursor, end):
            ranks[indexed[position][0]] = average_rank
        cursor = end
    return ranks


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) < 3 or len(left) != len(right):
        return None
    left_mean = statistics.mean(left)
    right_mean = statistics.mean(right)
    numerator = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right))
    denominator = math.sqrt(
        sum((x - left_mean) ** 2 for x in left) * sum((y - right_mean) ** 2 for y in right)
    )
    return numerator / denominator if denominator > 0 else None


def _spearman(rows: list[dict[str, Any]], field: str, horizon_min: int = 60) -> dict[str, Any]:
    return_key = f"local_return_{horizon_min}m_pct"
    pairs = [
        (_num(row.get(field)), _num(row.get(return_key)), str(row.get("session_date") or ""))
        for row in rows
        if _num(row.get(field)) is not None and _num(row.get(return_key)) is not None
    ]
    if not pairs:
        return {"n": 0, "overall": None, "session_count": 0, "median_session": None}
    overall = _pearson(
        _rankdata([pair[0] for pair in pairs]),
        _rankdata([pair[1] for pair in pairs]),
    )
    session_values: list[float] = []
    by_session: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for predictor, outcome, session_date in pairs:
        by_session[session_date].append((predictor, outcome))
    for session_pairs in by_session.values():
        correlation = _pearson(
            _rankdata([pair[0] for pair in session_pairs]),
            _rankdata([pair[1] for pair in session_pairs]),
        )
        if correlation is not None:
            session_values.append(correlation)
    return {
        "n": len(pairs),
        "overall": _round(overall),
        "session_count": len(session_values),
        "mean_session": _round(_mean(session_values)),
        "median_session": _round(_median(session_values)),
    }


def _session_topk(
    rows: list[dict[str, Any]],
    field: str,
    *,
    lower_is_better: bool,
    ks: tuple[int, ...] = (5, 10, 20, 40),
    horizon_min: int = 60,
) -> dict[str, Any]:
    return_key = f"local_return_{horizon_min}m_pct"
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        # Selection must be frozen before observing whether the forward label
        # exists. Filtering missing labels first would silently replace an
        # unlabeled top-ranked name with a lower-ranked labeled name.
        if _num(row.get(field)) is not None:
            by_session[str(row.get("session_date") or "")].append(row)
    result: dict[str, Any] = {}
    for k in ks:
        session_returns: list[float] = []
        fully_labeled_session_returns: list[float] = []
        selected_candidates = 0
        matched_labels = 0
        for session_rows in by_session.values():
            ordered = sorted(
                session_rows,
                key=lambda row: _num(row.get(field)) or 0.0,
                reverse=not lower_is_better,
            )[:k]
            selected_candidates += len(ordered)
            labeled_returns = [
                value
                for row in ordered
                if (value := _num(row.get(return_key))) is not None
            ]
            matched_labels += len(labeled_returns)
            if labeled_returns:
                session_mean = statistics.mean(labeled_returns)
                session_returns.append(session_mean)
                if len(labeled_returns) == len(ordered):
                    fully_labeled_session_returns.append(session_mean)
        result[f"top_{k}"] = {
            "eligible_sessions": len(by_session),
            "sessions_with_labels": len(session_returns),
            "fully_labeled_sessions": len(fully_labeled_session_returns),
            "selected_candidates": selected_candidates,
            "matched_labels": matched_labels,
            "label_coverage": _round(
                matched_labels / selected_candidates if selected_candidates else None
            ),
            "mean_session_gross_pct": _round(_mean(session_returns)),
            "median_session_gross_pct": _round(_median(session_returns)),
            "positive_session_rate": _round(
                sum(value > 0 for value in session_returns) / len(session_returns)
                if session_returns
                else None
            ),
            "fully_labeled_mean_session_gross_pct": _round(
                _mean(fully_labeled_session_returns)
            ),
            "fully_labeled_median_session_gross_pct": _round(
                _median(fully_labeled_session_returns)
            ),
            "selection_contract": (
                "select top-k from every ranked candidate before checking label availability; "
                "session return uses available labels and reports coverage"
            ),
        }
    return result


def _session_topk_counterfactual(
    rows: list[dict[str, Any]],
    *,
    control_field: str,
    treatment_field: str,
    control_lower_is_better: bool,
    treatment_lower_is_better: bool,
    ks: tuple[int, ...] = (5, 10, 20),
    horizon_min: int = 60,
    min_label_coverage: float = 0.8,
) -> dict[str, Any]:
    """Compare two independently selected top-k lists at the session unit.

    This is still an observational replay: it does not claim executable PnL.
    Requiring label coverage on both arms prevents a result from being promoted
    merely because one arm happened to have more locally cached minute bars.
    """

    return_key = f"local_return_{horizon_min}m_pct"
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_session[str(row.get("session_date") or "")].append(row)

    def _select(
        session_rows: list[dict[str, Any]],
        field: str,
        *,
        lower_is_better: bool,
        k: int,
    ) -> list[dict[str, Any]]:
        ranked = [row for row in session_rows if _num(row.get(field)) is not None]
        return sorted(
            ranked,
            key=lambda row: _num(row.get(field)) or 0.0,
            reverse=not lower_is_better,
        )[:k]

    result: dict[str, Any] = {}
    for k in ks:
        all_deltas: list[float] = []
        qualified_deltas: list[float] = []
        overlap_rates: list[float] = []
        control_coverages: list[float] = []
        treatment_coverages: list[float] = []
        for session_rows in by_session.values():
            control = _select(
                session_rows,
                control_field,
                lower_is_better=control_lower_is_better,
                k=k,
            )
            treatment = _select(
                session_rows,
                treatment_field,
                lower_is_better=treatment_lower_is_better,
                k=k,
            )
            if not control or not treatment:
                continue
            control_returns = [
                value
                for row in control
                if (value := _num(row.get(return_key))) is not None
            ]
            treatment_returns = [
                value
                for row in treatment
                if (value := _num(row.get(return_key))) is not None
            ]
            control_coverage = len(control_returns) / len(control)
            treatment_coverage = len(treatment_returns) / len(treatment)
            control_coverages.append(control_coverage)
            treatment_coverages.append(treatment_coverage)
            control_tickers = {str(row.get("ticker") or "") for row in control}
            treatment_tickers = {str(row.get("ticker") or "") for row in treatment}
            overlap_rates.append(
                len(control_tickers & treatment_tickers)
                / max(1, min(len(control_tickers), len(treatment_tickers)))
            )
            if not control_returns or not treatment_returns:
                continue
            delta = statistics.mean(treatment_returns) - statistics.mean(control_returns)
            all_deltas.append(delta)
            if (
                control_coverage >= min_label_coverage
                and treatment_coverage >= min_label_coverage
            ):
                qualified_deltas.append(delta)
        result[f"top_{k}"] = {
            "paired_sessions": len(all_deltas),
            "mean_treatment_minus_control_pct": _round(_mean(all_deltas)),
            "median_treatment_minus_control_pct": _round(_median(all_deltas)),
            "treatment_win_rate": _round(
                sum(value > 0 for value in all_deltas) / len(all_deltas)
                if all_deltas
                else None
            ),
            "coverage_qualified_sessions": len(qualified_deltas),
            "qualified_mean_delta_pct": _round(_mean(qualified_deltas)),
            "qualified_median_delta_pct": _round(_median(qualified_deltas)),
            "qualified_treatment_win_rate": _round(
                sum(value > 0 for value in qualified_deltas) / len(qualified_deltas)
                if qualified_deltas
                else None
            ),
            "mean_control_label_coverage": _round(_mean(control_coverages)),
            "mean_treatment_label_coverage": _round(_mean(treatment_coverages)),
            "mean_selection_overlap_rate": _round(_mean(overlap_rates)),
            "coverage_qualification": (
                f"both arms have at least {min_label_coverage:.0%} label coverage"
            ),
            "interpretation": (
                f"treatment={treatment_field} top-k minus "
                f"control={control_field} top-k; gross observational replay"
            ),
        }
    return result


def _kr_provider_health(start_date: str, end_date: str) -> dict[str, Any]:
    start_key = start_date.replace("-", "")
    end_key = end_date.replace("-", "")
    snapshots: list[dict[str, Any]] = []
    for path in sorted((ROOT / "logs" / "screener").glob("*_KR_screen.jsonl")):
        date_key = path.name[:8]
        if not (start_key <= date_key <= end_key):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            counts = row.get("counts") if isinstance(row.get("counts"), dict) else {}
            raw_count = int(counts.get("kospi_raw") or 0) + int(counts.get("kosdaq_raw") or 0)
            candidates = row.get("final_candidates") if isinstance(row.get("final_candidates"), list) else []
            nonzero_price = sum(1 for item in candidates if (_num((item or {}).get("price")) or 0) > 0)
            state = "live_raw" if raw_count > 0 else "cache_or_fallback"
            if raw_count == 0 and nonzero_price == 0:
                state = "hard_fallback_zero_price"
            snapshots.append(
                {
                    "date": date_key,
                    "time": str(row.get("ts") or "")[11:16],
                    "phase": str(row.get("phase") or ""),
                    "state": state,
                    "raw_count": raw_count,
                    "final_count": int(counts.get("final") or 0),
                }
            )
    states = Counter(row["state"] for row in snapshots)
    by_time = Counter(
        f"{row['time']}|{row['state']}"
        for row in snapshots
        if row["state"] != "live_raw"
    )
    return {
        "snapshots": len(snapshots),
        "days": len({row["date"] for row in snapshots}),
        "states": dict(states),
        "non_live_by_time": dict(by_time),
        "live_raw_rate": _round(states.get("live_raw", 0) / len(snapshots) if snapshots else None),
    }


def _us_provider_health(start_date: str, end_date: str) -> dict[str, Any]:
    start_key = start_date.replace("-", "")
    end_key = end_date.replace("-", "")
    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    root = ROOT / "logs" / "screener_quality"
    for path in sorted(root.glob("*_US_candidates.jsonl")):
        date_key = path.name[:8]
        if not (start_key <= date_key <= end_key):
            continue
        try:
            handle = path.open(encoding="utf-8")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                key = (str(row.get("timestamp") or ""), str(row.get("phase") or ""))
                item = snapshots.setdefault(
                    key,
                    {
                        "date": date_key,
                        "state": str(row.get("screener_quality_state") or "UNKNOWN"),
                        "degraded": bool(row.get("screener_degraded")),
                        "cache_used": bool(row.get("screener_cache_used")),
                        "tickers": set(),
                    },
                )
                ticker = str(row.get("ticker") or "")
                if ticker:
                    item["tickers"].add(ticker)
    states = Counter(item["state"] for item in snapshots.values())
    return {
        "snapshots": len(snapshots),
        "days": len({item["date"] for item in snapshots.values()}),
        "states": dict(states),
        "degraded_count": sum(1 for item in snapshots.values() if item["degraded"]),
        "degraded_rate": _round(
            sum(1 for item in snapshots.values() if item["degraded"]) / len(snapshots)
            if snapshots
            else None
        ),
        "cache_used_count": sum(1 for item in snapshots.values() if item["cache_used"]),
        "cache_used_rate": _round(
            sum(1 for item in snapshots.values() if item["cache_used"]) / len(snapshots)
            if snapshots
            else None
        ),
        "mean_unique_candidates": _round(
            _mean([float(len(item["tickers"])) for item in snapshots.values()])
        ),
    }


def _sub_screener_operations(start_date: str, end_date: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for market in ("KR", "US"):
        counters: Counter[str] = Counter()
        files = 0
        for path in sorted((ROOT / "state").glob(f"sub_screener_{market}_*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            date = str(payload.get("date") or "")
            if not (start_date <= date <= end_date):
                continue
            files += 1
            for key in (
                "scan_count",
                "detection_count",
                "attempt_count",
                "success_count",
                "dedupe_suppressed_count",
                "triage_success_count",
            ):
                counters[key] += int(payload.get(key) or 0)
        result[market] = {"sessions": files, **dict(counters)}
    return result


def _pipeline_conversion(
    audit_db: Path,
    performance_db: Path,
    *,
    start_date: str,
    end_date: str,
    runtime_mode: str,
) -> dict[str, Any]:
    audit = sqlite3.connect(f"file:{audit_db}?mode=ro", uri=True, timeout=30)
    audit.row_factory = sqlite3.Row
    result: dict[str, Any] = {}
    try:
        for market in ("KR", "US"):
            rows = audit.execute(
                """
                SELECT final_prompt_included, actual_prompt_included,
                       route_final_action, claude_trade_ready,
                       buy_signal_count, filled_count
                FROM audit_candidate_latest_rows
                WHERE runtime_mode=? AND market=?
                  AND screener_seen=1
                  AND session_date BETWEEN ? AND ?
                """,
                (runtime_mode, market, start_date, end_date),
            ).fetchall()
            prompt = sum(
                1
                for row in rows
                if int(row["final_prompt_included"] or 0)
                or int(row["actual_prompt_included"] or 0)
            )
            actionable = sum(
                1
                for row in rows
                if str(row["route_final_action"] or "").upper() in READY_ACTIONS
                or int(row["claude_trade_ready"] or 0) > 0
            )
            signal = sum(1 for row in rows if int(row["buy_signal_count"] or 0) > 0)
            filled = sum(1 for row in rows if int(row["filled_count"] or 0) > 0)
            result[market] = {
                "candidate_latest_rows": len(rows),
                "prompt_rows": prompt,
                "prompt_rate": _round(prompt / len(rows) if rows else None),
                "actionable_rows": actionable,
                "actionable_rate_from_prompt": _round(actionable / prompt if prompt else None),
                "signal_rows": signal,
                "filled_rows_in_candidate_audit": filled,
            }
    finally:
        audit.close()

    if performance_db.exists():
        performance = sqlite3.connect(f"file:{performance_db}?mode=ro", uri=True, timeout=30)
        performance.row_factory = sqlite3.Row
        try:
            for row in performance.execute(
                """
                SELECT market,
                       COUNT(DISTINCT v2_decision_id) AS canonical_filled,
                       SUM(CASE WHEN closed=1 THEN 1 ELSE 0 END) AS canonical_closed
                FROM v2_canonical_performance
                WHERE runtime_mode=? AND filled=1
                  AND session_date BETWEEN ? AND ?
                GROUP BY market
                """,
                (runtime_mode, start_date, end_date),
            ):
                market = str(row["market"] or "")
                result.setdefault(market, {})
                result[market]["canonical_filled_decisions"] = int(row["canonical_filled"] or 0)
                result[market]["canonical_closed_decisions"] = int(row["canonical_closed"] or 0)
        finally:
            performance.close()
    return result


def build_review(
    *,
    audit_db: Path = DEFAULT_AUDIT_DB,
    performance_db: Path = DEFAULT_PERFORMANCE_DB,
    start_date: str,
    end_date: str,
    runtime_mode: str = "live",
    horizon_min: int = 60,
) -> dict[str, Any]:
    rows = _first_candidate_rows(
        audit_db,
        start_date=start_date,
        end_date=end_date,
        runtime_mode=runtime_mode,
    )
    attach_local_forward(rows, horizon_min=horizon_min)
    by_market: dict[str, Any] = {}
    for market in ("KR", "US"):
        market_rows = [row for row in rows if str(row.get("market") or "") == market]
        topk_values = (5, 10, 20, 28) if market == "KR" else (5, 10, 20, 24)
        by_market[market] = {
            "overall": _metric(market_rows, horizon_min),
            "by_candidate_source": _group_metrics(
                market_rows, "candidate_source", horizon_min=horizon_min, min_rows=5
            ),
            "by_primary_bucket": _group_metrics(
                market_rows, "primary_bucket", horizon_min=horizon_min, min_rows=5
            ),
            "by_prompt_inclusion": _group_metrics(
                market_rows, "final_prompt_included", horizon_min=horizon_min
            ),
            "by_data_quality": _group_metrics(
                market_rows, "data_quality", horizon_min=horizon_min, min_rows=5
            ),
            "raw_rank_bins": _rank_bin_metrics(
                market_rows, "raw_rank", horizon_min=horizon_min
            ),
            "trainer_rank_bins": _rank_bin_metrics(
                market_rows, "trainer_score_rank", horizon_min=horizon_min
            ),
            "raw_score_quintiles": _quintile_metrics(
                market_rows,
                "raw_score_current",
                higher_is_better=True,
                horizon_min=horizon_min,
            ),
            "quality_score_quintiles": _quintile_metrics(
                market_rows,
                "candidate_quality_score",
                higher_is_better=True,
                horizon_min=horizon_min,
            ),
            "trainer_prompt_score_quintiles": _quintile_metrics(
                market_rows,
                "trainer_prompt_score",
                higher_is_better=True,
                horizon_min=horizon_min,
            ),
            "spearman": {
                field: _spearman(market_rows, field, horizon_min)
                for field in (
                    "raw_score_current",
                    "candidate_quality_score",
                    "trainer_prompt_score",
                )
            },
            "session_topk_by_raw_rank": _session_topk(
                market_rows,
                "raw_rank",
                lower_is_better=True,
                ks=topk_values,
                horizon_min=horizon_min,
            ),
            "session_topk_by_trainer_rank": _session_topk(
                market_rows,
                "trainer_score_rank",
                lower_is_better=True,
                ks=topk_values,
                horizon_min=horizon_min,
            ),
            "rank_reorder_counterfactual": _session_topk_counterfactual(
                market_rows,
                control_field="raw_rank",
                treatment_field="trainer_score_rank",
                control_lower_is_better=True,
                treatment_lower_is_better=True,
                ks=topk_values,
                horizon_min=horizon_min,
            ),
        }
    return {
        "schema": "screener_performance_review.v1",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "external_api_calls_made": 0,
        "window": {
            "start_date": start_date,
            "end_date": end_date,
            "runtime_mode": runtime_mode,
            "horizon_min": horizon_min,
        },
        "candidate_contract": {
            "dedupe": "first market/session/ticker screener_seen row",
            "outcome": "first local minute bar at/after known_at",
            "causal_guard": "bars before known_at are never used",
            "limitations": [
                "local minute coverage is incomplete and non-random",
                "candidate audit contains screened candidates, not the full tradable universe",
                "forward return is gross opportunity, not executable net PnL",
                "16-session July window is diagnostic, not a promotion-grade sample",
            ],
        },
        "candidate_first_rows": len(rows),
        "local_minute_files_loaded": len(_MINUTE_CACHE),
        "by_market": by_market,
        "provider_health": {
            "KR": _kr_provider_health(start_date, end_date),
            "US": _us_provider_health(start_date, end_date),
        },
        "sub_screener_operations": _sub_screener_operations(start_date, end_date),
        "pipeline_conversion": _pipeline_conversion(
            audit_db,
            performance_db,
            start_date=start_date,
            end_date=end_date,
            runtime_mode=runtime_mode,
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
    parser.add_argument("--performance-db", default=str(DEFAULT_PERFORMANCE_DB))
    parser.add_argument("--start-date", default="2026-07-01")
    parser.add_argument("--end-date", default="2026-07-23")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizon-min", type=int, default=60)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    payload = build_review(
        audit_db=Path(args.audit_db),
        performance_db=Path(args.performance_db),
        start_date=args.start_date,
        end_date=args.end_date,
        runtime_mode=args.runtime_mode,
        horizon_min=max(1, int(args.horizon_min)),
    )
    text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.output:
        path = Path(args.output)
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
