# -*- coding: utf-8 -*-
"""유령 포지션(phantom_book) — 쉐도우를 실매매처럼 ②·③ (2026-09-03).

픽스처=프로덕션: 브리지 e2e 하니스(FakeBot·_build_db·_run)를 그대로 재사용하고, 출구 평가는
실제 RiskManager._isolated_strategy_exit_candidate를 바인딩한다(새 정산 코드 금지)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from risk_manager import RiskManager
from runtime import phantom_book
from runtime import us_swing_order_bridge as bridge
from runtime import virtual_overrides as vo

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("e2e_harness", ROOT / "tests" / "test_us_swing_order_bridge_e2e.py")
e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e2e)


@pytest.fixture
def phantom_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(phantom_book, "_state_path", lambda: tmp_path / "state" / "phantom_positions.json")
    monkeypatch.setattr(phantom_book, "_ledger_path", lambda: tmp_path / "data" / "shadow" / "phantom_ledger.jsonl")
    monkeypatch.setattr(phantom_book, "_picks_ledger_path", lambda: tmp_path / "data" / "shadow" / "arm_picks_realtime.jsonl")
    monkeypatch.setattr(phantom_book, "_entry_mark_path", lambda: tmp_path / "state" / "phantom_arm_entry_mark.json")
    monkeypatch.setattr(vo, "OVERRIDES_PATH", tmp_path / "state" / "virtual_strategy_overrides.json")
    monkeypatch.setattr(vo, "AUDIT_PATH", tmp_path / "data" / "shadow" / "control_tower_audit.jsonl")
    monkeypatch.setattr("telegram_reporter.send", lambda *a, **k: True)
    return tmp_path


def _risk_stub():
    r = RiskManager.__new__(RiskManager)  # __init__ 없이 — 평가 함수는 self 상태를 안 쓴다(09-03 실측)
    r.positions = []
    return r


def _eval_bot(*, price_usd: float | dict = 100.0, horizon_rows=None):
    bot = SimpleNamespace(
        usd_krw_rate=1400.0, current_market="US", session_active=True,
        risk=_risk_stub(),
        _current_session_date_str=lambda m: "2026-09-03",
        _count_session_holding_days=lambda m, a, b: 1,
        _fixed_horizon_strategy_exit_candidates=lambda positions=None: list(horizon_rows or []),
        _runtime_value=lambda k, d="": d,
        _token_for_market=lambda m: "tok",
    )
    if isinstance(price_usd, dict):
        return bot, (lambda t: {"price": price_usd.get(t.upper(), 0.0)})
    return bot, (lambda t: {"price": price_usd})


def _ledger_rows(tmp_path, rows):
    p = tmp_path / "data" / "shadow" / "arm_picks_realtime.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _pick(arm, ticker, *, session="2026-09-03", tp=0.12, slots=7, order_krw=540000.0, pos=1):
    return {"session_date": session, "arm": arm, "ticker": ticker, "pick_pos": pos, "universe": "wide",
            "book_session_date": session, "tp_pct": tp, "sl_pct": 0.25, "order_krw": order_krw,
            "slots": slots, "daily_cap": 1, "quote": 99.0, "quote_source": "yfinance_delayed"}


def _phantom_ledger(tmp_path):
    p = tmp_path / "data" / "shadow" / "phantom_ledger.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []


# ── ② 라이브 미러 ────────────────────────────────────────────────────────────
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
    assert rows[0]["arm"] == "us_live_dvol" and rows[0]["source_strategy"] == "us_swing_5d"
    assert rows[0]["display_avg_price"] == pytest.approx(100.5)   # 브리지 호가 그대로
    assert bot.submit_calls == 0
    assert not (tmp_path / "state" / "live_open_positions.json").exists()   # 실주문 파일 미오염
    assert bridge._current_us_swing_open_slots(bot) == 0                      # 슬롯 회계 불변
    assert [r["event"] for r in _phantom_ledger(tmp_path)] == ["OPEN"]


def test_take_profit_closes_phantom_via_real_evaluator(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd=113.0)          # +13% > TP12
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-09-02")
    out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out["closed"] == 1 and out["open"] == 0 and out["priced"] == 1
    close = [r for r in _phantom_ledger(tmp_path) if r["event"] == "CLOSE"][0]
    assert close["reason"] == "strategy_fixed_take_profit" and close["gross_pct"] == pytest.approx(13.0)
    assert close["arm"] == "us_live_dvol"
    assert phantom_book.load_positions() == []


def test_no_exit_keeps_position_and_tracks_peak(phantom_paths):
    bot, price_fn = _eval_bot(price_usd=103.0)          # +3% — BE락(4%) 미만, TP 미만
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-09-02")
    out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out["open"] == 1 and out["closed"] == 0 and out["priced"] == 1
    pos = phantom_book.load_positions()[0]
    assert pos["peak_pnl_pct"] == pytest.approx(3.0) and pos["held_days"] == 1


def test_horizon_exit_reuses_bot_fixed_horizon(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd=101.0, horizon_rows=[{"reason": "strategy_horizon_exit"}])
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-08-25")
    out = phantom_book.evaluate(bot, price_fn=price_fn)
    assert out["closed"] == 1
    assert _phantom_ledger(tmp_path)[-1]["reason"] == "strategy_horizon_exit"


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


# ── ③ 전 arm ─────────────────────────────────────────────────────────────────
def test_arm_picks_open_from_ledger_with_dedupe_slots_and_override(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd={"AAA": 50.0, "BBB": 200.0, "SN": 171.0, "CCC": 10.0})
    # 브리지가 먼저 만든 라이브 미러 행 — 원장 경로가 중복 생성하면 안 된다
    phantom_book.open_from_rehearsal(bot, ticker="SN", qty=2, quote_usd=171.0, session_date="2026-09-03")
    vo.set_override("us_wide_chg", "paused", memo="테스트", by="test")
    _ledger_rows(tmp_path, [
        _pick("us_live_dvol", "SN"),
        _pick("us_wide_dvol", "AAA"),
        _pick("us_wide_tp20", "BBB", tp=0.20),
        _pick("us_wide_chg", "CCC"),                       # paused → skip
        _pick("us_wide_dvol_k3", "AAA", slots=1, pos=1),
        _pick("us_wide_dvol_k3", "BBB", slots=1, pos=2),   # slots=1 → 두 번째 skip
    ])
    out = phantom_book.open_arm_picks_from_ledger(bot, session_date="2026-09-03", price_fn=price_fn, minutes_since_open=10)
    assert out["candidates"] == 6 and out["opened"] == 3
    assert out["skipped"]["us_wide_chg"] == "override:paused"
    assert out["skipped"]["us_wide_dvol_k3:BBB"] == "slots_full"
    rows = phantom_book.load_positions()
    keys = {(r["arm"], r["ticker"]) for r in rows}
    assert keys == {("us_live_dvol", "SN"), ("us_wide_dvol", "AAA"), ("us_wide_tp20", "BBB"), ("us_wide_dvol_k3", "AAA")}
    aaa = [r for r in rows if r["arm"] == "us_wide_dvol"][0]
    assert aaa["qty"] == int(540000.0 // (50.0 * 1400.0)) and aaa["source_strategy"] == "us_swing_5d"
    assert [r for r in rows if r["arm"] == "us_wide_tp20"][0]["tp_pct"] == pytest.approx(0.20)
    # 세션당 1회 — 두 번째 호출은 마커로 스킵
    again = phantom_book.open_arm_picks_from_ledger(bot, session_date="2026-09-03", price_fn=price_fn, minutes_since_open=12)
    assert again["opened"] == 0 and again["candidates"] == 0
    assert (tmp_path / "data" / "shadow" / "control_tower_audit.jsonl").exists()


def test_tp20_arm_closes_at_twenty_not_twelve(phantom_paths, tmp_path):
    bot, price_fn = _eval_bot(price_usd={"BBB": 115.0})
    _ledger_rows(tmp_path, [_pick("us_wide_tp20", "BBB", tp=0.20)])
    phantom_book.open_arm_picks_from_ledger(bot, session_date="2026-09-03",
                                            price_fn=lambda t: {"price": 100.0}, minutes_since_open=10)
    out = phantom_book.evaluate(bot, price_fn=price_fn)          # +15%: TP12면 청산, TP20이면 보유
    assert out["closed"] == 0 and out["open"] == 1
    bot2, price_fn2 = _eval_bot(price_usd={"BBB": 121.0})
    out2 = phantom_book.evaluate(bot2, price_fn=price_fn2)       # +21% > TP20
    assert out2["closed"] == 1
    assert _phantom_ledger(tmp_path)[-1]["reason"] == "strategy_fixed_take_profit"


def test_evaluate_preserves_positions_opened_concurrently(phantom_paths):
    bot, price_fn = _eval_bot(price_usd=100.0)
    phantom_book.open_from_rehearsal(bot, ticker="ABC", qty=3, quote_usd=100.0, session_date="2026-09-02")
    real_apply = phantom_book._apply_price

    def _apply_and_open(pos, px, rate):
        # 평가 도중(락 밖) 다른 스레드가 새 포지션을 연 상황을 흉내낸다
        phantom_book.open_from_rehearsal(bot, ticker="ZZZ", qty=1, quote_usd=10.0, session_date="2026-09-03")
        real_apply(pos, px, rate)
    phantom_book._apply_price = _apply_and_open
    try:
        phantom_book.evaluate(bot, price_fn=price_fn)
    finally:
        phantom_book._apply_price = real_apply
    assert {p["ticker"] for p in phantom_book.load_positions()} == {"ABC", "ZZZ"}


def test_overrides_set_and_audit(phantom_paths, tmp_path):
    e = vo.set_override("us_wide_ibs", "retired", memo="8월 -3.15", by="operator")
    assert e["state"] == "retired" and vo.arm_state("us_wide_ibs") == "retired" and vo.arm_state("us_wide_dvol") == "active"
    with pytest.raises(ValueError):
        vo.set_override("x", "promoted")
    audit = (tmp_path / "data" / "shadow" / "control_tower_audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) == 1 and json.loads(audit[0])["to"] == "retired"


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
