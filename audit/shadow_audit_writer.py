from __future__ import annotations

import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any

from audit.shadow_audit_models import ShadowAuditConfig
from audit.shadow_audit_store import ShadowAuditStore


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ShadowAuditWriter:
    """Best-effort non-blocking audit writer."""

    def __init__(self, config: ShadowAuditConfig) -> None:
        self.config = config
        self.enabled = bool(config.enabled)
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=config.queue_max)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._store: ShadowAuditStore | None = None
        self.queued = 0
        self.written = 0
        self.dropped = 0
        self.error_count = 0
        self.last_error = ""

    @classmethod
    def from_env(cls, *, runtime_mode: str) -> "ShadowAuditWriter":
        return cls(ShadowAuditConfig.from_env(runtime_mode=runtime_mode))

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        try:
            self._store = ShadowAuditStore(self.config.db_path, timeout=self.config.db_timeout_sec)
            self._thread = threading.Thread(target=self._worker, name="shadow-audit-writer", daemon=True)
            self._thread.start()
        except Exception as exc:
            self.enabled = False
            self.error_count += 1
            self.last_error = str(exc)[:500]

    def stop(self, *, timeout: float = 2.0) -> None:
        if not self.enabled:
            return
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(0.1, float(timeout or 0.1)))
        self.flush()

    def emit(self, event: dict[str, Any], *, block: bool = False) -> bool:
        if not self.enabled:
            return False
        try:
            self._queue.put(dict(event or {}), block=bool(block))
            self.queued += 1
            return True
        except queue.Full:
            self.dropped += 1
            return False
        except Exception as exc:
            self.error_count += 1
            self.last_error = str(exc)[:500]
            return False

    def emit_nowait(self, event: dict[str, Any]) -> bool:
        return self.emit(event, block=False)

    def flush(self) -> None:
        if not self.enabled:
            return
        batch: list[dict[str, Any]] = []
        while True:
            try:
                batch.append(self._queue.get_nowait())
            except queue.Empty:
                break
        if batch:
            self._write_batch(batch)

    def health_event(self, *, event_type: str = "writer_health") -> dict[str, Any]:
        return {
            "kind": "health",
            "ts": _utc_now(),
            "event_type": event_type,
            "queued": self.queued,
            "written": self.written,
            "dropped": self.dropped,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "queue_size": self._safe_qsize(),
        }

    def _safe_qsize(self) -> int:
        try:
            return int(self._queue.qsize())
        except Exception:
            return 0

    def _worker(self) -> None:
        last_health = time.time()
        while not self._stop.is_set():
            batch: list[dict[str, Any]] = []
            try:
                batch.append(self._queue.get(timeout=0.25))
                while len(batch) < self.config.flush_batch:
                    try:
                        batch.append(self._queue.get_nowait())
                    except queue.Empty:
                        break
            except queue.Empty:
                pass

            if batch:
                self._write_batch(batch)

            now = time.time()
            if now - last_health >= 60:
                last_health = now
                self._write_batch([self.health_event(event_type="periodic")])

        self.flush()
        self._write_batch([self.health_event(event_type="stopped")])

    def _write_batch(self, batch: list[dict[str, Any]]) -> None:
        if not batch:
            return
        try:
            store = self._store or ShadowAuditStore(self.config.db_path, timeout=self.config.db_timeout_sec)
            self._store = store
            written = store.write_events(batch)
            self.written += written
        except Exception as exc:
            self.error_count += 1
            self.last_error = str(exc)[:500]


def try_emit(writer: ShadowAuditWriter | None, event: dict[str, Any]) -> bool:
    try:
        if writer is None:
            return False
        return writer.emit_nowait(event)
    except Exception:
        return False

