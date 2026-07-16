from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from audit.candidate_audit_store import CandidateAuditStore
from tools.candidate_consensus_outcome_review import review_consensus_outcomes


def test_consensus_outcome_review_matures_without_orders(tmp_path: Path) -> None:
    db = tmp_path / "candidate.db"
    store = CandidateAuditStore(db)
    store.upsert_candidate(
        {
            "runtime_mode": "live",
            "market": "US",
            "session_date": "2026-07-16",
            "ticker": "PYPL",
            "call_id": "call1",
            "known_at": "2026-07-16T13:31:00+00:00",
            "price": 74.0,
        }
    )
    con = sqlite3.connect(db)
    try:
        candidate_key = con.execute(
            "SELECT first_candidate_key FROM candidate_registry_first"
        ).fetchone()[0]
    finally:
        con.close()
    store.upsert_outcomes(
        [
            {
                "candidate_key": candidate_key,
                "horizon_min": 30,
                "target_at": "2026-07-16T14:01:00+00:00",
                "observed_at": "2026-07-16T14:01:00+00:00",
                "observed_price": 75.0,
                "return_pct": 1.0,
                "max_runup_pct": 1.5,
                "max_drawdown_pct": -0.5,
                "status": "ok",
                "source": "test",
                "label_generated_at": "2026-07-16T14:02:00+00:00",
                "payload": {},
            },
            {
                "candidate_key": candidate_key,
                "horizon_min": 60,
                "target_at": "2026-07-16T14:31:00+00:00",
                "observed_at": "2026-07-16T14:31:00+00:00",
                "observed_price": 76.0,
                "return_pct": 2.0,
                "max_runup_pct": 2.5,
                "max_drawdown_pct": -0.5,
                "status": "ok",
                "source": "test",
                "label_generated_at": "2026-07-16T14:32:00+00:00",
                "payload": {},
            },
        ]
    )
    decision = tmp_path / "decisions.jsonl"
    decision.write_text(
        json.dumps(
            {
                "candidate_key": candidate_key,
                "session_date": "2026-07-16",
                "market": "US",
                "ticker": "PYPL",
                "decision": "SELECT_SHADOW",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    outcome = tmp_path / "outcomes.jsonl"

    summary = review_consensus_outcomes(
        session_date="2026-07-16",
        market="US",
        audit_db=db,
        decision_ledger=decision,
        outcome_ledger=outcome,
    )

    assert summary["selected_records"] == 1
    assert summary["horizons"]["30"]["SELECT_SHADOW"]["mean_return_pct"] == 1.0
    assert summary["horizons"]["60"]["SELECT_SHADOW"]["mean_return_pct"] == 2.0
    assert summary["promotion_eligible"] is False
    assert summary["updated_at"] == summary["generated_at"]
    assert len(outcome.read_text(encoding="utf-8").splitlines()) == 2
