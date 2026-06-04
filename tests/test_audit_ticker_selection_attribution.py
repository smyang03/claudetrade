from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.audit_ticker_selection_attribution import apply_exact_backfill, audit_ticker_selection_attribution, main


def _create_selection_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE ticker_selection_log (
                id INTEGER PRIMARY KEY,
                bot_mode TEXT,
                date TEXT,
                market TEXT,
                ticker TEXT,
                trade_ready INTEGER,
                source_type TEXT,
                signal_fired INTEGER,
                strategy_name TEXT,
                traded INTEGER,
                traded_at TEXT,
                execution_decision_id TEXT,
                execution_source_type TEXT,
                execution_strategy TEXT,
                execution_reason TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO ticker_selection_log
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    1,
                    "live",
                    "2026-06-01",
                    "KR",
                    "005930",
                    1,
                    "rescreen",
                    1,
                    "momentum",
                    1,
                    "2026-06-01T09:10:00+09:00",
                    "",
                    "",
                    "",
                    "",
                ),
                (
                    2,
                    "live",
                    "2026-06-01",
                    "KR",
                    "000660",
                    0,
                    "rescreen",
                    1,
                    "gap_pullback",
                    1,
                    "2026-06-01T09:12:00+09:00",
                    "",
                    "",
                    "",
                    "",
                ),
                (
                    3,
                    "live",
                    "2026-06-01",
                    "US",
                    "NVDA",
                    1,
                    "initial",
                    1,
                    "momentum",
                    1,
                    "2026-06-01T22:35:00+09:00",
                    "",
                    "",
                    "",
                    "",
                ),
                (
                    4,
                    "live",
                    "2026-06-01",
                    "KR",
                    "035420",
                    1,
                    "initial",
                    1,
                    "momentum",
                    1,
                    "2026-06-01T10:00:00+09:00",
                    "",
                    "",
                    "",
                    "",
                ),
            ],
        )


def _create_ml_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE v2_learning_performance (
                v2_decision_id TEXT PRIMARY KEY,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                status TEXT,
                route TEXT,
                path_type TEXT,
                strategy TEXT,
                filled INTEGER,
                closed INTEGER,
                filled_at TEXT,
                pnl_pct REAL,
                synced_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE v2_decision_fill_links (
                v2_decision_id TEXT PRIMARY KEY,
                market TEXT,
                runtime_mode TEXT,
                session_date TEXT,
                ticker TEXT,
                link_status TEXT,
                matched_by TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE decisions (
                id INTEGER PRIMARY KEY,
                ts TEXT,
                market TEXT,
                ticker TEXT,
                session_date TEXT,
                decision TEXT,
                strategy_used TEXT,
                filled INTEGER,
                order_status TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO v2_learning_performance
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "dec_kr_005930",
                    "KR",
                    "live",
                    "2026-06-01",
                    "005930",
                    "CLOSED",
                    "plan_a",
                    "",
                    "momentum",
                    1,
                    1,
                    "2026-06-01T00:10:30+00:00",
                    1.2,
                    "2026-06-02T00:00:00+00:00",
                ),
                (
                    "dec_kr_000660",
                    "KR",
                    "live",
                    "2026-06-01",
                    "000660",
                    "CLOSED",
                    "path_b",
                    "claude_price",
                    "gap_pullback",
                    1,
                    1,
                    "2026-06-01T00:12:20+00:00",
                    -0.4,
                    "2026-06-02T00:00:00+00:00",
                ),
                (
                    "dec_us_nvda_a",
                    "US",
                    "live",
                    "2026-06-01",
                    "NVDA",
                    "FILLED",
                    "path_b",
                    "claude_price",
                    "momentum",
                    1,
                    0,
                    "2026-06-01T13:35:10+00:00",
                    None,
                    "2026-06-02T00:00:00+00:00",
                ),
                (
                    "dec_us_nvda_b",
                    "US",
                    "live",
                    "2026-06-01",
                    "NVDA",
                    "FILLED",
                    "path_b",
                    "claude_price",
                    "momentum",
                    1,
                    0,
                    "2026-06-01T13:36:00+00:00",
                    None,
                    "2026-06-02T00:00:00+00:00",
                ),
            ],
        )
        conn.execute(
            """
            INSERT INTO decisions
            VALUES (10, '2026-06-01T01:00:00+00:00', 'KR', '035420', '2026-06-01',
                    'BUY_SIGNAL', 'momentum', 1, 'FILLED')
            """
        )


def test_audit_classifies_backfill_watch_split_ambiguous_and_legacy(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    ml_db = tmp_path / "decisions.db"
    _create_selection_db(selection_db)
    _create_ml_db(ml_db)

    report = audit_ticker_selection_attribution(
        selection_db=selection_db,
        ml_db=ml_db,
        mode="live",
        market="ALL",
        max_time_delta_min=10,
    )

    rows = {row["selection_log_id"]: row for row in report["rows_sample"]}
    assert report["summary"]["traded_rows"] == 4
    assert report["summary"]["contaminated_rows"] == 4
    assert report["summary"]["missing_execution_decision_id_rows"] == 4
    assert report["summary"]["watch_only_traded_rows"] == 1
    assert rows[1]["classification"] == "exact_v2_time_match"
    assert rows[1]["recommendation"] == "backfill_execution_decision_id"
    assert rows[2]["classification"] == "exact_v2_time_match"
    assert rows[2]["recommendation"] == "manual_review_split_watch_only_execution_row"
    assert rows[3]["classification"] == "ambiguous_v2_time_match"
    assert rows[4]["classification"] == "legacy_decisions_single_match"


def test_apply_exact_backfill_updates_only_trade_ready_exact_rows(tmp_path: Path) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    ml_db = tmp_path / "decisions.db"
    backup_dir = tmp_path / "backups"
    _create_selection_db(selection_db)
    _create_ml_db(ml_db)
    report = audit_ticker_selection_attribution(
        selection_db=selection_db,
        ml_db=ml_db,
        mode="live",
        market="ALL",
        max_time_delta_min=10,
    )

    result = apply_exact_backfill(
        selection_db=selection_db,
        report=report,
        backup_dir=backup_dir,
        expected_exact_count=1,
    )

    assert result["applied_count"] == 1
    assert result["skipped_count"] == 0
    assert Path(result["backup_path"]).exists()
    with sqlite3.connect(selection_db) as conn:
        rows = {
            row[0]: row
            for row in conn.execute(
                """
                SELECT id, execution_decision_id, execution_source_type, execution_strategy, execution_reason
                FROM ticker_selection_log
                ORDER BY id
                """
            ).fetchall()
        }
    assert rows[1][1] == "dec_kr_005930"
    assert rows[1][2] == "plan_a"
    assert rows[1][3] == "momentum"
    assert rows[1][4] == "audited_backfill:v2_exact_time_match"
    assert rows[2][1] == ""
    assert rows[3][1] == ""
    assert rows[4][1] == ""


def test_cli_apply_requires_expected_exact_count(tmp_path: Path, capsys) -> None:
    selection_db = tmp_path / "ticker_selection_log.db"
    ml_db = tmp_path / "decisions.db"
    _create_selection_db(selection_db)
    _create_ml_db(ml_db)

    exit_code = main(
        [
            "--selection-db",
            str(selection_db),
            "--ml-db",
            str(ml_db),
            "--apply-exact-backfill",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "--apply-exact-backfill requires --expected-exact-count" in captured.err
