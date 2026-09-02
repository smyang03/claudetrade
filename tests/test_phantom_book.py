# -*- coding: utf-8 -*-
"""유령 포지션(phantom_book) — 쉐도우를 실매매처럼 (2026-09-03).

픽스처=프로덕션: 브리지 e2e 하니스(FakeBot·_build_db·_run)를 그대로 재사용하고, 출구 평가는
실제 RiskManager._isolated_strategy_exit_candidate를 바인딩한다(새 정산 코드 금지)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from risk_manager import RiskManager
from runtime import phantom_book
from runtime import us_swing_order_bridge as bridge

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("e2e_harness", ROOT / "tests" / "test_us_swing_order_bridge_e2e.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


@pytest.fixture
def phantom_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(phantom_book, "_state_path", lambda: tmp_path / "state" / "phantom_positions.json")
    monkeypatch.setattr(phantom_book, "_ledger_path", lambda: tmp_path / "data" / "shadow" / "phantom_ledger.jsonl")
    monkeypatch.setattr("telegram_reporter.send", lambda *a, **k: True)
    return tmp_path


def _risk_stub():
    r = RiskManager.__new__(RiskManager)  # __init__ 없이 — 평가 함수는 self 상태를 안 쓴다(09-03 실측)
    r.positions = []
    return r


def test_rehearsal_creates_phantom_once_and_keeps_live_book_clean(phantom_paths, tmp_path):
    db_path = tmp_path / "swing.db"
    e2e._build_db(db_path)
    bot = e2e.FakeBot(db_path, submit_enabled=False)

    first = e2e._run(bot)
    second = e2e._run(bot)

    assert first["results"][0]["status"] == "REHEARSAL_READY"
    assert second["reason"] == "no_handoff_signal"     # 원장 고정 → 재평가 없음
    rows = phantom_book.load_positions()
    assert len(rows) == 1 and rows[0]["ticker"] == "TEST" and rows[0]["virtual"] is True
    assert rows[0]["display_avg_price"] == pytest.approx(100.5)   # 브리지 호가 그대로
    assert rows[0]["qty"] > 0 and rows[0]["source_strategy"] == "us_swing_5d"
    assert bot.submit_calls == 0
    assert not (tmp_path / "state" / "live_open_positions.json").exists()   # 실주문 파일 미오염
    assert bridge._current_us_swing_open_slots(bot) == 0                      # 슬롯 회계 불변
    ledger = (tmp_path / "data" / "shadow" / "phantom_ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(ledger) == 1 and json.loads(ledger[0])["event"] == "OPEN"


def _eval_bot(*, price_usd: float, horizon_rows=None):
    bot = SimpleNamespace(
        usd_krw_rate=1400.0, current_market="US", session_active=True,
        risk=_risk_stub(),
        _current_session_date_str=lambda m: "2026-09-03",
        _count_session_holding_days=lambda m, a, b: 1,
        _fixed_horizon_strategy_exit_candidates=lambda positions=None: list(horizon_rows or []),
        _runtime_value=lambda k, d="": d,
        _token_for_market=lambda m: "tok",
    )
    return bot, (lambda ticker: {"price": price_usd})


def test_take_profit_closes_phantom_via_real_evaluator(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd=113.0)          # +12.5% > TP12
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-09-02")
    execute_sell = patch.object(bridge, "_notify_rehearsal_pick")  # 실주문 경로 미호출 보증용 더미
    with execute_sell:
        out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out["closed"] == 1 and out["open"] == 0
    rows = [json.loads(l) for l in (tmp_path / "data" / "shadow" / "phantom_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    close = [r for r in rows if r["event"] == "CLOSE"][0]
    assert close["reason"] == "strategy_fixed_take_profit" and close["gross_pct"] == pytest.approx(13.0)
    assert phantom_book.load_positions() == []


def test_no_exit_keeps_position_and_tracks_peak(phantom_paths):
    bot, price_fn = _eval_bot(price_usd=103.0)          # +3% — BE락(4%) 미만, TP 미만
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-09-02")
    out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out == {"open": 1, "closed": 0, "priced": 1}
    pos = phantom_book.load_positions()[0]
    assert pos["peak_pnl_pct"] == pytest.approx(3.0) and pos["held_days"] == 1


def test_horizon_exit_reuses_bot_fixed_horizon(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd=101.0, horizon_rows=[{"reason": "strategy_horizon_exit"}])
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-08-25")
    out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out["closed"] == 1
    rows = [json.loads(l) for l in (tmp_path / "data" / "shadow" / "phantom_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["reason"] == "strategy_horizon_exit"


def test_retro_from_handoff_ledger_creates_missing_phantom(phantom_paths, tmp_path):
    import sqlite3
    db_path = tmp_path / "swing.db"
    e2e._build_db(db_path)
    con = sqlite3.connect(db_path)
    con.execute("UPDATE signals SET handoff_status='REHEARSAL_READY', handoff_quote_price=171.27, handoff_qty=2, "
                "signal_date=date('now') WHERE ticker='TEST'")
    con.commit(); con.close()
    bot, _ = _eval_bot(price_usd=170.0)
    bot._runtime_value = lambda k, d="": str(db_path) if k == "US_SWING_SHADOW_DB" else d
    assert phantom_book.ensure_from_handoff_ledger(bot) == 1
    assert phantom_book.ensure_from_handoff_ledger(bot) == 0          # 멱등
    pos = phantom_book.load_positions()[0]
    assert pos["ticker"] == "TEST" and pos["retro"] is True and pos["display_avg_price"] == pytest.approx(171.27)


def test_legacy_selection_alerts_gated(monkeypatch):
    import telegram_reporter as tg
    sent = []
    monkeypatch.setattr(tg, "send", lambda text, *a, **k: sent.append(text) or True)
    monkeypatch.setattr(tg, "LEGACY_SELECTION_ALERTS", False)
    tg.watchlist_alert("US", "CAUTIOUS", ["AAA"], {"AAA": "r"}, [], trigger="session_open")
    assert sent == []
    monkeypatch.setattr(tg, "LEGACY_SELECTION_ALERTS", True)
    tg.watchlist_alert("US", "CAUTIOUS", ["AAA"], {"AAA": "r"}, [], trigger="session_open")
    assert len(sent) == 1
