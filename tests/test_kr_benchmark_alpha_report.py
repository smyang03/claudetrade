from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audit.candidate_audit_store import CandidateAuditStore
from tools.kr_benchmark_alpha_report import build_kr_benchmark_alpha_report


class KrBenchmarkAlphaReportTests(unittest.TestCase):
    def test_builds_board_weighted_alpha_from_candidate_audit_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "candidate_audit.db"
            store = CandidateAuditStore(db_path)
            for ticker, board, pnl in [
                ("AAA", "KOSPI", 1.0),
                ("BBB", "KOSDAQ", -2.0),
                ("CCC", "KOSDAQ", 3.0),
            ]:
                store.upsert_candidate(
                    {
                        "call_id": f"call_{ticker}",
                        "runtime_mode": "live",
                        "market": "KR",
                        "session_date": "2026-05-15",
                        "known_at": "2026-05-15T09:30:00+09:00",
                        "ticker": ticker,
                        "market_type": board,
                    }
                )
                store.update_execution_by_ticker(
                    session_date="2026-05-15",
                    market="KR",
                    runtime_mode="live",
                    ticker=ticker,
                    values={"filled_count": 1, "pnl_pct": pnl},
                )

            def fake_index(_market: str, index: str) -> dict:
                return {
                    "index": index,
                    "change_pct": 1.0 if index == "KOSPI" else -1.0,
                    "source": "kis_index_price",
                    "observed_at": "2026-05-15T15:30:00+09:00",
                }

            summary = build_kr_benchmark_alpha_report(
                db_path=db_path,
                session_date="2026-05-15",
                get_index_snapshot_func=fake_index,
            )

        self.assertEqual(summary["filled_count"], 3)
        self.assertAlmostEqual(summary["strategy_return_pct"], 0.6667)
        self.assertEqual(summary["board_weights"], {"KOSPI": 0.3333, "KOSDAQ": 0.6667})
        self.assertAlmostEqual(summary["benchmark_return_pct"], -0.3333)
        self.assertAlmostEqual(summary["alpha_pct"], 1.0)
        self.assertEqual(summary["indexes"]["KOSPI"]["source"], "kis_index_price")


if __name__ == "__main__":
    unittest.main()
