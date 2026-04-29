from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from filelock import FileLock

from runtime_paths import get_runtime_path
from lifecycle.quality import live_clean_learning_allowed


class BrainApprovalQueue:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else get_runtime_path("state", "brain_approval_queue.jsonl")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def submit(
        self,
        *,
        candidate: dict[str, Any],
        runtime_mode: str,
        data_quality: str,
        forward_complete: bool,
    ) -> bool:
        if not live_clean_learning_allowed(
            runtime_mode=runtime_mode,
            quality=data_quality,
            forward_complete=forward_complete,
        ):
            return False
        payload = {
            "status": "PENDING_APPROVAL",
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "candidate": candidate,
            "runtime_mode": runtime_mode,
            "data_quality": data_quality,
            "forward_complete": bool(forward_complete),
        }
        lock = FileLock(str(self.path) + ".lock")
        with lock:
            with self.path.open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return True

    def read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

