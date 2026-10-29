# -*- coding: utf-8 -*-
"""작업 시작 전 DNS 준비 대기(10-30) — 아침 일시 장애 회귀 방지."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from net_ready import wait_dns  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_passes_immediately_when_resolvable():
    c, out = Clock(), []
    assert wait_dns(["a"], resolve=lambda h: None, sleep=c.sleep, clock=c, out=out.append)
    assert c.t == 0 and out == []


def test_waits_until_dns_recovers():
    c, out, calls = Clock(), [], []

    def resolve(h):
        calls.append(c.t)
        if c.t < 30:
            raise OSError(11001, "getaddrinfo failed")
    assert wait_dns(["openapi.koreainvestment.com"], resolve=resolve, sleep=c.sleep, clock=c, out=out.append)
    assert c.t == 30 and "복구" in out[-1]


def test_gives_up_after_timeout_and_reports():
    c, out = Clock(), []

    def resolve(h):
        raise OSError(11001, "getaddrinfo failed")
    assert wait_dns(["x"], timeout=120, resolve=resolve, sleep=c.sleep, clock=c, out=out.append) is False
    assert c.t == 120 and out[-1].startswith("[net] ⚠️")
