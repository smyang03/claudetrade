"""Network diagnostics for audit-lab data collection."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Callable


GetFunc = Callable[..., object]

DEFAULT_URLS = (
    "https://www.google.com",
    "https://query1.finance.yahoo.com/v8/finance/chart/ABBV?range=5d&interval=1d",
)


@dataclass(frozen=True)
class NetworkCheck:
    url: str
    status: str
    status_code: int | None
    error_type: str
    error_msg: str


def _default_get(url: str, *, timeout: int):
    import requests

    return requests.get(url, timeout=timeout)


def diagnose_network(
    urls: tuple[str, ...] = DEFAULT_URLS,
    *,
    timeout: int = 10,
    getter: GetFunc | None = None,
) -> dict:
    get = getter or _default_get
    checks: list[NetworkCheck] = []
    for url in urls:
        try:
            response = get(url, timeout=timeout)
            status_code = int(getattr(response, "status_code", 0) or 0)
            checks.append(NetworkCheck(url, "ok" if 200 <= status_code < 400 else "http_error", status_code, "", ""))
        except Exception as exc:
            checks.append(NetworkCheck(url, "failed", None, type(exc).__name__, str(exc)))

    failed = [check for check in checks if check.status != "ok"]
    win10013 = any("WinError 10013" in check.error_msg for check in failed)
    return {
        "python_executable": sys.executable,
        "checks": [asdict(check) for check in checks],
        "all_ok": not failed,
        "blocked_by_os_policy": win10013,
        "recommendation": (
            "Windows 방화벽/보안 정책에서 현재 python.exe의 아웃바운드 HTTPS가 차단된 상태입니다."
            if win10013
            else "개별 URL 오류입니다. 프록시, DNS, 데이터 제공자 상태를 확인하세요."
            if failed
            else "네트워크 연결 정상입니다."
        ),
    }
