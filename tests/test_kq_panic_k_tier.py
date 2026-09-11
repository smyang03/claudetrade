# -*- coding: utf-8 -*-
"""kq_panic_k_tier — 등급 경계와 **캐시 결측 가드**.

가장 중요한 것은 마지막 테스트다. `k=0`(급락 후보가 진짜 없음)은 사전등록 D의
의미 있는 등급인데, 가격 캐시에 그 날짜가 없어도 `build()`가 후보를 못 만들어
똑같이 0이 된다. 그 둘을 섞으면 forward 판정이 조용히 오염된다.
"""
import json

import pytest

from tools import kq_panic_k_tier as K


def test_tier_boundaries():
    assert K.tier_of(0) == "t0"
    assert K.tier_of(1) == "t1_19"
    assert K.tier_of(19) == "t1_19"
    assert K.tier_of(20) == "t20p"
    assert K.tier_of(500) == "t20p"


def _cache(dates, tickers):
    """dates에 바를 가진 tickers개 종목. 후보 조건은 만족하지 않는 평탄한 바."""
    return {f"{i:06d}": [{"d": d, "o": 10000.0, "h": 10000.0, "l": 10000.0,
                          "c": 10000.0, "v": 1000.0, "amt": 1e7} for d in dates]
            for i in range(tickers)}


def _wire(monkeypatch, tmp_path, cache, signals):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    monkeypatch.setattr(K, "CACHE", cache_path)
    monkeypatch.setattr(K, "OUT", tmp_path / "out.jsonl")
    monkeypatch.setattr(K, "_signal_dates", lambda: signals)
    return tmp_path / "out.jsonl"


def _rows(path):
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_low_coverage_date_is_not_recorded_as_zero(monkeypatch, tmp_path):
    """캐시가 그 날짜를 거의 안 덮으면 기록하지 않는다 — k=0으로 세면 안 된다."""
    day = "2026-05-04"
    out = _wire(monkeypatch, tmp_path,
                _cache([day], K.MIN_COVERAGE - 1),
                {day: {"mode": "live", "strength": "normal"}})
    assert K.annotate() == 0
    assert _rows(out) == []          # 보류됐다


def test_covered_date_records_zero_tier(monkeypatch, tmp_path):
    """충분히 덮인 날짜에서 후보가 없으면 그건 진짜 t0다."""
    day = "2026-05-04"
    out = _wire(monkeypatch, tmp_path,
                _cache([day], K.MIN_COVERAGE + 50),
                {day: {"mode": "live", "strength": "normal"}})
    assert K.annotate() == 0
    rows = _rows(out)
    assert len(rows) == 1
    assert rows[0]["session_date"] == day
    assert rows[0]["k"] == 0 and rows[0]["tier"] == "t0"
    assert rows[0]["coverage"] >= K.MIN_COVERAGE
    assert rows[0]["mode"] == "live"


def test_idempotent_and_retries_held_dates(monkeypatch, tmp_path):
    """이미 기록한 날은 다시 안 쓰고, 보류된 날은 캐시가 채워지면 다음 실행에서 들어온다."""
    covered, thin = "2026-05-04", "2026-05-06"
    cache = _cache([covered], K.MIN_COVERAGE + 50)
    sigs = {covered: {"mode": "backfill", "strength": "normal"},
            thin: {"mode": "live", "strength": "strong"}}
    out = _wire(monkeypatch, tmp_path, cache, sigs)

    assert K.annotate() == 0
    assert [r["session_date"] for r in _rows(out)] == [covered]

    assert K.annotate() == 0                      # 멱등 — 중복 기록 없음
    assert [r["session_date"] for r in _rows(out)] == [covered]

    # 캐시가 보류된 날짜까지 덮이면 그때 들어온다
    cache2 = _cache([covered, thin], K.MIN_COVERAGE + 50)
    K.CACHE.write_text(json.dumps(cache2), encoding="utf-8")
    assert K.annotate() == 0
    assert sorted(r["session_date"] for r in _rows(out)) == [covered, thin]


def test_missing_cache_returns_nonzero(monkeypatch, tmp_path):
    monkeypatch.setattr(K, "CACHE", tmp_path / "nope.json")
    monkeypatch.setattr(K, "OUT", tmp_path / "out.jsonl")
    monkeypatch.setattr(K, "_signal_dates", dict)
    assert K.annotate() == 1


@pytest.mark.parametrize("k,tier", [(0, "t0"), (5, "t1_19"), (20, "t20p")])
def test_tier_of_parametrized(k, tier):
    assert K.tier_of(k) == tier


def test_recent_held_date_warns(monkeypatch, tmp_path, capsys):
    """최근 신호일이 캐시 결측으로 보류되면 WARN을 낸다.

    오래된 날짜 보류는 정상이지만, 최근 것이 보류되면 캐시 갱신이 멈춘 것이다.
    경고가 없으면 '보류'가 정상처럼 보여 forward가 영영 안 쌓인다.
    """
    from datetime import date, timedelta

    recent = (date.today() - timedelta(days=2)).isoformat()
    old = "2019-01-10"
    covered = (date.today() - timedelta(days=3)).isoformat()
    _wire(monkeypatch, tmp_path, _cache([covered], K.MIN_COVERAGE + 50),
          {recent: {"mode": "live", "strength": "normal"},
           old: {"mode": "backfill", "strength": "normal"}})
    assert K.annotate() == 0
    out = capsys.readouterr().out
    assert "[KTIER][WARN]" in out
    assert recent in out
    assert old not in out.split("[KTIER][WARN]")[1]   # 오래된 날짜는 경고에 안 들어간다


def test_no_warn_when_only_old_dates_held(monkeypatch, tmp_path, capsys):
    from datetime import date, timedelta

    covered = (date.today() - timedelta(days=3)).isoformat()
    _wire(monkeypatch, tmp_path, _cache([covered], K.MIN_COVERAGE + 50),
          {"2019-01-10": {"mode": "backfill", "strength": "normal"}})
    assert K.annotate() == 0
    assert "[KTIER][WARN]" not in capsys.readouterr().out
