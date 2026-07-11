from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools.risk_recovery_runner_review import review


def _event_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE v2_path_runs (decision_id TEXT, plan_json TEXT)")
    con.execute(
        "INSERT INTO v2_path_runs VALUES (?,?)",
        (
            "d1",
            json.dumps({"actual_entry_price": 100.0, "stop_loss": 95.0}),
        ),
    )
    con.commit()
    con.close()


def _ml_db(path: Path, *, with_times: bool) -> None:
    con = sqlite3.connect(path)
    con.execute(
        """
        CREATE TABLE v2_learning_performance (
            v2_decision_id TEXT, market TEXT, qty INTEGER, mfe_pct REAL,
            mfe_time TEXT, mae_time TEXT, pnl_pct_net REAL, closed INTEGER
        )
        """
    )
    con.execute(
        "INSERT INTO v2_learning_performance VALUES (?,?,?,?,?,?,?,1)",
        (
            "d1", "KR", 4, 11.0,
            "2026-07-10T10:00:00+09:00" if with_times else None,
            "2026-07-10T11:00:00+09:00" if with_times else None,
            2.0,
        ),
    )
    con.commit()
    con.close()


def test_uses_entry_stop_risk_and_emits_no_profit_counterfactual(tmp_path: Path) -> None:
    ml = tmp_path / "ml.db"
    event = tmp_path / "event.db"
    _ml_db(ml, with_times=True)
    _event_db(event)

    payload = review(ml, event, trigger_r=2.0, min_qty=4)

    assert payload["entry_stop_risk_known_n"] == 1
    assert payload["trigger_candidate_n"] == 1
    assert payload["uses_realized_mae_as_initial_risk"] is False
    assert payload["counterfactual_return_emitted"] is False
    assert "runner_mean_net" not in payload


def test_missing_time_is_fail_closed_not_claimed_as_forward_only(tmp_path: Path) -> None:
    ml = tmp_path / "ml.db"
    event = tmp_path / "event.db"
    _ml_db(ml, with_times=False)
    _event_db(event)

    payload = review(ml, event)

    assert payload["mfe_mae_time_coverage_n"] == 0
    assert payload["trigger_candidate_n"] == 0
    assert "unverified" in payload["verdict"]
