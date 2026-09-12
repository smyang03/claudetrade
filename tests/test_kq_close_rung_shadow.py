# -*- coding: utf-8 -*-
"""kq_close_rung_shadow — 조건 경계와 **라이브 원장 불가침**.

가장 중요한 것은 마지막 두 개다.
· 이 모듈은 패닉 원장(`kr_index_etf_panic_shadow`)에 **한 행도 쓰지 않아야** 한다.
  `forward_gate_watch`·`kq_panic_k_tier`·`event_family_report`가 그 원장을
  `kind == "trade"`로 읽기 때문에, 섞이면 v2 신호 수와 성과가 조용히 오염된다.
· 다음날 시가가 없는 날은 **기록하지 않고 보류**해야 한다. 미완성 밤을 0으로 적으면
  forward 표본이 오염된다.
"""
import json

import pytest

from tools import kq_close_rung_shadow as R
from tools import kr_index_etf_panic_shadow as P


def _c(chg, gap):
    return {"kq_chg": chg, "kq_gap": gap}


@pytest.mark.parametrize("chg,gap,hit", [
    (-2.2, -0.3, True),      # 구간 안 · 갭 얕음 → rung
    (-2.5, -0.3, False),     # 경계: -2.5는 v2(종가 ≤ -2.5)라 제외
    (-2.0, -0.3, True),      # 경계: -2.0은 구간에 포함
    (-1.9, -0.3, False),     # 구간 위
    (-2.2, -1.0, False),     # 갭이 -1 이하면 이미 v2 신호
    (-2.2, -3.0, False),     # 강신호 갭 — 당연히 v2
    (-2.2, None, True),      # 갭 결측이면 종가만 본다
    (None, -0.3, False),     # 종가 결측이면 판단 불가
])
def test_rung_boundaries(chg, gap, hit):
    assert R.is_rung(_c(chg, gap)) is hit


def test_rung_never_overlaps_v2_signal():
    """rung 집합과 v2 신호 집합은 서로소여야 한다 — 겹치면 이중 계상이다."""
    for chg in [x / 10 for x in range(-40, 0)]:
        for gap in [None] + [x / 10 for x in range(-40, 11)]:
            c = _c(chg, gap)
            gap_hit = gap is not None and gap <= P.GAP_THR
            v2 = gap_hit or chg <= P.THR
            assert not (R.is_rung(c) and v2), f"겹침 chg={chg} gap={gap}"


def _wire(monkeypatch, tmp_path, cache):
    monkeypatch.setattr(R, "OUT", tmp_path / "rung.jsonl")
    monkeypatch.setattr(P, "_read_cache", lambda: cache)
    return tmp_path / "rung.jsonl"


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _cache(rows):
    """rows: [(date, kq_chg, kq_gap, close, next_open)] — next_open은 다음 날짜의 open."""
    out = {}
    for d, chg, gap, close in rows:
        out[d] = {"kq_chg": chg, "kq_gap": gap, f"{R.TK}_close": close,
                  f"{R.TK}_open": close}
    return out


def test_records_signal_day_with_next_open(monkeypatch, tmp_path):
    cache = _cache([("2020-05-04", -2.2, -0.3, 10000.0),
                    ("2020-05-06", 0.5, 0.2, 10100.0)])
    out = _wire(monkeypatch, tmp_path, cache)
    assert R.annotate() == 0
    rows = _rows(out)
    assert len(rows) == 1
    r = rows[0]
    assert r["session_date"] == "2020-05-04" and r["exit_date"] == "2020-05-06"
    assert r["overnight_pct"] == pytest.approx(1.0)
    assert r["net_pct"] == pytest.approx(1.0 - P.COST)
    assert r["units"] == 1                       # 2단 사이징 미적용
    assert r["mode"] == "backfill"


def test_last_day_is_held_not_recorded(monkeypatch, tmp_path):
    """마지막 날은 다음 시가가 원리상 없다 — 기록하면 안 된다."""
    cache = _cache([("2020-05-04", 0.1, 0.1, 10000.0),
                    ("2020-05-06", -2.2, -0.3, 10100.0)])
    out = _wire(monkeypatch, tmp_path, cache)
    assert R.annotate() == 0
    assert _rows(out) == []


def test_idempotent(monkeypatch, tmp_path):
    cache = _cache([("2020-05-04", -2.2, -0.3, 10000.0),
                    ("2020-05-06", 0.5, 0.2, 10100.0)])
    out = _wire(monkeypatch, tmp_path, cache)
    assert R.annotate() == 0
    assert R.annotate() == 0
    assert len(_rows(out)) == 1


def test_forward_mode_boundary(monkeypatch, tmp_path):
    """FORWARD_START 이후만 live다 — 백필을 forward 표본으로 세면 판정이 무너진다."""
    cache = _cache([(R.FORWARD_START, -2.2, -0.3, 10000.0),
                    ("2099-01-01", 0.5, 0.2, 10100.0)])
    out = _wire(monkeypatch, tmp_path, cache)
    assert R.annotate() == 0
    assert _rows(out)[0]["mode"] == "live"


def test_missing_cache_returns_nonzero(monkeypatch, tmp_path):
    _wire(monkeypatch, tmp_path, {})
    assert R.annotate() == 1


def test_does_not_write_to_panic_ledger(monkeypatch, tmp_path):
    """라이브 원장 불가침 — 이 모듈이 돈 뒤에도 패닉 원장 행 수가 같아야 한다."""
    before = len(P._load())
    cache = _cache([("2020-05-04", -2.2, -0.3, 10000.0),
                    ("2020-05-06", 0.5, 0.2, 10100.0)])
    _wire(monkeypatch, tmp_path, cache)
    called = []
    monkeypatch.setattr(P, "_append", lambda row: called.append(row))
    assert R.annotate() == 0
    assert called == [], "패닉 원장에 썼다"
    assert len(P._load()) == before


def test_live_thresholds_untouched():
    """이 모듈은 라이브 문턱을 바꾸지 않는다 — 상수 고정."""
    assert P.GAP_THR == -1.0
    assert P.THR == -2.5
    assert P.UNITS == {"strong": 2, "normal": 1, "unknown": 1}
