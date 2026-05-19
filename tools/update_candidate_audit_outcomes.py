from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.candidate_audit_store import CandidateAuditStore
from runtime_paths import get_runtime_path
import ticker_selection_db as _selection_price_db


DEFAULT_HORIZONS = (30, 60)
MIN_SAMPLES_BY_HORIZON = {30: 2, 60: 3}
DAILY_FORWARD_HORIZONS = {1440: 1, 2880: 2, 4320: 3}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        try:
            parsed = datetime.fromisoformat(normalized[:19])
        except Exception:
            return None
    if parsed.tzinfo is not None:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def _iso(value: datetime | None) -> str:
    return value.replace(microsecond=0).isoformat() if value else ""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        out = float(text)
    except Exception:
        return None
    return out if out > 0 else None


def _max_gap_sec(points: list[datetime]) -> int:
    if len(points) < 2:
        return 0
    ordered = sorted(points)
    return int(max((b - a).total_seconds() for a, b in zip(ordered, ordered[1:])))


def _candidate_filters(
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> tuple[str, list[Any]]:
    where = ["runtime_mode=?"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    return " AND ".join(where), params


def _load_candidate_rows(
    conn: sqlite3.Connection,
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> list[dict[str, Any]]:
    where, params = _candidate_filters(session_date=session_date, market=market, runtime_mode=runtime_mode)
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT candidate_key, call_id, runtime_mode, market, session_date,
                   known_at, ticker, price, classification,
                   consensus_mode, strength_capture_shadow, strength_capture_rules
            FROM audit_candidate_rows
            WHERE {where}
            ORDER BY session_date, market, ticker, known_at
            """,
            params,
        )
    ]


def _load_price_observations(
    conn: sqlite3.Connection,
    *,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
) -> dict[tuple[str, str, str, str], list[tuple[datetime, float]]]:
    where = ["runtime_mode=?", "known_at IS NOT NULL", "known_at!=''", "price IS NOT NULL", "price>0"]
    params: list[Any] = [str(runtime_mode or "live").lower()]
    if session_date:
        where.append("session_date=?")
        params.append(session_date)
    if market:
        where.append("market=?")
        params.append(str(market).upper())
    observations: dict[tuple[str, str, str, str], list[tuple[datetime, float]]] = {}
    for row in conn.execute(
        f"""
        SELECT runtime_mode, market, session_date, ticker, known_at, price
        FROM audit_candidate_rows
        WHERE {' AND '.join(where)}
        ORDER BY session_date, market, ticker, known_at
        """,
        params,
    ):
        ts = _parse_dt(row["known_at"])
        price = _to_float(row["price"])
        if ts is None or price is None:
            continue
        key = (
            str(row["runtime_mode"] or "live").lower(),
            str(row["market"] or "").upper(),
            str(row["session_date"] or ""),
            str(row["ticker"] or "").upper(),
        )
        observations.setdefault(key, []).append((ts, price))
    for values in observations.values():
        values.sort(key=lambda item: item[0])
    return observations


def _build_outcome_row(
    *,
    candidate: dict[str, Any],
    horizon_min: int,
    observations: list[tuple[datetime, float]],
    label_generated_at: str,
    min_samples: int,
) -> dict[str, Any]:
    if int(horizon_min) in DAILY_FORWARD_HORIZONS:
        raise ValueError("daily forward horizons must use _build_daily_forward_outcome_row")
    base_at = _parse_dt(candidate.get("known_at"))
    base_price = _to_float(candidate.get("price"))
    target_at = base_at + timedelta(minutes=horizon_min) if base_at else None
    payload: dict[str, Any] = {
        "base_at": _iso(base_at),
        "base_price": base_price,
        "known_at": _iso(target_at),
        "sample_count": 0,
        "outcome_quality": "insufficient_samples",
    }
    base = {
        "candidate_key": candidate.get("candidate_key"),
        "horizon_min": horizon_min,
        "target_at": _iso(target_at),
        "observed_at": "",
        "observed_price": None,
        "return_pct": None,
        "max_runup_pct": None,
        "max_drawdown_pct": None,
        "status": "insufficient_samples",
        "source": "audit_candidate_rows",
        "label_generated_at": label_generated_at,
        "payload": payload,
    }
    if base_at is None or base_price is None or target_at is None:
        payload["reason"] = "missing_base"
        return base

    future = [(ts, price) for ts, price in observations if base_at < ts <= target_at]
    payload["sample_count"] = len(future)
    payload["min_samples"] = min_samples
    if future:
        payload["first_sample_at"] = _iso(future[0][0])
        payload["last_sample_at"] = _iso(future[-1][0])
        payload["max_gap_sec"] = _max_gap_sec([base_at] + [ts for ts, _ in future])
    if len(future) < min_samples:
        payload["reason"] = "too_few_future_samples"
        return base

    observed_at, observed_price = future[-1]
    prices = [price for _, price in future]
    max_price = max(prices)
    min_price = min(prices)
    payload.update(
        {
            "outcome_quality": "audit_sparse",
            "max_price": max_price,
            "min_price": min_price,
            "reason": "",
        }
    )
    return {
        **base,
        "observed_at": _iso(observed_at),
        "observed_price": observed_price,
        "return_pct": ((observed_price / base_price) - 1.0) * 100.0,
        "max_runup_pct": ((max_price / base_price) - 1.0) * 100.0,
        "max_drawdown_pct": ((min_price / base_price) - 1.0) * 100.0,
        "status": "audit_sparse",
        "payload": payload,
    }


def _build_daily_forward_outcome_row(
    *,
    candidate: dict[str, Any],
    horizon_min: int,
    label_generated_at: str,
) -> dict[str, Any]:
    horizon_key = int(horizon_min)
    offset = DAILY_FORWARD_HORIZONS.get(horizon_key)
    session_date = str(candidate.get("session_date") or "")
    market = str(candidate.get("market") or "").upper()
    ticker = str(candidate.get("ticker") or "").upper()
    payload: dict[str, Any] = {
        "horizon_kind": "trading_day_close",
        "trading_day_offset": offset,
        "base_session_date": session_date,
        "base_price_source": "price_csv_close",
        "target_price_source": "price_csv_close",
        "strength_capture_shadow": bool(candidate.get("strength_capture_shadow")),
        "strength_capture_rules": candidate.get("strength_capture_rules") or "[]",
    }
    base = {
        "candidate_key": candidate.get("candidate_key"),
        "horizon_min": horizon_key,
        "target_at": "",
        "observed_at": "",
        "observed_price": None,
        "return_pct": None,
        "max_runup_pct": None,
        "max_drawdown_pct": None,
        "status": "daily_pending",
        "source": "audit_candidate_rows_daily_forward",
        "label_generated_at": label_generated_at,
        "payload": payload,
    }
    if offset is None:
        payload["reason"] = "unsupported_daily_horizon"
        return {**base, "status": "daily_unsupported_horizon"}

    price_data = _selection_price_db._load_price(market, ticker)
    if price_data is None:
        payload["reason"] = "missing_price_csv"
        return {**base, "status": "daily_missing_csv"}

    base_idx = (price_data.get("index") or {}).get(session_date)
    if base_idx is None:
        payload["reason"] = "session_date_not_in_price_csv"
        return {**base, "status": "daily_missing_base"}

    closes = list(price_data.get("closes") or [])
    dates = list(price_data.get("dates") or [])
    target_idx = int(base_idx) + int(offset)
    if base_idx >= len(closes) or base_idx >= len(dates):
        payload["reason"] = "invalid_base_index"
        return {**base, "status": "daily_missing_base"}

    try:
        base_close = float(closes[base_idx])
    except Exception:
        base_close = 0.0
    if base_close <= 0:
        payload["reason"] = "invalid_base_close"
        return {**base, "status": "daily_missing_base"}

    payload["base_close"] = base_close
    if target_idx >= len(closes) or target_idx >= len(dates):
        payload["reason"] = "target_session_not_available"
        return {**base, "status": "daily_pending"}

    try:
        target_close = float(closes[target_idx])
    except Exception:
        target_close = 0.0
    if target_close <= 0:
        payload["reason"] = "invalid_target_close"
        return {**base, "status": "daily_pending"}

    target_session_date = str(dates[target_idx])
    return_pct = _selection_price_db._calc_forward_return(price_data, session_date, int(offset))
    max_runup, max_drawdown = _selection_price_db._calc_window_excursion(price_data, session_date, int(offset))
    payload.update(
        {
            "reason": "",
            "target_session_date": target_session_date,
            "target_close": target_close,
        }
    )
    return {
        **base,
        "target_at": target_session_date,
        "observed_at": target_session_date,
        "observed_price": target_close,
        "return_pct": return_pct,
        "max_runup_pct": max_runup,
        "max_drawdown_pct": max_drawdown,
        "status": "daily_forward",
        "payload": payload,
    }


def update_candidate_audit_outcomes(
    *,
    db_path: str | Path | None = None,
    session_date: str = "",
    market: str = "",
    runtime_mode: str = "live",
    horizons: tuple[int, ...] = DEFAULT_HORIZONS,
    min_samples_by_horizon: dict[int, int] | None = None,
) -> dict[str, Any]:
    target = Path(db_path) if db_path else get_runtime_path("data", "audit", "candidate_audit.db")
    store = CandidateAuditStore(target)
    label_generated_at = _utc_now()
    next_due_at = (datetime.now(timezone.utc) + timedelta(minutes=max(horizons or DEFAULT_HORIZONS))).isoformat(timespec="seconds")
    min_samples = dict(MIN_SAMPLES_BY_HORIZON)
    if min_samples_by_horizon:
        min_samples.update({int(k): int(v) for k, v in min_samples_by_horizon.items()})

    conn = store.connect()
    try:
        candidates = _load_candidate_rows(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
        observations = _load_price_observations(
            conn,
            session_date=session_date,
            market=market,
            runtime_mode=runtime_mode,
        )
    finally:
        conn.close()

    outcome_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    for candidate in candidates:
        key = (
            str(candidate.get("runtime_mode") or "live").lower(),
            str(candidate.get("market") or "").upper(),
            str(candidate.get("session_date") or ""),
            str(candidate.get("ticker") or "").upper(),
        )
        ticker_observations = observations.get(key, [])
        for horizon in horizons:
            horizon_key = int(horizon)
            if horizon_key in DAILY_FORWARD_HORIZONS:
                row = _build_daily_forward_outcome_row(
                    candidate=candidate,
                    horizon_min=horizon_key,
                    label_generated_at=label_generated_at,
                )
            else:
                row = _build_outcome_row(
                    candidate=candidate,
                    horizon_min=horizon_key,
                    observations=ticker_observations,
                    label_generated_at=label_generated_at,
                    min_samples=int(min_samples.get(horizon_key, 1)),
                )
            outcome_rows.append(row)
            status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    written = store.upsert_outcomes(outcome_rows)
    return {
        "db_path": str(target),
        "session_date": session_date,
        "market": str(market or "").upper(),
        "runtime_mode": str(runtime_mode or "live").lower(),
        "candidate_rows": len(candidates),
        "outcome_rows": written,
        "horizons": list(horizons),
        "status_counts": status_counts,
        "label_generated_at": label_generated_at,
        "last_success_at": label_generated_at if written or not candidates else "",
        "next_due_at": next_due_at,
        "outcome_health": "ok" if written or not candidates else "no_outcomes_written",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Update candidate audit 30m/60m outcome labels.")
    parser.add_argument("--db", default="", help="candidate audit DB path")
    parser.add_argument("--date", default="", help="session date YYYY-MM-DD")
    parser.add_argument("--market", default="", help="KR or US; empty means all markets")
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--horizons", default="30,60", help="comma-separated minute horizons")
    args = parser.parse_args()
    horizons = tuple(int(part.strip()) for part in str(args.horizons).split(",") if part.strip())
    summary = update_candidate_audit_outcomes(
        db_path=args.db or None,
        session_date=args.date,
        market=args.market,
        runtime_mode=args.runtime_mode,
        horizons=horizons or DEFAULT_HORIZONS,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
