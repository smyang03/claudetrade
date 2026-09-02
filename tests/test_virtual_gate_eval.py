# -*- coding: utf-8 -*-
"""승격 게이트 검정 엔진 테스트 (2026-09-02). 픽스처 DB는 프로덕션 스키마(ensure_schema)로 만든다."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from tools import virtual_gate_eval as ge
from tools import virtual_books as vb


RNG = lambda: np.random.default_rng(7)  # noqa: E731


def test_block_lcb_strong_signal_positive_and_null_negative():
    strong = np.full(60, 2.0) + RNG().normal(0, 1.0, 60)
    mean, lcb = ge.block_lcb(strong, reps=2000, block_mean=10, alpha=0.05, rng=RNG())
    assert lcb > 0 and lcb < mean
    null = RNG().normal(0, 3.0, 60)
    _m, lcb0 = ge.block_lcb(null, reps=2000, block_mean=10, alpha=0.05, rng=RNG())
    assert lcb0 < 0


def test_reality_check_p_null_arms_not_tiny_and_strong_arm_small():
    rng = RNG()
    null_mat = rng.normal(0, 3.0, (60, 8))
    p_null, _ = ge.reality_check_p(null_mat, reps=2000, block_mean=10, rng=RNG())
    assert p_null > 0.05
    strong = null_mat.copy()
    strong[:, 3] += 3.0
    p_strong, best = ge.reality_check_p(strong, reps=2000, block_mean=10, rng=RNG())
    assert best == 3 and p_strong < 0.05


def test_reality_check_handles_nan_sessions():
    rng = RNG()
    mat = rng.normal(0, 1.0, (50, 3))
    mat[:25, 2] = np.nan  # arm 3은 후반 세션만 거래
    p, _ = ge.reality_check_p(mat, reps=500, block_mean=10, rng=RNG())
    assert 0.0 <= p <= 1.0


def test_holm_step_down():
    sig = ge.holm({"A": 0.001, "B": 0.03, "C": 0.2}, alpha=0.05)
    assert sig == {"A": True, "B": False, "C": False}  # B: 0.03 > 0.05/2
    sig2 = ge.holm({"A": 0.001, "B": 0.02}, alpha=0.05)
    assert sig2 == {"A": True, "B": True}


def _db_with_trades(path, rows):
    con = sqlite3.connect(path)
    vb.ensure_schema(con)
    now = "2026-09-02T00:00:00+00:00"
    for sid, sd, tk, net, backfill, pos in rows:
        con.execute(
            """INSERT INTO trades (strategy_id, session_date, ticker, entry_price, notional_krw,
                   backfill, pick_pos, status, exit_reason, net_pct, pnl_krw, opened_at, settled_at)
               VALUES (?,?,?,?,?,?,?,'CLOSED','TP',?,?,?,?)""",
            (sid, sd, tk, 100.0, 540000.0, backfill, pos, net, 540000.0 * net / 100.0, now, now))
    con.commit()
    return con


def test_forward_only_excludes_backfill_and_all_arm_uses_cash_null(tmp_path, monkeypatch):
    monkeypatch.setitem(ge.PARAMS, "min_sessions_for_bootstrap", 5)
    strat_pick = {"id": "s_pick", "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
                  "slots": 7, "order_krw": 540000, "capital_krw": 10_000_000}
    strat_all = {"id": "s_all", "universe": "wide", "pick": "all", "daily_cap": 999,
                 "slots": 999, "order_krw": 540000, "capital_krw": 100_000_000}
    rows = []
    for i in range(8):
        sd = f"2026-09-{i + 1:02d}"
        rows.append(("s_pick", sd, f"T{i}", 3.0, 0, 1))
        rows.append(("s_all", sd, f"T{i}", 3.0, 0, 1))
        rows.append(("s_all", sd, f"U{i}", -1.0, 0, 2))
    rows.append(("s_pick", "2026-08-20", "OLD", -50.0, 1, 1))  # 백필 — forward 판정에서 제외
    con = _db_with_trades(tmp_path / "vb.db", rows)
    pool_fn = lambda s, sd: [3.0, -1.0, -1.0, -1.0]  # 풀 평균 0 → 픽 알파 = +3  # noqa: E731
    out = ge.run(include_backfill=False, reps=300, seed=1, asof="2026-09-30", pool_fn=pool_fn,
                 con=con, write=False, strategies=[strat_pick, strat_all])
    pick, alla = out["strategies"]["s_pick"], out["strategies"]["s_all"]
    assert pick["n_settled"] == 8 and pick["mean_net_pct"] == 3.0       # OLD 제외
    assert pick["null_method"] == "paired_pool_expectation" and pick["alpha_mean_pct"] == 3.0
    assert alla["null_method"] == "cash_zero" and alla["alpha_mean_pct"] == 1.0  # (3-1)/2
    assert pick["n_stage"] == "INSUFFICIENT" and "descriptive_only" in pick["verdict"]
    assert "marginal_slots" in alla and set(alla["marginal_slots"]) == {"1", "2"}
    assert out["families"]["F0_FALLEN_V1"]["method"] == "white_reality_check"
    con.close()


def test_insufficient_sessions_yields_no_lcb(tmp_path):
    strat = {"id": "s1", "universe": "wide", "pick": "dvol_desc", "daily_cap": 1,
             "slots": 7, "order_krw": 540000, "capital_krw": 10_000_000}
    con = _db_with_trades(tmp_path / "vb.db", [("s1", "2026-09-01", "A", 5.0, 0, 1)])
    out = ge.run(include_backfill=False, reps=100, seed=1, asof="2026-09-02",
                 pool_fn=lambda s, sd: [1.0], con=con, write=False, strategies=[strat])
    r = out["strategies"]["s1"]
    assert r["alpha_lcb95_pct"] is None and r["checks"]["lcb"] is False
    assert out["families"] == {}
    con.close()
