from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from audit.shadow_audit_store import ShadowAuditStore


HORIZONS_MIN = (5, 15, 30, 60)
CLOSE_HORIZON = -1


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _pct(start: float, end: float) -> float | None:
    if start <= 0 or end <= 0:
        return None
    return (end / start - 1.0) * 100.0


class ShadowOutcomeUpdater:
    """Computes passive signal outcomes from stored audit price samples."""

    def __init__(self, path: str | Path, *, timeout: float = 2.0) -> None:
        self.store = ShadowAuditStore(path, timeout=timeout)

    def update_pending(self, *, session_date: str = "", market: str = "", force_close: bool = False) -> dict[str, Any]:
        summary = {
            "checked": 0,
            "written": 0,
            "missing_price": 0,
            "errors": [],
        }
        where = []
        params: list[Any] = []
        if session_date:
            where.append("session_date=?")
            params.append(session_date)
        if market:
            where.append("market=?")
            params.append(str(market).upper())
        where_clause = "WHERE " + " AND ".join(where) if where else ""
        all_events: list[dict[str, Any]] = []
        conn = self.store.connect()
        try:
            rows = [
                dict(row)
                for row in conn.execute(
                    f"""
                    SELECT signal_id, runtime_mode, market, session_date, ticker, signal_at, signal_price
                    FROM audit_signals
                    {where_clause}
                    """,
                    tuple(params),
                )
            ]
            for signal in rows:
                summary["checked"] += 1
                try:
                    events = self._outcomes_for_signal(conn, signal, force_close=force_close)
                    all_events.extend(events)
                    summary["missing_price"] += sum(1 for event in events if event.get("status") == "missing_price")
                except Exception as exc:
                    summary["errors"].append(f"{signal.get('signal_id')}:{exc}")
        finally:
            conn.close()
        if all_events:
            try:
                summary["written"] = self.store.write_events(all_events)
            except Exception as exc:
                summary["errors"].append(f"batch_write:{exc}")
        return summary

    def _outcomes_for_signal(
        self,
        conn: sqlite3.Connection,
        signal: dict[str, Any],
        *,
        force_close: bool,
    ) -> list[dict[str, Any]]:
        signal_id = str(signal.get("signal_id") or "")
        signal_at = _parse_dt(signal.get("signal_at"))
        signal_price = float(signal.get("signal_price") or 0)
        if not signal_id or signal_at is None or signal_price <= 0:
            return []

        samples = [
            dict(row)
            for row in conn.execute(
                """
                SELECT sampled_at, price
                FROM audit_price_samples
                WHERE runtime_mode=? AND market=? AND session_date=? AND ticker=?
                  AND sampled_at>=?
                ORDER BY sampled_at
                """,
                (
                    signal.get("runtime_mode", ""),
                    signal.get("market", ""),
                    signal.get("session_date", ""),
                    signal.get("ticker", ""),
                    signal_at.isoformat(),
                ),
            )
        ]
        parsed: list[tuple[datetime, float]] = []
        for sample in samples:
            dt = _parse_dt(sample.get("sampled_at"))
            price = float(sample.get("price") or 0)
            if dt is not None and price > 0:
                parsed.append((dt, price))

        events: list[dict[str, Any]] = []
        for horizon in HORIZONS_MIN:
            target = signal_at + timedelta(minutes=horizon)
            observed = next(((dt, price) for dt, price in parsed if dt >= target), None)
            window = [(dt, price) for dt, price in parsed if signal_at <= dt <= target]
            events.append(self._event(signal_id, horizon, target, signal_price, observed, window))

        if force_close:
            post_signal = [(dt, price) for dt, price in parsed if dt > signal_at]
            observed = post_signal[-1] if post_signal else None
            events.append(self._event(signal_id, CLOSE_HORIZON, None, signal_price, observed, parsed))
        return events

    @staticmethod
    def _event(
        signal_id: str,
        horizon: int,
        target: datetime | None,
        signal_price: float,
        observed: tuple[datetime, float] | None,
        window: list[tuple[datetime, float]],
    ) -> dict[str, Any]:
        if observed is None:
            return {
                "kind": "outcome",
                "signal_id": signal_id,
                "horizon_min": horizon,
                "target_at": target.isoformat() if target is not None else "",
                "status": "missing_price",
            }
        observed_at, observed_price = observed
        prices = [signal_price] + [price for _, price in window]
        max_runup = _pct(signal_price, max(prices))
        max_drawdown = _pct(signal_price, min(prices))
        return {
            "kind": "outcome",
            "signal_id": signal_id,
            "horizon_min": horizon,
            "target_at": target.isoformat() if target is not None else "",
            "observed_at": observed_at.isoformat(),
            "observed_price": observed_price,
            "return_pct": _pct(signal_price, observed_price),
            "max_runup_pct": max_runup,
            "max_drawdown_pct": max_drawdown,
            "status": "computed",
        }
