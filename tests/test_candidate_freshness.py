"""후보 신선도 원장 — 등급/패널티/면제 검증."""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import candidate_freshness as cf


def _make_dbs(tmp: Path, *, sessions, hist, plans):
    """hist: {ticker: [session,...]} / plans: [(ticker, session, status)]"""
    audit_dir = tmp / "data" / "audit"
    audit_dir.mkdir(parents=True)
    audit = sqlite3.connect(audit_dir / "candidate_audit.db")
    audit.execute("CREATE TABLE audit_candidate_rows (market TEXT, session_date TEXT, ticker TEXT, final_prompt_included INTEGER)")
    for t, ds in hist.items():
        for d in ds:
            audit.execute("INSERT INTO audit_candidate_rows VALUES ('US', ?, ?, 1)", (d, t))
    audit.commit(); audit.close()
    ev = sqlite3.connect(tmp / "data" / "v2_event_store.db")
    ev.execute("CREATE TABLE v2_path_runs (market TEXT, runtime_mode TEXT, ticker TEXT, session_date TEXT, status TEXT, plan_json TEXT)")
    for t, d, st in plans:
        ev.execute(
            "INSERT INTO v2_path_runs VALUES ('US', 'live', ?, ?, ?, ?)",
            (t, d, st, '{"pnl_pct": -2.2}' if st == "CLOSED" else "{}"),
        )
    ev.commit(); ev.close()


SESSIONS = [f"2026-06-{d:02d}" for d in (1, 2, 3, 4, 5, 8, 9, 10)]


class FreshnessMapTests(unittest.TestCase):
    def setUp(self):
        cf._CACHE.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        cf._CACHE.clear()
        self.tmp.cleanup()

    def _patch_paths(self):
        def fake_path(*parts, **kwargs):
            return self.root / Path(*parts)
        return patch.object(cf, "get_runtime_path", side_effect=fake_path)

    def test_grades_by_age_and_never_planned(self):
        _make_dbs(self.root, sessions=SESSIONS, hist={
            "ZOMBIE": SESSIONS,            # 8세션 연속, 플랜 전무
            "GOLD": SESSIONS[-4:],         # 4세션 → MATURE
            "FRESH": SESSIONS[-1:],        # 1세션 → NEW
        }, plans=[])
        with self._patch_paths():
            m = cf.get_freshness_map("US", force_refresh=True)
        self.assertEqual(m["ZOMBIE"]["grade"], "OLD")
        self.assertTrue(m["ZOMBIE"]["never_planned"])
        self.assertEqual(m["GOLD"]["grade"], "MATURE")
        self.assertEqual(m["FRESH"]["grade"], "NEW")

    def test_retrade_only_for_traded_plans(self):
        _make_dbs(self.root, sessions=SESSIONS, hist={
            "TRADED": SESSIONS[-2:],
            "NOFILL": SESSIONS[-2:],
        }, plans=[
            ("TRADED", SESSIONS[-2], "CLOSED"),     # 체결됐던 플랜 → 재탕
            ("NOFILL", SESSIONS[-2], "EXPIRED"),    # 미체결 → 무해
        ])
        with self._patch_paths():
            m = cf.get_freshness_map("US", force_refresh=True)
        self.assertTrue(m["TRADED"]["retrade"])
        self.assertFalse(m["NOFILL"]["retrade"])

    def test_active_plan_marks_exempt(self):
        _make_dbs(self.root, sessions=SESSIONS, hist={"HOLD": SESSIONS}, plans=[
            ("HOLD", SESSIONS[-1], "FILLED"),
        ])
        with self._patch_paths():
            m = cf.get_freshness_map("US", force_refresh=True)
        self.assertTrue(m["HOLD"]["exempt_active"])


