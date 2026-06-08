from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lifecycle.quality import evaluate_decision_quality, forward_measurement_complete, live_clean_learning_allowed


DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


V2_LEARNING_SCHEMA = """
CREATE TABLE IF NOT EXISTS v2_learning_performance (
    v2_decision_id       TEXT PRIMARY KEY,
    market               TEXT NOT NULL,
    runtime_mode         TEXT NOT NULL,
    session_date         TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    status               TEXT NOT NULL,
    route                TEXT,
    path_type            TEXT,
    path_run_id          TEXT,
    strategy             TEXT,
    strategy_attribution TEXT NOT NULL DEFAULT 'strategy',
    origin_action        TEXT,
    candidate_pool_role  TEXT,
    experiment_bucket    TEXT NOT NULL DEFAULT 'standard',
    discovery_live_experiment INTEGER NOT NULL DEFAULT 0,
    discovery_action_ceiling TEXT,
    discovery_signal_family TEXT,
    discovery_reason     TEXT,
    discovery_overlay_rank INTEGER,
    timing_style         TEXT,
    filled               INTEGER NOT NULL DEFAULT 0,
    closed               INTEGER NOT NULL DEFAULT 0,
    portfolio_realized   INTEGER NOT NULL DEFAULT 0,
    fill_event_id        INTEGER,
    close_event_id       INTEGER,
    filled_at            TEXT,
    closed_at            TEXT,
    entry_price          REAL,
    exit_price           REAL,
    qty                  REAL,
    pnl_krw              REAL,
    pnl_pct              REAL,
    mfe_pct              REAL,
    mae_pct              REAL,
    close_reason         TEXT,
    forward_complete     INTEGER NOT NULL DEFAULT 0,
    quality_grade        TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
    quality_reasons_json TEXT NOT NULL DEFAULT '[]',
    learning_allowed     INTEGER NOT NULL DEFAULT 0,
    source_event_count   INTEGER NOT NULL DEFAULT 0,
    synced_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_market_session
    ON v2_learning_performance(market, runtime_mode, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_ticker
    ON v2_learning_performance(market, ticker, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_learning
    ON v2_learning_performance(learning_allowed, quality_grade, session_date);

CREATE TABLE IF NOT EXISTS v2_canonical_performance (
    v2_decision_id       TEXT PRIMARY KEY,
    canonical_key        TEXT NOT NULL,
    market               TEXT NOT NULL,
    runtime_mode         TEXT NOT NULL,
    session_date         TEXT NOT NULL,
    ticker               TEXT NOT NULL,
    status               TEXT NOT NULL,
    route                TEXT,
    path_type            TEXT,
    path_run_id          TEXT,
    strategy             TEXT,
    strategy_attribution TEXT NOT NULL DEFAULT 'strategy',
    origin_action        TEXT,
    candidate_pool_role  TEXT,
    experiment_bucket    TEXT NOT NULL DEFAULT 'standard',
    discovery_live_experiment INTEGER NOT NULL DEFAULT 0,
    discovery_action_ceiling TEXT,
    discovery_signal_family TEXT,
    discovery_reason     TEXT,
    discovery_overlay_rank INTEGER,
    filled               INTEGER NOT NULL DEFAULT 0,
    closed               INTEGER NOT NULL DEFAULT 0,
    portfolio_realized   INTEGER NOT NULL DEFAULT 0,
    first_fill_event_id  INTEGER,
    first_close_event_id INTEGER,
    last_close_event_id  INTEGER,
    earliest_fill_at     TEXT,
    first_closed_at      TEXT,
    last_closed_at       TEXT,
    entry_price          REAL,
    first_exit_price     REAL,
    last_exit_price      REAL,
    qty                  REAL,
    pnl_krw              REAL,
    pnl_pct              REAL,
    mfe_pct              REAL,
    mae_pct              REAL,
    quality_grade        TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
    learning_allowed     INTEGER NOT NULL DEFAULT 0,
    raw_fill_event_count INTEGER NOT NULL DEFAULT 0,
    raw_close_event_count INTEGER NOT NULL DEFAULT 0,
    source_event_count   INTEGER NOT NULL DEFAULT 0,
    metric_contract_json TEXT NOT NULL,
    synced_at            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_canonical_perf_market_session
    ON v2_canonical_performance(market, runtime_mode, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_canonical_perf_ticker
    ON v2_canonical_performance(market, ticker, session_date);
CREATE INDEX IF NOT EXISTS idx_v2_canonical_perf_bucket
    ON v2_canonical_performance(filled, closed, learning_allowed, session_date);

CREATE TABLE IF NOT EXISTS v2_decision_fill_links (
    v2_decision_id              TEXT PRIMARY KEY,
    canonical_key               TEXT NOT NULL,
    legacy_decision_id          INTEGER,
    market                      TEXT NOT NULL,
    runtime_mode                TEXT NOT NULL,
    session_date                TEXT NOT NULL,
    ticker                      TEXT NOT NULL,
    link_status                 TEXT NOT NULL,
    matched_by                  TEXT NOT NULL,
    filled_from_canonical       INTEGER NOT NULL DEFAULT 0,
    legacy_filled_before        INTEGER,
    legacy_filled_after         INTEGER,
    legacy_order_status_before  TEXT,
    legacy_order_status_after   TEXT,
    repaired                    INTEGER NOT NULL DEFAULT 0,
    unmatched_reason            TEXT,
    metric_contract_json        TEXT NOT NULL,
    synced_at                   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v2_decision_fill_links_status
    ON v2_decision_fill_links(link_status, market, session_date);
"""


