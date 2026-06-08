from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tools import ops_kr_wait_re_evaluation_queue


def _init_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE candidate_counterfactual_paths (
                id INTEGER PRIMARY KEY,
                runtime_mode TEXT,
                session_date TEXT,
                market TEXT,
                ticker TEXT,
                candidate_key TEXT,
                call_id TEXT,
                signal_time TEXT,
                known_at TEXT,
                trade_ready_action TEXT,
                actual_path TEXT,
                path_name TEXT,
                trigger_time TEXT,
                trigger_price REAL,
                trigger_reason TEXT,
                entry_price REAL,
                entry_delay_min REAL,
                outcome_30m_pct REAL,
                outcome_60m_pct REAL,
                outcome_close_pct REAL,
                max_runup_60m_pct REAL,
                max_drawdown_60m_pct REAL,
                status TEXT,
                metadata_json TEXT,
                metadata_quality TEXT,
                label_source TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE audit_candidate_rows (
                candidate_key TEXT PRIMARY KEY,
                freshness_verdict TEXT,
                evidence_data_state TEXT,
                evidence_action_ceiling TEXT,
                route_final_action TEXT,
                route_reason TEXT,
                route_runtime_gate_reason TEXT,
                trainer_candidate_state TEXT,
                action_ceiling TEXT,
                why_not_watch TEXT,
                payload_json TEXT
            )
            """
        )


def _metadata(
    *,
    route_source: str = "analyst_reinvoke",
    evidence_state: str = "confirmed",
    evidence_ceiling: str = "WATCH",
    freshness: str = "FRESH",
    bucket: str = "OPEN_60_90",
) -> str:
    return json.dumps(
        {
            "context": {
                "route_source": route_source,
                "evidence_data_state": evidence_state,
                "evidence_action_ceiling": evidence_ceiling,
                "freshness_verdict": freshness,
                "entry_window_bucket": bucket,
            }
        },
        ensure_ascii=False,
    )


def _insert_candidate(
    path: Path,
    *,
    key: str,
    ticker: str,
    path_name: str = "wait_30m",
    route_source: str = "analyst_reinvoke",
    evidence_state: str = "confirmed",
    evidence_ceiling: str = "WATCH",
    freshness: str = "FRESH",
    bucket: str = "OPEN_60_90",
    outcome_60m_pct: float = 5.0,
    trigger_time: str = "2026-06-01T10:00:00+09:00",
    session_date: str = "2026-06-01",
) -> None:
    metadata = _metadata(
        route_source=route_source,
        evidence_state=evidence_state,
        evidence_ceiling=evidence_ceiling,
        freshness=freshness,
        bucket=bucket,
    )
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            INSERT INTO candidate_counterfactual_paths (
                runtime_mode, session_date, market, ticker, candidate_key, call_id,
                signal_time, known_at, trade_ready_action, actual_path, path_name,
                trigger_time, trigger_price, trigger_reason, entry_price,
                entry_delay_min, outcome_30m_pct, outcome_60m_pct, outcome_close_pct,
                max_runup_60m_pct, max_drawdown_60m_pct, status, metadata_json,
                metadata_quality, label_source
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "live",
                session_date,
                "KR",
                ticker,
                key,
                "call_1",
                trigger_time,
                trigger_time,
                "WATCH",
                "no_entry",
                path_name,
                trigger_time,
                1000.0,
                "wait",
                1000.0,
                30,
                outcome_60m_pct / 2.0,
                outcome_60m_pct,
                outcome_60m_pct,
                max(outcome_60m_pct, 1.0),
                -1.0,
                "CLOSE_OUTCOME_FILLED",
                metadata,
                "runtime_authoritative",
                "test",
            ),
        )
        conn.execute(
            """
            INSERT INTO audit_candidate_rows (
                candidate_key, freshness_verdict, evidence_data_state,
                evidence_action_ceiling, route_final_action, route_reason,
                route_runtime_gate_reason, trainer_candidate_state, action_ceiling,
                why_not_watch, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                freshness,
                evidence_state,
                evidence_ceiling,
                "WATCH",
                "",
                "",
                "",
                evidence_ceiling,
                "",
                "{}",
            ),
        )


