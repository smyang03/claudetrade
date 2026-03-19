from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
_RUNTIME_ROOT: Path | None = None


def _is_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        probe.unlink()
        return True
    except OSError:
        return False


def get_runtime_root() -> Path:
    global _RUNTIME_ROOT
    if _RUNTIME_ROOT is not None:
        return _RUNTIME_ROOT

    env_root = os.getenv("CLAUDETRADE_RUNTIME_DIR", "").strip()
    candidates = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.append(BASE_DIR)
    candidates.append(Path.home() / ".claudetrade")

    for candidate in candidates:
        if _is_writable_dir(candidate):
            _RUNTIME_ROOT = candidate
            return candidate

    raise RuntimeError("No writable runtime directory available.")


def get_runtime_path(*parts: str, make_parents: bool = True) -> Path:
    path = get_runtime_root().joinpath(*parts)
    if make_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path
