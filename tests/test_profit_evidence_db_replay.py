from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import tempfile

from tools.profit_evidence_db_replay import replay


def _make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    con.executescript(
        """
        CREATE TABLE audit_candidate_rows (
          market TEXT, session_date TEXT, ticker TEXT, known_at TEXT, created_at TEXT,
          candidate_key TEXT, actual_prompt_included INTEGER, final_prompt_included INTEGER,
          in_prompt INTEGER, recommended_strategy TEXT, strategy_used TEXT,
          primary_bucket TEXT, payload_json TEXT
        );
        CREATE TABLE audit_candidate_outcomes (
          candidate_key TEXT, horizon_min INTEGER, status TEXT, return_pct REAL,
          max_runup_pct REAL, max_drawdown_pct REAL
        );
        CREATE TABLE candidate_counterfactual_paths (
          market TEXT, path_name TEXT, entry_price REAL, outcome_60m_pct REAL,
          outcome_close_pct REAL, max_runup_60m_pct REAL, max_drawdown_60m_pct REAL
        );
        """
    )
    now = datetime.now(timezone.utc).isoformat()
    evidence = {
        "profit_evidence": {
            "schema_version": "profit_evidence_v1",
            "model_version": "test_v1",
            "model_state": "PROBE",
            "decision_ts": now,
            "p_target_before_stop_calibrated": 0.65,
            "expected_gross_pct": 1.2,
            "expected_cost_pct_p75": 0.55,
            "expected_net_pct": 0.6,
            "uncertainty": 0.15,
            "ood": False,
            "drift_state": "healthy",
            "validation_sample_n": 100,
            "validation_net_lcb_pct": 0.05,
            "calibration_ece": 0.05,
        }
    }
    rows = [
        ("US", "2026-07-10", "PASS", now, now, "k1", 1, 0, 0, "momentum", "", "momentum", json.dumps(evidence)),
        ("US", "2026-07-10", "MISS", now, now, "k2", 1, 0, 0, "momentum", "", "momentum", "{}"),
    ]
    con.executemany("INSERT INTO audit_candidate_rows VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany(
        "INSERT INTO audit_candidate_outcomes VALUES (?,?,?,?,?,?)",
        [("k1", 1440, "daily_forward", 1.0, 2.0, -0.5), ("k2", 1440, "daily_forward", -1.0, 0.2, -1.5)],
    )
    con.execute("INSERT INTO candidate_counterfactual_paths VALUES ('US','immediate',100,0.5,1.0,1.2,-0.4)")
    con.commit()
    con.close()


def test_replay_shadow_and_enforce_separate_missing_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.db"
        _make_db(path)
        shadow = replay(path, mode="shadow")
        enforce = replay(path, mode="enforce")
    assert shadow["markets"]["US"]["gate_allowed_forward_1d"]["n"] == 2
    assert enforce["markets"]["US"]["gate_allowed_forward_1d"]["n"] == 1
    assert enforce["markets"]["US"]["historical_profit_evidence_rows"] == 1
    assert enforce["markets"]["US"]["would_block_rows"] == 1
    assert enforce["counterfactual_path_coverage"]["US"]["immediate"]["outcome_close_n"] == 1

