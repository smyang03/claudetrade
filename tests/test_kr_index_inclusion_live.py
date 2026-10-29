# -*- coding: utf-8 -*-
"""KR 지수 정기변경 편입 실전 레인(10-30) — 배분·달력·멱등·청산 대상. 네트워크·주문 없음."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_index_inclusion_live as L  # noqa: E402
import risk_manager  # noqa: E402


def test_isolation_registered():
    assert "kr_index_inclusion" in risk_manager.ISOLATED_STRATEGY_SOURCES
    assert risk_manager.KR_INDEX_INCLUSION_SOURCE == L.SOURCE


def test_allocate_max_inclusion_then_equal_weight_top_up():
    plan = L.allocate(500_000, {"A": 10_000, "B": 20_000, "C": 400_000, "D": 1_200_000})
    # 싼 순 1주씩: A·B·C(합 43만) 포함, D 제외 → 몫 16.7만 목표: A 16주, B 8주까지(예산 안)
    assert set(plan) == {"A", "B", "C"} and plan["C"] == 1
    assert sum(q * p for q, p in ((plan["A"], 10_000), (plan["B"], 20_000), (plan["C"], 400_000))) <= 500_000
    assert plan["A"] >= 2 and plan["B"] >= 2
    assert L.allocate(500_000, {"X": 600_000}) == {}
    assert L.allocate(500_000, {}) == {}
    many = {f"T{i}": 15_000 + i * 500 for i in range(20)}          # 1주 합계 39.5만 — 20종목 모두 들어간다
    p2 = L.allocate(500_000, many)
    assert len(p2) == 20 and sum(q * many[t] for t, q in p2.items()) <= 500_000
    tight = {f"T{i}": 20_000 + i * 1_000 for i in range(20)}             # 1주 합계 59만 → 싼 17종목
    assert len(L.allocate(500_000, tight)) == 17


def test_calendar_entry_after_announce_exit_before_effective():
    assert L.entry_session(date(2026, 5, 22)) == date(2026, 5, 26)      # 05-25 휴장(대체공휴일)
    assert L.exit_session(date(2026, 6, 12)) == date(2026, 6, 11)
    assert L.entry_session(date(2026, 10, 2)) == date(2026, 10, 6)       # 10-05 휴장


def _csv(tmp_path):
    p = tmp_path / "chg.csv"
    p.write_text("index,announce_date,effective_date,ticker,name,action,source_url\n"
                 "KOSDAQ150,2026-11-20,2026-12-11,000001,가,add,u\n"
                 "KOSPI200,2026-11-20,2026-12-11,000002,나,add,u\n"
                 "KOSPI200,2026-11-20,2026-12-11,000003,다,delete,u\n", encoding="utf-8")
    return p


def test_load_rounds_adds_only_and_index_filter(tmp_path):
    p = _csv(tmp_path)
    r = L.load_rounds(p, {"KOSPI200", "KOSDAQ150"})
    assert list(r) == ["2026-11-20"] and [x["ticker"] for x in r["2026-11-20"]["adds"]] == ["000001", "000002"]
    assert [x["ticker"] for x in L.load_rounds(p, {"KOSDAQ150"})["2026-11-20"]["adds"]] == ["000001"]


def test_season_warning():
    assert L.season_warning(date(2026, 11, 24), {}) is not None
    assert L.season_warning(date(2026, 11, 24), {"2026-11-20": {}}) is None
    assert L.season_warning(date(2026, 10, 30), {}) is None


def test_buy_dry_plans_due_round_and_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(L, "CHANGES", _csv(tmp_path))
    led = tmp_path / "led.jsonl"
    monkeypatch.setattr(L, "LEDGER", led)
    monkeypatch.setattr(L, "_today", lambda: date(2026, 11, 23))
    monkeypatch.setattr(L, "_env_gate", lambda: (True, {"round_krw": 500_000, "loss_limit": 100_000,
                                                       "indexes": {"KOSPI200", "KOSDAQ150"}, "mismatch": {}, "enabled_flag": "true"}))
    monkeypatch.setattr(L, "_prev_close", lambda t, d: {"000001": 12_000.0, "000002": 300_000.0}[t])
    assert L.cmd_buy(True) == 0
    out = capsys.readouterr().out
    assert "편입 2 · 매수 2 · 예산상 제외 0" in out and "000002 x1" in out and "청산 2026-12-10" in out
    led.write_text(json.dumps({"kind": "order", "side": "buy", "round": "2026-11-20", "ticker": "000001"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(L, "_bot_has_isolation", lambda: (True, ""))
    monkeypatch.setattr(L, "kr_order_window", lambda kind: (True, ""))
    assert L.cmd_buy(False) == 0 and "이미 있음" in capsys.readouterr().out


def test_due_sells_today_or_overdue_only():
    rows = [{"kind": "position", "ticker": "A", "status": "OPEN", "filled_qty": 3, "exit_session": "2026-12-10", "round": "r"},
            {"kind": "position", "ticker": "B", "status": "OPEN", "filled_qty": 2, "exit_session": "2026-12-11", "round": "r"},
            {"kind": "position", "ticker": "C", "status": "OPEN", "filled_qty": 0, "exit_session": "2026-12-01", "round": "r"},
            {"kind": "position", "ticker": "D", "status": "CLOSED", "filled_qty": 0, "exit_session": "2026-12-01", "round": "r"}]
    assert [p["ticker"] for p in L.due_sells(rows, date(2026, 12, 10))] == ["A"]
    assert [p["ticker"] for p in L.due_sells(rows, date(2026, 12, 14))] == ["A", "B"]


def test_close_pnl_includes_fee_and_tax():
    assert L.close_pnl(10_000, 11_000, 10) == round(10_000 - 21_000 * 10 * 0.00015 - 110_000 * 0.002)
