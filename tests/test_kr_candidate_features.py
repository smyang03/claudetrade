from __future__ import annotations

import unittest

import pandas as pd

from bot.kr_candidate_features import (
    build_kr_candidate_features,
    enrich_kr_candidate_with_features,
    rolling_flow_features,
)


def _frame(days: int, *, start: float = 100.0, step: float = 1.0, volume: int = 1_000_000) -> pd.DataFrame:
    rows = []
    for idx in range(days):
        close = start + idx * step
        rows.append(
            {
                "date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=idx),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": volume + idx * 10_000,
            }
        )
    return pd.DataFrame(rows)


class KrCandidateFeaturesTests(unittest.TestCase):
    def test_build_features_from_ohlcv_and_index_history(self) -> None:
        stock = _frame(80, start=100.0, step=1.2, volume=1_500_000)
        index = _frame(80, start=100.0, step=0.25, volume=1)

        features = build_kr_candidate_features(
            {"ticker": "005930", "price": 195.0, "volume": 2_700_000},
            stock,
            index_ohlcv=index,
            flow={"foreign": 1000, "institution": -200},
        )

        self.assertGreater(features["ret_20d_pct"], 0)
        self.assertGreater(features["rs_20d_vs_board"], 0)
        self.assertGreater(features["avg_turnover_20d"], 0)
        self.assertGreater(features["turnover_vs_20d"], 0)
        self.assertEqual(features["foreign_net_qty_1d"], 1000)
        self.assertEqual(features["institution_net_qty_1d"], -200)
        self.assertIn(features["candidate_quality_grade"], {"A", "B", "C"})
        self.assertGreater(features["candidate_quality_score"], 40)

    def test_flow_quality_metadata_is_propagated_to_candidate_features(self) -> None:
        features = build_kr_candidate_features(
            {"ticker": "005930", "price": 195.0, "volume": 2_700_000},
            _frame(80),
            flow={
                "foreign": 0,
                "institution": 0,
                "individual": 0,
                "flow_data_quality": "bad_zero_flow_cluster",
                "flow_quality_flags": ["kr_investor_flow_all_zero_cluster"],
                "flow_source_date": "2026-05-29",
                "requested_session_date": "2026-06-01",
                "flow_age_trading_days": 1,
            },
        )

        self.assertEqual(features["flow_data_quality"], "bad_zero_flow_cluster")
        self.assertEqual(features["investor_flow_quality"], "bad_zero_flow_cluster")
        self.assertEqual(features["flow_source_date"], "2026-05-29")
        self.assertEqual(features["requested_session_date"], "2026-06-01")
        self.assertEqual(features["flow_age_trading_days"], 1)
        self.assertIn("kr_investor_flow_all_zero_cluster", features["flow_quality_flags"])
        self.assertIn("flow_invalid_all_zero_cluster", features["quality_data_gaps"])
        self.assertIn("flow_missing", features["quality_data_gaps"])
        self.assertFalse(features["flow_values_trusted"])
        self.assertNotIn("foreign_net_qty_1d", features)
        self.assertNotIn("institution_net_qty_1d", features)
        self.assertEqual(features["candidate_quality_components"]["flow_support"], 45.0)
        self.assertIn("flow_missing", features["candidate_quality_flags"])

    def test_short_history_marks_gaps_without_zero_placeholders(self) -> None:
        features = build_kr_candidate_features({"ticker": "123456"}, _frame(10))

        self.assertIn("ret_20d_pct_missing", features["quality_data_gaps"])
        self.assertNotIn("ret_20d_pct", features)
        self.assertIn("candidate_quality_score", features)

    def test_existing_quality_gaps_are_preserved(self) -> None:
        features = build_kr_candidate_features(
            {"ticker": "123456", "quality_data_gaps": ["index_history_cache_error"]},
            _frame(10),
        )

        self.assertIn("index_history_cache_error", features["quality_data_gaps"])
        self.assertIn("ret_20d_pct_missing", features["quality_data_gaps"])

    def test_enrich_preserves_candidate_fields(self) -> None:
        enriched = enrich_kr_candidate_with_features(
            {"ticker": "005930", "name": "Samsung", "price": 150.0, "volume": 1_000_000},
            _frame(70),
        )

        self.assertEqual(enriched["ticker"], "005930")
        self.assertEqual(enriched["name"], "Samsung")
        self.assertIn("candidate_quality_score", enriched)

    def test_rolling_flow_features_ignores_missing_values(self) -> None:
        features = rolling_flow_features(
            [
                {"foreign": 10, "institution": -1},
                {"foreign": None, "institution": 2},
                {"foreign": 5, "institution": ""},
            ]
        )

        self.assertEqual(features["flow_window_5d_count"], 3)
        self.assertEqual(features["foreign_net_qty_5d"], 15)
        self.assertEqual(features["institution_net_qty_5d"], 1)

    def test_rolling_flow_features_sorts_dated_records(self) -> None:
        features = rolling_flow_features(
            [
                {"date": "2026-05-06", "foreign": 6, "institution": 6},
                {"date": "2026-05-01", "foreign": 1, "institution": 1},
                {"date": "2026-05-04", "foreign": 4, "institution": 4},
                {"date": "2026-05-03", "foreign": 3, "institution": 3},
                {"date": "2026-05-05", "foreign": 5, "institution": 5},
                {"date": "2026-05-02", "foreign": 2, "institution": 2},
            ]
        )

        self.assertEqual(features["flow_window_5d_count"], 5)
        self.assertEqual(features["foreign_net_qty_5d"], 20)
        self.assertEqual(features["institution_net_qty_5d"], 20)


if __name__ == "__main__":
    unittest.main()
