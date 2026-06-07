from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.kr_nosignal_orp_report import analyze_kr_nosignal_orp


def _create_selection_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE ticker_selection_log (
            date TEXT,
            market TEXT,
            ticker TEXT,
            source_type TEXT,
            selected_at TEXT,
            signal_at TEXT,
            trade_ready INTEGER,
            signal_fired INTEGER,
            traded INTEGER,
            strategy_name TEXT,
            recommended_strategy TEXT,
            blocked_reason TEXT,
            selected_reason_tag TEXT,
            execution_reason TEXT,
            bot_mode TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO ticker_selection_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-06-10",
                "KR",
                "005930",
                "rescreen",
                "2026-06-10T10:05:00",
                None,
                1,
                0,
                0,
                None,
                "opening_range_pullback",
                None,
                None,
                None,
                "live",
            ),
            (
                "2026-06-10",
                "KR",
                "000660",
                "rescreen",
                "2026-06-10T09:35:00",
                None,
                1,
                0,
                0,
                None,
                "gap_pullback",
                None,
                None,
                None,
                "live",
            ),
            (
                "2026-06-10",
                "KR",
                "035420",
                "rescreen",
                "2026-06-10T09:40:00",
                "2026-06-10T09:42:00",
                1,
                1,
                0,
                None,
                "momentum",
                None,
                None,
                None,
                "live",
            ),
            (
                "2026-06-10",
                "KR",
                "068270",
                "watch",
                "2026-06-10T09:50:00",
                None,
                0,
                0,
                0,
                None,
                "mean_reversion",
                None,
                None,
                None,
                "live",
            ),
        ],
    )
    conn.commit()
    conn.close()


def _create_intraday_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE intraday_strategy_log (
            ts TEXT,
            session_date TEXT,
            market TEXT,
            ticker TEXT,
            strategy_name TEXT,
            stage TEXT,
            or_formed INTEGER,
            entry_window_elapsed_min REAL,
            signal_fired INTEGER,
            traded INTEGER,
            blocked_reason TEXT,
            note TEXT,
            bot_mode TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO intraday_strategy_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "2026-06-10T09:08:00",
                "2026-06-10",
                "KR",
                "005930",
                "opening_range_pullback",
                "probe",
                0,
                0,
                0,
                0,
                "orp_forming",
                None,
                "live",
            ),
            (
                "2026-06-10T10:04:00",
                "2026-06-10",
                "KR",
                "005930",
                "opening_range_pullback",
                "probe",
                1,
                54.0,
                0,
                0,
                "orp_range_too_high",
                None,
                "live",
            ),
            (
                "2026-06-10T10:20:00",
                "2026-06-10",
                "KR",
                "005930",
                "opening_range_pullback",
                "probe",
                1,
                70.0,
                0,
                0,
                "orp_entry_window_expired",
                None,
                "live",
            ),
        ],
    )
    conn.commit()
    conn.close()


def test_kr_nosignal_orp_report_classifies_trade_ready_no_signal(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    intraday_db = tmp_path / "intraday_strategy_log.db"
    _create_selection_db(selection_db)
    _create_intraday_db(intraday_db)

    report = analyze_kr_nosignal_orp(
        selection_db=selection_db,
        intraday_db=intraday_db,
        date_from="2026-06-10",
        date_to="2026-06-10",
        windows=("primary", "full_available"),
    )

    primary = report["windows"]["primary"]
    assert report["read_only"] is True
    assert primary["total_selection_rows"] == 4
    assert primary["trade_ready"] == 3
    assert primary["signal_fired"] == 1
    assert primary["traded"] == 0
    assert primary["no_signal"] == 2
    assert primary["by_strategy"] == {
        "gap_pullback": 1,
        "momentum": 1,
        "opening_range_pullback": 1,
    }
    assert primary["by_orp_block_reason"] == {"orp_range_too_high": 1}
    assert primary["by_window_phase"] == {"near_expiry": 1, "not_applicable": 1}

    orp_row = next(row for row in primary["rows"] if row["strategy"] == "opening_range_pullback")
    assert orp_row["ticker"] == "005930"
    assert orp_row["entry_window_elapsed_min"] == 54.0
    assert orp_row["nosignal_bucket"] == "strategy_signal:orp:orp_range_too_high"
    assert orp_row["evidence_bucket"] == "intraday_orp_evidence_present"
    assert orp_row["risk_order_bucket"] == "not_observed"


def test_kr_nosignal_orp_report_returns_empty_for_no_data_date_range(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    intraday_db = tmp_path / "intraday_strategy_log.db"
    _create_selection_db(selection_db)
    _create_intraday_db(intraday_db)

    report = analyze_kr_nosignal_orp(
        selection_db=selection_db,
        intraday_db=intraday_db,
        date_from="2026-07-01",
        date_to="2026-07-02",
        windows=("primary",),
    )

    primary = report["windows"]["primary"]
    assert report["source"]["selection_table_available"] is True
    assert report["source"]["intraday_table_available"] is True
    assert primary["total_selection_rows"] == 0
    assert primary["trade_ready"] == 0
    assert primary["signal_fired"] == 0
    assert primary["traded"] == 0
    assert primary["no_signal"] == 0
    assert primary["rows"] == []


def test_kr_nosignal_orp_report_handles_missing_optional_intraday_table(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    intraday_db = tmp_path / "intraday_strategy_log.db"
    _create_selection_db(selection_db)
    sqlite3.connect(intraday_db).close()

    report = analyze_kr_nosignal_orp(
        selection_db=selection_db,
        intraday_db=intraday_db,
        date_from="2026-06-10",
        date_to="2026-06-10",
        windows=("primary",),
    )

    primary = report["windows"]["primary"]
    assert report["source"]["selection_table_available"] is True
    assert report["source"]["intraday_table_available"] is False
    assert primary["no_signal"] == 2
    orp_row = next(row for row in primary["rows"] if row["strategy"] == "opening_range_pullback")
    assert orp_row["orp_block_reason"] is None
    assert orp_row["window_phase"] == "not_applicable"
    assert orp_row["nosignal_bucket"] == "strategy_signal:orp:orp_evidence_missing"
    assert orp_row["evidence_bucket"] == "intraday_orp_evidence_missing"


def test_kr_nosignal_orp_report_handles_missing_required_selection_table(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    intraday_db = tmp_path / "intraday_strategy_log.db"
    sqlite3.connect(selection_db).close()
    _create_intraday_db(intraday_db)

    report = analyze_kr_nosignal_orp(
        selection_db=selection_db,
        intraday_db=intraday_db,
        date_from="2026-06-10",
        date_to="2026-06-10",
        windows=("primary",),
    )

    primary = report["windows"]["primary"]
    assert report["source"]["selection_table_available"] is False
    assert report["source"]["intraday_table_available"] is True
    assert primary["total_selection_rows"] == 0
    assert primary["trade_ready"] == 0
    assert primary["no_signal"] == 0
    assert primary["rows"] == []
