from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import hashlib
import json

from filelock import FileLock

from runtime_paths import get_runtime_path


@dataclass(frozen=True)
class BrainSnapshot:
    brain_snapshot_id: str
    prompt_version: str
    brain_hash: str
    market: str
    session_date: str
    runtime_mode: str
    created_at: str
    path: str


class BrainSnapshotStore:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else get_runtime_path("state", "brain_snapshots", make_parents=False)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_snapshot(
        self,
        *,
        prompt_version: str,
        market: str,
        session_date: str,
        runtime_mode: str,
        patterns: list[dict[str, Any]] | dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> BrainSnapshot:
        created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        normalized = {
            "prompt_version": prompt_version,
            "market": str(market).upper(),
            "session_date": session_date,
            "runtime_mode": runtime_mode,
            "patterns": patterns,
            "metadata": metadata or {},
        }
        brain_hash = hashlib.sha256(
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        day = str(session_date).replace("-", "")
        snapshot_id = f"brain_{runtime_mode}_{str(market).upper()}_{day}_{brain_hash[:12]}"
        payload = {
            **normalized,
            "brain_snapshot_id": snapshot_id,
            "brain_hash": brain_hash,
            "created_at": created_at,
        }
        path = self.root / f"{snapshot_id}.json"
        lock = FileLock(str(path) + ".lock")
        with lock:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return BrainSnapshot(
            brain_snapshot_id=snapshot_id,
            prompt_version=prompt_version,
            brain_hash=brain_hash,
            market=str(market).upper(),
            session_date=session_date,
            runtime_mode=runtime_mode,
            created_at=created_at,
            path=str(path),
        )

    def load(self, brain_snapshot_id: str) -> dict[str, Any]:
        path = self.root / f"{brain_snapshot_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))

