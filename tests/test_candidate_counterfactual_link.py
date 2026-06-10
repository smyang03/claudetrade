"""candidate_counterfactual_link 매칭 헬퍼 테스트.

audit ↔ counterfactual은 candidate_key 직접 조인이 0건이므로
(session_date, market, ticker) + known_at 근접 매칭이 올바른지 검증한다.
"""
import sqlite3
import unittest

from audit.candidate_counterfactual_link import link_candidate_counterfactual


def _make_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE candidate_counterfactual_paths (
            session_date TEXT, market TEXT, ticker TEXT, known_at TEXT, signal_time TEXT,
            path_name TEXT, actual_path TEXT, status TEXT,
            outcome_30m_pct REAL, outcome_60m_pct REAL, outcome_close_pct REAL,
            max_runup_60m_pct REAL, max_drawdown_60m_pct REAL
        )
        """
    )
    return conn


def _insert(conn, **kw):
    base = {
        "session_date": "2026-06-10", "market": "KR", "ticker": "100090",
        "known_at": "2026-06-10T09:00:00+09:00", "signal_time": "2026-06-10T09:00:00",
        "path_name": "or_break", "actual_path": None, "status": "ok",
        "outcome_30m_pct": None, "outcome_60m_pct": None, "outcome_close_pct": None,
        "max_runup_60m_pct": None, "max_drawdown_60m_pct": None,
    }
    base.update(kw)
    conn.execute(
        "INSERT INTO candidate_counterfactual_paths VALUES "
        "(:session_date,:market,:ticker,:known_at,:signal_time,:path_name,:actual_path,:status,"
        ":outcome_30m_pct,:outcome_60m_pct,:outcome_close_pct,:max_runup_60m_pct,:max_drawdown_60m_pct)",
        base,
    )


class CandidateCounterfactualLinkTests(unittest.TestCase):
    def test_no_paths_returns_unmatched(self):
        conn = _make_db()
        res = link_candidate_counterfactual(conn, "2026-06-10", "KR", "100090", "2026-06-10T09:00:00+09:00")
        self.assertFalse(res["matched"])
        self.assertEqual(res["match_basis"], "none")
        self.assertEqual(res["path_count"], 0)

    def test_known_at_exact_match(self):
        conn = _make_db()
        _insert(conn, known_at="2026-06-10T09:00:00+09:00", path_name="or_break")
        _insert(conn, known_at="2026-06-10T09:00:00+09:00", path_name="vwap_reclaim")
        res = link_candidate_counterfactual(conn, "2026-06-10", "KR", "100090", "2026-06-10T09:00:00+09:00")
        self.assertTrue(res["matched"])
        self.assertEqual(res["match_basis"], "known_at_exact")
        self.assertEqual(res["delta_min"], 0.0)
        self.assertEqual(res["path_count"], 2)

    def test_known_at_nearest_within_tolerance(self):
        conn = _make_db()
        _insert(conn, known_at="2026-06-10T09:10:00+09:00", path_name="or_break")
        res = link_candidate_counterfactual(
            conn, "2026-06-10", "KR", "100090", "2026-06-10T09:00:00+09:00", tolerance_min=20.0
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["match_basis"], "known_at_nearest")
        self.assertEqual(res["delta_min"], 10.0)

    def test_outside_tolerance_falls_back_to_ticker_day(self):
        conn = _make_db()
        _insert(conn, known_at="2026-06-10T13:00:00+09:00", path_name="or_break")
        _insert(conn, known_at="2026-06-10T14:00:00+09:00", path_name="wait_60m")
        res = link_candidate_counterfactual(
            conn, "2026-06-10", "KR", "100090", "2026-06-10T09:00:00+09:00", tolerance_min=20.0
        )
        self.assertTrue(res["matched"])
        self.assertEqual(res["match_basis"], "ticker_day")
        self.assertIsNone(res["nearest_known_at"])
        self.assertEqual(res["path_count"], 2)  # 폴백 시 종목-일 전체 path

    def test_no_known_at_uses_ticker_day(self):
        conn = _make_db()
        _insert(conn, known_at="2026-06-10T09:00:00+09:00", path_name="or_break")
        _insert(conn, known_at="2026-06-10T11:00:00+09:00", path_name="wait_60m")
        res = link_candidate_counterfactual(conn, "2026-06-10", "KR", "100090", None)
        self.assertTrue(res["matched"])
        self.assertEqual(res["match_basis"], "ticker_day")
        self.assertEqual(res["path_count"], 2)

    def test_best_runup_and_actual_path(self):
        conn = _make_db()
        _insert(conn, path_name="or_break", max_runup_60m_pct=3.5, actual_path="or_break")
        _insert(conn, path_name="vwap_reclaim", max_runup_60m_pct=1.2)
        _insert(conn, path_name="no_entry", max_runup_60m_pct=None)
        res = link_candidate_counterfactual(conn, "2026-06-10", "KR", "100090", "2026-06-10T09:00:00+09:00")
        self.assertEqual(res["best_runup_path"], "or_break")
        self.assertEqual(res["best_runup_pct"], 3.5)
        self.assertEqual(res["actual_path"], "or_break")

    def test_us_ticker_case_insensitive(self):
        conn = _make_db()
        _insert(conn, market="US", ticker="AAPL", known_at="2026-06-10T22:35:00+09:00")
        res = link_candidate_counterfactual(conn, "2026-06-10", "US", "aapl", "2026-06-10T22:35:00+09:00")
        self.assertTrue(res["matched"])
        self.assertEqual(res["path_count"], 1)


if __name__ == "__main__":
    unittest.main()
