from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.pathb_invalid_price_miss_report import analyze_pathb_invalid_price_miss


def _create_event_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE pathb_miss_quality (
            id INTEGER PRIMARY KEY,
            path_run_id TEXT,
            decision_id TEXT,
            market TEXT,
            runtime_mode TEXT,
            session_date TEXT,
            ticker TEXT,
            cancelled_at TEXT,
            cancel_reason TEXT,
            current_at_plan REAL,
            open_price REAL,
            buy_zone_low REAL,
            buy_zone_high REAL,
            cancel_if_open_above REAL,
            cancel_trigger_price REAL,
            reference_price REAL,
            baseline_price REAL,
            baseline_source TEXT,
            market_close_at TEXT,
            followup_due_at TEXT,
            followup_filled_at TEXT,
            followup_status TEXT,
            zone_reentered_after_cancel INTEGER,
            mfe_30m_pct REAL,
            mae_30m_pct REAL,
            observed_price_30m REAL,
            quote_sample_count INTEGER,
            payload_json TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE v2_path_runs (
            path_run_id TEXT PRIMARY KEY,
            status TEXT,
            path_type TEXT,
            ticker TEXT,
            session_date TEXT,
            updated_at TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO pathb_miss_quality VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                1,
                "path_1",
                "decision_1",
                "US",
                "live",
                "2026-06-01",
                "QCOM",
                "2026-06-01T14:00:00+00:00",
                "INVALID_PRICE",
                None,
                None,
                230.0,
                240.0,
                None,
                None,
                235.0,
                235.0,
                "reference_price",
                None,
                None,
                "2026-06-01T14:20:00+00:00",
                "filled",
                1,
                1.5,
                -0.2,
                238.0,
                8,
                '{"sample_source":"post_open_history","path_status":"CANCELLED"}',
                "2026-06-01T14:00:00+00:00",
                "2026-06-01T14:30:00+00:00",
            ),
            (
                2,
                "path_2",
                "decision_2",
                "US",
                "live",
                "2026-06-02",
                "MDB",
                "2026-06-02T14:00:00+00:00",
                "INVALID_PRICE",
                0.0,
                None,
                300.0,
                320.0,
                None,
                None,
                310.0,
                310.0,
                "reference_price",
                None,
                None,
                None,
                "pending",
                0,
                0.5,
                -0.1,
                None,
                0,
                '{"sample_source":"none","path_status":"CANCELLED"}',
                "2026-06-02T14:00:00+00:00",
                "2026-06-02T14:30:00+00:00",
            ),
            (
                3,
                "path_3",
                "decision_3",
                "US",
                "live",
                "2026-06-02",
                "NVDA",
                "2026-06-02T14:00:00+00:00",
                "EXPIRED",
                100.0,
                None,
                95.0,
                105.0,
                None,
                None,
                100.0,
                100.0,
                "reference_price",
                None,
                None,
                None,
                "pending",
                1,
                2.0,
                -0.3,
                None,
                5,
                '{"sample_source":"post_open_history","path_status":"CANCELLED"}',
                "2026-06-02T14:00:00+00:00",
                "2026-06-02T14:30:00+00:00",
            ),
        ],
    )
    conn.executemany(
        "INSERT INTO v2_path_runs VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("path_1", "CANCELLED", "claude_price", "QCOM", "2026-06-01", "2026-06-01T14:30:00+00:00"),
            ("path_2", "CANCELLED", "claude_price", "MDB", "2026-06-02", "2026-06-02T14:30:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()


def test_pathb_invalid_price_report_reproduces_core_metrics(tmp_path: Path) -> None:
    event_db = tmp_path / "v2_event_store.db"
    _create_event_db(event_db)

    report = analyze_pathb_invalid_price_miss(
        event_db=event_db,
        date_from="2026-06-01",
        date_to="2026-06-02",
        windows=("recent", "full_available"),
    )

    recent = report["windows"]["recent"]
    assert report["read_only"] is True
    assert report["source"]["optional_path_run_join"] is True
    assert recent["n"] == 2
    assert recent["zone_reentered"] == 1
    assert recent["zone_reentered_rate"] == 50.0
    assert recent["avg_mfe_30m_pct"] == 1.0
    assert recent["avg_mae_30m_pct"] == -0.15
    assert recent["by_price_diagnostic_bucket"] == {
        "current_missing_but_followup_quotes_available": 1,
        "current_price_non_positive": 1,
    }
    assert recent["by_conversion_bucket"] == {"native_usd_scale_plausible": 2}
    assert recent["by_order_timing_bucket"] == {
        "followup_filled_within_30m": 1,
        "followup_pending": 1,
    }
    assert recent["by_path_run_status"] == {"CANCELLED": 2}


def test_pathb_invalid_price_report_returns_empty_for_no_data_date_range(tmp_path: Path) -> None:
    event_db = tmp_path / "v2_event_store.db"
    _create_event_db(event_db)

    report = analyze_pathb_invalid_price_miss(
        event_db=event_db,
        date_from="2026-07-01",
        date_to="2026-07-02",
        windows=("recent",),
    )

    recent = report["windows"]["recent"]
    assert report["source"]["table_available"] is True
    assert recent["n"] == 0
    assert recent["zone_reentered"] == 0
    assert recent["zone_reentered_rate"] == 0.0
    assert recent["avg_mfe_30m_pct"] is None
    assert recent["avg_mae_30m_pct"] is None
    assert recent["rows"] == []


def test_pathb_invalid_price_report_handles_missing_required_table(tmp_path: Path) -> None:
    event_db = tmp_path / "v2_event_store.db"
    sqlite3.connect(event_db).close()

    report = analyze_pathb_invalid_price_miss(
        event_db=event_db,
        date_from="2026-06-01",
        date_to="2026-06-02",
        windows=("recent",),
    )

    recent = report["windows"]["recent"]
    assert report["source"]["table_available"] is False
    assert report["source"]["optional_path_run_join"] is False
    assert report["source"]["optional_lifecycle_join"] is False
    assert recent["n"] == 0
    assert recent["zone_reentered"] == 0
    assert recent["rows"] == []