class AnnotateTests(unittest.TestCase):
    def setUp(self):
        cf._CACHE.clear()

    def tearDown(self):
        cf._CACHE.clear()

    def _with_map(self, fresh_map):
        return patch.object(cf, "get_freshness_map", return_value=fresh_map)

    def test_old_never_planned_gets_combined_penalty(self):
        rows = [{"ticker": "ONDS", "trainer_prompt_score": 70.0}]
        fresh = {"ONDS": {"age_sessions": 9, "grade": "OLD", "never_planned": True, "retrade": False, "exempt_active": False}}
        with self._with_map(fresh), patch.dict(os.environ, {"CANDIDATE_FRESHNESS_ENABLED": "true"}):
            summary = cf.annotate_candidate_freshness(rows, "US")
        self.assertEqual(rows[0]["trainer_prompt_score"], 40.0)  # 70 - 15 - 15
        self.assertEqual(rows[0]["trainer_prompt_score_raw"], 70.0)
        self.assertEqual(summary["penalized"], 1)

    def test_rel_vol_surge_exempts_zombie(self):
        rows = [{"ticker": "ZULU", "trainer_prompt_score": 70.0, "rel_vol_shadow": 3.5}]
        fresh = {"ZULU": {"age_sessions": 8, "grade": "OLD", "never_planned": True, "retrade": False, "exempt_active": False}}
        with self._with_map(fresh), patch.dict(os.environ, {"CANDIDATE_FRESHNESS_ENABLED": "true"}):
            cf.annotate_candidate_freshness(rows, "US")
        self.assertEqual(rows[0]["trainer_prompt_score"], 70.0)
        self.assertEqual(rows[0]["freshness_exempt"], "rel_vol_surge")

    def test_mature_and_new_untouched(self):
        rows = [
            {"ticker": "GOLD", "trainer_prompt_score": 60.0},
            {"ticker": "FRESH", "trainer_prompt_score": 50.0},
        ]
        fresh = {
            "GOLD": {"age_sessions": 4, "grade": "MATURE", "never_planned": True, "retrade": False, "exempt_active": False},
            "FRESH": {"age_sessions": 1, "grade": "NEW", "never_planned": True, "retrade": False, "exempt_active": False},
        }
        with self._with_map(fresh), patch.dict(os.environ, {"CANDIDATE_FRESHNESS_ENABLED": "true"}):
            cf.annotate_candidate_freshness(rows, "US")
        self.assertEqual(rows[0]["trainer_prompt_score"], 60.0)
        self.assertEqual(rows[1]["trainer_prompt_score"], 50.0)

    def test_disabled_flag_no_op(self):
        rows = [{"ticker": "ONDS", "trainer_prompt_score": 70.0}]
        with patch.dict(os.environ, {"CANDIDATE_FRESHNESS_ENABLED": "false"}):
            summary = cf.annotate_candidate_freshness(rows, "US")
        self.assertEqual(rows[0]["trainer_prompt_score"], 70.0)
        self.assertFalse(summary["enabled"])

    def test_empty_map_failsafe_no_penalty(self):
        rows = [{"ticker": "AAA", "trainer_prompt_score": 70.0}]
        with self._with_map({}), patch.dict(os.environ, {"CANDIDATE_FRESHNESS_ENABLED": "true"}):
            cf.annotate_candidate_freshness(rows, "US")
        self.assertEqual(rows[0]["trainer_prompt_score"], 70.0)


class SmartSkipIntegrationTests(unittest.TestCase):
    def test_penalty_changes_semantic_signature(self):
        """패널티가 score_bucket을 바꿔 스마트 스킵 시그니처가 자동 갱신되는지."""
        from runtime import selection_smart_skip as sk

        def sig(score):
            return sk.semantic_signature(
                market="US", session_date="2026-06-11", consensus_mode="NEUTRAL",
                execution_phase="regular",
                candidates=[{"ticker": "ONDS", "trainer_prompt_score": score}],
                prompt_contract="c", watch_cap=15, trade_cap=5,
            )

        self.assertNotEqual(sig(70.0), sig(40.0))  # OLD+플랜전무 강등(-30) → 시그니처 변경
        self.assertEqual(sig(70.0), sig(71.0))     # 같은 5점 버킷 → 불변 (노이즈 무시 유지)


if __name__ == "__main__":
    unittest.main()
