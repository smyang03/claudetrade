from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

from tools.analyze_kr_live_replay import analyze_kr_live_replay, to_markdown


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )


def _init_v2_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE v2_decisions (
              decision_id TEXT PRIMARY KEY,
              market TEXT NOT NULL,
              runtime_mode TEXT NOT NULL,
              session_date TEXT NOT NULL,
              ticker TEXT NOT NULL,
              prompt_version TEXT NOT NULL,
              brain_snapshot_id TEXT NOT NULL,
              strategy_hint TEXT,
              timing_style TEXT,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE v2_path_runs (
              path_run_id TEXT PRIMARY KEY,
              decision_id TEXT NOT NULL,
              path_type TEXT NOT NULL,
              market TEXT NOT NULL,
              runtime_mode TEXT NOT NULL,
              session_date TEXT NOT NULL,
              ticker TEXT NOT NULL,
              status TEXT NOT NULL,
              plan_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            INSERT INTO v2_decisions (
              decision_id, market, runtime_mode, session_date, ticker,
              prompt_version, brain_snapshot_id, strategy_hint, timing_style,
              status, created_at, updated_at, payload_json
            ) VALUES (
              'd1', 'KR', 'live', '2026-05-29', '208710',
              'v2', 'brain', '', '', 'ACTIVE', '2026-05-29T09:00:00',
              '2026-05-29T09:00:00', '{}'
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_kr_live_replay_separates_shadow_and_final_routes() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        funnel = root / "logs" / "funnel"
        preopen = root / "logs" / "preopen"
        db = root / "data" / "v2_event_store.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        _init_v2_db(db)
        _write_jsonl(
            funnel / "candidate_funnel_snapshot_20260529_KR.jsonl",
            [
                {
                    "candidate_action_counts": {"BUY_READY": 1, "PULLBACK_WAIT": 1},
                    "candidate_action_routes": [],
                    "pathb_wait_tickers": [],
                }
            ],
        )
        _write_jsonl(
            funnel / "action_routing_shadow_20260529_KR.jsonl",
            [
                {
                    "routes": [
                        {"ticker": "208710", "final_action": "PULLBACK_WAIT", "route": "PathB.wait", "reason": "pullback_wait"},
                        {"ticker": "208710", "final_action": "BUY_READY", "route": "PlanA.buy", "reason": "buy_ready"},
                    ]
                }
            ],
        )
        _write_jsonl(
            funnel / "gate_evaluation_20260529_KR.jsonl",
            [
                {
                    "ticker": "208710",
                    "claude_action": "BUY_READY",
                    "requested_action": "BUY_READY",
                    "final_action": "BUY_READY",
                    "route": "PlanA.buy",
                    "reason": "buy_ready",
                    "runtime_gate": {"data_quality": "minute_complete", "evidence_data_state": "confirmed"},
                },
                {
                    "ticker": "126730",
                    "claude_action": "PULLBACK_WAIT",
                    "requested_action": "PULLBACK_WAIT",
                    "final_action": "WATCH",
                    "route": None,
                    "reason": "pullback_wait_blocked_negative_context",
                    "runtime_gate": {"data_quality": "minute_complete", "evidence_data_state": "confirmed"},
                },
            ],
        )
        _write_jsonl(
            funnel / "selection_intraday_evidence_coverage_20260529_KR.jsonl",
            [
                {
                    "written_at": "2026-05-29T12:02:27",
                    "requested": 26,
                    "fetched": 20,
                    "complete": 20,
                    "partial": 0,
                    "missing": 6,
                    "coverage_ratio": 0.7692,
                    "errors_sample": ["090710:provider_timeout", "021880:prefetch_timeout"],
                }
            ],
        )
        _write_jsonl(
            funnel / "post_open_features_20260529_KR.jsonl",
            [{"ticker": "208710", "data_quality": "first_observed"}],
        )
        _write_jsonl(
            preopen / "20260529_KR_outcome.jsonl",
            [{"ticker": "208710", "post_open_5m_return_pct": 20.62, "post_open_mfe_pct": 29.96}],
        )

        payload = analyze_kr_live_replay(
            date="2026-05-29",
            market="KR",
            log_dir=funnel,
            v2_db_path=db,
        )

        assert payload["action_routing_shadow"]["pre_gate_pathb_wait_count"] == 1
        assert payload["gate_evaluation"]["final_pathb_wait_count"] == 0
        assert payload["gate_evaluation"]["final_plan_a_buy_count"] == 1
        assert payload["v2_event_store"]["v2_decisions"] == 1
        assert payload["v2_event_store"]["v2_path_runs"] == 0
        assert payload["phase2_shadow_events"]["healthy_pullback"]["available"] is False
        assert payload["phase2_shadow_events"]["healthy_pullback"]["decision_counts"] == {"not_available": 0}
        assert payload["selection_intraday_evidence_coverage"]["timeline"][0]["reason_visibility"] == "sample_limited"
        assert "Final PathB.wait: 0" in to_markdown(payload)


def test_kr_live_replay_reads_phase2_shadow_events_when_present() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        funnel = root / "logs" / "funnel"
        db = root / "data" / "v2_event_store.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        _init_v2_db(db)
        _write_jsonl(funnel / "kr_healthy_pullback_shadow_20260529_KR.jsonl", [{"ticker": "208710", "shadow_decision": "accepted"}])
        _write_jsonl(funnel / "kr_plan_a_no_signal_pathb_shadow_20260529_KR.jsonl", [{"ticker": "208710", "reason": "plan_a_no_signal_shadow"}])

        payload = analyze_kr_live_replay(
            date="20260529",
            market="KR",
            log_dir=funnel,
            v2_db_path=db,
        )

        assert payload["phase2_shadow_events"]["healthy_pullback"]["available"] is True
        assert payload["phase2_shadow_events"]["healthy_pullback"]["decision_counts"] == {"accepted": 1}
        assert payload["phase2_shadow_events"]["plan_a_no_signal"]["count"] == 1
