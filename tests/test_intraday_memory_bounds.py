"""2026-10-02 봇 메모리 누수 수리 회귀 — 분봉 캐시 3곳이 세션을 넘어 쌓이지 않는다."""
from __future__ import annotations

from datetime import datetime, timedelta

import kis_api
from runtime.intraday_minute_cache import IntradayMinuteCache
from runtime.time_normalized_rvol import LocalTimeNormalizedRvolStore


def _write_csv(path, days: int):
    lines = ["ts,volume"]
    d0 = datetime(2026, 5, 1)
    for i in range(days):
        d = (d0 + timedelta(days=i)).strftime("%Y-%m-%d")
        lines += [f"{d}T09:00:00+09:00,100", f"{d}T09:01:00+09:00,120"]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_rvol_store_keeps_recent_sessions_and_drops_old_session_keys(tmp_path):
    (tmp_path / "kr").mkdir()
    _write_csv(tmp_path / "kr" / "kr_005930.csv", 120)
    store = LocalTimeNormalizedRvolStore(root=tmp_path, lookback_sessions=20)
    rows = store._history("KR", "005930", "2026-08-25")
    assert len({r["ts"][:10] for r in rows}) == 40                     # lookback × 2 세션만 보관
    assert all(r["ts"][:10] < "2026-08-25" for r in rows)
    store._history("KR", "005930", "2026-08-26")
    assert [k[2] for k in store._cache] == ["2026-08-26"]              # 지난 세션 키 제거


def test_minute_cache_drops_previous_session_keys_on_store():
    cache = IntradayMinuteCache.__new__(IntradayMinuteCache)
    import threading
    cache._lock = threading.Lock()
    cache._now = lambda: 0.0
    cache.ttl_sec = 30
    cache._cache, cache._bar_cache = {}, {}
    old = ("KR", "2026-09-30", "005930", "kis")
    new = ("KR", "2026-10-01", "005930", "kis")
    us = ("US", "2026-09-30", "AAPL", "yfinance")
    cache._store(old, {"data_quality": "ok"}, bars=[{"ts": "a"}])
    cache._store(us, {"data_quality": "ok"}, bars=[{"ts": "b"}])
    cache._store(new, {"data_quality": "ok"}, bars=[{"ts": "c"}])
    assert set(cache._cache) == {new, us} and set(cache._bar_cache) == {new, us}   # 다른 시장은 유지


def test_kis_intraday_cache_prunes_expired_entries(monkeypatch):
    monkeypatch.setattr(kis_api, "KIS_CACHE_TTL_SEC", 30)
    cache = {"old": {"value": 1, "ts": datetime.now() - timedelta(seconds=120)},
             "fresh": {"value": 2, "ts": datetime.now()}}
    kis_api._cache_prune_expired(cache)
    assert list(cache) == ["fresh"]
