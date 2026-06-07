from __future__ import annotations

from datetime import datetime

import bot.market_utils as market_utils
from bot.market_utils import KST, MarketUtilsMixin, _market_close_anchor_at


class _MarketBot(MarketUtilsMixin):
    pass


def _dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 4, 27, hour, minute, tzinfo=KST)


def _freeze_now(monkeypatch, fixed: datetime) -> None:
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(market_utils, "datetime", FixedDateTime)


def test_us_close_anchor_before_open_is_next_0500() -> None:
    close_dt = _market_close_anchor_at("US", _dt(22, 24))
    assert close_dt == datetime(2026, 4, 28, 5, 0, tzinfo=KST)


def test_us_close_anchor_at_open_is_next_0500() -> None:
    close_dt = _market_close_anchor_at("US", _dt(22, 30))
    assert close_dt == datetime(2026, 4, 28, 5, 0, tzinfo=KST)


def test_us_close_anchor_after_midnight_is_same_day_0500() -> None:
    close_dt = _market_close_anchor_at("US", datetime(2026, 4, 28, 0, 30, tzinfo=KST))
    assert close_dt == datetime(2026, 4, 28, 5, 0, tzinfo=KST)


def test_us_close_anchor_near_close_is_same_day_0500() -> None:
    close_dt = _market_close_anchor_at("US", datetime(2026, 4, 28, 4, 55, tzinfo=KST))
    assert close_dt == datetime(2026, 4, 28, 5, 0, tzinfo=KST)


def test_us_close_anchor_after_close_rolls_to_next_day() -> None:
    close_dt = _market_close_anchor_at("US", datetime(2026, 4, 28, 5, 1, tzinfo=KST))
    assert close_dt == datetime(2026, 4, 29, 5, 0, tzinfo=KST)


def test_kr_close_anchor_is_same_day_1530() -> None:
    close_dt = _market_close_anchor_at("KR", _dt(10, 0))
    assert close_dt == datetime(2026, 4, 27, 15, 30, tzinfo=KST)


def test_known_kr_holiday_override_blocks_outdated_exchange_calendar() -> None:
    assert not market_utils._is_trading_day("KR", datetime(2026, 7, 17, tzinfo=KST).date())
    assert not market_utils._is_trading_day("kr", datetime(2026, 7, 17, tzinfo=KST).date())
    assert market_utils._is_trading_day("KR", datetime(2026, 6, 8, tzinfo=KST).date())


def test_order_allowed_blocks_non_trading_kr_session_inside_regular_hours(monkeypatch) -> None:
    fixed = datetime(2026, 6, 6, 10, 0, tzinfo=KST)
    checked = []

    _freeze_now(monkeypatch, fixed)

    def fake_is_trading_day(market, check_date=None):
        checked.append((market, check_date))
        return False

    monkeypatch.setattr(market_utils, "_is_trading_day", fake_is_trading_day)

    assert not _MarketBot()._is_order_allowed_now("KR")
    assert checked == [("KR", fixed.date())]


def test_order_allowed_allows_trading_kr_session_inside_regular_hours(monkeypatch) -> None:
    fixed = datetime(2026, 6, 8, 10, 0, tzinfo=KST)

    _freeze_now(monkeypatch, fixed)
    monkeypatch.setattr(market_utils, "_is_trading_day", lambda market, check_date=None: True)

    assert _MarketBot()._is_order_allowed_now("KR")


def test_order_allowed_uses_previous_us_session_date_after_midnight(monkeypatch) -> None:
    fixed = datetime(2026, 6, 6, 1, 0, tzinfo=KST)
    checked = []

    _freeze_now(monkeypatch, fixed)

    def fake_is_trading_day(market, check_date=None):
        checked.append((market, check_date))
        return True

    monkeypatch.setattr(market_utils, "_is_trading_day", fake_is_trading_day)

    assert _MarketBot()._is_order_allowed_now("US")
    assert checked == [("US", datetime(2026, 6, 5, tzinfo=KST).date())]
