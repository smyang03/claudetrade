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
    """'approved' / 'rejected' / '' (대기). 명시 승인·기각이 우선, 그 다음 반복 자동 신뢰."""
    item = load_approvals().get(str(lesson_id or "").strip())
    explicit = str((item or {}).get("status") or "")
    if explicit:
        return explicit
    try:
        if _auto_trusted(lesson_id):
            return "approved"
    except Exception:
        pass
    return ""


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


# ── 반복 기반 자동 신뢰 (운영자 철학 2026-06-12: "같은 문장이 여러 책에 나오면 진리") ──
# 독립 관찰(postmortem 등 세션별 패턴 교훈)이 서로 다른 세션에서 N회 반복되면 자동 승인.
# 메트릭 리뷰 교훈(ops_review)은 같은 계산이 매일 재생성되는 것이라 반복으로 치지 않는다
# (실증: 권고문 14일 반복 주입에도 지표 무변화).
_INDEPENDENT_SOURCES = {"postmortem", "recent_day", "data_analysis", "manual"}


def _recurrence_path():
    return get_runtime_path("state", "lesson_recurrence.json")


def _text_key(text: str) -> str:
    norm = "".join(ch for ch in str(text or "").lower() if ch.isalnum())
    return norm[:80]


def _load_recurrence() -> dict[str, Any]:
    try:
        path = _recurrence_path()
        if not path.exists():
            return {"entries": {}, "id_to_key": {}}
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("entries", {})
            data.setdefault("id_to_key", {})
            return data
    except Exception:
        pass
    return {"entries": {}, "id_to_key": {}}


def auto_trust_threshold() -> int:
    try:
        return max(2, int(float(os.getenv("LESSONS_AUTO_TRUST_RECURRENCE", "3") or 3)))
    except Exception:
        return 3


def note_lesson_observations(items: list[dict], session_date: str) -> dict[str, Any]:
    """세션별 교훈 관찰을 반복 원장에 기록. 임계 도달 시 auto_trusted 마킹."""
    session = str(session_date or "")[:10]
    if not session:
        return {"noted": 0}
    with _LOCK:
        data = _load_recurrence()
        entries = data["entries"]
        id_to_key = data["id_to_key"]
        noted = 0
        newly_trusted: list[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            lesson_id = str(item.get("id") or "").strip()
            text = str(item.get("summary") or item.get("text") or "").strip()
            key = _text_key(text) or lesson_id
            if not key:
                continue
            source = str(item.get("source") or "").strip().lower()
            entry = entries.get(key) or {"sessions": [], "source": source, "sample_id": lesson_id}
            if session not in entry["sessions"]:
                entry["sessions"] = sorted(set(entry["sessions"] + [session]))[-30:]
                noted += 1
            entry["source"] = source or entry.get("source", "")
            independent = entry.get("source", "") in _INDEPENDENT_SOURCES
            was_trusted = bool(entry.get("auto_trusted"))
            entry["auto_trusted"] = bool(independent and len(entry["sessions"]) >= auto_trust_threshold())
            if entry["auto_trusted"] and not was_trusted:
                newly_trusted.append(key)
            entries[key] = entry
            if lesson_id:
                id_to_key[lesson_id] = key
        path = _recurrence_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    if newly_trusted:
        log.info(f"[lesson recurrence] 반복 {auto_trust_threshold()}세션 도달 — 자동 신뢰 승격: {newly_trusted[:5]}")
    return {"noted": noted, "newly_trusted": newly_trusted}


def _auto_trusted(lesson_id: str) -> bool:
    data = _load_recurrence()
    key = (data.get("id_to_key") or {}).get(str(lesson_id or "").strip())
    if not key:
        return False
    entry = (data.get("entries") or {}).get(key) or {}
    return bool(entry.get("auto_trusted"))
