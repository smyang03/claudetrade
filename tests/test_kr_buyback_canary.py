"""KR 자사주 canary — 주문 계획·만기·게이트·봇 격리 (네트워크·주문 없음)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_buyback_canary as c  # noqa: E402
import risk_manager  # noqa: E402

CFG = {"order_krw": 100_000.0, "max_open": 2, "loss_limit": 50_000.0}


def _sig(code, close, dvol, day="2026-10-01", rule="Y1", q=True):
    return {"key": f"KR|{code}", "stock_code": code, "corp_name": code, "close_D": close, "dvol20_D": dvol,
            "entry_date": day, "rule": rule, "qualifies": q, "rcept_no": "r" + code, "ratio": 0.015}


def test_signals_take_last_row_per_key_and_filter():
    rows = [_sig("111111", 5000, 3e9, q=False), _sig("111111", 5000, 3e9),        # 대기 → 채택으로 갱신
            _sig("222222", 5000, 3e9, day="2026-10-02"), _sig("333333", 5000, 3e9, rule=None)]
    got = c.signals_for(date(2026, 10, 1), rows)
    assert [s["stock_code"] for s in got] == ["111111"]


def test_plan_orders_by_dvol_and_respects_slots_cash_held():
    sigs = [_sig("A", 20_000, 1e9), _sig("B", 30_000, 5e9), _sig("C", 10_000, 3e9)]
    plans, notes = c.plan_buys(sigs, CFG, open_pos={}, held=set(), cash=1_000_000, realized=0)
    assert [p["ticker"] for p in plans] == ["B", "C"]                               # dvol 큰 순, 슬롯 2
    assert plans[0]["qty"] == 3 and plans[1]["qty"] == 10
    assert any("슬롯 가득" in n for n in notes)
    plans, notes = c.plan_buys(sigs, CFG, open_pos={"X": {}}, held={"B"}, cash=1_000_000, realized=0)
    assert [p["ticker"] for p in plans] == ["C"]
    plans, _ = c.plan_buys([_sig("D", 150_000, 9e9)], CFG, {}, set(), 1_000_000, 0)
    assert plans == []                                                              # 1주가 한도 초과
    plans, notes = c.plan_buys([_sig("E", 10_000, 9e9)], CFG, {}, set(), cash=50_000, realized=0)
    assert plans == [] and "현금 부족" in notes[0]                                    # 10주 × 1만 × 1.3 = 13만 > 5만


def test_loss_limit_blocks_new_buys():
    plans, notes = c.plan_buys([_sig("A", 10_000, 9e9)], CFG, {}, set(), 1_000_000, realized=-50_000)
    assert plans == [] and "신규 매수 중단" in notes[0]


def test_due_exits_after_five_sessions_skips_holiday():
    pos = {"A": {"status": "OPEN", "filled_qty": 3, "entry_session": "2026-09-30"}}
    # 10-01(1) 10-02(2) [10-05 대체휴일] 10-06(3) 10-07(4) 10-08(5)
    assert c.due_exits(pos, date(2026, 10, 7)) == []
    assert [t for t, _, n in c.due_exits(pos, date(2026, 10, 8))] == ["A"]
    assert c.due_exits({"B": {"status": "OPEN", "filled_qty": 0, "entry_session": "2026-09-30"}}, date(2026, 10, 20)) == []


def test_due_exits_not_triggered_on_holiday_today():
    # 10-09 한글날 실사고: 10-01 진입분을 휴장일에 5세션으로 세어 매도를 냈다(브로커 거부)
    pos = {"A": {"status": "OPEN", "filled_qty": 5, "entry_session": "2026-10-01"}}
    assert c.sessions_since("2026-10-01", date(2026, 10, 9)) == 4
    assert c.due_exits(pos, date(2026, 10, 9)) == []
    assert c.sessions_since("2026-10-01", date(2026, 10, 10)) == 4      # 토요일
    assert [n for _, _, n in c.due_exits(pos, date(2026, 10, 12))] == [5]


def test_sell_outside_window_places_no_order(tmp_path, monkeypatch, capsys):
    led = tmp_path / "led.jsonl"
    led.write_text(json.dumps({"kind": "position", "ticker": "A", "status": "OPEN", "entry_session": "2026-10-01",
                             "filled_qty": 5}) + "\n", encoding="utf-8")
    monkeypatch.setattr(c, "LEDGER", led)
    monkeypatch.setattr(c, "cmd_reconcile", lambda: 0)
    monkeypatch.setattr(c, "_today_kst", lambda: date(2026, 10, 12))
    monkeypatch.setattr(c, "kr_order_window", lambda kind: (False, "주문 창 밖(20:11)"))
    monkeypatch.setattr(c, "_kis", lambda: (_ for _ in ()).throw(AssertionError("KIS 호출 금지")))
    assert c.cmd_sell(False) == 0
    assert "만기 매도 보류" in capsys.readouterr().out
    assert len(led.read_text(encoding="utf-8").splitlines()) == 1        # 거부 주문 행도 남기지 않는다


def test_realized_pnl_uses_last_position_row(tmp_path):
    rows = [{"kind": "position", "ticker": "A", "status": "OPEN"},
            {"kind": "position", "ticker": "A", "status": "CLOSED", "pnl_krw": -30_000},
            {"kind": "position", "ticker": "B", "status": "CLOSED", "pnl_krw": 5_000}]
    assert c._realized_pnl(rows) == -25_000


def test_env_gate_requires_both_sources(tmp_path, monkeypatch):
    env = tmp_path / ".env.live"
    cfgf = tmp_path / "cfg.json"
    env.write_text("KR_BUYBACK_CANARY_ENABLED=true\nKR_BUYBACK_CANARY_ORDER_KRW=100000\n"
                   "KR_BUYBACK_CANARY_MAX_OPEN=2\nKR_BUYBACK_CANARY_LOSS_LIMIT_KRW=50000\n", encoding="utf-8")
    cfgf.write_text(json.dumps({"env_overrides": {"KR_BUYBACK_CANARY_ENABLED": "true", "KR_BUYBACK_CANARY_ORDER_KRW": "100000",
                                                  "KR_BUYBACK_CANARY_MAX_OPEN": "2",
                                                  "KR_BUYBACK_CANARY_LOSS_LIMIT_KRW": "50000"}}), encoding="utf-8")
    monkeypatch.setattr(c, "ROOT", tmp_path)
    monkeypatch.setattr(c, "START_CFG", cfgf)
    ok, cfg = c._env_gate()
    assert ok and cfg["order_krw"] == 100_000 and cfg["max_open"] == 2
    cfgf.write_text(json.dumps({"env_overrides": {"KR_BUYBACK_CANARY_ENABLED": "false"}}), encoding="utf-8")
    ok, cfg = c._env_gate()
    assert not ok and cfg["mismatch"]


def test_bot_isolation_contract(tmp_path):
    assert c.SOURCE in risk_manager.ISOLATED_STRATEGY_SOURCES
    led = tmp_path / "l.jsonl"
    led.write_text("\n".join(json.dumps(r) for r in [
        {"kind": "order", "ticker": "005930", "order_no": "0001", "side": "buy"},
        {"kind": "position", "ticker": "005930", "status": "OPEN"},
        {"kind": "position", "ticker": "000660", "status": "OPEN"},
        {"kind": "position", "ticker": "000660", "status": "CLOSED"}]), encoding="utf-8")
    m = risk_manager.kr_buyback_canary_manifest(led)
    assert m["open_tickers"] == frozenset({"005930"}) and m["order_nos"] == frozenset({"0001"})


def test_bot_restart_gate(tmp_path, monkeypatch):
    import os
    (tmp_path / "state").mkdir()
    for f in ("risk_manager.py", "trading_bot.py"):
        (tmp_path / f).write_text("x", encoding="utf-8")
        os.utime(tmp_path / f, (2_000_000_000, 2_000_000_000))                      # 2033 — 코드가 더 새것
    (tmp_path / "state" / "live_trading_bot.pid").write_text(json.dumps({"started_at": "2026-09-29T00:42:36+09:00"}),
                                                             encoding="utf-8")
    monkeypatch.setattr(c, "ROOT", tmp_path)
    ok, msg = c._bot_has_isolation()
    assert not ok and "재시작" in msg
    (tmp_path / "state" / "live_trading_bot.pid").write_text(json.dumps({"started_at": "2034-01-01T00:00:00+09:00"}),
                                                             encoding="utf-8")
    assert c._bot_has_isolation()[0]


def test_isolated_exit_owner_never_sells_canary():
    rm = risk_manager.RiskManager.__new__(risk_manager.RiskManager)
    pos = {"source_strategy": c.SOURCE, "entry": 10_000, "current_price": 5_000, "market": "KR"}
    handled, cand = rm._isolated_strategy_exit_candidate(pos)
    assert handled is True and cand is None


def test_rejected_sell_today_does_not_block_retry(tmp_path, monkeypatch, capsys):
    led = tmp_path / "led.jsonl"
    rows = [{"kind": "position", "ticker": "A", "status": "OPEN", "entry_session": "2026-10-01", "filled_qty": 5},
            {"kind": "order", "session": "2026-10-12", "ticker": "A", "side": "sell", "qty": 5, "order_no": "",
             "success": False, "state": "rejected", "msg": "장운영시간이 아닙니다"}]
    led.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(c, "LEDGER", led)
    monkeypatch.setattr(c, "_today_kst", lambda: date(2026, 10, 12))
    assert c.cmd_sell(True) == 0
    assert "(dry) 시장가 매도 A x5" in capsys.readouterr().out
