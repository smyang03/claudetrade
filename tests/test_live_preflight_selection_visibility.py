from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from tools import live_preflight


def _runtime_path(root: Path):
    def _inner(*parts: str, make_parents: bool = True) -> Path:
        path = root.joinpath(*parts)
        if make_parents:
            path.parent.mkdir(parents=True, exist_ok=True)
        return path

    return _inner


def test_candidate_audit_outcome_check_reports_daily_pending_freshness(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "audit" / "candidate_audit.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE audit_candidate_outcomes (
                candidate_key TEXT,
                horizon_min INTEGER,
                status TEXT,
                source TEXT,
                label_generated_at TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO audit_candidate_outcomes
            VALUES ('cand_30', 30, 'audit_sparse', 'audit_candidate_rows', '2026-06-04T05:00:00+00:00')
            """
        )
        conn.execute(
            """
            INSERT INTO audit_candidate_outcomes
            VALUES ('cand_daily', 1440, 'daily_pending', 'audit_candidate_rows_daily_forward', '2026-05-19T08:00:00+00:00')
            """
        )

    with sqlite3.connect(db_path) as conn:
        # 세션 날짜가 7일 초과로 오래된 pending → stale 적체로 WARN
        conn.execute("CREATE TABLE audit_candidate_rows (candidate_key TEXT, session_date TEXT)")
        conn.execute("INSERT INTO audit_candidate_rows VALUES ('cand_daily', '2026-05-19')")
        conn.execute("INSERT INTO audit_candidate_rows VALUES ('cand_30', '2026-06-04')")

    with patch.object(live_preflight, "get_runtime_path", side_effect=_runtime_path(tmp_path)):
        checks = live_preflight._candidate_audit_outcome_checks("live")

    check = checks[0]
    assert check.status == "WARN"
    assert check.data["daily_pending_rows"] == 1
    assert check.data["stale_daily_pending_rows"] == 1
    assert any(
        item["horizon_min"] == 1440 and item["status"] == "daily_pending"
        for item in check.data["horizon_status"]
    )
    assert "daily_pending_rows=1" in check.detail


def test_candidate_audit_outcome_check_passes_with_fresh_daily_pending(tmp_path: Path) -> None:
    from datetime import datetime

    db_path = tmp_path / "data" / "audit" / "candidate_audit.db"
    db_path.parent.mkdir(parents=True)
    today = datetime.now().date().isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE audit_candidate_outcomes (
                candidate_key TEXT,
                horizon_min INTEGER,
                status TEXT,
                source TEXT,
                label_generated_at TEXT
            )
            """
        )
        conn.execute("CREATE TABLE audit_candidate_rows (candidate_key TEXT, session_date TEXT)")
        conn.execute(
            "INSERT INTO audit_candidate_outcomes VALUES ('cand_fresh', 1440, 'daily_pending', 's', ?)",
            (f"{today}T08:00:00+00:00",),
        )
        conn.execute("INSERT INTO audit_candidate_rows VALUES ('cand_fresh', ?)", (today,))

    with patch.object(live_preflight, "get_runtime_path", side_effect=_runtime_path(tmp_path)):
        checks = live_preflight._candidate_audit_outcome_checks("live")

    # 목표 거래일 도래 전의 최근 pending은 정상 대기 → PASS
    check = checks[0]
    assert check.status == "PASS"
    assert check.data["stale_daily_pending_rows"] == 0


def test_ticker_selection_attribution_check_reports_missing_execution_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "ticker_selection_log.db"
    db_path.parent.mkdir(parents=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE ticker_selection_log (
                bot_mode TEXT,
                market TEXT,
                date TEXT,
                ticker TEXT,
                trade_ready INTEGER,
                traded INTEGER,
                execution_decision_id TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ticker_selection_log
            VALUES ('live', 'KR', '2026-06-04', '005930', 0, 1, '')
            """
        )
        conn.execute(
            """
            INSERT INTO ticker_selection_log
            VALUES ('live', 'US', '2026-06-04', 'NVDA', 1, 1, 'dec_us_nvda')
            """
        )

    with patch.object(live_preflight, "get_runtime_path", side_effect=_runtime_path(tmp_path)):
        checks = live_preflight._ticker_selection_attribution_checks("live")

    check = checks[0]
    assert check.status == "WARN"
    assert check.data["traded_rows"] == 2
    assert check.data["missing_execution_decision_id_rows"] == 1
    assert check.data["watch_only_traded_rows"] == 1
    assert "missing_execution_decision_id=1" in check.detail
