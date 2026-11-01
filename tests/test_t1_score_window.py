# -*- coding: utf-8 -*-
"""T1 쉐도우 채점 창 — 09:05~09:26 ET만 채점(여름·겨울 두 트리거 중 계절이 맞는 쪽만, 11-01)."""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import us_earnings_t1_shadow as sh  # noqa: E402


def test_score_window():
    ok = lambda h, m: sh._score_window_ok(lambda: datetime(2026, 11, 2, h, m))
    assert not ok(8, 15)          # 겨울의 22:15 KST 트리거
    assert ok(9, 15)              # 계절에 맞는 트리거
    assert not ok(10, 15)         # 여름의 23:15 KST 트리거
    assert not ok(9, 26)


def test_out_of_window_score_returns_before_model(monkeypatch):
    monkeypatch.setattr(sh, "_score_window_ok", lambda now_et=None: False)
    monkeypatch.setattr(sh, "MODEL", Path("Z:/no/such/model.txt"))
    assert sh.cmd_score(False) == 0   # 모델 확인(rc 2) 전에 끝난다
