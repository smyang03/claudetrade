"""fast-fill 매수 재호가 후 로컬 매수 pending stale 정리(_remove_pathb_pending_buy_order).

버그: fast-fill이 broker 주문+path_run은 취소하나 로컬 pending_orders는 안 지워
PENDING_ORDER_EXISTS로 재호가 차단 + 유령 미체결 표시(2026-06-23 키스트론 475430).
"""

import types
import unittest

from runtime.pathb_runtime import PathBRuntime


def _rt(pending):
    rt = PathBRuntime.__new__(PathBRuntime)
    rt.bot = types.SimpleNamespace(pending_orders=pending)
    rt._save_pending_orders_if_possible = lambda: None
    return rt


def _buy(ticker, market="KR", order_no="", path_run_id=""):
    return {"ticker": ticker, "market": market, "order_no": order_no,
            "pathb_path_run_id": path_run_id, "strategy": "claude_price"}


class RemovePathbPendingBuyOrderTests(unittest.TestCase):
    def test_removes_buy_pending_by_path_run_id(self):
        rt = _rt([_buy("475430", order_no="0013207900", path_run_id="run-1")])
        n = rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="run-1")
        self.assertEqual(n, 1)
        self.assertEqual(rt.bot.pending_orders, [])

    def test_removes_buy_pending_by_order_no(self):
        rt = _rt([_buy("475430", order_no="0013207900", path_run_id="other")])
        n = rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="", execution_id="0013207900")
        self.assertEqual(n, 1)
        self.assertEqual(rt.bot.pending_orders, [])

    def test_keeps_other_ticker(self):
        keep = _buy("001440", order_no="x", path_run_id="run-2")
        rt = _rt([_buy("475430", path_run_id="run-1"), keep])
        n = rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="run-1")
        self.assertEqual(n, 1)
        self.assertEqual(rt.bot.pending_orders, [keep])

    def test_never_removes_sell_pending(self):
        # 같은 종목/같은 run이라도 매도 pending은 절대 제거 금지.
        sell = {"ticker": "475430", "market": "KR", "side": "sell",
                "pathb_path_run_id": "run-1", "pathb_pending_sell_order_no": "S1"}
        rt = _rt([sell])
        n = rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="run-1")
        self.assertEqual(n, 0)
        self.assertEqual(rt.bot.pending_orders, [sell])

    def test_sell_marker_field_excluded_even_without_side(self):
        sell = {"ticker": "475430", "market": "KR", "pathb_path_run_id": "run-1",
                "pathb_pending_sell_order_no": "S1"}
        rt = _rt([sell])
        self.assertEqual(rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="run-1"), 0)

    def test_no_match_no_change(self):
        rt = _rt([_buy("475430", path_run_id="run-1")])
        n = rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="nope")
        self.assertEqual(n, 0)
        self.assertEqual(len(rt.bot.pending_orders), 1)

    def test_empty_pending_safe(self):
        rt = _rt([])
        self.assertEqual(rt._remove_pathb_pending_buy_order("KR", "475430", path_run_id="run-1"), 0)


if __name__ == "__main__":
    unittest.main()
