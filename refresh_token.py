"""KIS API 토큰 사전 갱신 스크립트 — schtasks에서 호출.

2026-09-10: 자체 로그 추가. `claudetrade_token_am`이 09-09·09-10 이틀 연속 rc=1이었는데
schtasks 액션에 출력 리다이렉트가 없고 TaskScheduler 이벤트 로그도 비활성이라
**실패 사유가 어디에도 남지 않았다**(수동 실행은 성공, token_pm은 정상, 두 태스크 정의는 동일).
스케줄 정의를 건드리지 않고 스크립트가 직접 `logs/system/token_refresh.log`에 남긴다.

2026-09-11: 그 로그가 바로 원인을 잡았다 —
  `EGW00133 접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)` / cooldown 70초.
08:20은 KR 장 준비 구간이라 봇·프리오픈 스케줄러가 같은 분에 토큰을 발급받고,
이 스크립트의 `force_refresh`가 **1분당 1회 제한**에 부딪힌다. 21:50(PM)은 KR이 조용해서 안 겹쳤다.
→ rate limit이면 서버가 알려준 `retry_after_sec`만큼 기다렸다 **한 번 더** 시도한다. 그래도 막히면 실패로 남긴다.
"""
import os
import sys
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
# Task Scheduler starts actions with an empty Start In directory.  Resolve the
# live env and token paths from the project root instead of System32.
os.chdir(ROOT)
sys.path.insert(0, ROOT)

LOG_PATH = os.path.join(ROOT, "logs", "system", "token_refresh.log")
MAX_WAIT_SEC = 120          # 예약 작업이 무한정 물고 있지 않도록 상한


def _log(line: str) -> None:
    """실패해도 갱신 자체를 막지 않는다 — 로그는 진단용이다."""
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    text = f"{stamp} {line}"
    print(text)
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        from kis_api import get_access_token, IS_PAPER, KISTokenRateLimitError
    except Exception as exc:  # noqa: BLE001
        _log(f"[ERROR] kis_api import 실패: {type(exc).__name__}: {exc}")
        _log("[TRACE] " + traceback.format_exc(limit=6).replace("\n", " | "))
        return 1

    mode = "paper" if IS_PAPER else "live"
    _log(f"[START] KIS {mode} 토큰 강제 갱신 요청 (호출자 pid {os.getpid()})")
    for attempt in (1, 2):
        try:
            get_access_token(force_refresh=True)
        except KISTokenRateLimitError as exc:
            wait = min(int(getattr(exc, "retry_after_sec", 0) or 0) + 5, MAX_WAIT_SEC)
            if attempt == 1 and wait > 0:
                # 1분당 1회 제한 — 같은 분에 다른 프로세스가 먼저 발급받은 경우다. 기다렸다 한 번만 더.
                _log(f"[RETRY] rate limit(EGW00133) — {wait}초 대기 후 재시도 "
                     f"(cooldown_until={getattr(exc, 'cooldown_until', '')})")
                time.sleep(wait)
                continue
            _log(f"[ERROR] KIS {mode} 토큰 갱신 실패(rate limit 지속): {exc}")
            return 1
        except Exception as exc:  # noqa: BLE001
            _log(f"[ERROR] KIS {mode} 토큰 갱신 실패: {type(exc).__name__}: {exc}")
            _log("[TRACE] " + traceback.format_exc(limit=6).replace("\n", " | "))
            return 1
        _log(f"[OK] KIS {mode} 토큰 갱신 완료" + (" (재시도 성공)" if attempt == 2 else ""))
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
