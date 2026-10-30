# -*- coding: utf-8 -*-
"""레인 중단·확대 조건 진행도(10-30) — 판정 시점 감지·벤치 실패 처리·조회 전용."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import lane_conditions as lc  # noqa: E402


def _t1_rows(n_fill, slip, n_exit=0, ret=0.0):
    rows = []
    for i in range(n_fill):
        rows.append({"kind": "fill", "side": "buy", "ticker": f"T{i}", "slip_bp_vs_open": slip})
    for i in range(n_exit):
        d0, d1 = f"2026-01-{(i % 28) + 1:02d}", f"2026-02-{(i % 28) + 1:02d}"
        rows.append({"kind": "position", "ticker": f"X{i}", "entry_session": d0, "status": "OPEN", "entry_price": 10.0})
        rows.append({"kind": "order", "side": "sell", "success": True, "ticker": f"X{i}", "session": d1})
        rows.append({"kind": "position", "ticker": f"X{i}", "entry_session": d0, "status": "CLOSED",
                     "close_reason": "moo_exit", "entry_price": 10.0, "exit_price": 10.0 * (1 + ret)})
    return rows


def test_t1_slip_not_reached_before_20_fills():
    c1, c2 = lc.t1_conditions(_t1_rows(19, 50.0), bench=lambda a, b: 0.0)
    assert not c1["reached"] and c1["verdict"] is None and c1["n"] == 19


def test_t1_slip_halt_reached_at_20():
    c1, _ = lc.t1_conditions(_t1_rows(20, 25.0), bench=lambda a, b: 0.0)
    assert c1["reached"] and c1["verdict"] == "중단 조건 도달"
    c1, _ = lc.t1_conditions(_t1_rows(20, 10.0), bench=lambda a, b: 0.0)
    assert c1["reached"] and c1["verdict"] == "통과"


def test_t1_exit_net_vs_bench_with_cost():
    # 수익 +1% − 비용 0.5% − IWM 0.2% = +0.3% → 확대 조건
    _, c2 = lc.t1_conditions(_t1_rows(0, 0, n_exit=50, ret=0.01), bench=lambda a, b: 0.002)
    assert c2["reached"] and c2["verdict"] == "확대 조건 도달" and abs(c2["value"] - 0.3) < 1e-9
    # 수익 +0.6% − 0.5% − 0.2% < 0 → 중단
    _, c2 = lc.t1_conditions(_t1_rows(0, 0, n_exit=50, ret=0.006), bench=lambda a, b: 0.002)
    assert c2["verdict"] == "중단 조건 도달"


def test_t1_bench_failure_is_not_silent():
    _, c2 = lc.t1_conditions(_t1_rows(0, 0, n_exit=50, ret=0.05), bench=lambda a, b: None)
    assert c2["n"] == 50 and c2["verdict"] == "벤치 산출 실패" and "벤치 산출 실패 50" in c2["line"]


def test_panic_forward_20():
    rows = [{"kind": "close", "entry_price": 10000.0, "qty": 10, "pnl_krw": -1000} for _ in range(20)]
    (c,) = lc.panic_conditions(rows)
    assert c["reached"] and c["verdict"] == "폐기 조건 도달" and c["value"] == -1.0


def test_canary_counts_only_h5_exits():
    rows = [{"kind": "position", "ticker": "A", "entry_session": "s1", "status": "CLOSED", "close_reason": "unfilled", "pnl_krw": 0},
            {"kind": "position", "ticker": "B", "entry_session": "s1", "status": "CLOSED", "close_reason": "h5_exit", "pnl_krw": 1200}]
    (c,) = lc.canary_conditions(rows)
    assert c["n"] == 1 and not c["reached"] and c["value"] == 1200


def test_index_incl_open_round_not_counted():
    rows = [{"kind": "close", "round": "r1", "pnl_krw": 5000},
            {"kind": "close", "round": "r2", "pnl_krw": -100},
            {"kind": "position", "ticker": "Z", "entry_session": "s", "round": "r2", "status": "OPEN"}]
    (c,) = lc.index_incl_conditions(rows)
    assert c["n"] == 1 and c["value"] == 4900


def test_all_conditions_survives_bad_lane(tmp_path):
    (tmp_path / "kr_panic_etf_micro_live.jsonl").write_text('{"kind": "close", "entry_price": 0, "qty": 1, "pnl_krw": 1}\n'
                                                           '{"kind": "close", "entry_price": "x", "qty": 1, "pnl_krw": 1}\n',
                                                           encoding="utf-8")
    out = lc.all_conditions(tmp_path, bench=lambda a, b: None)
    assert any(c["cond"] == "계산 실패" for c in out) and any(c["lane"] == "T1" for c in out)
