from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.analyze_kr_promotion_candidates import (
    Thresholds,
    analyze_kr_promotion_candidates,
    classify_metrics,
    forward_return,
    metrics_from_values,
)


class AnalyzeKrPromotionCandidatesTests(unittest.TestCase):
    def test_forward_return_uses_entry_price_as_basis(self) -> None:
        self.assertAlmostEqual(forward_return(10.0, 21.0), 10.0)
        self.assertAlmostEqual(forward_return(-5.0, 4.5), 10.0)

    def test_classify_metrics_separates_live_probe_shadow_and_block(self) -> None:
        thresholds = Thresholds(min_n=3, min_days=2, max_top_day_share=0.80)

        live = metrics_from_values([0.5, 0.4, -0.1, 0.8], ["d1", "d1", "d2", "d2"])
        self.assertEqual(classify_metrics(live, thresholds)["verdict"], "LIVE_READY")

        probe = metrics_from_values([0.2, 0.2, -0.1, 0.2], ["d1", "d1", "d2", "d2"])
        self.assertEqual(classify_metrics(probe, thresholds)["verdict"], "PROBE_READY")

        shadow = metrics_from_values([0.2, 0.2], ["d1", "d2"])
        self.assertEqual(classify_metrics(shadow, thresholds)["verdict"], "SHADOW_ONLY")

        blocked = metrics_from_values([-0.2, 0.1, -0.1], ["d1", "d2", "d2"])
        self.assertEqual(classify_metrics(blocked, thresholds)["verdict"], "BLOCK")

    def test_analyze_promotion_candidates_uses_local_sources_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            ml_db = base / "decisions.db"
            audit_db = base / "candidate_audit.db"
            state_dir = base / "state"
            state_dir.mkdir()
            self._make_ml_db(ml_db)
            self._make_audit_db(audit_db)
            self._make_preopen_state(state_dir)

            payload = analyze_kr_promotion_candidates(
                market="KR",
                runtime_mode="live",
                ml_db=ml_db,
                audit_db=audit_db,
                state_dir=state_dir,
                thresholds=Thresholds(min_n=3, min_days=2, max_top_day_share=0.60),
            )

        self.assertTrue(payload["read_only"])
        self.assertTrue(payload["truth_contract"]["no_broker_api"])
        self.assertEqual(payload["summary"]["should_enable_live"], True)
        live_names = {item["name"] for item in payload["top_live_ready"]}
        blocked_names = {item["name"] for item in payload["top_blocked"]}
        self.assertIn("preopen:d60_ret60_ge_3_top10|fwd_to_120", live_names)
        self.assertIn("closed:strategy=momentum", blocked_names)

    def _make_ml_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE v2_learning_performance (
                    market TEXT,
                    runtime_mode TEXT,
                    session_date TEXT,
                    ticker TEXT,
                    status TEXT,
                    route TEXT,
                    path_type TEXT,
                    strategy TEXT,
                    origin_action TEXT,
                    filled INTEGER,
                    closed INTEGER,
                    pnl_pct REAL,
                    mfe_pct REAL,
                    mae_pct REAL,
                    close_reason TEXT
                )
                """
            )
            conn.executemany(
                """
                INSERT INTO v2_learning_performance (
                    market, runtime_mode, session_date, ticker, status, route,
                    path_type, strategy, origin_action, filled, closed,
                    pnl_pct, mfe_pct, mae_pct, close_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    ("KR", "live", "2026-05-01", "000001", "closed", "plan_a", "plan_a", "momentum", "BUY_READY", 1, 1, -1.0, 1.0, -2.0, "LOSS_CAP"),
                    ("KR", "live", "2026-05-02", "000002", "closed", "plan_a", "plan_a", "momentum", "BUY_READY", 1, 1, -0.5, 0.5, -1.5, "LOSS_CAP"),
                    ("KR", "live", "2026-05-03", "000003", "closed", "path_b", "claude_price", "claude_price", "PULLBACK_WAIT", 1, 1, 0.8, 2.0, -0.4, "TARGET"),
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def _make_audit_db(self, path: Path) -> None:
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                CREATE TABLE audit_candidate_rows (
                    candidate_key TEXT,
                    runtime_mode TEXT,
                    market TEXT,
                    session_date TEXT,
                    known_at TEXT,
                    ticker TEXT,
                    evidence_action_ceiling TEXT,
                    recommended_strategy TEXT,
                    claude_action TEXT,
                    claude_trade_ready INTEGER,
                    route_original_action TEXT,
                    route_final_action TEXT,
                    route_reason TEXT,
                    route_runtime_gate_reason TEXT,
                    trainer_candidate_state TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE audit_candidate_outcomes (
                    candidate_key TEXT,
                    horizon_min INTEGER,
                    return_pct REAL,
                    max_runup_pct REAL,
                    max_drawdown_pct REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE candidate_counterfactual_paths (
                    runtime_mode TEXT,
                    session_date TEXT,
                    market TEXT,
                    ticker TEXT,
                    candidate_key TEXT,
                    path_name TEXT,
                    status TEXT,
                    outcome_30m_pct REAL,
                    outcome_60m_pct REAL,
                    outcome_close_pct REAL
                )
                """
            )
            for idx, session_date in enumerate(["2026-05-01", "2026-05-02", "2026-05-03"], start=1):
                key = f"k{idx}"
                conn.execute(
                    """
                    INSERT INTO audit_candidate_rows (
                        candidate_key, runtime_mode, market, session_date, known_at,
                        ticker, evidence_action_ceiling, recommended_strategy,
                        claude_action, claude_trade_ready, route_original_action,
                        route_final_action, route_reason, route_runtime_gate_reason,
                        trainer_candidate_state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        key,
                        "live",
                        "KR",
                        session_date,
                        f"{session_date}T10:00:00",
                        f"00{idx}",
                        "BUY_READY",
                        "opening_range_pullback",
                        "WATCH",
                        0,
                        "BUY_READY",
                        "BUY_READY",
                        "",
                        "",
                        "PLAN_A",
                    ),
                )
                conn.executemany(
                    """
                    INSERT INTO audit_candidate_outcomes (
                        candidate_key, horizon_min, return_pct, max_runup_pct,
                        max_drawdown_pct
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (key, 30, 0.2, 1.0, -0.1),
                        (key, 60, 0.5, 1.5, -0.2),
                    ],
                )
                conn.execute(
                    """
                    INSERT INTO candidate_counterfactual_paths (
                        runtime_mode, session_date, market, ticker, candidate_key,
                        path_name, status, outcome_30m_pct, outcome_60m_pct,
                        outcome_close_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("live", session_date, "KR", f"00{idx}", key, "immediate", "TRIGGERED", -0.5, -0.8, -1.0),
                )
            conn.commit()
        finally:
            conn.close()

    def _make_preopen_state(self, state_dir: Path) -> None:
        payload = {
            "market": "KR",
            "session_date": "2026-05-01",
            "candidates": [
                self._preopen_candidate("100001", "2026-05-01", 3.0, 7.0),
                self._preopen_candidate("100002", "2026-05-01", 4.0, 8.0),
            ],
        }
        (state_dir / "preopen_KR_20260501.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )
        payload["session_date"] = "2026-05-02"
        payload["candidates"] = [
            self._preopen_candidate("200001", "2026-05-02", 3.5, 7.5),
            self._preopen_candidate("200002", "2026-05-02", 4.5, 8.5),
        ]
        (state_dir / "preopen_KR_20260502.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )

    def _preopen_candidate(
        self,
        ticker: str,
        session_date: str,
        ret60: float,
        ret120: float,
    ) -> dict[str, object]:
        return {
            "ticker": ticker,
            "market": "KR",
            "session_date": session_date,
            "outcome_samples": [
                {"offset_min": 5, "return_pct": 0.0},
                {"offset_min": 30, "return_pct": 1.0},
                {"offset_min": 60, "return_pct": ret60},
                {"offset_min": 120, "return_pct": ret120},
            ],
        }


if __name__ == "__main__":
    unittest.main()
