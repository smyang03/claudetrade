from __future__ import annotations

import sqlite3
from pathlib import Path

import ticker_selection_db as tsdb


def test_recent_selection_feedback_uses_distinct_ticker_date_basis(tmp_path: Path) -> None:
    original_db_path = tsdb.DB_PATH
    tsdb.DB_PATH = str(tmp_path / "ticker_selection_log.db")
    try:
        tsdb.init()
        for source_type in ("initial", "rescreen"):
            tsdb.insert_batch(
                date="2026-04-21",
                market="KR",
                source_type=source_type,
                selected=["005930", "000660"],
                candidates=[
                    {"ticker": "005930", "market_type": "KOSPI", "category": "large_cap"},
                    {"ticker": "000660", "market_type": "KOSPI", "category": "large_cap"},
                ],
                sel_reasons={},
                consensus_mode="MILD_BULL",
                selection_meta={"trade_ready": ["005930"]},
            )

        with sqlite3.connect(tsdb.DB_PATH) as conn:
            conn.execute(
                """
                UPDATE ticker_selection_log
                SET forward_3d=CASE ticker WHEN '005930' THEN 4.0 ELSE 2.0 END,
                    max_runup_3d=CASE ticker WHEN '005930' THEN 6.0 ELSE 8.0 END,
                    max_drawdown_3d=CASE ticker WHEN '005930' THEN -1.0 ELSE -2.0 END
                """
            )

        summary = tsdb.get_recent_selection_feedback("KR", days=20, as_of="2026-04-30")
        text = tsdb.format_recent_selection_feedback("KR", days=20, as_of="2026-04-30")

        assert summary["metric_basis"] == "distinct_ticker_date"
        assert summary["total_rows"] == 2
        assert summary["raw_row_count"] == 4
        assert summary["trade_ready_rows"] == 1
        assert summary["watch_only_rows"] == 1
        assert summary["missed_watch_only_count"] == 1
        assert summary["missed_watch_only_rate_3d"] == 100.0
        assert "selected=2 basis=distinct_ticker_date raw_rows=4" in text

        breakdown = tsdb.get_recent_selection_feedback_breakdown(
            "KR",
            "category",
            days=20,
            as_of="2026-04-30",
        )
        assert breakdown[0]["group_value"] == "large_cap"
        assert breakdown[0]["total_rows"] == 2
        assert breakdown[0]["raw_row_count"] == 4
    finally:
        tsdb.DB_PATH = original_db_path
        tsdb._price_cache.clear()
