# -*- coding: utf-8 -*-
"""예약 작업 시작 전 네트워크(DNS) 준비 대기 — 10-30 도입.

10월 실측: 08:30~08:42 작업이 DNS 실패(getaddrinfo 11001)로 죽는 날이 반복됐다(10-15·21·22·23 — 자사주 스캔·캐너리 매수·
패닉 매도). 같은 날 09:10 재실행은 매번 성공 → 수 분짜리 일시 장애다. 그 결과 캐너리 실행 가능 신호 6건 중
2건(삼성E&A DNS · 에스원 아침 스캔 중단)을 놓쳤다. 주문을 내기 전 단계에서 이름 해석이 될 때까지 기다린다.
이름이 바로 풀리면 즉시 통과한다(정상 날 지연 0). 주문 로직은 건드리지 않는다.
"""
from __future__ import annotations

import socket
import time
from typing import Callable, Iterable


def wait_dns(hosts: Iterable[str], timeout: float = 120.0, poll: float = 10.0, *, tag: str = "net",
             resolve: Callable[[str], object] | None = None, sleep: Callable[[float], None] | None = None,
             clock: Callable[[], float] | None = None, out: Callable[[str], None] = print) -> bool:
    """모든 host가 해석될 때까지 최대 timeout초 대기. 성공 True, 시간 초과 False(호출 측은 평소처럼 진행 — 실패는 원래 경로가 처리)."""
    resolve = resolve or (lambda h: socket.getaddrinfo(h, 443))
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    hosts = list(hosts)
    t0, waited = clock(), False
    while True:
        bad = []
        for h in hosts:
            try:
                resolve(h)
            except OSError:
                bad.append(h)
        if not bad:
            if waited:
                out(f"[{tag}] 네트워크 복구 확인({clock() - t0:.0f}초 대기)")
            return True
        if clock() - t0 >= timeout:
            out(f"[{tag}] ⚠️ DNS 해석 실패 {timeout:.0f}초 지속: {', '.join(bad)} — 그대로 진행")
            return False
        if not waited:
            out(f"[{tag}] DNS 해석 실패({', '.join(bad)}) — 최대 {timeout:.0f}초 대기")
            waited = True
        sleep(poll)
