from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

from runtime_paths import get_runtime_path


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name, str(default)) or str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ShadowAuditConfig:
    enabled: bool
    runtime_mode: str
    db_path: Path
    queue_max: int
    flush_batch: int
    db_timeout_sec: float
    drop_price_on_full: bool

    @classmethod
    def from_env(cls, *, runtime_mode: str) -> "ShadowAuditConfig":
        mode = str(runtime_mode or "live").lower()
        return cls(
            enabled=_env_bool("SHADOW_AUDIT_ENABLED", False),
            runtime_mode=mode,
            db_path=get_runtime_path("data", "audit", f"{mode}_shadow_audit.db"),
            queue_max=max(1, _env_int("SHADOW_AUDIT_QUEUE_MAX", 5000)),
            flush_batch=max(1, _env_int("SHADOW_AUDIT_FLUSH_BATCH", 100)),
            db_timeout_sec=max(0.1, _env_float("SHADOW_AUDIT_DB_TIMEOUT_SEC", 2.0)),
            drop_price_on_full=_env_bool("SHADOW_AUDIT_DROP_PRICE_ON_FULL", True),
        )

