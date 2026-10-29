"""대기 현금 파킹 — 수량 계산·호가·장 시간·봇 격리 (네트워크·주문 없음)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import cash_parking as c  # noqa: E402
import risk_manager  # noqa: E402


def test_plan_qty_keeps_buffer():
    assert c.plan_qty(979_270, 400_000, 110_720) == 5
    assert c.plan_qty(300_000, 400_000, 110_720) == 0
    assert c.plan_qty(2_300, 900, 100.6) == 13
    assert c.plan_qty(1_000, 900, 0) == 0


def test_kr_limit_price_one_tick_above():
    assert c.kr_limit_price(110_715) == 110_720
    assert c.kr_limit_price(110_720) == 110_725


def test_market_open_windows():
    assert c.market_open("KR", datetime(2026, 10, 5, 1, 0, tzinfo=timezone.utc))          # 10:00 KST 월
    assert not c.market_open("KR", datetime(2026, 10, 5, 7, 0, tzinfo=timezone.utc))      # 16:00 KST
    assert not c.market_open("KR", datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc))      # 일요일
    assert c.market_open("US", datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc))         # 11:00 EDT
    assert not c.market_open("US", datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc))     # 09:00 EDT 개장 전


def test_bot_isolation_contract(tmp_path):
    assert c.SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES
    led = tmp_path / "l.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "order", "ticker": "SGOV", "order_no": "9", "side": "buy"},
        {"kind": "position", "ticker": "SGOV", "status": "OPEN"},
        {"kind": "position", "ticker": "423160", "status": "OPEN"}]), encoding="utf-8")
    m = risk_manager.cash_parking_manifest(led)
    assert m["open_tickers"] == frozenset({"SGOV", "423160"}) and m["order_nos"] == frozenset({"9"})


def test_isolated_exit_owner_never_sells_parking():
    rm = risk_manager.RiskManager.__new__(risk_manager.RiskManager)
    handled, cand = rm._isolated_strategy_exit_candidate({"source_strategy": c.SOURCE, "entry": 100, "current_price": 50})
    assert handled is True and cand is None


def test_rebalance_decision_parks_excess_unparks_deficit_with_hysteresis():
    import cash_parking as cp
    assert cp.rebalance_decision(1708.0, 600.0, 100.7, 6) == ("park", 11)        # 10-30 실측 현금·SGOV 단가
    assert cp.rebalance_decision(650.0, 600.0, 100.7, 6) == ("hold", 0)          # 한 단위 미만 초과
    assert cp.rebalance_decision(500.0, 600.0, 100.7, 6) == ("hold", 0)          # 80% 위 → 왕복 방지
    assert cp.rebalance_decision(300.0, 600.0, 100.7, 6) == ("unpark", 3)        # ceil(300/100.7)
    assert cp.rebalance_decision(0.0, 600.0, 100.7, 2) == ("unpark", 2)          # 보유 상한
    assert cp.rebalance_decision(0.0, 600.0, 100.7, 0) == ("hold", 0)
    assert cp.rebalance_decision(10.0, 600.0, 0.0, 3) == ("hold", 0)
