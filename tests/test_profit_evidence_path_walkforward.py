from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile

import pandas as pd

from tools.profit_evidence_path_walkforward import _policy_first, build_path_dataset


def _db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE audit_candidate_rows (
          candidate_key TEXT, market TEXT, session_date TEXT, ticker TEXT, known_at TEXT,
          actual_prompt_included INTEGER, final_prompt_included INTEGER, in_prompt INTEGER,
          price REAL, change_pct REAL, volume_ratio REAL, from_high_pct REAL,
          raw_score_current REAL, primary_bucket TEXT, recommended_strategy TEXT,
          candidate_source TEXT, liquidity_bucket TEXT, market_type TEXT
        );
        CREATE TABLE candidate_counterfactual_paths (
          id INTEGER, market TEXT, session_date TEXT, ticker TEXT, known_at TEXT,
          signal_time TEXT, trigger_time TEXT, path_name TEXT, trigger_reason TEXT,
          entry_price REAL, entry_delay_min REAL, outcome_60m_pct REAL, outcome_close_pct REAL,
          max_runup_60m_pct REAL, max_drawdown_60m_pct REAL, metadata_quality TEXT,
          label_source TEXT, metadata_json TEXT
        );
        """
    )
    con.executemany(
        "INSERT INTO audit_candidate_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("past", "US", "2026-07-10", "NVDA", "2026-07-10T10:00:00+00:00", 1, 0, 0, 100, 1, 2, -1, 1, "momentum", "momentum", "screen", "high", "NASDAQ"),
            ("future", "US", "2026-07-10", "NVDA", "2026-07-10T10:02:00+00:00", 1, 0, 0, 101, 2, 3, -2, 999, "momentum", "momentum", "screen", "high", "NASDAQ"),
        ],
    )
    metadata = json.dumps(
        {
            "consensus_mode": "MILD_BULL",
            "mode_family": "RISK_ON",
            "context": {
                "route_source": "test",
                "data_quality": "complete",
                "market_open_elapsed_min": 30,
                "ret_3m_pct": 0.2,
                "ret_5m_pct": 0.3,
                "ret_10m_pct": 0.4,
                "ret_30m_pct": 0.5,
                "volume_ratio_open": 2.0,
                "vwap_distance_pct": 0.1,
                "pullback_from_high_pct": -0.5,
            },
        }
    )
    con.executemany(
        "INSERT INTO candidate_counterfactual_paths VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (1, "US", "2026-07-10", "NVDA", "2026-07-10T10:01:00+00:00", "2026-07-10T10:01:00+00:00", "2026-07-10T10:01:00+00:00", "immediate", "test", 100.5, 1, 1.2, 1.3, 2, -0.5, "runtime_authoritative", "test", metadata),
            (2, "US", "2026-07-10", "NVDA", "2026-07-10T10:05:01+00:00", "2026-07-10T10:05:01+00:00", "2026-07-10T10:05:01+00:00", "wait_30m", "test", 100.0, 30, 0.4, 0.5, 1, -0.3, "runtime_authoritative", "test", metadata),
        ],
    )
    con.commit()
    con.close()


def test_path_join_is_backward_only_and_rejects_future_candidate_features() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.db"
        _db(path)
        con = sqlite3.connect(str(path))
        try:
            frame, coverage = build_path_dataset(con, "US", tolerance_min=2)
        finally:
            con.close()
    assert coverage["path_rows"] == 2
    assert coverage["usable_rows"] == 1
    assert frame.iloc[0]["audit_candidate_key"] == "past"
    assert frame.iloc[0]["raw_score_current"] == 1
    assert frame.iloc[0]["join_delta_sec"] == 60


def test_policy_first_keeps_earliest_accepted_path_per_ticker_day() -> None:
    rows = pd.DataFrame(
        [
            {"session_date": "2026-07-10", "ticker_key": "NVDA", "entry_ts": "2026-07-10T10:30:00Z", "path_id": 2},
            {"session_date": "2026-07-10", "ticker_key": "NVDA", "entry_ts": "2026-07-10T10:00:00Z", "path_id": 1},
        ]
    )
    selected = _policy_first(rows, pd.Series([True, True]).to_numpy())
    assert selected["path_id"].tolist() == [1]

