"""research_focus_scoreboard 오프라인 테스트 — DB·가격 파일 없이 합성 픽스처만 쓴다."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools", ROOT / "tools" / "research"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import research_focus_scoreboard as fs  # noqa: E402

CON = (12.0, -25.0, 7)


def _dates(n, start=date(2026, 8, 3)):
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


DATES = _dates(40)                     # 2026-08-03 ~
BARS = [(d, 100.0, 101.0, 99.5, 100.0 + 0.1 * i, 1e6) for i, d in enumerate(DATES)]
SPEC = {"c_kr_x": {"id": "c_kr_x", "universe": "xkr", "tp": 12.0, "sl": -25.0, "hold": 7, "order_krw": 220_000},
        "c_us_x": {"id": "c_us_x", "universe": "xus", "tp": 12.0, "sl": -25.0, "hold": 7, "order_krw": 540_000}}


def _uni():
    cuts = {d: [1e9] * 9 for d in DATES}          # 전 종목 0분위
    sz = {}
    for d in DATES:
        sz[(d, 0, CON)] = (0.5, 30)
        sz[(d, 0, ("hold", 7))] = (0.3, 30)
    return {"KR": (cuts, sz), "US": (cuts, sz)}


def _run(rows, asof):
    cal = {"KR": [d for d in DATES if d <= asof], "US": [d for d in DATES if d <= asof]}
    return fs.arm_records(rows, asof, cal, lambda mk, tk: fs.truncate(BARS, asof), _uni(),
                          lambda *a: 1.0, SPEC)


def test_maturity_boundary_kr():
    sd = DATES[25]
    asof_mat, asof_not = DATES[32], DATES[31]      # 신호 후 7세션 / 6세션
    row = ("c_kr_x", sd, "000001", 100.0, 2.0, "CLOSED", 0, "{}")
    recs, info = _run([row], asof_mat)
    assert len(recs) == 1 and recs[0]["seg"] == "fwd" and info["c_kr_x"]["mat_fwd"] == 1
    recs, info = _run([row], asof_not)
    assert recs == [] and info["c_kr_x"] == {"entries_fwd": 1, "mat_fwd": 0}


def test_asof_truncation_drops_future_signal():
    row = ("c_kr_x", DATES[30], "000001", 100.0, 2.0, "CLOSED", 0, "{}")
    recs, info = _run([row], DATES[29])
    assert recs == [] and "c_kr_x" not in info


def test_september_backfill_row_is_bf():
    row = ("c_kr_x", DATES[25], "000001", 100.0, 2.0, "CLOSED", 1, "{}")
    recs, info = _run([row], DATES[-1])
    assert recs[0]["seg"] == "bf" and "c_kr_x" not in info
    s = fs.summarize_arm(recs, "c_kr_x", "fwd")
    assert s["n"] == 0 and s["sel"][2] == 0


def test_selection_excess_and_krw():
    rows = [("c_kr_x", DATES[25], "000001", 100.0, 2.0, "CLOSED", 0, "{}"),
            ("c_kr_x", DATES[25], "000002", 100.0, 4.0, "CLOSED", 0, "{}")]
    recs, _ = _run(rows, DATES[-1])
    s = fs.summarize_arm(recs, "c_kr_x", "fwd")
    assert s["n"] == 2 and s["sel"][2] == 1
    assert abs(s["sel"][0] - (3.0 - 0.5)) < 1e-9          # 날짜 평균 net − S_con
    assert abs(s["krw_raw"] - (0.02 + 0.04) * 220_000) < 1e-6


def test_gate_three_ways():
    ok, msg = fs.gate(30, 1.0, 3.0, 0.5)
    assert not ok and "30/50" in msg
    ok, msg = fs.gate(60, 1.0, 2.5, 0.5)
    assert ok and "승격 검토" in msg
    ok, msg = fs.gate(60, 1.0, 2.5, -0.5)
    assert not ok and "불일치" in msg
    ok, msg = fs.gate(60, 1.0, 2.5, None)
    assert not ok and "미상" in msg


def test_buyback_actionable_and_exit_filter():
    base = {"rule": "Y1", "h": 5, "elig_final": True, "entry": "2026-10-05", "r_px": 3.0, "ex": 1.5, "xk": 2.0, "xq": 1.0}
    settle = [dict(base, skey="a", actionable=True, exit="2026-10-09"),
              dict(base, skey="b", actionable=False, exit="2026-10-09"),
              dict(base, skey="c", actionable=True, exit="2026-10-20")]
    ledger = [{"qualifies": True, "rule": "Y1", "actionable": True, "entry_date": "2026-10-05"},
              {"qualifies": True, "rule": "Y1", "actionable": True, "entry_date": "2026-10-12"}]
    s = fs.buyback_summary(settle, ledger, "Y1", "2026-10-10")
    assert s["n"] == 1 and s["entries_fwd"] == 1 and s["ledger_backfill_settled"] == 1
    assert s["sel"][0] == 1.5 and abs(s["krw_raw"] - 0.0275 * 220_000) < 1e-6


def test_render_zero_forward():
    s = fs.buyback_summary([], [], "L1_lowliq", "2026-09-30")
    row = {"group": "g", "kind": "buyback", "id": "L1_lowliq", "context": False, "stats": s, "bf_mean": 2.1,
           "bf_src": "연구", "gate": fs.gate(0, None, None, 2.1)}
    md = fs.render("2026-09-30", [row], {"n": 3, "with_fwd": 1},
                   {"kr_last": "-", "us_last": "-", "generated": "-", "nobench": "0건"})
    assert "`L1_lowliq`" in md and "0/50" in md and "기록 전용 arm 3개" in md
