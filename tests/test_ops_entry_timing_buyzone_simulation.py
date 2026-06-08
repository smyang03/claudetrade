from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools import ops_entry_timing_buyzone_simulation


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


def _init_candidate_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE candidate_counterfactual_paths (
                runtime_mode TEXT,
                session_date TEXT,
                market TEXT,
                ticker TEXT,
                candidate_key TEXT,
                call_id TEXT,
                signal_time TEXT,
                known_at TEXT,
                trade_ready_action TEXT,
                actual_path TEXT,
                path_name TEXT,
                trigger_time TEXT,
                trigger_price REAL,
                trigger_reason TEXT,
                entry_price REAL,
                entry_delay_min REAL,
                outcome_30m_pct REAL,
                outcome_60m_pct REAL,
                outcome_close_pct REAL,
                max_runup_60m_pct REAL,
                max_drawdown_60m_pct REAL,
                status TEXT,
                metadata_json TEXT,
                metadata_quality TEXT,
                label_source TEXT
            )
            """
        )


def _write_price_file(root: Path) -> None:
    price_dir = root / "minute" / "us"
    price_dir.mkdir(parents=True)
    (price_dir / "us_TEST.csv").write_text(
        "\n".join(
            [
                "ts,open,high,low,close,volume",
                "2026-06-01T23:00:00+09:00,107,108,106,107,10",
                "2026-06-01T23:05:00+09:00,106,107,105,106,10",
                "2026-06-01T23:10:00+09:00,105,106,104,105,10",
                "2026-06-01T23:30:00+09:00,104,105,103,104,10",
            ]
        ),
        encoding="utf-8",
    )


def test_entry_timing_buyzone_simulation_reports_delay_and_zone_policy(tmp_path: Path) -> None:
    event_db = tmp_path / "event.db"
    candidate_db = tmp_path / "candidate.db"
    price_root = tmp_path / "price"
    _init_event_db(event_db)
    _init_candidate_db(candidate_db)
    _write_price_file(price_root)

    plan = {
        "buy_zone_low": 100,
        "buy_zone_high": 110,
        "confidence": 0.55,
        "origin_action": "PULLBACK_WAIT",
        "actual_entry_price": 108,
        "actual_exit_price": 112,
        "pnl_pct": 3.7037037037,
    }
    with sqlite3.connect(event_db) as conn:
        conn.execute(
            """
            INSERT INTO v2_path_runs (
                path_run_id, decision_id, path_type, market, runtime_mode, session_date,
                ticker, status, plan_json, created_at, updated_at
            )
            VALUES ('path_test', 'dec_test', 'claude_price', 'US', 'live', '2026-06-01',
                    'TEST', 'CLOSED', ?, '2026-06-01T14:00:00+00:00', '2026-06-01T15:00:00+00:00')
            """,
            (json.dumps(plan),),
        )
        conn.executemany(
            """
            INSERT INTO lifecycle_events (
                event_id, event_type, market, runtime_mode, session_date, ticker,
                decision_id, occurred_at, reason_code, payload_json
            )
            VALUES (?, ?, 'US', 'live', '2026-06-01', 'TEST', 'dec_test', ?, ?, ?)
            """,
            [
                (
                    1,
                    "CLAUDE_PRICE_HIT",
                    "2026-06-01T14:00:00+00:00",
                    "",
                    '{"path_run_id":"path_test","price":107}',
                ),
                (
                    2,
                    "ORDER_SENT",
                    "2026-06-01T14:00:01+00:00",
                    "",
                    '{"path_run_id":"path_test","side":"buy","price":108}',
                ),
                (
                    3,
                    "FILLED",
                    "2026-06-01T14:00:02+00:00",
                    "",
                    '{"path_run_id":"path_test","side":"buy","price":108}',
                ),
                (
                    4,
                    "CLOSED",
                    "2026-06-01T15:00:00+00:00",
                    "CLOSED_TARGET",
                    '{"path_run_id":"path_test","price":112,"pnl_pct":3.7037037037}',
                ),
            ],
        )

    with sqlite3.connect(candidate_db) as conn:
        values = [
            ("immediate", -2.0),
            ("wait_30m", 1.0),
        ]
        conn.executemany(
            """
            INSERT INTO candidate_counterfactual_paths (
                runtime_mode, session_date, market, ticker, candidate_key, call_id,
                signal_time, trade_ready_action, path_name, outcome_close_pct, status
            )
            VALUES ('live', '2026-06-01', 'KR', '000001', 'key1', 'call1',
                    '2026-06-01T09:00:00+09:00', 'WATCH', ?, ?, 'CLOSE_OUTCOME_FILLED')
            """,
            values,
        )

    payload = ops_entry_timing_buyzone_simulation.build_entry_timing_buyzone_simulation(
        event_db=event_db,
        candidate_db=candidate_db,
        price_root=price_root,
        output_root=tmp_path / "analysis",
        output_dir="out",
    )

    assert payload["live_writes_performed"] is False
    assert payload["summary"]["closed_trade_count"] == 1
    delay_10 = payload["summary"]["delay_replay"]["by_market_delay"]["US"]["10"]
    assert delay_10["delta"]["avg"] > 2.0
    lower_half = {
        row["policy"]: row for row in payload["summary"]["zone_policy"]["US"]["policies"]
    }["lower_half_only"]
    assert lower_half["kept"]["count"] == 0
    best_wait = payload["summary"]["counterfactual_wait"]["best_by_market_action"]["KR"]["WATCH"]
    assert best_wait["path_name"] == "wait_30m"
    assert best_wait["delta"]["avg"] == 3.0
    early = payload["summary"]["early_zone_entry"]["by_market"]["US"]
    assert early["delta"]["avg"] > 0
    assert early["entry_lag_min"]["median"] > 0
    for output_path in payload["output_paths"].values():
        assert Path(output_path).exists()
