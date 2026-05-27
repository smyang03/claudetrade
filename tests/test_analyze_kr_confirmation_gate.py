from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from tools.analyze_kr_confirmation_gate import analyze_kr_confirmation_gate, to_markdown


def _init_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE audit_candidate_rows (
              candidate_key TEXT PRIMARY KEY,
              session_date TEXT,
              known_at TEXT,
              market TEXT,
              runtime_mode TEXT,
              ticker TEXT,
              route_original_action TEXT,
              route_final_action TEXT,
              route_reason TEXT,
              route_runtime_gate_reason TEXT,
              route_demoted_to TEXT,
              from_high_pct REAL,
              payload_json TEXT
            );

            CREATE TABLE audit_candidate_outcomes (
              candidate_key TEXT,
              horizon_min INTEGER,
              return_pct REAL,
              max_runup_pct REAL,
              max_drawdown_pct REAL
            );
            """
        )
        rows = [
            (
                "kept",
                "2026-05-26",
                "2026-05-26T09:30:00+09:00",
                "KR",
                "live",
                "005930",
                "BUY_READY",
                "BUY_READY",
                "buy_ready",
                "",
                "",
                -0.2,
                "{}",
            ),
            (
                "confirmation",
                "2026-05-26",
                "2026-05-26T09:31:00+09:00",
                "KR",
                "live",
                "000660",
                "PROBE_READY",
                "WATCH",
                "kr_fast_trigger_not_confirmed",
                "kr_fast_trigger_not_confirmed",
                "WATCH",
                -0.1,
                '{"runtime_gate": {"kr_confirmation_reason": "kr_fast_trigger_not_confirmed"}}',
            ),
            (
                "evidence_pending",
                "2026-05-26",
                "2026-05-26T09:32:00+09:00",
                "KR",
                "live",
                "035420",
                "BUY_READY",
                "WATCH",
                "evidence_ceiling_watch",
                "evidence_action_ceiling",
                "WATCH",
                2.0,
                "{'confirmation_reason': 'kr_fast_trigger_not_confirmed'}",
            ),
            (
                "negative_pullback",
                "2026-05-26",
                "2026-05-26T09:33:00+09:00",
                "KR",
                "live",
                "051910",
                "PULLBACK_WAIT",
                "WATCH",
                "pullback_wait_blocked_negative_context",
                "negative_pullback_context",
                "WATCH",
                None,
                "{}",
            ),
            (
                "hard_block",
                "2026-05-26",
                "2026-05-26T09:34:00+09:00",
                "KR",
                "live",
                "068270",
                "BUY_READY",
                "HARD_BLOCK",
                "same_day_stopped",
                "",
                "",
                -3.0,
                "{}",
            ),
        ]
        conn.executemany(
            """
            INSERT INTO audit_candidate_rows (
              candidate_key, session_date, known_at, market, runtime_mode, ticker,
              route_original_action, route_final_action, route_reason, route_runtime_gate_reason,
              route_demoted_to, from_high_pct, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        outcomes = [
            ("kept", 30, 0.5, 0.8, -0.2),
            ("kept", 60, 1.2, 1.8, -0.4),
            ("confirmation", 60, -2.0, 0.1, -2.5),
            ("negative_pullback", 60, -1.0, 0.0, -1.2),
        ]
        conn.executemany(
            """
            INSERT INTO audit_candidate_outcomes (
              candidate_key, horizon_min, return_pct, max_runup_pct, max_drawdown_pct
            ) VALUES (?, ?, ?, ?, ?)
            """,
            outcomes,
        )
        conn.commit()
    finally:
        conn.close()


def test_analyze_kr_confirmation_gate_classifies_route_groups() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        _init_db(db)

        payload = analyze_kr_confirmation_gate(
            db_path=db,
            start_date="2026-05-26",
            end_date="2026-05-26",
        )

        assert payload["row_count"] == 5
        assert payload["groups"]["kept_executable"]["rows"] == 1
        assert payload["groups"]["demoted_by_confirmation"]["rows"] == 1
        assert payload["groups"]["demoted_by_evidence_ceiling_with_confirmation_pending"]["rows"] == 1
        assert payload["groups"]["demoted_by_negative_pullback_context"]["rows"] == 1
        assert payload["groups"]["hard_block_after_ready_action"]["rows"] == 1
        assert payload["groups"]["kept_executable"]["ret60"]["avg_pct"] == 1.2
        assert payload["groups"]["demoted_by_confirmation"]["ret60"]["avg_pct"] == -2.0
        assert "kept_executable_ret60_label_n_below_30" in payload["warnings"]


def test_kr_confirmation_gate_markdown_escapes_group_reason_keys() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "candidate_audit.db"
        _init_db(db)

        markdown = to_markdown(analyze_kr_confirmation_gate(db_path=db))

        assert "# KR Confirmation Gate Outcome Review" in markdown
        assert "| demoted_by_confirmation\\|kr_fast_trigger_not_confirmed |" in markdown
        assert "| demoted_by_confirmation|kr_fast_trigger_not_confirmed |" not in markdown
