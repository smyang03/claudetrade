"""
shared_judgment_cache.py
paper/live 두 프로세스 간 아침 Claude 판단 공유 캐시.

같은 날, 같은 장에 대해 get_three_judgments + build_consensus 는 1회만 호출하고
결과를 이 캐시를 통해 공유한다.  종목 선택(select_tickers) 및 스크리너는
각 런타임이 개별 실행한다 (포트폴리오 맥락이 다르므로).

파일 위치: state/shared_judgment_{market}_{yyyymmdd}.json
동시 쓰기 보호: filelock (timeout=10s)
TTL: 같은 session_date 당일만 유효 (session_date 필드로 검증)
"""

import json
import logging
from pathlib import Path
from typing import Optional

try:
    from filelock import FileLock
except ImportError:  # pragma: no cover
    FileLock = None  # type: ignore

from runtime_paths import get_runtime_path

log = logging.getLogger("trading")


def _cache_path(market: str, trade_date: str) -> Path:
    day = str(trade_date or "").replace("-", "")
    return get_runtime_path("state", f"shared_judgment_{market}_{day}.json")


def load(market: str, trade_date: str) -> Optional[dict]:
    """공유 판단 캐시 로드.

    Returns:
        dict with keys: judgments, consensus, digest_prompt, digest_raw,
                        round1_judgments, debate_changes, session_date
        None if cache is missing, stale, or invalid.
    """
    path = _cache_path(market, trade_date)
    if not path.exists():
        return None

    try:
        if FileLock is not None:
            lock = FileLock(str(path) + ".lock", timeout=5)
            with lock:
                data = json.loads(path.read_text(encoding="utf-8"))
        else:
            data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning(f"[공유판단캐시] load 실패 ({market} {trade_date}): {e}")
        return None

    if not isinstance(data, dict):
        return None
    if data.get("session_date") != trade_date:
        return None
    if not data.get("judgments") or not data.get("consensus"):
        return None

    return data


def save(market: str, trade_date: str, judgment: dict) -> None:
    """공유 판단 캐시 저장.

    judgment 딕셔너리에서 캐시 가능한 필드만 추출해 저장한다.
    tickers / universe_tickers 는 런타임별로 다르므로 저장하지 않는다.
    """
    path = _cache_path(market, trade_date)

    payload = {
        "session_date":     trade_date,
        "market":           market,
        "judgments":        judgment.get("judgments", {}),
        "consensus":        judgment.get("consensus", {}),
        "digest_prompt":    judgment.get("digest_prompt", ""),
        "digest_raw":       judgment.get("digest_raw") or {},
        "round1_judgments": judgment.get("round1_judgments", {}),
        "debate_changes":   judgment.get("debate_changes", []),
    }

    try:
        if FileLock is not None:
            lock = FileLock(str(path) + ".lock", timeout=10)
            with lock:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        log.info(f"[공유판단캐시] 저장 완료 ({market} {trade_date}) consensus={payload['consensus'].get('mode', '?')}")
    except Exception as e:
        log.warning(f"[공유판단캐시] save 실패 ({market} {trade_date}): {e}")