UPSERT_SQL = """
INSERT INTO v2_learning_performance (
    v2_decision_id, market, runtime_mode, session_date, ticker, status,
    route, path_type, path_run_id, strategy, strategy_attribution, origin_action,
    candidate_pool_role, experiment_bucket, discovery_live_experiment,
    discovery_action_ceiling, discovery_signal_family, discovery_reason,
    discovery_overlay_rank, timing_style,
    filled, closed, portfolio_realized, fill_event_id, close_event_id, filled_at, closed_at,
    entry_price, exit_price, qty, pnl_krw, pnl_pct, mfe_pct, mae_pct,
    close_reason, forward_complete, quality_grade, quality_reasons_json,
    learning_allowed, source_event_count, synced_at
)
VALUES (
    :v2_decision_id, :market, :runtime_mode, :session_date, :ticker, :status,
    :route, :path_type, :path_run_id, :strategy, :strategy_attribution, :origin_action,
    :candidate_pool_role, :experiment_bucket, :discovery_live_experiment,
    :discovery_action_ceiling, :discovery_signal_family, :discovery_reason,
    :discovery_overlay_rank, :timing_style,
    :filled, :closed, :portfolio_realized, :fill_event_id, :close_event_id, :filled_at, :closed_at,
    :entry_price, :exit_price, :qty, :pnl_krw, :pnl_pct, :mfe_pct, :mae_pct,
    :close_reason, :forward_complete, :quality_grade, :quality_reasons_json,
    :learning_allowed, :source_event_count, :synced_at
)
ON CONFLICT(v2_decision_id) DO UPDATE SET
    market=excluded.market,
    runtime_mode=excluded.runtime_mode,
    session_date=excluded.session_date,
    ticker=excluded.ticker,
    status=excluded.status,
    route=excluded.route,
    path_type=excluded.path_type,
    path_run_id=excluded.path_run_id,
    strategy=excluded.strategy,
    strategy_attribution=excluded.strategy_attribution,
    origin_action=excluded.origin_action,
    candidate_pool_role=excluded.candidate_pool_role,
    experiment_bucket=excluded.experiment_bucket,
    discovery_live_experiment=excluded.discovery_live_experiment,
    discovery_action_ceiling=excluded.discovery_action_ceiling,
    discovery_signal_family=excluded.discovery_signal_family,
    discovery_reason=excluded.discovery_reason,
    discovery_overlay_rank=excluded.discovery_overlay_rank,
    timing_style=excluded.timing_style,
    filled=excluded.filled,
    closed=excluded.closed,
    portfolio_realized=excluded.portfolio_realized,
    fill_event_id=excluded.fill_event_id,
    close_event_id=excluded.close_event_id,
    filled_at=excluded.filled_at,
    closed_at=excluded.closed_at,
    entry_price=excluded.entry_price,
    exit_price=excluded.exit_price,
    qty=excluded.qty,
    pnl_krw=excluded.pnl_krw,
    pnl_pct=excluded.pnl_pct,
    mfe_pct=excluded.mfe_pct,
    mae_pct=excluded.mae_pct,
    close_reason=excluded.close_reason,
    forward_complete=excluded.forward_complete,
    quality_grade=excluded.quality_grade,
    quality_reasons_json=excluded.quality_reasons_json,
    learning_allowed=excluded.learning_allowed,
    source_event_count=excluded.source_event_count,
    synced_at=excluded.synced_at
"""

CANONICAL_UPSERT_SQL = """
INSERT INTO v2_canonical_performance (
    v2_decision_id, canonical_key, market, runtime_mode, session_date, ticker,
    status, route, path_type, path_run_id, strategy, strategy_attribution, origin_action,
    candidate_pool_role, experiment_bucket, discovery_live_experiment,
    discovery_action_ceiling, discovery_signal_family, discovery_reason,
    discovery_overlay_rank,
    filled, closed, portfolio_realized, first_fill_event_id, first_close_event_id, last_close_event_id,
    earliest_fill_at, first_closed_at, last_closed_at,
    entry_price, first_exit_price, last_exit_price, qty, pnl_krw, pnl_pct,
    mfe_pct, mae_pct, quality_grade, learning_allowed, raw_fill_event_count,
    raw_close_event_count, source_event_count, metric_contract_json, synced_at
)
VALUES (
    :v2_decision_id, :canonical_key, :market, :runtime_mode, :session_date, :ticker,
    :status, :route, :path_type, :path_run_id, :strategy, :strategy_attribution, :origin_action,
    :candidate_pool_role, :experiment_bucket, :discovery_live_experiment,
    :discovery_action_ceiling, :discovery_signal_family, :discovery_reason,
    :discovery_overlay_rank,
    :filled, :closed, :portfolio_realized, :first_fill_event_id, :first_close_event_id, :last_close_event_id,
    :earliest_fill_at, :first_closed_at, :last_closed_at,
    :entry_price, :first_exit_price, :last_exit_price, :qty, :pnl_krw, :pnl_pct,
    :mfe_pct, :mae_pct, :quality_grade, :learning_allowed, :raw_fill_event_count,
    :raw_close_event_count, :source_event_count, :metric_contract_json, :synced_at
)
ON CONFLICT(v2_decision_id) DO UPDATE SET
    canonical_key=excluded.canonical_key,
    market=excluded.market,
    runtime_mode=excluded.runtime_mode,
    session_date=excluded.session_date,
    ticker=excluded.ticker,
    status=excluded.status,
    route=excluded.route,
    path_type=excluded.path_type,
    path_run_id=excluded.path_run_id,
    strategy=excluded.strategy,
    strategy_attribution=excluded.strategy_attribution,
    origin_action=excluded.origin_action,
    candidate_pool_role=excluded.candidate_pool_role,
    experiment_bucket=excluded.experiment_bucket,
    discovery_live_experiment=excluded.discovery_live_experiment,
    discovery_action_ceiling=excluded.discovery_action_ceiling,
    discovery_signal_family=excluded.discovery_signal_family,
    discovery_reason=excluded.discovery_reason,
    discovery_overlay_rank=excluded.discovery_overlay_rank,
    filled=excluded.filled,
    closed=excluded.closed,
    portfolio_realized=excluded.portfolio_realized,
    first_fill_event_id=excluded.first_fill_event_id,
    first_close_event_id=excluded.first_close_event_id,
    last_close_event_id=excluded.last_close_event_id,
    earliest_fill_at=excluded.earliest_fill_at,
    first_closed_at=excluded.first_closed_at,
    last_closed_at=excluded.last_closed_at,
    entry_price=excluded.entry_price,
    first_exit_price=excluded.first_exit_price,
    last_exit_price=excluded.last_exit_price,
    qty=excluded.qty,
    pnl_krw=excluded.pnl_krw,
    pnl_pct=excluded.pnl_pct,
    mfe_pct=excluded.mfe_pct,
    mae_pct=excluded.mae_pct,
    quality_grade=excluded.quality_grade,
    learning_allowed=excluded.learning_allowed,
    raw_fill_event_count=excluded.raw_fill_event_count,
    raw_close_event_count=excluded.raw_close_event_count,
    source_event_count=excluded.source_event_count,
    metric_contract_json=excluded.metric_contract_json,
    synced_at=excluded.synced_at
"""

LINK_UPSERT_SQL = """
INSERT INTO v2_decision_fill_links (
    v2_decision_id, canonical_key, legacy_decision_id, market, runtime_mode,
    session_date, ticker, link_status, matched_by, filled_from_canonical,
    legacy_filled_before, legacy_filled_after, legacy_order_status_before,
    legacy_order_status_after, repaired, unmatched_reason, metric_contract_json,
    synced_at
)
VALUES (
    :v2_decision_id, :canonical_key, :legacy_decision_id, :market, :runtime_mode,
    :session_date, :ticker, :link_status, :matched_by, :filled_from_canonical,
    :legacy_filled_before, :legacy_filled_after, :legacy_order_status_before,
    :legacy_order_status_after, :repaired, :unmatched_reason, :metric_contract_json,
    :synced_at
)
ON CONFLICT(v2_decision_id) DO UPDATE SET
    canonical_key=excluded.canonical_key,
    legacy_decision_id=excluded.legacy_decision_id,
    market=excluded.market,
    runtime_mode=excluded.runtime_mode,
    session_date=excluded.session_date,
    ticker=excluded.ticker,
    link_status=excluded.link_status,
    matched_by=excluded.matched_by,
    filled_from_canonical=excluded.filled_from_canonical,
    legacy_filled_before=excluded.legacy_filled_before,
    legacy_filled_after=excluded.legacy_filled_after,
    legacy_order_status_before=excluded.legacy_order_status_before,
    legacy_order_status_after=excluded.legacy_order_status_after,
    repaired=excluded.repaired,
    unmatched_reason=excluded.unmatched_reason,
    metric_contract_json=excluded.metric_contract_json,
    synced_at=excluded.synced_at
"""

