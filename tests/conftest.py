from __future__ import annotations

import os
import tempfile

import pytest


# 2026-09-10: 키 목록 방식(아래 6개)으로는 부족했다. `tests/test_kr_event_lane.py`가 실행 중
# `load_dotenv()` 경로를 타면서 **실계정 .env 477개 키를 통째로 os.environ에 올리고**,
# 그 뒤 알파벳순으로 실행되는 테스트들이 테스트 기본값 대신 라이브 설정을 읽어 무더기로 깨졌다.
# 실측: 전체 스위트 130 failed / 3776 passed인데 실패 파일을 개별로 돌리면 전부 통과.
#   test_pathb_runtime 단독 142 passed → test_kr_event_lane과 함께면 35 failed
#   (예: _pathb_qty_with_context가 PATHB_FIXED_ORDER_KRW_US 라이브값을 읽어 수량 1→0)
# 그래서 특정 키가 아니라 **os.environ 전체를 테스트마다 스냅샷·복원**한다.
_RESTORE_ENV_KEYS = (
    "AUTO_SELL_REVIEW_FORCE_SELL_LOSS_PCT",
    "CLAUDE_REVIEW_ALL_AUTOMATED_SELLS",
    "HOLD_ADVISOR_SOFT_CACHE_ENABLED",
    "KR_DAILY_ENTRY_CAP",
    "US_DAILY_ENTRY_CAP",
    "V2_MAX_DAILY_ENTRIES",
)


def pytest_configure(config) -> None:  # pragma: no cover - pytest hook
    os.environ.setdefault("TRADING_BOT_MODE", "test")
    if os.environ.get("CLAUDETRADE_KEEP_REPO_RUNTIME_FOR_TESTS", "").strip().lower() in {"1", "true", "yes", "y", "on"}:
        return
    os.environ.setdefault("CLAUDETRADE_RUNTIME_DIR", tempfile.mkdtemp(prefix="claudetrade_pytest_"))
    try:
        import runtime_paths

        runtime_paths._RUNTIME_ROOT = None
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _restore_live_control_env_keys():  # pragma: no cover - test hygiene
    """테스트마다 os.environ 전체를 스냅샷하고 되돌린다.

    monkeypatch.setenv는 스스로 되돌리지만, 테스트 코드가 직접 os.environ을 쓰거나
    load_dotenv()가 호출되면 다음 테스트로 샌다. 이 픽스처가 그 경로까지 막는다.
    (정의 순서상 가장 먼저 시작 = 가장 나중에 정리되므로 monkeypatch 되돌림 뒤에 복원된다.)
    """
    snapshot = dict(os.environ)
    yield
    if os.environ != snapshot:
        for key in [k for k in os.environ if k not in snapshot]:
            os.environ.pop(key, None)
        for key, value in snapshot.items():
            if os.environ.get(key) != value:
                os.environ[key] = value


@pytest.fixture(autouse=True)
def _no_real_telegram_in_tests(monkeypatch, tmp_path):  # pragma: no cover - test hygiene
    """테스트가 실제 텔레그램을 쏘지 못하게 한다 (2026-09-02 사고: REHEARSAL 통보 테스트가
    운영자 채팅에 실제 메시지를 보냄). 토큰을 비우면 telegram_reporter.send()가 즉시 False.
    send() 자체를 검증하는 테스트는 TOKEN/CHAT_ID를 명시적으로 다시 patch한다."""
    import sys as _sys
    monkeypatch.setenv("TELEGRAM_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")
    mod = _sys.modules.get("telegram_reporter")
    if mod is not None:
        monkeypatch.setattr(mod, "TOKEN", "", raising=False)
        monkeypatch.setattr(mod, "CHAT_ID", "", raising=False)
        # 실제 일일 카운터 파일(state/)도 건드리지 않는다 — fallback 테스트가 #8을 올린 실측
        monkeypatch.setattr(mod, "_SEND_COUNTER_PATH", tmp_path / "telegram_send_counter.json", raising=False)
    yield