def test_kr_wait_queue_uses_live_visible_fields_not_outcome_labels(tmp_path: Path) -> None:
    db = tmp_path / "candidate.db"
    _init_db(db)
    _insert_candidate(db, key="eligible_positive", ticker="111111", outcome_60m_pct=8.0)
    _insert_candidate(db, key="eligible_negative", ticker="222222", outcome_60m_pct=-4.0)
    _insert_candidate(db, key="blocked_positive_route", ticker="333333", route_source="rescreen", outcome_60m_pct=20.0)
    _insert_candidate(db, key="blocked_positive_evidence", ticker="444444", evidence_state="missing", outcome_60m_pct=18.0)

    payload = ops_kr_wait_re_evaluation_queue.build_kr_wait_re_evaluation_queue(
        db_path=db,
        output_root=tmp_path / "analysis",
        output_dir="out",
        max_per_day=10,
        max_per_ticker_day=10,
    )

    queued_keys = {row["candidate_key"] for row in payload["queued_candidates"]}
    assert queued_keys == {"eligible_positive", "eligible_negative"}
    assert payload["leakage_contract"]["labels_used_for_queue_selection"] is False
    assert payload["summary"]["rejection_counts"]["route_source_not_allowed"] == 1
    assert payload["summary"]["rejection_counts"]["evidence_state_not_allowed"] == 1
    assert payload["summary"]["queued"]["outcome_60m"]["worst"] == -4.0
    for output_path in payload["output_paths"].values():
        assert Path(output_path).exists()


def test_kr_wait_queue_applies_daily_and_ticker_caps(tmp_path: Path) -> None:
    db = tmp_path / "candidate.db"
    _init_db(db)
    _insert_candidate(db, key="same_ticker_old", ticker="111111", trigger_time="2026-06-01T10:00:00+09:00")
    _insert_candidate(db, key="same_ticker_new", ticker="111111", trigger_time="2026-06-01T10:05:00+09:00")
    _insert_candidate(db, key="other_ticker", ticker="222222", trigger_time="2026-06-01T10:04:00+09:00")
    _insert_candidate(db, key="third_ticker", ticker="333333", trigger_time="2026-06-01T10:03:00+09:00")

    payload = ops_kr_wait_re_evaluation_queue.build_kr_wait_re_evaluation_queue(
        db_path=db,
        output_root=tmp_path / "analysis",
        output_dir="caps",
        max_per_day=2,
        max_per_ticker_day=1,
    )

    queued_keys = {row["candidate_key"] for row in payload["queued_candidates"]}
    assert len(queued_keys) == 2
    assert "same_ticker_new" in queued_keys
    assert "same_ticker_old" not in queued_keys
    assert payload["summary"]["rejection_counts"]["ticker_daily_quota_exceeded"] == 1
    assert payload["summary"]["rejection_counts"]["daily_quota_exceeded"] == 1


def test_kr_wait_queue_filters_path_ceiling_and_required_bucket(tmp_path: Path) -> None:
    db = tmp_path / "candidate.db"
    _init_db(db)
    _insert_candidate(
        db,
        key="eligible_probe_wait30",
        ticker="111111",
        path_name="wait_30m",
        evidence_state="partial",
        evidence_ceiling="PROBE_READY",
        bucket="OPEN_60_90",
    )
    _insert_candidate(
        db,
        key="blocked_wait60",
        ticker="222222",
        path_name="wait_60m",
        evidence_state="partial",
        evidence_ceiling="PROBE_READY",
        bucket="OPEN_60_90",
    )
    _insert_candidate(
        db,
        key="blocked_watch",
        ticker="333333",
        path_name="wait_30m",
        evidence_state="partial",
        evidence_ceiling="WATCH",
        bucket="OPEN_60_90",
    )
    _insert_candidate(
        db,
        key="blocked_bucket",
        ticker="444444",
        path_name="wait_30m",
        evidence_state="partial",
        evidence_ceiling="PROBE_READY",
        bucket="OPEN_0_30",
    )

    payload = ops_kr_wait_re_evaluation_queue.build_kr_wait_re_evaluation_queue(
        db_path=db,
        output_root=tmp_path / "analysis",
        output_dir="strict",
        path_names={"wait_30m"},
        evidence_states={"partial"},
        evidence_action_ceilings={"PROBE_READY"},
        required_entry_buckets={"OPEN_60_90"},
        excluded_entry_buckets=set(),
        max_per_day=10,
        max_per_ticker_day=10,
    )

    queued_keys = {row["candidate_key"] for row in payload["queued_candidates"]}
    assert queued_keys == {"eligible_probe_wait30"}
    assert payload["summary"]["rejection_counts"]["path_name_not_allowed"] == 1
    assert payload["summary"]["rejection_counts"]["evidence_action_ceiling_not_allowed"] == 1
    assert payload["summary"]["rejection_counts"]["entry_window_bucket_not_required"] == 1
    assert payload["policy"]["path_names"] == ["wait_30m"]
    assert payload["policy"]["evidence_action_ceilings"] == ["PROBE_READY"]


def test_kr_wait_queue_cli_json(tmp_path: Path, capsys) -> None:
    db = tmp_path / "candidate.db"
    _init_db(db)
    _insert_candidate(db, key="eligible", ticker="111111")

    rc = ops_kr_wait_re_evaluation_queue.main(
        [
            "--db-path",
            str(db),
            "--output-root",
            str(tmp_path / "analysis"),
            "--output-dir",
            "cli",
            "--json",
        ]
    )

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["summary"]["queued_count"] == 1
