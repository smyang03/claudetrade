from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.reconcile_order_truth import _load_unknown_runs


def _make_run(order_no: str = "", ticker: str = "005930", market: str = "KR") -> dict:
    plan = {}
    if order_no:
        plan["order_no"] = order_no
    return {
        "path_run_id": "run_1",
        "market": market,
        "ticker": ticker,
        "session_date": "2026-05-16",
        "status": "ORDER_UNKNOWN",
        "plan": plan,
    }


def _store_with_runs(runs: list[dict]) -> MagicMock:
    store = MagicMock()
    store.path_runs_for_session.return_value = runs
    return store


class OrderIdFilterTests(unittest.TestCase):
    def test_order_id_skips_rows_without_order_no(self):
        """--order-id 지정 시 run_order가 빈 행은 skip돼야 한다."""
        run_no_order = _make_run(order_no="")
        run_with_order = _make_run(order_no="ORD001")
        store = _store_with_runs([run_no_order, run_with_order])

        rows = _load_unknown_runs(store, date="2026-05-16", market="KR", order_id="ORD001")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_order_id"], "ORD001")

    def test_order_id_skips_mismatched_order_no(self):
        """order_no가 있어도 order_id와 다르면 skip돼야 한다."""
        run_other = _make_run(order_no="ORD999")
        run_match = _make_run(order_no="ORD001")
        store = _store_with_runs([run_other, run_match])

        rows = _load_unknown_runs(store, date="2026-05-16", market="KR", order_id="ORD001")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_order_id"], "ORD001")

    def test_no_order_id_includes_rows_without_order_no(self):
        """order_id 미지정 시 run_order가 빈 행도 포함돼야 한다."""
        run_no_order = _make_run(order_no="")
        run_with_order = _make_run(order_no="ORD001")
        store = _store_with_runs([run_no_order, run_with_order])

        rows = _load_unknown_runs(store, date="2026-05-16", market="KR", order_id="")

        self.assertEqual(len(rows), 2)

    def test_order_id_only_returns_exact_match(self):
        """order_id 지정 시 정확히 일치하는 1건만 반환된다."""
        runs = [
            _make_run(order_no=""),
            _make_run(order_no="ORD999"),
            _make_run(order_no="ORD001"),
        ]
        store = _store_with_runs(runs)

        rows = _load_unknown_runs(store, date="2026-05-16", market="KR", order_id="ORD001")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["_order_id"], "ORD001")


if __name__ == "__main__":
    unittest.main()
