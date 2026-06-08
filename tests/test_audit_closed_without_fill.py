from __future__ import annotations

import sqlite3
from pathlib import Path

from tools import audit_closed_without_fill


def _init_perf_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE v2_learning_performance (
                v2_decision_id TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                status TEXT,
                route TEXT,
                path_type TEXT,
                path_run_id TEXT,
                strategy TEXT,
                origin_action TEXT,
                filled INTEGER,
                closed INTEGER,
                fill_event_id INTEGER,
                close_event_id INTEGER,
                filled_at TEXT,
                closed_at TEXT,
                entry_price REAL,
                exit_price REAL,
                qty REAL,
                pnl_krw REAL,
                pnl_pct REAL,
                mfe_pct REAL,
                mae_pct REAL,
                close_reason TEXT,
                quality_grade TEXT,
                quality_reasons_json TEXT,
                learning_allowed INTEGER
            )
            """
        )


def _init_event_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE lifecycle_events (
                event_id INTEGER PRIMARY KEY,
                event_type TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                decision_id TEXT,
                execution_id TEXT,
                occurred_at TEXT,
                reason_code TEXT,
                payload_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE v2_path_runs (
                path_run_id TEXT PRIMARY KEY,
                decision_id TEXT,
                path_type TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                status TEXT,
                plan_json TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )


def test_closed_without_fill_audit_reports_repairable_distortion(tmp_path: Path) -> None:
    perf_db = tmp_path / "perf.db"
    event_db = tmp_path / "event.db"
    _init_perf_db(perf_db)
    _init_event_db(event_db)
    with sqlite3.connect(perf_db) as conn:
        conn.executemany(
            """
            INSERT INTO v2_learning_performance (
                v2_decision_id, market, runtime_mode, session_date, ticker,
                status, route, path_type, path_run_id, strategy, filled, closed,
                pnl_pct, close_reason, quality_grade, quality_reasons_json, learning_allowed
            )
            VALUES (?, 'US', 'live', ?, ?, 'CLOSED', 'path_b', 'claude_price', ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            [
                ("dec_filled", "2026-06-01", "AAPL", "path_filled", "claude_price", 1, 2.0, "CLOSED_TARGET", "OK", "[]", 1),
                (
                    "dec_missing",
                    "2026-06-02",
                    "MSFT",
                    "path_missing",
                    "claude_price",
                    0,
                    -1.0,
                    "CLOSED_HARD_STOP",
                    "DIRTY",
                    '["CLOSED_WITHOUT_FILL"]',
                    0,
                ),
                (
                    "dec_mismatch",
                    "2026-06-03",
                    "SMCI",
                    "path_later_cancelled",
                    "gap_pullback",
                    0,
                    3.0,
                    "CLOSED_TARGET",
                    "DIRTY",
                    '["CLOSED_WITHOUT_FILL"]',
                    0,
                ),
            ],
        )
    with sqlite3.connect(event_db) as conn:
        conn.executemany(
            """
            INSERT INTO v2_path_runs (
                path_run_id, decision_id, path_type, market, runtime_mode, session_date,
                ticker, status, plan_json, created_at, updated_at
            )
            VALUES (?, ?, 'claude_price', 'US', 'live', ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "path_missing",
                    "dec_missing",
                    "2026-06-02",
                    "MSFT",
                    "CLOSED",
                    '{"actual_entry_price":100.0,"filled_qty":1,"entry_order_price":100.1,"entry_qty":1}',
                    "2026-06-02T13:30:00+00:00",
                    "2026-06-02T14:00:00+00:00",
                ),
                (
                    "path_real",
                    "dec_mismatch",
                    "2026-06-03",
                    "SMCI",
                    "CLOSED",
                    '{"actual_entry_price":50.0,"filled_qty":2,"entry_order_price":50.1,"entry_qty":2}',
                    "2026-06-03T13:30:00+00:00",
                    "2026-06-03T14:00:00+00:00",
                ),
                (
                    "path_later_cancelled",
                    "dec_mismatch",
                    "2026-06-03",
                    "SMCI",
                    "CANCELLED",
                    "{}",
                    "2026-06-03T15:00:00+00:00",
                    "2026-06-03T15:01:00+00:00",
                ),
            ],
        )
        conn.executemany(
            """
            INSERT INTO lifecycle_events (
                event_id, event_type, market, runtime_mode, session_date, ticker,
                decision_id, execution_id, occurred_at, reason_code, payload_json
            )
            VALUES (?, ?, 'US', 'live', ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (1, "ORDER_SENT", "2026-06-02", "MSFT", "dec_missing", "buy1", "2026-06-02T13:31:00+00:00", "", '{"path_run_id":"path_missing","side":"buy","price":100.1,"qty":1}'),
                (2, "CLOSED", "2026-06-02", "MSFT", "dec_missing", "sell1", "2026-06-02T14:00:00+00:00", "CLOSED_HARD_STOP", '{"path_run_id":"path_missing","price":99.0,"pnl_pct":-1.0}'),
                (3, "ORDER_SENT", "2026-06-03", "SMCI", "dec_mismatch", "buy2", "2026-06-03T13:31:00+00:00", "", '{"path_run_id":"path_real","side":"buy","price":50.1,"qty":2}'),
                (4, "CLOSED", "2026-06-03", "SMCI", "dec_mismatch", "sell2", "2026-06-03T14:00:00+00:00", "CLOSED_TARGET", '{"path_run_id":"path_real","price":51.5,"pnl_pct":3.0}'),
            ],
        )

    payload = audit_closed_without_fill.build_closed_without_fill_audit(
        event_db=event_db,
        perf_db=perf_db,
        output_root=tmp_path / "analysis",
        output_dir="out",
    )

    assert payload["live_writes_performed"] is False
    assert payload["summary"]["case_count"] == 2
    assert payload["summary"]["classification_counts"] == {
        "linked_closed_without_fill_event": 1,
        "path_run_attribution_mismatch": 1,
    }
    assert payload["summary"]["repair_confidence_counts"] == {"high": 2}
    distortion = payload["summary"]["performance_distortion"]["US:claude_price"]
    assert distortion["current_filled_closed"]["avg"] == 2.0
    assert distortion["adjusted_high_confidence_only"]["count"] == 3
    assert distortion["adjusted_high_confidence_only"]["avg"] == 1.3333
    for output_path in payload["output_paths"].values():
        assert Path(output_path).exists()
