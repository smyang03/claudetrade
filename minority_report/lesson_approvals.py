"""교훈 승인 저장소 — 승인형 컨베이어의 손잡이 (2026-06-12 운영자 승인).

배경: 자동 생성 교훈은 truth_status 안전장치로 분석가 주입이 차단되는데,
승인 절차가 없어 17건이 영구 대기 상태였다 (안전장치가 영구 차단기로 변질).
이 저장소가 텔레그램 /lessons 승인·기각을 영속화하고, lesson_candidates.json이
세션마다 재생성돼도 id 기준으로 승인 상태가 생존한다.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from typing import Any

from runtime_paths import get_runtime_path

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


def _path():
    return get_runtime_path("state", "lesson_approvals.json")


def load_approvals() -> dict[str, dict[str, Any]]:
    try:
        path = _path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def approval_status(lesson_id: str) -> str:
    """'approved' / 'rejected' / '' (대기)."""
    item = load_approvals().get(str(lesson_id or "").strip())
    return str((item or {}).get("status") or "")


def set_approval(lesson_id: str, status: str, *, by: str = "telegram") -> bool:
    key = str(lesson_id or "").strip()
    if not key or status not in {"approved", "rejected"}:
        return False
    with _LOCK:
        data = load_approvals()
        data[key] = {
            "status": status,
            "by": by,
            "at": datetime.now().isoformat(timespec="seconds"),
        }
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    log.info(f"[lesson approval] {key} -> {status} (by {by})")
    return True


def approval_required() -> bool:
    return str(os.getenv("LESSONS_REQUIRE_APPROVAL", "true") or "").strip().lower() in {"1", "true", "yes", "on"}
