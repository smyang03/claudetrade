# -*- coding: utf-8 -*-
"""봇 US 일정이 서머타임을 따라간다(11-01) — 여름 22:20~05:00 KST · 겨울 23:20~06:00 KST."""
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

import trading_bot as tb

KST = ZoneInfo("Asia/Seoul")


def test_shift_is_zero_in_summer_one_in_winter():
    assert tb._us_kst_shift_hours(datetime(2026, 10, 30, 22, 20, tzinfo=KST)) == 0
    assert tb._us_kst_shift_hours(datetime(2026, 11, 2, 23, 20, tzinfo=KST)) == 1
    assert tb._us_kst_shift_hours(datetime(2027, 3, 15, 22, 20, tzinfo=KST)) == 0   # 2027-03-14 서머타임 시작


def test_shift_hm_wraps_midnight():
    assert tb._shift_hm(22, 20, 1) == dt_time(23, 20)
    assert tb._shift_hm(23, 30, 1) == dt_time(0, 30)
    assert tb._shift_hm(5, 0, 0) == dt_time(5, 0)


def test_us_jobs_registered_for_both_seasons_and_gated(monkeypatch):
    """_every_us_day_at은 main 안의 클로저라, 같은 규칙(두 시각 + 계절 게이트)을 schedule로 재현해 확인한다."""
    import schedule
    sched = schedule.Scheduler()
    ran = []

    def every_us_day_at(hhmm, fn, *args):
        hh, mm = (int(x) for x in hhmm.split(":"))
        for k in (0, 1):
            def _job(_k=k):
                if tb._us_kst_shift_hours() == _k:
                    fn(*args)
            sched.every().day.at(tb._shift_hm(hh, mm, k).strftime("%H:%M")).do(_job)

    every_us_day_at("22:20", lambda m: ran.append(m), "US")
    assert sorted(j.at_time.strftime("%H:%M") for j in sched.jobs) == ["22:20", "23:20"]
    monkeypatch.setattr(tb, "_us_kst_shift_hours", lambda now=None: 1)
    for j in sched.jobs:
        j.job_func()
    assert ran == ["US"]                       # 겨울엔 23:20 잡만 실행


def test_bot_source_uses_dst_helper_for_us_schedule():
    """US 일정 등록이 고정 schedule.at이 아니라 계절 게이트를 거친다 — 회귀 방지."""
    src = open(tb.__file__, encoding="utf-8").read()
    assert 'schedule.every().day.at("22:20").do(bot.session_open, "US")' not in src
    assert '_every_us_day_at("22:20", bot.session_open, "US")' in src
    assert '_every_us_day_at("05:00", bot.session_close, "US")' in src
