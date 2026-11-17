# -*- coding: utf-8 -*-
"""레인 실행 수율 점검(11-17) — 신호→주문→체결 누수만 골라내고, 계약대로 건너뛴 건은 누수로 치지 않는다.
canary·L7 건너뜀 원장 기록도 함께 검증. 네트워크·주문 없음."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import lane_yield as Y  # noqa: E402

TODAY = date(2026, 11, 17)


def _sig(session, ticker, dvol=1e7):
    return {"kind": "signal", "session": session, "ticker": ticker, "selected_I1": True, "dvol20": dvol}


def test_t1_no_record_only_when_session_has_neither_order_nor_skip():
    shadow = [_sig("2026-11-13", "YSS"), _sig("2026-11-14", "AAA"), _sig("2026-11-14", "ILLQ", 1e6),
              _sig("2026-11-15", "BBB"), _sig("2026-11-17", "TODAY")]
    live = [{"kind": "order", "session": "2026-11-13", "ticker": "YSS", "side": "buy", "success": True, "order_no": "1"},
            {"kind": "fill", "order_no": "1", "filled_qty": 15},
            {"kind": "skip", "session": "2026-11-15", "reason": "slots_full"}]
    leaks = Y.t1_leaks(shadow, live, TODAY)
    assert [(x["kind"], x["session"]) for x in leaks] == [("no_record", "2026-11-14")]   # 비유동·오늘·건너뜀은 제외
    assert leaks[0]["ticker"] == "AAA"


def test_fill_checks_flag_missing_reconcile_and_zero_fill_with_reason():
    live = [{"kind": "order", "session": "2026-11-13", "ticker": "YSS", "side": "buy", "success": True, "order_no": "1"},
            {"kind": "fill", "order_no": "1", "filled_qty": 0},
            {"kind": "position", "ticker": "YSS", "status": "CLOSED", "entry_session": "2026-11-13",
             "close_reason": "loo_rejected", "reject_reason": "LimitPrice 20% thru nbbo"},
            {"kind": "order", "session": "2026-11-14", "ticker": "ZZZ", "side": "buy", "success": True, "order_no": "2"},
            {"kind": "order", "session": "2026-11-14", "ticker": "REJ", "side": "buy", "success": False, "order_no": ""}]
    got = {(x["kind"], x["ticker"]): x["detail"] for x in Y._fill_checks("T1", live, TODAY)}
    assert "20% thru nbbo" in got[("not_filled", "YSS")]
    assert ("no_fill", "ZZZ") in got and ("no_fill", "REJ") not in got                # 접수 실패는 대사 대상 아님


def test_canary_signal_without_order_or_skip_is_a_leak():
    shadow = [{"key": "a", "qualifies": True, "rule": "L1_lowliq", "stock_code": "137310", "entry_date": "2026-11-11",
               "corp_name": "에스디"},
              {"key": "b", "qualifies": True, "rule": "Y1", "stock_code": "002900", "entry_date": "2026-11-12"},
              {"key": "c", "qualifies": True, "rule": "Y1", "stock_code": "005930", "entry_date": "2026-11-13"},
              {"key": "d", "qualifies": True, "rule": "OTHER", "stock_code": "000001", "entry_date": "2026-11-13"}]
    live = [{"kind": "skip", "session": "2026-11-12", "ticker": "002900", "reason": "슬롯 가득"},
            {"kind": "skip", "session": "2026-11-13", "reason": "누적 실현손실 −5만 — 신규 매수 중단"}]
    leaks = Y.canary_leaks(shadow, live, TODAY)
    assert [x["ticker"] for x in leaks] == ["137310"]


def test_l7_missing_tickers_in_due_round():
    rounds = {"2026-11-06": {"effective": "2026-12-11", "adds": [{"ticker": "000001"}, {"ticker": "000002"}, {"ticker": "000003"}]},
              "2026-05-22": {"effective": "2026-06-12", "adds": [{"ticker": "999999"}]}}
    entry = lambda a: date(2026, 11, 12) if a.isoformat() == "2026-11-06" else date(2026, 5, 26)   # noqa: E731
    live = [{"kind": "order", "round": "2026-11-06", "ticker": "000001", "session": "2026-11-12", "side": "buy",
             "success": True, "order_no": "9"},
            {"kind": "fill", "order_no": "9", "filled_qty": 3},
            {"kind": "skip", "round": "2026-11-06", "ticker": "000002", "reason": "cash_short"}]
    leaks = Y.l7_leaks(rounds, entry, live, TODAY)
    assert len(leaks) == 1 and leaks[0]["ticker"] == "000003"
    live.append({"kind": "skip", "round": "2026-11-06", "reason": "late_run"})          # 회차 전체 건너뜀 → 누수 아님
    assert Y.l7_leaks(rounds, entry, live, TODAY) == []


def test_lines_format():
    assert "누수 없음" in Y.lines([])[0]
    out = Y.lines([{"lane": "T1", "kind": "no_record", "session": "2026-11-14", "ticker": "AAA", "detail": "x"}])
    assert "누수 1건" in out[0] and "T1 2026-11-14 AAA" in out[1]


def test_canary_skip_rows_from_plan_notes():
    import kr_buyback_canary as c
    rows = c.skip_rows(["137310: 슬롯 가득", "누적 실현손실 −50,000원 ≤ −50,000 — 신규 매수 중단"], {"137310"}, "2026-11-11")
    assert rows[0]["ticker"] == "137310" and rows[0]["reason"] == "슬롯 가득" and rows[0]["kind"] == "skip"
    assert "ticker" not in rows[1] and "신규 매수 중단" in rows[1]["reason"]


def test_l7_buy_logs_budget_and_cash_skips(tmp_path, monkeypatch):
    import kr_index_inclusion_live as L
    p = tmp_path / "chg.csv"
    p.write_text("index,announce_date,effective_date,ticker,name,action,source_url\n"
                 "KOSDAQ150,2026-11-20,2026-12-11,000001,가,add,u\n"
                 "KOSDAQ150,2026-11-20,2026-12-11,000002,나,add,u\n"
                 "KOSDAQ150,2026-11-20,2026-12-11,000003,다,add,u\n", encoding="utf-8")
    led = tmp_path / "led.jsonl"
    monkeypatch.setattr(L, "CHANGES", p)
    monkeypatch.setattr(L, "LEDGER", led)
    monkeypatch.setattr(L, "_today", lambda: date(2026, 11, 23))
    monkeypatch.setattr(L, "_env_gate", lambda: (True, {"round_krw": 500_000, "loss_limit": 100_000,
                                                       "indexes": {"KOSDAQ150"}, "mismatch": {}, "enabled_flag": "true"}))
    monkeypatch.setattr(L, "_prev_close", lambda t, d: {"000001": 10_000.0, "000002": 200_000.0, "000003": 600_000.0}[t])
    monkeypatch.setattr(L, "_bot_has_isolation", lambda: (True, ""))
    monkeypatch.setattr(L, "kr_order_window", lambda kind: (True, ""))

    class _Kis:
        @staticmethod
        def get_balance(token, market, force_refresh):
            return {}
    monkeypatch.setattr(L, "_kis", lambda: (_Kis, "tok"))
    monkeypatch.setattr(L, "safe_cash_krw", lambda bal: 400_000.0)          # 000002(20만×1.3)만 살 현금
    monkeypatch.setattr(L, "place_safely", lambda *a, **k: {"success": True, "order_no": "1", "msg": "ok"})
    monkeypatch.setattr(L, "order_state", lambda res: "ok")
    assert L.cmd_buy(False) == 0
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    skips = {(r.get("ticker"), r["reason"]) for r in rows if r["kind"] == "skip"}
    assert ("000003", "budget") in skips and ("000001", "cash_short") in skips
    assert [r["ticker"] for r in rows if r["kind"] == "order"] == ["000002"]