METRIC_CONTRACT = {
    "runtime_mode_axis": "live_or_paper",
    "market_axis": "KR_or_US",
    "dedupe_axis": "v2_decision_id_market_session_ticker",
    "bucket_axis": "filled_closed_unmatched",
    "watch_axis": "filled_demoted_pure_watch_separated",
    "experiment_bucket_axis": "standard_vs_discovery_live",
    "truth_source": "lifecycle_events_and_v2_path_runs",
}

ENTRY_PRICE_KEYS = (
    "entry_price",
    "actual_fill_price",
    "fill_price_native",
    "fill_price",
    "price",
    "avg_price",
)

EXIT_PRICE_KEYS = (
    "exit_price",
    "actual_fill_price",
    "fill_price",
    "price",
    "close_price",
    "exit_price_native",
    "sell_fill_price_native",
    "broker_sell_fill_price_native",
)

CLOSE_QTY_KEYS = (
    "qty",
    "filled_qty",
    "exit_qty",
    "sell_filled_qty",
    "sell_fill_qty",
    "broker_sell_filled_qty",
    "broker_sell_fill_qty",
)

ENTRY_QTY_KEYS = ("qty", "filled_qty", "entry_qty", "order_qty")

LEARNING_COMPARISON_FIELDS = (
    "market",
    "runtime_mode",
    "session_date",
    "ticker",
    "status",
    "route",
    "path_type",
    "path_run_id",
    "strategy",
    "strategy_attribution",
    "origin_action",
    "candidate_pool_role",
    "experiment_bucket",
    "discovery_live_experiment",
    "discovery_action_ceiling",
    "discovery_signal_family",
    "discovery_reason",
    "discovery_overlay_rank",
    "timing_style",
    "filled",
    "closed",
    "portfolio_realized",
    "fill_event_id",
    "close_event_id",
    "filled_at",
    "closed_at",
    "entry_price",
    "exit_price",
    "qty",
    "pnl_krw",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
    "close_reason",
    "forward_complete",
    "quality_grade",
    "quality_reasons_json",
    "learning_allowed",
    "source_event_count",
)

