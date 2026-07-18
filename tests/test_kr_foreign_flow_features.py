from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.kr_candidate_features import foreign_flow_features
from bot.kr_investor_flow_cache import (
    effective_flow_source_date,
    load_recent_flow_records,
    save_flow_cache,
)


class ForeignFlowFeaturesTests(unittest.TestCase):
    def test_consecutive_buy_days_and_signal(self) -> None:
        recs = [
            {"date": "2026-05-04", "foreign": -5, "institution": 1},
            {"date": "2026-05-06", "foreign": 3, "institution": -2},
            {"date": "2026-05-07", "foreign": 4, "institution": -1},
            {"date": "2026-05-08", "foreign": 6, "institution": 2},
        ]
        out = foreign_flow_features(recs, today_volume=100)
        self.assertEqual(out["foreign_flow_window_count"], 4)
        self.assertEqual(out["foreign_flow_buy_days_consec"], 3)
        self.assertEqual(out["foreign_flow_sell_days_consec"], 0)
        self.assertEqual(out["foreign_flow_net_qty_5d"], 8.0)  # -5+3+4+6
        self.assertEqual(out["foreign_flow_signal"], "accumulation")
        self.assertAlmostEqual(out["foreign_flow_net_to_volume_1d"], 0.06)  # 6/100
        # 기관은 관측만(신호 계산 제외)
        self.assertEqual(out["foreign_flow_institution_net_qty_5d_obs"], 0.0)  # 1-2-1+2

    def test_sell_run_marks_distribution(self) -> None:
        recs = [
            {"date": "2026-05-06", "foreign": -3},
            {"date": "2026-05-07", "foreign": -4},
            {"date": "2026-05-08", "foreign": -2},
        ]
        out = foreign_flow_features(recs)
        self.assertEqual(out["foreign_flow_sell_days_consec"], 3)
        self.assertEqual(out["foreign_flow_buy_days_consec"], 0)
        self.assertEqual(out["foreign_flow_signal"], "distribution")
        # 거래량 미제공이면 정규화 필드 없음
        self.assertNotIn("foreign_flow_net_to_volume_1d", out)

    def test_mixed_run_net_buy_below_threshold(self) -> None:
        recs = [
            {"date": "2026-05-07", "foreign": -1},
            {"date": "2026-05-08", "foreign": 5},
        ]
        out = foreign_flow_features(recs)
        self.assertEqual(out["foreign_flow_buy_days_consec"], 1)  # 최신만 양수
        self.assertEqual(out["foreign_flow_net_qty_5d"], 4.0)
        self.assertEqual(out["foreign_flow_signal"], "net_buy")  # 연속<3, 5d 양수

    def test_none_breaks_consecutive(self) -> None:
        recs = [
            {"date": "d1", "foreign": 5},
            {"date": "d2", "foreign": None},
            {"date": "d3", "foreign": 4},
        ]
        out = foreign_flow_features(recs)
        self.assertEqual(out["foreign_flow_buy_days_consec"], 1)  # 최신 4>0, 앞이 None → 중단

    def test_empty_records(self) -> None:
        out = foreign_flow_features([])
        self.assertEqual(out["foreign_flow_window_count"], 0)
        self.assertNotIn("foreign_flow_signal", out)


class LoadRecentFlowRecordsTests(unittest.TestCase):
    def test_loads_recent_completed_days_ordered_and_filters_untrusted(self) -> None:
        session = "2026-05-11"
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)

            def fake_runtime_path(_kind: str, name: str) -> Path:
                return tmpdir / name

            with mock.patch(
                "bot.kr_investor_flow_cache.get_runtime_path",
                side_effect=fake_runtime_path,
            ):
                dates = [effective_flow_source_date(session, lag_trading_days=lag) for lag in (1, 2, 3)]
                # 최신일은 untrusted(전량 zero cluster 대체로 flow_values_trusted False) → 제외 확인
                for idx, day in enumerate(dates):
                    trusted = idx != 0  # dates[0]=가장 최신(lag1)만 untrusted 표시
                    cache = {
                        "date": day,
                        "records": {
                            "005930": {
                                "status": "ok",
                                "foreign": (idx + 1) * 10,
                                "institution": -idx,
                                "flow_values_trusted": trusted,
                            }
                        },
                    }
                    save_flow_cache(cache)

                recs = load_recent_flow_records(session, "005930", days=3)
                # untrusted 1건 제외 → 2건
                self.assertEqual(len(recs), 2)
                # 오래된→최신 정렬
                self.assertEqual([r["date"] for r in recs], sorted(dates)[:2])
                for rec in recs:
                    self.assertIsNot(rec.get("flow_values_trusted"), False)


if __name__ == "__main__":
    unittest.main()
