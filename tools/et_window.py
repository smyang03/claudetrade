# -*- coding: utf-8 -*-
"""US 장 기준 예약 작업의 ET 창 가드 (2026-11-01).

예약 작업은 KST 고정 시각으로 돈다. 미국 서머타임이 바뀌면 같은 KST 시각이 ET로 한 시간 밀린다.
그래서 US 장에 묶인 작업은 여름 시각·겨울 시각(+1시간) 트리거를 둘 다 걸고, 스크립트가 `--et-window`로
그날 계절에 맞는 쪽만 일하게 한다. 창 밖 실행은 아무것도 쓰지 않고 rc 0으로 끝난다.
  예: --et-window 09:31-10:30   (시작 포함, 끝 제외)
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def parse_window(spec: str) -> tuple[tuple[int, int], tuple[int, int]]:
    a, b = spec.split("-")
    h1, m1 = (int(x) for x in a.split(":"))
    h2, m2 = (int(x) for x in b.split(":"))
    return (h1, m1), (h2, m2)


def in_et_window(spec: str | None, now_et: datetime | None = None) -> bool:
    """spec이 없으면 항상 True(수동 실행·다른 체인 호출은 그대로)."""
    if not spec:
        return True
    lo, hi = parse_window(spec)
    t = now_et or datetime.now(ET)
    return lo <= (t.hour, t.minute) < hi
