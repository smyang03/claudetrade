"""KR 패닉 ETF 소액 실전 — 신호 읽기·수량·손익·격리 (네트워크·주문 없음)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_panic_etf_micro as m  # noqa: E402
import risk_manager  # noqa: E402


def _sess(day, sig=True, holiday=False, stale=False, px=6980):
    return {"kind": "session", "session_date": day, "signal_1519": sig, "holiday": holiday, "stale_1519": stale,
            "etf_1519": {"233740": px}, "reason_1519": "gap"}


def test_today_signal_reads_units_and_guards():
    rows = [_sess("2026-10-06"), {"kind": "trade", "session_date": "2026-10-06", "ticker": "233740", "units": 2}]
    s = m.today_signal(rows, "2026-10-06")
    assert s == {"units": 2, "price": 6980.0, "reason": "gap"}
    assert m.today_signal([_sess("2026-10-06", sig=False)], "2026-10-06") is None
    assert m.today_signal([_sess("2026-10-06", holiday=True)], "2026-10-06") is None
    assert m.today_signal([_sess("2026-10-06", stale=True)], "2026-10-06") is None
    assert m.today_signal([_sess("2026-10-05")], "2026-10-06") is None


def test_plan_qty_caps_units_at_two():
    assert m.plan_qty(100_000, 1, 6980) == 14
    assert m.plan_qty(100_000, 2, 6980) == 28
    assert m.plan_qty(100_000, 5, 6980) == 28
    assert m.plan_qty(100_000, 1, 0) == 0


def test_close_pnl_taxes_gains_only():
    assert m.close_pnl(7000, 7100, 10) == round(1000 - (7000 + 7100) * 10 * m.FEE_RATE - 1000 * m.TAX_RATE)
    assert m.close_pnl(7000, 6900, 10) == round(-1000 - (7000 + 6900) * 10 * m.FEE_RATE)


def test_realized_pnl_sums_closes():
    assert m.realized_pnl([{"kind": "close", "pnl_krw": -30_000}, {"kind": "close", "pnl_krw": 5_000}, {"kind": "fill"}]) == -25_000


def test_bot_isolation_contract(tmp_path):
    assert m.SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES
    led = tmp_path / "l.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in [{"kind": "order", "ticker": "233740", "order_no": "7", "side": "buy"},
                                                     {"kind": "position", "ticker": "233740", "status": "OPEN"}]), encoding="utf-8")
    man = risk_manager.kr_panic_etf_micro_manifest(led)
    assert man["open_tickers"] == frozenset({"233740"}) and man["order_nos"] == frozenset({"7"})
    rm = risk_manager.RiskManager.__new__(risk_manager.RiskManager)
    assert rm._isolated_strategy_exit_candidate({"source_strategy": m.SOURCE, "entry": 100, "current_price": 50}) == (True, None)


def test_configurable_instrument_and_tax_only_on_leveraged():
    rows = [{"kind": "session", "session_date": "2026-10-06", "signal_1519": True, "holiday": False, "stale_1519": False,
             "etf_1519": {"233740": 6980, "229200": 14150}, "reason_1519": "gap"},
            {"kind": "trade", "session_date": "2026-10-06", "ticker": "229200", "units": 1}]
    assert m.today_signal(rows, "2026-10-06", "229200")["price"] == 14150.0
    assert m.close_pnl(14000, 14100, 10, "229200") == round(1000 - (14000 + 14100) * 10 * m.FEE_RATE)   # 비과세
    assert m.close_pnl(7000, 7100, 10, "233740") < m.close_pnl(7000, 7100, 10, "229200")
