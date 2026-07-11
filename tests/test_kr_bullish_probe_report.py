from __future__ import annotations

import sqlite3

from tools.kr_bullish_probe_report import build_report


def test_report_joins_nearest_counterfactual_cycle_and_subtracts_cost() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE candidate_counterfactual_paths (
            id INTEGER PRIMARY KEY,
            runtime_mode TEXT,
            session_date TEXT,
            market TEXT,
            ticker TEXT,
            candidate_key TEXT,
            path_name TEXT,
            known_at TEXT,
            trigger_time TEXT,
            entry_price REAL,
            outcome_close_pct REAL,
            outcome_30m_pct REAL,
            outcome_60m_pct REAL,
            status TEXT,
            metadata_quality TEXT,
            label_source TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO candidate_counterfactual_paths VALUES
        (?, 'live', '2026-07-10', 'KR', '003280', ?, 'immediate', ?, '', ?, ?, NULL, NULL, 'FILLED', 'good', 'test')
        """,
        [
            (1, "early", "2026-07-10T08:53:14+09:00", 1633.0, 6.0),
            (2, "matched", "2026-07-10T09:37:04+09:00", 1705.0, 1.5835777126),
        ],
    )

    payload = build_report(
        conn,
        [
            {
                "session_date": "2026-07-10",
                "known_at": "2026-07-10T09:37:05+09:00",
                "ticker": "003280",
                "price": 1704.0,
                "trainer_plan_a_score": 76.644,
                "trainer_risk_score": 25.0,
            }
        ],
        cost_pct=0.5,
    )

    immediate = next(row for row in payload["rows"] if row["path_name"] == "immediate")
    assert immediate["entry_price"] == 1705.0
    assert round(immediate["net_close_pct"], 6) == round(1.5835777126 - 0.5, 6)
    assert payload["promotion_eligible"] is False
