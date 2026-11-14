# -*- coding: utf-8 -*-
"""T1 실전 상태 출력의 체결가−시가 요약(11-14) — 중앙값·평균 병기, 매수·매도 분리, 짝수 건 중앙값. 네트워크·주문 없음."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import us_earnings_t1_live as T  # noqa: E402


def _fill(side, bp):
    return {"kind": "fill", "side": side, "slip_bp_vs_open": bp}


def test_slip_stats_median_and_mean_differ_on_skewed_fills():
    # 11-14 실전 9건과 같은 모양: 중앙값은 0인데 평균은 +19.9bp
    rows = [_fill("buy", x) for x in (-65.5, 32.2, 24.0, 48.1, 0.0, 0.0, 0.0, 140.5, 0.0)]
    s = T.slip_stats(rows)
    assert s["n"] == 9 and s["median"] == 0.0
    assert round(s["mean"], 1) == 19.9 and s["min"] == -65.5 and s["max"] == 140.5


def test_slip_stats_even_count_uses_true_median_and_splits_sides():
    rows = [_fill("buy", 0.0), _fill("buy", 24.0), _fill("sell", -500.0), _fill("buy", None),
            {"kind": "order", "side": "buy", "slip_bp_vs_open": 999.0}]
    s = T.slip_stats(rows)
    assert s["n"] == 2 and s["median"] == 12.0          # 그전 표시는 위쪽 값 24를 찍었다
    assert T.slip_stats(rows, "sell")["n"] == 1
    assert T.slip_stats([]) is None
