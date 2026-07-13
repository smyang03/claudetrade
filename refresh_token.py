"""KIS API 토큰 사전 갱신 스크립트 — schtasks에서 호출."""
import sys
import os

ROOT = os.path.dirname(os.path.abspath(__file__))
# Task Scheduler starts actions with an empty Start In directory.  Resolve the
# live env and token paths from the project root instead of System32.
os.chdir(ROOT)
sys.path.insert(0, ROOT)

from kis_api import get_access_token, IS_PAPER

mode = "paper" if IS_PAPER else "live"
try:
    token = get_access_token(force_refresh=True)
    print(f"[OK] KIS {mode} 토큰 갱신 완료")
except Exception as e:
    print(f"[ERROR] KIS {mode} 토큰 갱신 실패: {e}")
    sys.exit(1)
