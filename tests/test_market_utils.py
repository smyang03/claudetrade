from __future__ import annotations

from datetime import datetime

from bot.market_utils import KST, _market_close_anchor_at


def _dt(hour: int, minute: int) -> datetime:
    return datetime(2026, 4, 27, hour, minute, tzinfo=KST)


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
