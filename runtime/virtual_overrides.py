"""가상 전략 오버라이드 — 관제 페이지의 폐기/일시정지/재개 (2026-09-03, 설계 정본 §6).

파일: state/virtual_strategy_overrides.json  {arm: {"state": "active|paused|retired", "memo": str, "ts": str, "by": str}}
소비처 3곳: virtual_books.open_new_trades(신규 진입 skip), 유령 엔진 진입(skip), virtual_gate_eval(retired 제외).
paused ≠ retired: paused는 보유분 정산·판정 유지, retired는 신규 0 + 판정 제외. 승격은 여기서 다루지 않는다.
모든 쓰기는 data/shadow/control_tower_audit.jsonl에 감사 기록.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
ROOT = Path(__file__).resolve().parents[1]
OVERRIDES_PATH = ROOT / "state" / "virtual_strategy_overrides.json"
AUDIT_PATH = ROOT / "data" / "shadow" / "control_tower_audit.jsonl"
STATES = ("active", "paused", "retired")


def load_overrides(path: Path | None = None) -> dict[str, dict]:
    p = path or OVERRIDES_PATH
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
        return {k: v for k, v in data.items() if isinstance(v, dict)}
    except Exception:
        return {}


def arm_state(arm: str, overrides: dict | None = None) -> str:
    ov = overrides if overrides is not None else load_overrides()
    st = str((ov.get(arm) or {}).get("state") or "active").lower()
    return st if st in STATES else "active"


def set_override(arm: str, state: str, *, memo: str = "", by: str = "dashboard",
                 path: Path | None = None, audit_path: Path | None = None) -> dict:
    state = str(state).lower()
    if state not in STATES:
        raise ValueError(f"state must be one of {STATES}")
    p = path or OVERRIDES_PATH
    ov = load_overrides(p)
    prev = ov.get(arm) or {}
    entry = {"state": state, "memo": str(memo or "")[:500], "ts": datetime.now(KST).isoformat(timespec="seconds"),
             "by": str(by or "")[:40]}
    ov[arm] = entry
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ov, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    a = audit_path or AUDIT_PATH
    a.parent.mkdir(parents=True, exist_ok=True)
    with a.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": entry["ts"], "arm": arm, "from": prev.get("state", "active"),
                             "to": state, "memo": entry["memo"], "by": entry["by"]}, ensure_ascii=False) + "\n")
    return entry
