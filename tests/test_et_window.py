# -*- coding: utf-8 -*-
"""ET 창 가드 — US 장 기준 예약 작업이 여름·겨울 트리거 중 계절 맞는 쪽만 일한다(11-01)."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import et_window as W  # noqa: E402


def test_no_spec_always_runs():
    assert W.in_et_window(None) and W.in_et_window("")


def test_window_bounds():
    at = lambda h, m: W.in_et_window("09:31-10:30", datetime(2026, 11, 2, h, m))
    assert not at(8, 36)     # 겨울의 22:36 KST 트리거(개장 전)
    assert at(9, 36)         # 계절 맞는 트리거
    assert not at(10, 36)    # 여름의 23:36 KST 트리거
    assert at(9, 31) and not at(10, 30)


def test_scripts_exit_before_work_outside_window(monkeypatch, capsys):
    import earnings_pit_ledger as P
    import observe_arm_picks_realtime as A
    monkeypatch.setattr(W, "in_et_window", lambda spec, now_et=None: False)
    monkeypatch.setattr(P, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("run 금지")))
    monkeypatch.setattr(A.vb, "load_sessions", lambda: (_ for _ in ()).throw(AssertionError("로드 금지")))
    monkeypatch.setattr(sys, "argv", ["x", "--et-window", "09:05-09:30"])
    assert P.main() == 0
    monkeypatch.setattr(sys, "argv", ["x", "--et-window", "09:31-10:30"])
    assert A.main() == 0
    out = capsys.readouterr().out
    assert "[PIT] ET 창" in out and "[arm_picks] ET 창" in out
