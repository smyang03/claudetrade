from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from runtime.rehearsal.context import RehearsalGuardError
from tools import ops_us_high_price_simulation


def _init_event_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE lifecycle_events (
                event_id INTEGER PRIMARY KEY,
                event_uuid TEXT,
                event_type TEXT,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                decision_id TEXT,
                execution_id TEXT,
                position_id TEXT,
                prompt_version TEXT,
                brain_snapshot_id TEXT,
                occurred_at TEXT,
                reason_code TEXT,
                data_quality TEXT,
                payload_json TEXT,
                created_at TEXT
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
                timing_style TEXT,
                filled INTEGER,
                closed INTEGER,
                fill_event_id INTEGER,
                close_event_id INTEGER,
                filled_at TEXT,
                closed_at TEXT,
                entry_price REAL,
                exit_price REAL,
                qty INTEGER,
                pnl_krw REAL,
                pnl_pct REAL,
                mfe_pct REAL,
                mae_pct REAL,
                close_reason TEXT,
                forward_complete INTEGER,
                quality_grade TEXT,
                quality_reasons_json TEXT,
                learning_allowed INTEGER,
                source_event_count INTEGER,
                synced_at TEXT,
                candidate_pool_role TEXT,
                experiment_bucket TEXT,
                discovery_live_experiment TEXT
            )
            """
        )


def _write_price_file(root: Path) -> None:
    price_dir = root / "minute" / "us"
    price_dir.mkdir(parents=True)
    (price_dir / "us_CLS.csv").write_text(
        "\n".join(
            [
                "ts,open,high,low,close,volume",
                "2026-06-02T23:30:00+09:00,100,100,99,100,10",
                "2026-06-03T00:00:00+09:00,100,102,100,102,10",
                "2026-06-03T00:30:00+09:00,102,103,101,103,10",
                "2026-06-03T05:00:00+09:00,103,104,102,104,10",
            ]
        ),
        encoding="utf-8",
    )


def test_build_us_high_price_simulation_is_read_only_and_reports_candidate(tmp_path: Path) -> None:
    event_db = tmp_path / "event.db"
    perf_db = tmp_path / "perf.db"
    price_root = tmp_path / "price"
    _init_event_db(event_db)
    _init_perf_db(perf_db)
    _write_price_file(price_root)

    payload = {
        "path_run_id": "path_1",
        "price_krw": 650000,
        "cash_krw": 1_000_000,
        "original_budget_krw": 450000,
        "effective_budget_krw": 225000,
        "early_gate_applied": True,
        "pathb_sizing": {"blocker": "high_price_one_share_blocked"},
    }
    plan = {
        "confidence": 0.62,
        "buy_zone_low": 99,
        "buy_zone_high": 101,
        "sell_target": 104,
        "stop_loss": 97,
    }
    with sqlite3.connect(event_db) as conn:
        conn.execute(
            """
            INSERT INTO lifecycle_events (
                event_id, event_type, market, runtime_mode, session_date, ticker,
                occurred_at, reason_code, payload_json
            )
            VALUES (1, 'SAFETY_BLOCKED', 'US', 'live', '2026-06-02', 'CLS',
                    '2026-06-02T22:50:00+09:00', 'HIGH_PRICE_BUDGET_BLOCK', ?)
            """,
            (json.dumps(payload),),
        )
        conn.execute(
            """
            INSERT INTO v2_path_runs (
                path_run_id, path_type, market, runtime_mode, session_date, ticker,
                status, plan_json, created_at, updated_at
            )
            VALUES ('path_1', 'claude_price', 'US', 'live', '2026-06-02', 'CLS',
                    'CANCELLED', ?, '2026-06-02T22:30:00+09:00', '2026-06-03T05:00:00+09:00')
            """,
            (json.dumps(plan),),
        )

    result = ops_us_high_price_simulation.build_us_high_price_simulation(
        event_db=event_db,
        perf_db=perf_db,
        price_root=price_root,
        output_root=tmp_path / "analysis",
        output_dir="out",
    )

    assert result["live_writes_performed"] is False
    assert result["summary"]["candidate_count"] == 1
    assert result["summary"]["displacement"]["addable_cancelled_count"] == 1
    assert result["summary"]["price_replay_eod"]["avg"] == 4.0
    for output_path in result["output_paths"].values():
        assert Path(output_path).exists()


def test_output_dir_must_stay_under_output_root(tmp_path: Path) -> None:
    with pytest.raises(RehearsalGuardError):
        ops_us_high_price_simulation._output_dir(tmp_path / "analysis", "../outside")
