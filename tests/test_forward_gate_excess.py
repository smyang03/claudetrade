# -*- coding: utf-8 -*-
"""forward_gate_watch v3 — 판정 기준이 원 net이 아니라 **지수 초과분**인지 고정한다.

09-12 개정의 핵심은 하나다: 강세장에서 원 net은 모든 롱 전략을 통과시킨다.
KR 쉐도우 실측에서 원 net +4.98%가 같은 창 KODEX200을 빼면 −0.71%였다.
그래서 아래 두 테스트가 제일 중요하다.
  · 지수가 전략보다 더 오른 구간에서 **REFUTED**가 나오는가(원 net은 양수인데).
  · 벤치를 못 구하면 **원 net으로 조용히 되돌아가지 않는가**.
"""
import sqlite3

import pytest

from tools import forward_gate_watch as G


def _db(rows, note="TP12/SL25/D5 테스트"):
    """메모리 DB에 (session_date, net_pct, status) 행을 넣는다. sid는 KR 계열."""
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE trades (strategy_id TEXT, session_date TEXT, net_pct REAL, "
                "status TEXT, backfill INTEGER)")
    con.execute("CREATE TABLE strategies (id TEXT, note TEXT)")
    con.execute("INSERT INTO strategies VALUES (?,?)", ("c_kr_fallen_regime", note))
    con.executemany("INSERT INTO trades VALUES ('c_kr_fallen_regime',?,?,?,0)", rows)
    return con


def _bars(days, *, open_px, close_px):
    """(date, open, high, low, close, ...) 형태의 벤치 봉."""
    return [(d, open_px, open_px, open_px, close_px) for d in days]


@pytest.fixture(autouse=True)
def _clear_cache():
    G._BENCH_CACHE.clear()
    yield
    G._BENCH_CACHE.clear()


def _wire_bench(monkeypatch, bars):
    monkeypatch.setattr(G, "bench_index",
                        lambda market: (bars, {b[0]: i for i, b in enumerate(bars)}) if bars else (None, None))


def test_excess_subtracts_same_window_index(monkeypatch):
    """전략 +5%, 같은 창 지수 +10% → 초과는 −5%여야 한다."""
    days = [f"2026-03-{d:02d}" for d in range(1, 12)]
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=110.0))   # 창 수익 +10%
    con = _db([(days[0], 5.0, "CLOSED")])
    s = G.forward_arm_stats(con)["c_kr_fallen_regime"]
    assert s["raw_session_mean"] == pytest.approx(5.0)
    assert s["session_mean"] == pytest.approx(-5.0)
    assert s["metric"] == "index_excess" and s["benchmark"] == "069500"


def test_positive_raw_but_negative_excess_is_refuted(monkeypatch):
    """원 net은 양수인데 지수가 더 오른 경우 — v2는 통과시켰고 v3는 REFUTED다."""
    days = [f"2026-03-{d:02d}" for d in range(1, 40)]
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=110.0))
    # 세션마다 원 net을 +4~+6%로 흔들어 분산을 준다(전부 같으면 t가 정의되지 않는다).
    rows = [(days[i], 5.0 + (i % 3 - 1), "CLOSED") for i in range(30)]
    con = _db(rows)
    s = G.forward_arm_stats(con)["c_kr_fallen_regime"]
    assert s["raw_session_mean"] > 0                      # 원 net은 양수
    assert s["session_mean"] < 0                          # 초과는 음수
    assert s["session_t"] is not None and s["session_t"] < 0
    assert G.verdict("c_kr_fallen_regime", s) == "REFUTED"


def test_bench_unavailable_does_not_fall_back_to_raw(monkeypatch):
    """벤치가 없으면 원 net으로 판정하지 않는다 — 되돌아가면 개정이 무의미하다."""
    _wire_bench(monkeypatch, None)
    days = [f"2026-03-{d:02d}" for d in range(1, 40)]
    con = _db([(days[i], 5.0, "CLOSED") for i in range(30)])
    s = G.forward_arm_stats(con)["c_kr_fallen_regime"]
    assert s["bench_available"] is False
    assert s["session_mean"] is None                      # 초과를 못 냈다
    assert s["raw_session_mean"] == pytest.approx(5.0)    # 원 net은 남아 있다(관측용)
    assert G.verdict("c_kr_fallen_regime", s) == "BENCH_UNAVAILABLE"


def test_hold_horizon_comes_from_strategies_not_note(monkeypatch):
    """보유기간 정본은 `virtual_books.STRATEGIES["hold"]`다 — note 표기가 아니다.

    09-16 정정. 전에는 note의 D<n>을 읽고 없으면 "KR이면 5"로 추정했는데, 56 arm 중 46개는
    note에 D표기가 없고 KR 가상 북은 **전부 hold=7**이다. 짧은 창을 대면 벤치 수익이
    작게 잡혀 **초과가 과대평가**된다. 그래서 note와 소스가 엇갈리면 **소스가 이긴다.**
    """
    days = [f"2026-03-{d:02d}" for d in range(1, 20)]
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=110.0))
    # note는 D10이라 우기지만 c_kr_fallen_regime의 실제 계약은 hold=7이다.
    con = _db([(days[0], 5.0, "CLOSED")], note="E3 — ... TP12/SL25/D10. 반증: forward 30건 t<0")
    assert G.forward_arm_stats(con)["c_kr_fallen_regime"]["hold_sessions"] == 7
    # note가 비어도 추정(KR=5)으로 떨어지지 않는다.
    con2 = _db([(days[0], 5.0, "CLOSED")], note="보유기간 표기 없음")
    assert G.forward_arm_stats(con2)["c_kr_fallen_regime"]["hold_sessions"] == 7


def test_unknown_arm_falls_back_to_note(monkeypatch):
    """STRATEGIES에 없는(은퇴한) id만 note 표기로 보조한다."""
    import sqlite3
    days = [f"2026-03-{d:02d}" for d in range(1, 20)]
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=110.0))
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE strategies (id TEXT, note TEXT)")
    con.execute("INSERT INTO strategies VALUES (?,?)", ("c_retired_arm", "옛 계약 TP12/SL25/D10"))
    assert G.arm_hold(con, "c_retired_arm", "KR") == 10


def test_open_session_still_excluded(monkeypatch):
    """만기 편향 가드는 유지 — 같은 세션에 OPEN이 섞이면 그 세션은 안 센다."""
    days = [f"2026-03-{d:02d}" for d in range(1, 12)]
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=100.0))
    con = _db([(days[0], 5.0, "CLOSED"), (days[0], None, "OPEN")])
    s = G.forward_arm_stats(con)["c_kr_fallen_regime"]
    assert s["n"] == 0 and s["sessions"] == 0
    assert s["incomplete_sessions"] == 1


def test_bench_missing_row_is_counted_not_substituted(monkeypatch):
    """벤치 창이 모자란 행은 초과에서 빠지되 원 net으로 대체하지 않는다."""
    days = [f"2026-03-{d:02d}" for d in range(1, 4)]      # 창(D5)을 채울 봉이 부족
    _wire_bench(monkeypatch, _bars(days, open_px=100.0, close_px=100.0))
    con = _db([(days[0], 5.0, "CLOSED")])
    s = G.forward_arm_stats(con)["c_kr_fallen_regime"]
    assert s["bench_missing_rows"] == 1
    assert s["n"] == 0                                    # 초과 표본에는 안 들어갔다
    assert s["raw_session_mean"] == pytest.approx(5.0)
