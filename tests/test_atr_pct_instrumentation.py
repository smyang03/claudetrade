from __future__ import annotations

import sqlite3
from pathlib import Path

import ticker_selection_db as tsdb


def _columns(db_path: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(ticker_selection_log)")}


def test_init_creates_atr_pct_column(tmp_path: Path) -> None:
    original = tsdb.DB_PATH
    tsdb.DB_PATH = str(tmp_path / "ticker_selection_log.db")
    try:
        tsdb.init()
        assert "atr_pct" in _columns(tsdb.DB_PATH)
    finally:
        tsdb.DB_PATH = original


def test_legacy_db_migrates_atr_pct_column(tmp_path: Path) -> None:
    original = tsdb.DB_PATH
    db_path = str(tmp_path / "ticker_selection_log.db")
    tsdb.DB_PATH = db_path
    try:
        # 구버전 스키마(atr_pct 없음) 모사
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE ticker_selection_log (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_mode  TEXT NOT NULL DEFAULT 'paper',
                    date      TEXT NOT NULL,
                    market    TEXT NOT NULL,
                    ticker    TEXT NOT NULL
                )
                """
            )
        assert "atr_pct" not in _columns(db_path)
        tsdb.init()  # 마이그레이션 실행
        assert "atr_pct" in _columns(db_path)
    finally:
        tsdb.DB_PATH = original


def test_update_atr_pct_writes_once_and_coalesces(tmp_path: Path) -> None:
    original = tsdb.DB_PATH
    tsdb.DB_PATH = str(tmp_path / "ticker_selection_log.db")
    try:
        tsdb.init()
        ids = tsdb.insert_batch(
            date="2026-06-10",
            market="KR",
            source_type="initial",
            selected=["005930"],
            candidates=[{"ticker": "005930", "market_type": "KOSPI"}],
            sel_reasons={},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["005930"]},
        )
        row_id = ids["005930"]

        # 최초 기록
        tsdb.update_atr_pct(row_id, 0.072)
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            assert conn.execute(
                "SELECT atr_pct FROM ticker_selection_log WHERE id=?", (row_id,)
            ).fetchone()[0] == 0.072

        # COALESCE: 기존 값 있으면 덮어쓰지 않음
        tsdb.update_atr_pct(row_id, 0.099)
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            assert conn.execute(
                "SELECT atr_pct FROM ticker_selection_log WHERE id=?", (row_id,)
            ).fetchone()[0] == 0.072

        # None / 잘못된 값 / 빈 row_id 는 무시(예외 없음)
        tsdb.update_atr_pct(row_id, None)
        tsdb.update_atr_pct(row_id, "nan_text")  # type: ignore[arg-type]
        tsdb.update_atr_pct(0, 0.5)
    finally:
        tsdb.DB_PATH = original


def test_atr_blocked_reason_and_value_feed_ops_review(tmp_path: Path) -> None:
    """momentum_atr_too_high 차단 행이 update_blocked_reason로 기록되면
    forward outcome 채워진 뒤 ops review atr_blocked 집계 조건을 만족한다.
    단, signal_fired는 켜지지 않아 trade_ready→signal 전환 메트릭을 교란하지 않는다."""
    original = tsdb.DB_PATH
    tsdb.DB_PATH = str(tmp_path / "ticker_selection_log.db")
    try:
        tsdb.init()
        ids = tsdb.insert_batch(
            date="2026-06-10",
            market="KR",
            source_type="initial",
            selected=["005930"],
            candidates=[{"ticker": "005930", "market_type": "KOSPI"}],
            sel_reasons={},
            consensus_mode="MILD_BULL",
            selection_meta={"trade_ready": ["005930"]},
        )
        row_id = ids["005930"]

        # 첫 루프 momentum ATR 차단 경로 모사 (signal_fired 미설정)
        tsdb.update_blocked_reason(
            row_id, "momentum_atr_too_high",
            signal_at="2026-06-10T09:40:00", atr_pct=0.085,
        )
        # forward outcome 채워짐 (forward_updater 모사)
        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                "UPDATE ticker_selection_log SET max_runup_3d=6.5 WHERE id=?", (row_id,)
            )
            row = conn.execute(
                """
                SELECT blocked_reason, signal_fired, atr_pct, max_runup_3d, signal_at
                FROM ticker_selection_log
                WHERE blocked_reason='momentum_atr_too_high' AND max_runup_3d IS NOT NULL
                """
            ).fetchone()
        assert row is not None
        assert row[0] == "momentum_atr_too_high"
        assert row[1] == 0  # signal_fired 미설정 — 전환 메트릭/튜닝 트리거 비교란
        assert row[2] == 0.085
        assert row[3] == 6.5
        assert row[4] == "2026-06-10T09:40:00"
    finally:
        tsdb.DB_PATH = original
