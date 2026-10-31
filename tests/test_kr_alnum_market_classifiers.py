# -*- coding: utf-8 -*-
"""KR/US 시장 분류 — 신규 상장 영숫자 코드(0009K0 등)를 US로 오분류하지 않는다(10-31)."""
from tools import backfill_instrument_health as B
from tools import execution_shortfall_report as E
from tools import exit_decision_scorer as X


def test_alphanumeric_kr_codes_classified_as_kr():
    for code in ("005930", "0009K0", "0126Z0"):
        assert X._market_of(code, {}) == "KR"
        assert not E._is_us_ticker(code)
        assert B._is_market(code, "KR") and not B._is_market(code, "US")


def test_us_tickers_stay_us():
    for t in ("AAPL", "SCHG", "BRK.B", "ARGX", "VRRM"):
        assert X._market_of(t, {}) == "US"
        assert E._is_us_ticker(t)
        assert B._is_market(t, "US") and not B._is_market(t, "KR")


def test_explicit_market_field_wins():
    assert X._market_of("0009K0", {"market": "us"}) == "US"
