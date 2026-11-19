# -*- coding: utf-8 -*-
"""KR 개장 지연일(수능·새해 첫 거래일) 주문 창 + L7 1회 지연 진입 (11-19 수능일 24건 전량 거부 수리). 네트워크 없음."""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import kr_index_inclusion_live as L  # noqa: E402
import live_lane_common as C  # noqa: E402


def _kst(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=C.KST)


def test_session_delay_suneung_and_new_year():
    assert C.kr_session_delay_min(date(2026, 11, 19)) == (60, 60)
    assert C.kr_session_delay_min(date(2027, 1, 4)) == (60, 0)     # 01-01 휴장 → 01-04가 새해 첫 거래일
    assert C.kr_session_delay_min(date(2027, 1, 5)) == (0, 0)
    assert C.kr_session_delay_min(date(2026, 11, 20)) == (0, 0)


def test_order_window_shifts_on_suneung():
    assert C.kr_order_window("open_auction", _kst(2026, 11, 19, 8, 41))[0] is False     # 오늘 실측 거부 시각
    assert C.kr_order_window("open_auction", _kst(2026, 11, 19, 9, 41))[0] is True
    assert C.kr_order_window("close_auction", _kst(2026, 11, 19, 15, 25))[0] is False   # 수능일 마감 16:30
    assert C.kr_order_window("close_auction", _kst(2026, 11, 19, 16, 25))[0] is True
    assert C.kr_order_window("open_auction", _kst(2026, 11, 20, 8, 41))[0] is True      # 평소 날 불변
    assert C.kr_order_window("close_auction", _kst(2027, 1, 4, 15, 25))[0] is True      # 새해 첫날 마감은 그대로


def test_wait_for_delayed_open_sleeps_only_on_delayed_day():
    slept = []
    msg = C.kr_wait_for_delayed_open("open_auction", _kst(2026, 11, 19, 8, 41), sleep=slept.append)
    assert msg and abs(slept[0] - 3600) < 1                                             # 08:41 → 09:41
    assert C.kr_wait_for_delayed_open("open_auction", _kst(2026, 11, 20, 8, 41), sleep=slept.append) is None
    assert C.kr_wait_for_delayed_open("open_auction", _kst(2026, 11, 19, 9, 50), sleep=slept.append) is None
    assert len(slept) == 1


def test_late_due_one_shot_rules():
    rounds = {"2026-11-18": {"adds": [], "effective": "2026-12-11"}}
    rej = [{"kind": "order", "side": "buy", "round": "2026-11-18", "session": "2026-11-19", "state": "rejected"}]
    assert L.late_due(rounds, rej, "2026-11-18", date(2026, 11, 20)) == [("2026-11-18", rounds["2026-11-18"], 1)]
    assert L.late_due(rounds, rej, "2026-11-18", date(2026, 11, 19)) == []              # 진입일 당일은 정규 경로
    assert L.late_due(rounds, rej, "2026-11-18", date(2026, 11, 25)) == []              # 지연 2세션 초과
    assert L.late_due(rounds, rej, None, date(2026, 11, 20)) == []                      # 지시 없으면 안 함
    ok = rej + [{"kind": "order", "side": "buy", "round": "2026-11-18", "session": "2026-11-20", "state": "ok"}]
    assert L.late_due(rounds, ok, "2026-11-18", date(2026, 11, 20)) == []               # 이미 지연 주문 → 1회만
