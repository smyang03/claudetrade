from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from lifecycle.event_store import EventStore
from tools import ops_residual_risk_audit


def _empty_sqlite(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path):
        pass


def test_residual_risk_audit_is_read_only_dry_run(tmp_path: Path) -> None:
    event_db = tmp_path / "events.db"
    EventStore(event_db)
    selection_db = tmp_path / "ticker_selection_log.db"
    ml_db = tmp_path / "decisions.db"
    candidate_db = tmp_path / "candidate_audit.db"
    _empty_sqlite(selection_db)
    _empty_sqlite(ml_db)
    _empty_sqlite(candidate_db)

    report = ops_residual_risk_audit.build_residual_risk_audit(
        event_db=event_db,
        selection_db=selection_db,
        ml_db=ml_db,
        candidate_db=candidate_db,
        candidate_days=1,
    )

    assert report["ok"] is True
    assert report["dry_run"] is True
    assert report["live_writes_performed"] is False
    assert report["summary"]["pathb"]["cross_run_closed_lifecycle_evidence"] == 0
    assert report["summary"]["selection_attribution"]["traded_rows"] == 0
    assert report["summary"]["candidate_outcome_catchup_dry_run"]["dry_run"] is True


def test_residual_risk_audit_cli_writes_report_to_requested_root(tmp_path: Path, capsys) -> None:
    event_db = tmp_path / "events.db"
    EventStore(event_db)
    selection_db = tmp_path / "ticker_selection_log.db"
    ml_db = tmp_path / "decisions.db"
    candidate_db = tmp_path / "candidate_audit.db"
    _empty_sqlite(selection_db)
    _empty_sqlite(ml_db)
    _empty_sqlite(candidate_db)

    rc = ops_residual_risk_audit.main(
        [
            "--event-db",
            str(event_db),
            "--selection-db",
            str(selection_db),
            "--ml-db",
            str(ml_db),
            "--candidate-db",
            str(candidate_db),
            "--candidate-days",
            "1",
            "--write-report",
            "--report-root",
            str(tmp_path / "reports"),
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    paths = payload["report_paths"]
    assert Path(paths["json"]).resolve().is_relative_to((tmp_path / "reports").resolve())
    assert Path(paths["md"]).exists()
    assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))["live_writes_performed"] is False
