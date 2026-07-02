"""트렌드 방어 오버레이 게이트 로직 테스트."""
from datetime import datetime, timedelta, timezone

from bot.trend_overlay_gate import (
    evaluate_trend_overlay_gate,
    normalize_mode,
)


def _sig(market="US", below=True, age_days=0.0, sym="SPY"):
    # staleness는 generated_at(갱신 시각) 기준. as_of(월말종가 날짜)는 월간이라 묵어도 정상.
    gen = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"generated_at": gen,
            "markets": {market: {"index_sym": sym, "index_close": 100.0,
                                 "sma": 110.0 if below else 90.0,
                                 "below_sma": below, "as_of": "2026-06-01"}}}


def test_normalize_mode_defaults_off():
    assert normalize_mode(None) == "off"
    assert normalize_mode("garbage") == "off"
    assert normalize_mode("SHADOW") == "shadow"
    assert normalize_mode("Enforce") == "enforce"


def test_downtrend_shadow_observes_but_does_not_block():
    v = evaluate_trend_overlay_gate(_sig(below=True), "shadow", "US")
    assert v["trusted"] is True
    assert v["below_sma"] is True
    assert v["would_skip"] is True
    assert v["block"] is False  # shadow는 관측만


def test_downtrend_enforce_blocks():
    v = evaluate_trend_overlay_gate(_sig(below=True), "enforce", "US")
    assert v["would_skip"] is True
    assert v["block"] is True


def test_uptrend_allows_entry():
    v = evaluate_trend_overlay_gate(_sig(below=False), "enforce", "US")
    assert v["below_sma"] is False
    assert v["block"] is False
    assert v["would_skip"] is False


def test_missing_market_fail_open():
    v = evaluate_trend_overlay_gate(_sig(market="US"), "enforce", "KR")
    assert v["trusted"] is False
    assert v["block"] is False
    assert v["reason"] == "no_signal"


def test_stale_signal_fail_open():
    v = evaluate_trend_overlay_gate(_sig(below=True, age_days=30.0), "enforce", "US")
    assert v["trusted"] is False
    assert v["block"] is False
    assert v["reason"] == "stale_signal"


def test_empty_signal_fail_open():
    v = evaluate_trend_overlay_gate({}, "enforce", "US")
    assert v["block"] is False
    assert v["trusted"] is False
