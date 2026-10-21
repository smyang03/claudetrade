"""격리 실주문 레인 리뷰 수리(10-05) 회귀 — 주문 예외·미결제 현금·파킹 원장·부분 체결·손실 합산."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cash_parking as cp  # noqa: E402
import kr_buyback_canary as can  # noqa: E402
from live_lane_common import KST, kr_order_window, order_state, place_safely, safe_cash_krw  # noqa: E402


class _Boom:
    def place_order(self, *a, **k):
        raise RuntimeError("HTTP 500 recovery failed")


class _Ok:
    def place_order(self, *a, **k):
        return {"success": True, "order_no": "1"}


def test_place_safely_turns_exception_into_unknown():
    r = place_safely(_Boom(), "X", 1, 0, "buy", None, market="KR")
    assert order_state(r) == "unknown" and r["msg"].startswith("unknown:")
    assert order_state(place_safely(_Ok(), "X", 1, 0, "buy", None)) == "ok"
    assert order_state({"success": False}) == "rejected"


def test_safe_cash_uses_settlement_minimum():
    assert safe_cash_krw({"orderable_cash": 979_270, "d1_settlement_krw": 885_467, "d2_settlement_krw": 885_467}) == 885_467
    assert safe_cash_krw({"orderable_cash": 1000}) == 1000
    assert safe_cash_krw({}) == 0


def test_parking_holdings_come_from_fill_sum():
    rows = [{"kind": "fill", "ticker": "SGOV", "side": "buy", "filled_qty": 100, "order_no": "a", "final": True},
            {"kind": "fill", "ticker": "SGOV", "side": "buy", "filled_qty": 10, "order_no": "b", "final": True},
            {"kind": "fill", "ticker": "SGOV", "side": "sell", "filled_qty": 30, "order_no": "c", "final": True}]
    assert cp.held_from_fills(rows, "SGOV") == 80


def test_parking_pending_order_blocks_new_order():
    rows = [{"kind": "order", "ticker": "SGOV", "order_no": "a", "success": True, "state": "ok"}]
    assert cp.pending_orders(rows, "SGOV")
    rows.append({"kind": "fill", "ticker": "SGOV", "order_no": "a", "filled_qty": 5, "final": True})
    assert not cp.pending_orders(rows, "SGOV")
    assert not cp.pending_orders([{"kind": "order", "ticker": "SGOV", "order_no": "", "success": False,
                                   "state": "rejected"}], "SGOV")


def test_canary_realized_pnl_counts_every_close_even_after_rebuy():
    rows = [{"kind": "position", "ticker": "A", "status": "CLOSED", "pnl_krw": -30_000},
            {"kind": "position", "ticker": "A", "status": "OPEN"},                    # 같은 종목 재매수
            {"kind": "close", "ticker": "B", "partial": True, "pnl_krw": -5_000}]
    assert can._realized_pnl(rows) == -35_000


def test_kr_order_window_blocks_holiday_and_off_hours():
    from datetime import datetime
    at = lambda m, d, h, mi: datetime(2026, m, d, h, mi, tzinfo=KST)  # noqa: E731
    assert kr_order_window("exit", at(10, 12, 8, 42))[0]
    assert kr_order_window("exit", at(10, 12, 11, 0))[0]                 # 시가 놓쳐도 장중 청산 허용
    assert not kr_order_window("exit", at(10, 12, 20, 11))[0]            # 10-12 늦은 실행 실사고
    assert not kr_order_window("exit", at(10, 9, 8, 42))[0]              # 한글날 휴장
    assert not kr_order_window("exit", at(10, 10, 8, 42))[0]             # 토요일
    assert kr_order_window("open_auction", at(10, 13, 8, 40))[0]
    assert not kr_order_window("open_auction", at(10, 13, 9, 30))[0]     # 시가 진입 계약 — 장중 추격 금지
    assert kr_order_window("close_auction", at(10, 13, 15, 20))[0]
    assert not kr_order_window("close_auction", at(10, 13, 15, 30))[0]
