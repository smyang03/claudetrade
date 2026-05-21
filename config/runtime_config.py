from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from runtime_paths import get_runtime_path


SECRET_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASS",
    "ACCOUNT",
    "ACCT",
    "APP_SECRET",
)

NON_SECRET_PATH_KEYS = {
    "V2_START_CONFIG_PATH",
    "CONFIG_PROFILE",
    "LIVE_CONFIG_PATH",
    "CLAUDETRADE_RUNTIME_DIR",
}

NON_SECRET_SUFFIXES = (
    "_MAX_TOKENS",
)


def _is_secret_key(key: str) -> bool:
    upper = str(key or "").upper()
    if upper in NON_SECRET_PATH_KEYS:
        return False
    if any(upper.endswith(suffix) for suffix in NON_SECRET_SUFFIXES):
        return False
    return any(marker in upper for marker in SECRET_MARKERS)


def redact_config_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    if not _is_secret_key(key):
        return value
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"{text[:2]}***{text[-2:]}"


@dataclass
class EffectiveRuntimeConfig:
    values: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    runtime_mode: str = "paper"
    env_file: str = ""
    start_config_path: str = ""
    source_granularity: str = "env_only_v1"

    @classmethod
    def from_env(
        cls,
        *,
        runtime_mode: str = "",
        environ: Mapping[str, str] | None = None,
        defaults: Mapping[str, Any] | None = None,
        env_file: str = "",
        start_config_path: str = "",
    ) -> "EffectiveRuntimeConfig":
        env = dict(os.environ if environ is None else environ)
        values: dict[str, str] = {}
        sources: dict[str, str] = {}
        for key, value in (defaults or {}).items():
            values[str(key)] = str(value)
            sources[str(key)] = "code_default"
        for key, value in env.items():
            values[str(key)] = str(value)
            sources[str(key)] = "env"
        mode = runtime_mode or env.get("TRADING_BOT_MODE") or ("live" if "--live" in os.sys.argv else "paper")
        return cls(
            values=values,
            sources=sources,
            runtime_mode=str(mode),
            env_file=str(env_file),
            start_config_path=str(start_config_path or values.get("V2_START_CONFIG_PATH", "")),
            source_granularity="env_only_v1",
        )

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(str(key), default)

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key, None)
        if value is None or str(value).strip() == "":
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def get_int(self, key: str, default: int = 0) -> int:
        value = self.get(key, None)
        if value is None or str(value).strip() == "":
            return default
        try:
            return int(float(str(value).replace(",", "")))
        except Exception:
            return default

    def get_float(self, key: str, default: float = 0.0) -> float:
        value = self.get(key, None)
        if value is None or str(value).strip() == "":
            return default
        try:
            return float(str(value).replace(",", ""))
        except Exception:
            return default

    def source_of(self, key: str) -> str:
        return self.sources.get(str(key), "missing")

    def source_report(self) -> dict[str, Any]:
        return {
            "runtime_mode": self.runtime_mode,
            "env_file": self.env_file,
            "start_config_path": self.start_config_path,
            "source_granularity": self.source_granularity,
            "sources": dict(sorted(self.sources.items())),
        }

    def redacted_values(self) -> dict[str, Any]:
        return {
            key: redact_config_value(key, value)
            for key, value in sorted(self.values.items())
        }

    def write_redacted_snapshot(self, *, label: str = "startup") -> Path:
        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = get_runtime_path("logs", "config", f"effective_config_{now}_{label}.redacted.json")
        payload = {
            "written_at": datetime.now().isoformat(timespec="seconds"),
            "runtime_mode": self.runtime_mode,
            "env_file": self.env_file,
            "start_config_path": self.start_config_path,
            "source_report": self.source_report(),
            "effective": self.redacted_values(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
