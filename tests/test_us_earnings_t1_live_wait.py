# -*- coding: utf-8 -*-
"""T1 매수 작업이 채점 완료(run 행)를 기다리는지 — 10-29 경합(채점 22:20:44 완료 > 매수 22:20:02 읽기) 회귀 방지."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import us_earnings_t1_live as t1  # noqa: E402


def _w(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_wait_returns_once_run_row_appears(tmp_path, monkeypatch):
    sh = tmp_path / "shadow.jsonl"
    _w(sh, [])
    monkeypatch.setattr(t1, "SHADOW", sh)
    calls = []

    def fake_sleep(_):
        calls.append(1)
        if len(calls) == 2:                                   # 두 번째 대기 뒤 채점이 끝난다
            _w(sh, [{"kind": "signal", "session": "2026-10-29", "ticker": "ALKT"},
                    {"kind": "run", "session": "2026-10-29"}])

    ok = t1._wait_for_score("2026-10-29", now_et=lambda: datetime(2026, 10, 29, 9, 20), sleep=fake_sleep)
    assert ok and len(calls) == 2


def test_wait_gives_up_at_deadline(tmp_path, monkeypatch):
    sh = tmp_path / "shadow.jsonl"
    _w(sh, [{"kind": "run", "session": "2026-10-28"}])       # 어제 run은 오늘 완료가 아니다
    monkeypatch.setattr(t1, "SHADOW", sh)
    clock = iter([datetime(2026, 10, 29, 9, 24), datetime(2026, 10, 29, 9, 26)])
    assert t1._wait_for_score("2026-10-29", now_et=lambda: next(clock), sleep=lambda _: None) is False


def test_no_wait_when_already_scored(tmp_path, monkeypatch):
    sh = tmp_path / "shadow.jsonl"
    _w(sh, [{"kind": "run", "session": "2026-10-29"}])
    monkeypatch.setattr(t1, "SHADOW", sh)

    def boom(_):
        raise AssertionError("대기하면 안 됨")

    assert t1._wait_for_score("2026-10-29", now_et=lambda: datetime(2026, 10, 29, 9, 30), sleep=boom)


def test_buy_skips_with_rc3_when_score_never_finishes(tmp_path, monkeypatch, capsys):
    sh, led = tmp_path / "shadow.jsonl", tmp_path / "live.jsonl"
    _w(sh, [])
    monkeypatch.setattr(t1, "SHADOW", sh)
    monkeypatch.setattr(t1, "LEDGER", led)
    monkeypatch.setattr(t1, "_env_gate", lambda: (True, {"enabled_flag": "true", "mismatch": False, "min_dvol": 5e6}))
    monkeypatch.setattr(t1, "_isolation_gate", lambda: True)
    monkeypatch.setattr(t1, "_today_et", lambda: datetime(2026, 10, 29).date())
    monkeypatch.setattr(t1, "_wait_for_score", lambda today: False)
    assert t1.cmd_buy(False) == 3
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["reason"] == "score_not_ready"
    assert "선택 없음' 아님" in capsys.readouterr().out


def test_buy_records_every_unexecuted_candidate_with_reason(tmp_path, monkeypatch):
    """실행 수율 추적(10-30): 주문 안 한 유동 선정 후보를 사유별 skip 행으로 남긴다."""
    sh, led = tmp_path / "shadow.jsonl", tmp_path / "live.jsonl"
    sig = lambda tk, sc, px: {"kind": "signal", "session": "2026-10-29", "ticker": tk, "selected_I1": True,
                              "dvol20": 9e6, "score": sc, "close_a": px}
    _w(sh, [sig("HELD", 0.05, 10.0), sig("ARGX", 0.04, 870.0), sig("ALKT", 0.033, 15.5), sig("NMRK", 0.024, 12.0),
            {"kind": "run", "session": "2026-10-29"}])
    monkeypatch.setattr(t1, "SHADOW", sh)
    monkeypatch.setattr(t1, "LEDGER", led)
    monkeypatch.setattr(t1, "_env_gate", lambda: (True, {"enabled_flag": "true", "mismatch": False, "min_dvol": 5e6,
                                                         "budget": 2e6, "slot": 28e4, "max_slots": 7, "max_new": 1}))
    monkeypatch.setattr(t1, "_isolation_gate", lambda: True)
    monkeypatch.setattr(t1, "_today_et", lambda: datetime(2026, 10, 29).date())
    monkeypatch.setattr(t1, "_usdkrw", lambda: 1336.0)

    class K:
        def get_balance(self, token, **kw):
            return {"stocks": [{"ticker": "HELD"}], "orderable_cash": 1500.0}

        def place_order(self, *a, **k):
            return {"success": True, "order_no": "0001", "msg": "ok"}
    monkeypatch.setattr(t1, "_kis", lambda: (K(), "tok"))
    monkeypatch.setattr(t1, "place_safely", lambda kis, *a, **k: {"success": True, "order_no": "0001", "msg": "ok"})
    assert t1.cmd_buy(False) == 0
    rows = [json.loads(x) for x in led.read_text(encoding="utf-8").splitlines()]
    skips = {r["ticker"]: r["reason"] for r in rows if r["kind"] == "skip"}
    assert skips == {"HELD": "already_held", "ARGX": "price_gt_slot", "NMRK": "daily_cap"}
    assert [r["ticker"] for r in rows if r["kind"] == "order"] == ["ALKT"]
    assert all(r["kind"] != "position" or r["ticker"] == "ALKT" for r in rows)
