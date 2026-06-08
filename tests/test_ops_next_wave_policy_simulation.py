from __future__ import annotations

import sqlite3
from pathlib import Path

from tools import ops_next_wave_policy_simulation


def _init_perf_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE v2_learning_performance (
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                path_type TEXT,
                strategy TEXT,
                filled INTEGER,
                closed INTEGER,
                filled_at TEXT,
                closed_at TEXT,
                pnl_pct REAL,
                close_reason TEXT,
                quality_grade TEXT,
                candidate_pool_role TEXT
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
                ticker TEXT,
                session_date TEXT,
                market TEXT,
                runtime_mode TEXT,
                path_type TEXT,
                status TEXT,
                plan_json TEXT,
                updated_at TEXT
            )
            """
        )


def test_kr_policy_simulation_reports_loss_avoidance(tmp_path: Path) -> None:
    perf_db = tmp_path / "perf.db"
    event_db = tmp_path / "event.db"
    _init_perf_db(perf_db)
    _init_event_db(event_db)
    with sqlite3.connect(perf_db) as conn:
        conn.executemany(
            """
            INSERT INTO v2_learning_performance (
                market, runtime_mode, session_date, ticker, path_type, strategy,
                filled, closed, filled_at, closed_at, pnl_pct, close_reason
            )
            VALUES ('KR', 'live', ?, ?, 'claude_price', ?, 1, 1, ?, ?, ?, ?)
            """,
            [
                ("2026-06-01", "111111", "momentum", "2026-06-01T00:05:00+00:00", "2026-06-01T01:00:00+00:00", -5.0, "CLOSED_LOSS_CAP"),
                ("2026-06-01", "222222", "gap_pullback", "2026-06-01T03:00:00+00:00", "2026-06-01T04:00:00+00:00", 2.0, "CLOSED_TARGET"),
                ("2026-06-01", "333333", "momentum", "2026-06-01T05:40:00+00:00", "2026-06-01T06:00:00+00:00", -3.0, "CLOSED_USER_MANUAL"),
            ],
        )

    payload = ops_next_wave_policy_simulation.build_next_wave_policy_simulation(
        event_db=event_db,
        perf_db=perf_db,
        price_root=tmp_path / "price",
        output_root=tmp_path / "analysis",
        output_dir="out",
    )

    assert payload["live_writes_performed"] is False
    assert payload["summary"]["kr_policy"]["baseline"]["count"] == 3
    by_policy = {row["policy"]: row for row in payload["summary"]["kr_policy"]["policies"]}
    assert by_policy["exclude_open_0_30_and_late"]["kept"]["avg"] == 2.0
    assert by_policy["exclude_open_0_30_and_late"]["loss_avoided_proxy"] == 8.0
    for output_path in payload["output_paths"].values():
        assert Path(output_path).exists()


def test_us_unfilled_audit_separates_closed_without_fill_event(tmp_path: Path) -> None:
    perf_db = tmp_path / "perf.db"
    event_db = tmp_path / "event.db"
    _init_perf_db(perf_db)
    _init_event_db(event_db)
    with sqlite3.connect(event_db) as conn:
        conn.execute(
            """
            INSERT INTO v2_path_runs (
                path_run_id, decision_id, ticker, session_date, market, runtime_mode,
                path_type, status, plan_json, updated_at
            )
            VALUES (?, ?, 'SMCI', '2026-05-29', 'US', 'live', 'claude_price', 'CLOSED', '{}', '2026-05-29T15:16:24+00:00')
            """,
            ("path_closed_missing_fill", "dec_smci"),
        )
        conn.executemany(
            """
            INSERT INTO lifecycle_events (
                event_id, event_type, market, runtime_mode, session_date, ticker,
                decision_id, occurred_at, reason_code, payload_json
            )
            VALUES (?, ?, 'US', 'live', '2026-05-29', 'SMCI', 'dec_smci', ?, ?, ?)
            """,
            [
                (
                    1,
                    "ORDER_SENT",
                    "2026-05-29T13:35:20+00:00",
                    "",
                    '{"path_run_id":"path_closed_missing_fill","side":"buy","price":45.71,"qty":3}',
                ),
                (
                    2,
                    "CLOSED",
                    "2026-05-29T15:16:24+00:00",
                    "CLOSED_CLAUDE_PRICE_TARGET",
                    '{"path_run_id":"path_closed_missing_fill","close_reason":"CLOSED_CLAUDE_PRICE_TARGET","price":47.24}',
                ),
            ],
        )

    payload = ops_next_wave_policy_simulation.build_next_wave_policy_simulation(
        event_db=event_db,
        perf_db=perf_db,
        price_root=tmp_path / "price",
        output_root=tmp_path / "analysis",
        output_dir="out",
    )

    unfilled = payload["summary"]["us_unfilled"]
    assert unfilled["audit_row_count"] == 1
    assert unfilled["unfilled_order_count"] == 0
    assert unfilled["classification_counts"] == {"closed_without_fill_event": 1}