PERFORMANCE_EXPERIMENT_COLUMNS = {
    "strategy_attribution": "TEXT NOT NULL DEFAULT 'strategy'",
    "portfolio_realized": "INTEGER NOT NULL DEFAULT 0",
    "candidate_pool_role": "TEXT",
    "experiment_bucket": "TEXT NOT NULL DEFAULT 'standard'",
    "discovery_live_experiment": "INTEGER NOT NULL DEFAULT 0",
    "discovery_action_ceiling": "TEXT",
    "discovery_signal_family": "TEXT",
    "discovery_reason": "TEXT",
    "discovery_overlay_rank": "INTEGER",
}


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(V2_LEARNING_SCHEMA)
    _ensure_columns(conn, "v2_learning_performance", PERFORMANCE_EXPERIMENT_COLUMNS)
    _ensure_columns(conn, "v2_canonical_performance", PERFORMANCE_EXPERIMENT_COLUMNS)
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_learning_perf_experiment
        ON v2_learning_performance(experiment_bucket, candidate_pool_role, session_date)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_v2_canonical_perf_experiment
        ON v2_canonical_performance(experiment_bucket, candidate_pool_role, session_date)
        """
    )


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(value or "{}")
        return dict(parsed) if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _row_to_dict(row: sqlite3.Row | None, payload_key: str = "payload_json") -> dict[str, Any]:
    if row is None:
        return {}
    data = dict(row)
    if payload_key in data:
        data["payload"] = _json_loads(data.pop(payload_key))
    if "plan_json" in data:
        data["plan"] = _json_loads(data.pop("plan_json"))
    return data


def _num(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except Exception:
            continue
    return None


def _text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    return ""


def _code(value: Any) -> str:
    return str(value or "").strip().upper()


def _actor(value: Any) -> str:
    return str(value or "").strip().lower()


def _close_price(payload: dict[str, Any]) -> float | None:
    return _num(payload, *EXIT_PRICE_KEYS)


def _close_qty(close_payload: dict[str, Any], fill_payload: dict[str, Any]) -> float | None:
    return _num(close_payload, *CLOSE_QTY_KEYS) or _num(fill_payload, *ENTRY_QTY_KEYS)


def _close_payload_qty(close_payload: dict[str, Any]) -> float | None:
    return _num(close_payload, *CLOSE_QTY_KEYS)


def _strategy_attribution(close_event: dict[str, Any], close_payload: dict[str, Any]) -> str:
    close_reason = _code(_text(close_payload, "close_reason", "exit_reason") or close_event.get("reason_code"))
    data_quality = _code(close_event.get("data_quality") or close_payload.get("data_quality"))
    closed_by = _actor(close_payload.get("closed_by"))
    if (
        close_reason == "CLOSED_AUDITED_BROKER_SELL"
        or data_quality == "AUDITED_BROKER_BACKFILL"
        or closed_by == "manual_broker_reconcile_backfill"
    ):
        return "audited_broker_backfill"
    if close_reason == "CLOSED_AUDITED_BROKER_ABSENT":
        return "audited_broker_absent"
    return "strategy"


def _portfolio_realized(close: dict[str, Any], close_reason: str, exit_price: float | None, pnl_krw: float | None, pnl_pct: float | None) -> int:
    if not close or _code(close_reason) == "CLOSED_AUDITED_BROKER_ABSENT":
        return 0
    return 1 if any(value is not None for value in (exit_price, pnl_krw, pnl_pct)) else 0


def _sync_degraded_reason(close: dict[str, Any], close_reason: str, close_payload: dict[str, Any], exit_price: float | None) -> str:
    if not close:
        return ""
    if _code(close_reason) == "CLOSED_AUDITED_BROKER_SELL" and exit_price is None:
        return "MISSING_AUDITED_EXIT_PRICE"
    if exit_price is not None and _close_payload_qty(close_payload) is None:
        return "MISSING_CLOSE_QTY"
    return ""


def _first_event(events: list[dict[str, Any]], *event_types: str) -> dict[str, Any]:
    allowed = set(event_types)
    for event in events:
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _entry_fill_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fills: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("event_type") or "") not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("side") or "").strip().lower() == "sell":
            continue
        fills.append(event)
    return fills


def _close_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if str(event.get("event_type") or "") == "CLOSED"]


def _last_event(events: list[dict[str, Any]], *event_types: str) -> dict[str, Any]:
    allowed = set(event_types)
    for event in reversed(events):
        if str(event.get("event_type") or "") in allowed:
            return event
    return {}


def _metric_contract_json() -> str:
    return json.dumps(METRIC_CONTRACT, ensure_ascii=False, sort_keys=True)


def _load_decisions(
    conn: sqlite3.Connection,
    *,
    market: str | None = None,
    runtime_mode: str | None = "live",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    where: list[str] = []
    params: list[Any] = []
    if market and market.upper() != "ALL":
        where.append("market=?")
        params.append(market.upper())
    if runtime_mode:
        where.append("runtime_mode=?")
        params.append(runtime_mode.lower())
    if start_date:
        where.append("session_date>=?")
        params.append(start_date)
    if end_date:
        where.append("session_date<=?")
        params.append(end_date)
    sql = "SELECT * FROM v2_decisions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY session_date, market, ticker, decision_id"
    rows = conn.execute(sql, params).fetchall()
    by_decision = {str(row["decision_id"]): _row_to_dict(row) for row in rows}

    event_where: list[str] = []
    event_params: list[Any] = []
    if market and market.upper() != "ALL":
        event_where.append("market=?")
        event_params.append(market.upper())
    if runtime_mode:
        event_where.append("runtime_mode=?")
        event_params.append(runtime_mode.lower())
    if start_date:
        event_where.append("session_date>=?")
        event_params.append(start_date)
    if end_date:
        event_where.append("session_date<=?")
        event_params.append(end_date)
    event_sql = """
        SELECT
            grouped.decision_id,
            grouped.market,
            grouped.runtime_mode,
            grouped.session_date,
            grouped.ticker,
            grouped.prompt_version,
            grouped.brain_snapshot_id,
            COALESCE(latest_status.event_type, latest_any.event_type, '') AS status
        FROM (
            SELECT
                decision_id,
                MAX(market) AS market,
                MAX(runtime_mode) AS runtime_mode,
                MIN(session_date) AS session_date,
                MAX(ticker) AS ticker,
                MAX(prompt_version) AS prompt_version,
                MAX(brain_snapshot_id) AS brain_snapshot_id,
                MAX(event_id) AS latest_event_id,
                MAX(
                    CASE
                        WHEN event_type NOT IN ('QUALITY_MARKED', 'EXECUTION_ADVISOR_DECISION')
                        THEN event_id
                    END
                ) AS latest_status_event_id
            FROM lifecycle_events
    """
    if event_where:
        event_sql += " WHERE " + " AND ".join(event_where)
    event_sql += """
            GROUP BY decision_id
        ) grouped
        LEFT JOIN lifecycle_events latest_status
            ON latest_status.event_id = grouped.latest_status_event_id
        LEFT JOIN lifecycle_events latest_any
            ON latest_any.event_id = grouped.latest_event_id
        ORDER BY grouped.session_date, grouped.market, grouped.ticker, grouped.decision_id
    """
    for event_row in conn.execute(event_sql, event_params).fetchall():
        decision_id = str(event_row["decision_id"] or "")
        if decision_id and decision_id not in by_decision:
            by_decision[decision_id] = {
                "decision_id": decision_id,
                "market": str(event_row["market"] or ""),
                "runtime_mode": str(event_row["runtime_mode"] or ""),
                "session_date": str(event_row["session_date"] or ""),
                "ticker": str(event_row["ticker"] or ""),
                "prompt_version": str(event_row["prompt_version"] or ""),
                "brain_snapshot_id": str(event_row["brain_snapshot_id"] or ""),
                "strategy_hint": "",
                "timing_style": "",
                "status": str(event_row["status"] or ""),
                "payload": {},
            }
    return sorted(
        by_decision.values(),
        key=lambda row: (
            str(row.get("session_date") or ""),
            str(row.get("market") or ""),
            str(row.get("ticker") or ""),
            str(row.get("decision_id") or ""),
        ),
    )


def _load_events(conn: sqlite3.Connection, decision_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM lifecycle_events WHERE decision_id=? ORDER BY event_id",
        (decision_id,),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _entry_path_run_id_from_events(events: list[dict[str, Any]]) -> str:
    for event in events:
        event_type = str(event.get("event_type") or "")
        if event_type not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("side") or "").strip().lower() == "sell":
            continue
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            return path_run_id
    for event in reversed(events):
        if str(event.get("event_type") or "") != "CLOSED":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            return path_run_id
    for event in reversed(events):
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        path_run_id = str(payload.get("path_run_id") or payload.get("pathb_path_run_id") or "").strip()
        if path_run_id:
            return path_run_id
    return ""


def _load_path_run(conn: sqlite3.Connection, decision_id: str, path_run_id: str = "") -> dict[str, Any]:
    path_key = str(path_run_id or "").strip()
    if path_key:
        row = conn.execute(
            "SELECT * FROM v2_path_runs WHERE path_run_id=? LIMIT 1",
            (path_key,),
        ).fetchone()
        if row is not None:
            return _row_to_dict(row)
    row = conn.execute(
        """
        SELECT *
        FROM v2_path_runs
        WHERE decision_id=?
        ORDER BY created_at, path_run_id
        LIMIT 1
        """,
        (decision_id,),
    ).fetchone()
    return _row_to_dict(row)


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def _selection_meta_for_decision(decision_payload: dict[str, Any]) -> dict[str, Any]:
    raw = decision_payload.get("selection_meta")
    return dict(raw) if isinstance(raw, dict) else {}


def _prompt_row_for_ticker(selection_meta: dict[str, Any], market: str, ticker: str) -> dict[str, Any]:
    key = _ticker_key(market, ticker)
    for raw in list(selection_meta.get("_final_prompt_pool") or []):
        if not isinstance(raw, dict):
            continue
        row_key = _ticker_key(market, raw.get("ticker"))
        if row_key == key:
            return dict(raw)
    return {}


def _map_value_for_ticker(value: Any, market: str, ticker: str, default: Any = "") -> Any:
    if not isinstance(value, dict):
        return default
    key = _ticker_key(market, ticker)
    for raw_key in (ticker, key, str(ticker or "").upper()):
        if raw_key in value:
            return value.get(raw_key)
    for raw_key, raw_value in value.items():
        if _ticker_key(market, raw_key) == key:
            return raw_value
    return default


def _discovery_performance_context(decision: dict[str, Any], path_run: dict[str, Any]) -> dict[str, Any]:
    market = str(decision.get("market") or path_run.get("market") or "")
    ticker = str(decision.get("ticker") or path_run.get("ticker") or "")
    decision_payload = dict(decision.get("payload") or {})
    selection_meta = _selection_meta_for_decision(decision_payload)
    prompt_row = _prompt_row_for_ticker(selection_meta, market, ticker)
    path_plan = dict(path_run.get("plan") or {})
    ticker_origin = decision_payload.get("ticker_origin")
    if not isinstance(ticker_origin, dict):
        ticker_origin = {}

    role = (
        str(prompt_row.get("candidate_pool_role") or "").strip().upper()
        or str(_map_value_for_ticker(selection_meta.get("_discovery_role_by_ticker"), market, ticker, "") or "").strip().upper()
        or str(ticker_origin.get("candidate_pool_role") or "").strip().upper()
        or str(path_plan.get("_candidate_pool_role") or path_plan.get("candidate_pool_role") or "").strip().upper()
    )
    is_discovery = role == "DISCOVERY"
    ceiling = (
        str(prompt_row.get("discovery_action_ceiling") or "").strip().upper()
        or str(_map_value_for_ticker(selection_meta.get("_discovery_action_ceiling_by_ticker"), market, ticker, "") or "").strip().upper()
        or str(ticker_origin.get("discovery_action_ceiling") or "").strip().upper()
        or str(path_plan.get("_discovery_action_ceiling") or path_plan.get("discovery_action_ceiling") or "").strip().upper()
    )
    signal_family = (
        str(prompt_row.get("discovery_signal_family") or "").strip()
        or str(ticker_origin.get("discovery_signal_family") or "").strip()
        or str(path_plan.get("_discovery_signal_family") or path_plan.get("discovery_signal_family") or "").strip()
    )
    reason = (
        str(prompt_row.get("discovery_reason") or "").strip()
        or str(ticker_origin.get("discovery_reason") or "").strip()
        or str(path_plan.get("_discovery_reason") or path_plan.get("discovery_reason") or "").strip()
    )
    overlay_rank = (
        prompt_row.get("discovery_overlay_rank")
        if prompt_row.get("discovery_overlay_rank") not in (None, "")
        else ticker_origin.get("discovery_overlay_rank")
    )
    if overlay_rank in (None, ""):
        overlay_rank = path_plan.get("_discovery_overlay_rank") or path_plan.get("discovery_overlay_rank")
    try:
        overlay_rank_value = int(float(overlay_rank)) if overlay_rank not in (None, "") else None
    except Exception:
        overlay_rank_value = None

    return {
        "candidate_pool_role": role,
        "experiment_bucket": "discovery_live" if is_discovery else "standard",
        "discovery_live_experiment": 1 if is_discovery else 0,
        "discovery_action_ceiling": ceiling if is_discovery else "",
        "discovery_signal_family": signal_family if is_discovery else "",
        "discovery_reason": reason if is_discovery else "",
        "discovery_overlay_rank": overlay_rank_value if is_discovery else None,
    }


def build_learning_row(decision: dict[str, Any], events: list[dict[str, Any]], path_run: dict[str, Any]) -> dict[str, Any]:
    decision_payload = dict(decision.get("payload") or {})
    path_plan = dict(path_run.get("plan") or {})
    fill = _first_event(events, "FILLED", "PARTIAL_FILLED")
    close = _last_event(events, "CLOSED")
    forward_complete = forward_measurement_complete(events)
    fill_payload = dict(fill.get("payload") or {})
    close_payload = dict(close.get("payload") or {})
    event_path_run_id = _entry_path_run_id_from_events(events)
    # QUALITY_MARKED can be provisional when written at close time. The learning
    # table always recalculates quality from the full current event set.
    quality = evaluate_decision_quality(events)
    latest_event = events[-1] if events else {}
    runtime_mode = str(decision.get("runtime_mode") or "")

    path_type = str(path_run.get("path_type") or close_payload.get("path_type") or fill_payload.get("path_type") or "")
    route = _text(close_payload, "entry_route", "route") or _text(fill_payload, "entry_route", "route")
    if not route and (path_type == "claude_price" or str(path_run.get("path_run_id") or "")):
        route = "path_b"
    strategy = (
        _text(close_payload, "strategy", "source_strategy", "strategy_used")
        or _text(fill_payload, "strategy", "source_strategy", "strategy_used")
        or str(decision.get("strategy_hint") or "")
        or ("claude_price" if path_type == "claude_price" else "")
    )
    close_reason = _text(close_payload, "close_reason", "exit_reason") or str(close.get("reason_code") or "")
    status = "CLOSED" if close else ("FILLED" if fill else str(decision.get("status") or latest_event.get("event_type") or ""))
    experiment_context = _discovery_performance_context(decision, path_run)
    entry_price = _num(fill_payload, *ENTRY_PRICE_KEYS)
    exit_price = _close_price(close_payload)
    qty = _close_qty(close_payload, fill_payload)
    pnl_krw = _num(close_payload, "pnl_krw", "pnl")
    pnl_pct = _num(close_payload, "pnl_pct", "position_pnl_pct")
    sync_degraded_reason = _sync_degraded_reason(close, close_reason, close_payload, exit_price)
    strategy_attribution = _strategy_attribution(close, close_payload)
    quality_reasons = list(quality.reasons)
    if sync_degraded_reason:
        quality_reasons.append(sync_degraded_reason)
    learning_allowed = live_clean_learning_allowed(
        runtime_mode=runtime_mode,
        quality=quality.grade,
        forward_complete=forward_complete,
    )
    learning_allowed = bool(learning_allowed and not sync_degraded_reason and strategy_attribution == "strategy")
    return {
        "v2_decision_id": str(decision.get("decision_id") or ""),
        "market": str(decision.get("market") or ""),
        "runtime_mode": runtime_mode,
        "session_date": str(decision.get("session_date") or ""),
        "ticker": str(decision.get("ticker") or ""),
        "status": status,
        "route": route,
        "path_type": path_type,
        "path_run_id": str(path_run.get("path_run_id") or event_path_run_id or close_payload.get("path_run_id") or fill_payload.get("path_run_id") or ""),
        "strategy": strategy,
        "strategy_attribution": strategy_attribution,
        "origin_action": str(path_plan.get("origin_action") or decision_payload.get("origin_action") or ""),
        **experiment_context,
        "timing_style": str(decision.get("timing_style") or decision_payload.get("timing_style") or ""),
        "filled": 1 if fill else 0,
        "closed": 1 if close else 0,
        "portfolio_realized": _portfolio_realized(close, close_reason, exit_price, pnl_krw, pnl_pct),
        "fill_event_id": fill.get("event_id") if fill else None,
        "close_event_id": close.get("event_id") if close else None,
        "filled_at": fill.get("occurred_at") if fill else None,
        "closed_at": close.get("occurred_at") if close else None,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "qty": qty,
        "pnl_krw": pnl_krw,
        "pnl_pct": pnl_pct,
        "mfe_pct": _num(close_payload, "mfe_pct", "position_mfe_pct"),
        "mae_pct": _num(close_payload, "mae_pct", "position_mae_pct"),
        "close_reason": close_reason,
        "forward_complete": 1 if forward_complete else 0,
        "quality_grade": quality.grade.value,
        "quality_reasons_json": json.dumps(quality_reasons, ensure_ascii=False),
        "learning_allowed": 1 if learning_allowed else 0,
        "source_event_count": len(events),
        "sync_degraded_reason": sync_degraded_reason,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def build_canonical_row(
    decision: dict[str, Any],
    events: list[dict[str, Any]],
    path_run: dict[str, Any],
    learning_row: dict[str, Any],
) -> dict[str, Any]:
    fill_events = _entry_fill_events(events)
    close_events = _close_events(events)
    first_fill = fill_events[0] if fill_events else {}
    first_close = close_events[0] if close_events else {}
    last_close = close_events[-1] if close_events else {}
    first_close_payload = dict(first_close.get("payload") or {})
    last_close_payload = dict(last_close.get("payload") or {})
    canonical_key = "|".join(
        [
            str(learning_row.get("runtime_mode") or ""),
            str(learning_row.get("market") or ""),
            str(learning_row.get("session_date") or ""),
            str(learning_row.get("ticker") or ""),
            str(learning_row.get("v2_decision_id") or ""),
        ]
    )
    return {
        "v2_decision_id": str(learning_row.get("v2_decision_id") or ""),
        "canonical_key": canonical_key,
        "market": str(learning_row.get("market") or ""),
        "runtime_mode": str(learning_row.get("runtime_mode") or ""),
        "session_date": str(learning_row.get("session_date") or ""),
        "ticker": str(learning_row.get("ticker") or ""),
        "status": str(learning_row.get("status") or ""),
        "route": learning_row.get("route"),
        "path_type": learning_row.get("path_type"),
        "path_run_id": learning_row.get("path_run_id"),
        "strategy": learning_row.get("strategy"),
        "strategy_attribution": learning_row.get("strategy_attribution"),
        "origin_action": learning_row.get("origin_action"),
        "candidate_pool_role": learning_row.get("candidate_pool_role"),
        "experiment_bucket": learning_row.get("experiment_bucket"),
        "discovery_live_experiment": learning_row.get("discovery_live_experiment"),
        "discovery_action_ceiling": learning_row.get("discovery_action_ceiling"),
        "discovery_signal_family": learning_row.get("discovery_signal_family"),
        "discovery_reason": learning_row.get("discovery_reason"),
        "discovery_overlay_rank": learning_row.get("discovery_overlay_rank"),
        "filled": 1 if fill_events else 0,
        "closed": 1 if close_events else 0,
        "portfolio_realized": learning_row.get("portfolio_realized"),
        "first_fill_event_id": first_fill.get("event_id") if first_fill else None,
        "first_close_event_id": first_close.get("event_id") if first_close else None,
        "last_close_event_id": last_close.get("event_id") if last_close else None,
        "earliest_fill_at": first_fill.get("occurred_at") if first_fill else None,
        "first_closed_at": first_close.get("occurred_at") if first_close else None,
        "last_closed_at": last_close.get("occurred_at") if last_close else None,
        "entry_price": learning_row.get("entry_price"),
        "first_exit_price": _close_price(first_close_payload),
        "last_exit_price": learning_row.get("exit_price"),
        "qty": learning_row.get("qty"),
        "pnl_krw": learning_row.get("pnl_krw"),
        "pnl_pct": learning_row.get("pnl_pct"),
        "mfe_pct": learning_row.get("mfe_pct"),
        "mae_pct": learning_row.get("mae_pct"),
        "quality_grade": learning_row.get("quality_grade"),
        "learning_allowed": learning_row.get("learning_allowed"),
        "raw_fill_event_count": len(fill_events),
        "raw_close_event_count": len(close_events),
        "source_event_count": len(events),
        "metric_contract_json": _metric_contract_json(),
        "synced_at": learning_row.get("synced_at"),
    }


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _legacy_decision_id_from_events(events: list[dict[str, Any]]) -> int | None:
    for event in events:
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        for key in ("legacy_decision_id", "ml_decision_id", "ml_db_decision_id", "decision_db_id"):
            value = payload.get(key)
            try:
                legacy_id = int(value)
            except Exception:
                continue
            if legacy_id > 0:
                return legacy_id
        value = payload.get("decision_id")
        try:
            legacy_id = int(value)
        except Exception:
            legacy_id = 0
        if legacy_id > 0:
            return legacy_id
    return None


def _legacy_order_status_after(canonical: dict[str, Any]) -> str:
    if int(canonical.get("closed") or 0):
        return "CLOSED"
    if int(canonical.get("filled") or 0):
        return "FILLED"
    return ""


def _legacy_update_values(canonical: dict[str, Any], columns: set[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "filled" in columns:
        values["filled"] = int(canonical.get("filled") or 0)
    status_after = _legacy_order_status_after(canonical)
    if status_after and "order_status" in columns:
        values["order_status"] = status_after if status_after == "FILLED" else "FILLED"
    if canonical.get("entry_price") is not None and "entry_price" in columns:
        values["entry_price"] = canonical.get("entry_price")
    if canonical.get("last_exit_price") is not None and "exit_price" in columns:
        values["exit_price"] = canonical.get("last_exit_price")
    if canonical.get("pnl_pct") is not None and "pnl_pct" in columns:
        values["pnl_pct"] = canonical.get("pnl_pct")
    if canonical.get("pnl_krw") is not None and "pnl_krw" in columns:
        values["pnl_krw"] = canonical.get("pnl_krw")
    return values


def _db_values_equal(current: Any, desired: Any) -> bool:
    if current == desired:
        return True
    if current in (None, "") and desired in (None, ""):
        return True
    try:
        return float(current) == float(desired)
    except Exception:
        return str(current or "") == str(desired or "")


def _changed_legacy_updates(row: sqlite3.Row, updates: dict[str, Any]) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {
        key: value
        for key, value in updates.items()
        if key in row_keys and not _db_values_equal(row[key], value)
    }


def _learning_change_counts(ml_path: Path, rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"insert": 0, "update": 0, "unchanged": 0, "skipped": 0}
    if not rows:
        return counts
    if not ml_path.exists():
        counts["insert"] = len(rows)
        return counts
    try:
        with _connect(ml_path) as conn:
            if not _table_exists(conn, "v2_learning_performance"):
                counts["insert"] = len(rows)
                return counts
            existing_rows = {
                str(row["v2_decision_id"]): row
                for row in conn.execute("SELECT * FROM v2_learning_performance").fetchall()
            }
    except Exception:
        counts["skipped"] = len(rows)
        return counts

    for row in rows:
        decision_id = str(row.get("v2_decision_id") or "")
        existing = existing_rows.get(decision_id)
        if existing is None:
            counts["insert"] += 1
            continue
        existing_keys = set(existing.keys())
        changed = False
        for field in LEARNING_COMPARISON_FIELDS:
            if field not in existing_keys or not _db_values_equal(existing[field], row.get(field)):
                changed = True
                break
        if changed:
            counts["update"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def _row_value_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "").strip()
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def _live_legacy_decision_where(columns: set[str]) -> str:
    clauses: list[str] = []
    if "is_simulated" in columns:
        clauses.append("(is_simulated = 0 OR is_simulated IS NULL)")
    return " AND ".join(clauses)


def _is_truthy_db_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"", "0", "false", "f", "no", "n", "none", "null"}:
        return False
    return True


def _is_simulated_legacy_row(row: sqlite3.Row) -> bool:
    if "is_simulated" not in row.keys():
        return False
    return _is_truthy_db_value(row["is_simulated"])


def _scope_value(field: str, value: Any) -> str:
    text = str(value or "").strip()
    if field in {"market", "ticker"}:
        return text.upper()
    if field == "session_date":
        return text[:10]
    return text


def _legacy_scope_mismatch_reason(row: sqlite3.Row, canonical: dict[str, Any]) -> str:
    row_keys = set(row.keys())
    for field in ("market", "ticker", "session_date"):
        if field not in row_keys:
            continue
        row_value = _scope_value(field, row[field])
        canonical_value = _scope_value(field, canonical.get(field))
        if not row_value or not canonical_value or row_value != canonical_value:
            return f"payload_legacy_{field}_mismatch"
    return ""


def _find_legacy_decision(
    conn: sqlite3.Connection,
    canonical: dict[str, Any],
    events: list[dict[str, Any]],
) -> tuple[sqlite3.Row | None, str, str]:
    if not _table_exists(conn, "decisions"):
        return None, "UNMATCHED_NO_DECISIONS_TABLE", "decisions_table_missing"
    columns = _table_columns(conn, "decisions")
    legacy_id = _legacy_decision_id_from_events(events)
    if legacy_id and "id" in columns:
        row = conn.execute("SELECT * FROM decisions WHERE id=?", (legacy_id,)).fetchone()
        if row is not None:
            if _is_simulated_legacy_row(row):
                return None, "UNMATCHED_NO_LIVE_ROW", "payload_simulated_legacy_row_excluded"
            mismatch_reason = _legacy_scope_mismatch_reason(row, canonical)
            if mismatch_reason:
                return None, "PAYLOAD_LEGACY_MISMATCH", mismatch_reason
            return row, "MATCHED", "payload_legacy_decision_id"
    required = {"market", "ticker", "session_date"}
    if not required.issubset(columns):
        return None, "UNMATCHED_SCHEMA", "decisions_missing_market_ticker_session_date"
    live_clause = _live_legacy_decision_where(columns)
    where_extra = f" AND {live_clause}" if live_clause else ""
    params = (canonical.get("market"), canonical.get("ticker"), canonical.get("session_date"))
    rows = conn.execute(
        f"""
        SELECT *
        FROM decisions
        WHERE market=? AND ticker=? AND session_date=?{where_extra}
        ORDER BY
            CASE WHEN decision='BUY_SIGNAL' THEN 0 ELSE 1 END,
            id
        """,
        params,
    ).fetchall()
    if not rows:
        if live_clause:
            any_row = conn.execute(
                """
                SELECT 1
                FROM decisions
                WHERE market=? AND ticker=? AND session_date=?
                LIMIT 1
                """,
                params,
            ).fetchone()
            if any_row is not None:
                return None, "UNMATCHED_NO_LIVE_ROW", "simulated_legacy_rows_excluded"
        return None, "UNMATCHED_NO_ROW", "no_market_ticker_session_match"
    if len(rows) > 1:
        buy_rows = [row for row in rows if str(row["decision"] if "decision" in row.keys() else "") == "BUY_SIGNAL"]
        if len(buy_rows) == 1:
            return buy_rows[0], "MATCHED", "unique_buy_signal_market_ticker_session"
        return None, "AMBIGUOUS", "multiple_market_ticker_session_matches"
    return rows[0], "MATCHED", "unique_market_ticker_session"


def _apply_decision_repair(
    conn: sqlite3.Connection,
    canonical: dict[str, Any],
    link_row: dict[str, Any],
    *,
    write: bool = True,
) -> dict[str, Any]:
    if link_row.get("link_status") != "MATCHED" or not int(canonical.get("filled") or 0):
        return link_row
    legacy_id = link_row.get("legacy_decision_id")
    if legacy_id is None:
        return link_row
    row = conn.execute("SELECT * FROM decisions WHERE id=?", (legacy_id,)).fetchone()
    if row is None:
        link_row["link_status"] = "UNMATCHED_NO_ROW"
        link_row["matched_by"] = ""
        link_row["unmatched_reason"] = "legacy_row_missing_at_repair"
        return link_row
    columns = _table_columns(conn, "decisions")
    if _is_simulated_legacy_row(row):
        link_row["link_status"] = "REPAIR_SKIPPED_SIMULATED"
        link_row["matched_by"] = ""
        link_row["legacy_filled_after"] = link_row.get("legacy_filled_before")
        link_row["legacy_order_status_after"] = link_row.get("legacy_order_status_before")
        link_row["repaired"] = 0
        link_row["unmatched_reason"] = "simulated_legacy_row_excluded_at_repair"
        return link_row
    mismatch_reason = _legacy_scope_mismatch_reason(row, canonical)
    if mismatch_reason:
        link_row["link_status"] = "REPAIR_SKIPPED_SCOPE_MISMATCH"
        link_row["matched_by"] = ""
        link_row["legacy_filled_after"] = link_row.get("legacy_filled_before")
        link_row["legacy_order_status_after"] = link_row.get("legacy_order_status_before")
        link_row["repaired"] = 0
        link_row["unmatched_reason"] = mismatch_reason
        return link_row
    updates = _legacy_update_values(canonical, columns)
    changed_updates = _changed_legacy_updates(row, updates)
    if changed_updates:
        if write:
            assignments = ", ".join(f"{name}=?" for name in changed_updates)
            conn.execute(
                f"UPDATE decisions SET {assignments} WHERE id=?",
                (*changed_updates.values(), legacy_id),
            )
        link_row["repaired"] = 1
    if "filled" in row.keys():
        filled_after = changed_updates.get("filled", row["filled"])
        link_row["legacy_filled_after"] = None if filled_after is None else int(filled_after)
    link_row["legacy_order_status_after"] = (
        str(changed_updates.get("order_status", row["order_status"] or ""))
        if "order_status" in row.keys()
        else link_row.get("legacy_order_status_after")
    )
    return link_row


def _mark_shared_legacy_ambiguity(link_rows: list[dict[str, Any]]) -> None:
    by_legacy_id: dict[int, list[dict[str, Any]]] = {}
    for row in link_rows:
        if row.get("link_status") != "MATCHED" or row.get("legacy_decision_id") is None:
            continue
        by_legacy_id.setdefault(int(row["legacy_decision_id"]), []).append(row)
    for rows in by_legacy_id.values():
        if len(rows) <= 1:
            continue
        for row in rows:
            row["link_status"] = "AMBIGUOUS_SHARED_LEGACY"
            row["matched_by"] = ""
            row["legacy_filled_after"] = row.get("legacy_filled_before")
            row["legacy_order_status_after"] = row.get("legacy_order_status_before")
            row["repaired"] = 0
            row["unmatched_reason"] = "shared_legacy_decision_id"


def build_decision_fill_link(
    conn: sqlite3.Connection,
    canonical: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    repair_decisions: bool,
) -> dict[str, Any]:
    if not int(canonical.get("filled") or 0):
        return {
            "v2_decision_id": canonical.get("v2_decision_id"),
            "canonical_key": canonical.get("canonical_key"),
            "legacy_decision_id": None,
            "market": canonical.get("market"),
            "runtime_mode": canonical.get("runtime_mode"),
            "session_date": canonical.get("session_date"),
            "ticker": canonical.get("ticker"),
            "link_status": "NO_CANONICAL_FILL",
            "matched_by": "",
            "filled_from_canonical": 0,
            "legacy_filled_before": None,
            "legacy_filled_after": None,
            "legacy_order_status_before": "",
            "legacy_order_status_after": "",
            "repaired": 0,
            "unmatched_reason": "canonical_has_no_fill",
            "metric_contract_json": _metric_contract_json(),
            "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    row, status, reason = _find_legacy_decision(conn, canonical, events)
    legacy_id = int(row["id"]) if row is not None and "id" in row.keys() else None
    before_filled = int(row["filled"]) if row is not None and "filled" in row.keys() and row["filled"] is not None else None
    before_status = str(row["order_status"] or "") if row is not None and "order_status" in row.keys() else ""
    after_filled = before_filled
    after_status = before_status
    link_row = {
        "v2_decision_id": canonical.get("v2_decision_id"),
        "canonical_key": canonical.get("canonical_key"),
        "legacy_decision_id": legacy_id,
        "market": canonical.get("market"),
        "runtime_mode": canonical.get("runtime_mode"),
        "session_date": canonical.get("session_date"),
        "ticker": canonical.get("ticker"),
        "link_status": status,
        "matched_by": reason if status == "MATCHED" else "",
        "filled_from_canonical": int(canonical.get("filled") or 0),
        "legacy_filled_before": before_filled,
        "legacy_filled_after": after_filled,
        "legacy_order_status_before": before_status,
        "legacy_order_status_after": after_status,
        "repaired": 0,
        "unmatched_reason": "" if status == "MATCHED" else reason,
        "metric_contract_json": _metric_contract_json(),
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if repair_decisions:
        return _apply_decision_repair(conn, canonical, link_row)
    return link_row


def _build_decision_fill_links(
    conn: sqlite3.Connection,
    canonical_rows: list[dict[str, Any]],
    event_sets: dict[str, list[dict[str, Any]]],
    *,
    repair_decisions: bool,
    write_repairs: bool,
) -> list[dict[str, Any]]:
    link_rows: list[dict[str, Any]] = []
    canonical_by_decision = {
        str(row.get("v2_decision_id") or ""): row
        for row in canonical_rows
    }
    for canonical in canonical_rows:
        link_rows.append(
            build_decision_fill_link(
                conn,
                canonical,
                event_sets.get(str(canonical.get("v2_decision_id") or ""), []),
                repair_decisions=False,
            )
        )
    _mark_shared_legacy_ambiguity(link_rows)
    if repair_decisions:
        for index, link_row in enumerate(link_rows):
            canonical = canonical_by_decision.get(str(link_row.get("v2_decision_id") or ""))
            if canonical is None:
                continue
            link_rows[index] = _apply_decision_repair(
                conn,
                canonical,
                link_row,
                write=write_repairs,
            )
    return link_rows


def sync_v2_learning_performance(
    *,
    event_db: str | Path = DEFAULT_EVENT_DB,
    ml_db: str | Path = DEFAULT_ML_DB,
    market: str | None = None,
    runtime_mode: str | None = "live",
    start_date: str | None = None,
    end_date: str | None = None,
    dry_run: bool = False,
    repair_decisions: bool = False,
) -> dict[str, Any]:
    event_path = Path(event_db)
    ml_path = Path(ml_db)
    with _connect(event_path) as event_conn:
        decisions = _load_decisions(
            event_conn,
            market=market,
            runtime_mode=runtime_mode,
            start_date=start_date,
            end_date=end_date,
        )
        rows = []
        canonical_rows = []
        event_sets: dict[str, list[dict[str, Any]]] = {}
        for decision in decisions:
            decision_id = str(decision.get("decision_id") or "")
            events = _load_events(event_conn, decision_id)
            path_run = _load_path_run(event_conn, decision_id, _entry_path_run_id_from_events(events))
            learning_row = build_learning_row(decision, events, path_run)
            rows.append(learning_row)
            canonical_rows.append(build_canonical_row(decision, events, path_run, learning_row))
            event_sets[decision_id] = events
    learning_change_counts = _learning_change_counts(ml_path, rows)
    written = 0
    link_rows: list[dict[str, Any]] = []
    if dry_run:
        if ml_path.exists():
            with _connect(ml_path) as ml_conn:
                link_rows = _build_decision_fill_links(
                    ml_conn,
                    canonical_rows,
                    event_sets,
                    repair_decisions=repair_decisions,
                    write_repairs=False,
                )
    else:
        ml_path.parent.mkdir(parents=True, exist_ok=True)
        with _connect(ml_path) as ml_conn:
            ensure_schema(ml_conn)
            ml_conn.executemany(UPSERT_SQL, rows)
            ml_conn.executemany(CANONICAL_UPSERT_SQL, canonical_rows)
            link_rows = _build_decision_fill_links(
                ml_conn,
                canonical_rows,
                event_sets,
                repair_decisions=repair_decisions,
                write_repairs=True,
            )
            ml_conn.executemany(LINK_UPSERT_SQL, link_rows)
            ml_conn.commit()
            written = len(rows)
    filled = sum(1 for row in rows if row["filled"])
    closed = sum(1 for row in rows if row["closed"])
    learning_allowed = sum(1 for row in rows if row["learning_allowed"])
    degraded_reason_counts = _row_value_counts(rows, "sync_degraded_reason")
    strategy_attribution_counts = _row_value_counts(rows, "strategy_attribution")
    experiment_buckets = sorted({str(row.get("experiment_bucket") or "standard") for row in rows})
    return {
        "event_db": str(event_path),
        "ml_db": str(ml_path),
        "dry_run": bool(dry_run),
        "repair_decisions": bool(repair_decisions),
        "selected": len(rows),
        "insert": learning_change_counts["insert"],
        "update": learning_change_counts["update"],
        "unchanged": learning_change_counts["unchanged"],
        "skipped": learning_change_counts["skipped"],
        "degraded": sum(degraded_reason_counts.values()),
        "degraded_reason_counts": degraded_reason_counts,
        "written": written,
        "canonical_written": 0 if dry_run else len(canonical_rows),
        "decision_links_written": 0 if dry_run else len(link_rows),
        "decision_links_matched": sum(1 for row in link_rows if row.get("link_status") == "MATCHED"),
        "decision_links_repaired": sum(1 for row in link_rows if row.get("repaired")),
        "decision_links_unmatched": sum(1 for row in link_rows if row.get("link_status") != "MATCHED"),
        "filled": filled,
        "closed": closed,
        "forward_complete": sum(1 for row in rows if row["forward_complete"]),
        "learning_allowed": learning_allowed,
        "strategy_attribution_counts": strategy_attribution_counts,
        "experiment_bucket_counts": {
            bucket: sum(1 for row in rows if str(row.get("experiment_bucket") or "standard") == bucket)
            for bucket in experiment_buckets
        },
        "discovery_live_experiment": sum(1 for row in rows if int(row.get("discovery_live_experiment") or 0)),
        "quality_grade_counts": {
            grade: sum(1 for row in rows if row["quality_grade"] == grade)
            for grade in sorted({row["quality_grade"] for row in rows})
        },
        "metric_contract": dict(METRIC_CONTRACT),
        "sample": rows[:5],
        "canonical_sample": canonical_rows[:5],
        "decision_link_sample": link_rows[:5],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync V2 lifecycle truth into ML v2_learning_performance.")
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--market", default="ALL", choices=["ALL", "KR", "US"])
    parser.add_argument("--runtime-mode", default="live")
    parser.add_argument("--start-date", default="")
    parser.add_argument("--end-date", default="")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--repair-decisions", action="store_true", help="update matched legacy decisions rows from canonical fill truth")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    summary = sync_v2_learning_performance(
        event_db=args.event_db,
        ml_db=args.ml_db,
        market=args.market,
        runtime_mode=args.runtime_mode or None,
        start_date=args.start_date or None,
        end_date=args.end_date or None,
        dry_run=bool(args.dry_run),
        repair_decisions=bool(args.repair_decisions),
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
