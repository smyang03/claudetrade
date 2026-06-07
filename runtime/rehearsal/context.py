from __future__ import annotations

import builtins
import contextlib
import hashlib
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import traceback
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REHEARSAL_ROOT = REPO_ROOT / ".runtime" / "ops_rehearsal"
PROTECTED_REPO_PATHS = (
    REPO_ROOT / "state",
    REPO_ROOT / "data",
    REPO_ROOT / "logs",
    REPO_ROOT / "config",
    REPO_ROOT / "claude_memory" / "brain.json",
)


class RehearsalGuardError(RuntimeError):
    """Raised when a rehearsal run tries to touch live state or the network."""


@dataclass(frozen=True)
class RehearsalContext:
    profile: str
    backend: str
    scenario: str
    sandbox_root: Path
    started_at: str
    runtime_context: str = "ops_rehearsal"
    order_send: bool = False
    broker_call: bool = False
    claude_call: bool = False

    @property
    def order_intents_path(self) -> Path:
        return self.sandbox_root / "data" / "rehearsal" / "order_intents.jsonl"

    @property
    def report_path(self) -> Path:
        return self.sandbox_root / "reports" / f"{self.scenario}_summary.json"


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_%f")


def _abs(path: str | os.PathLike[str] | Path) -> Path:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    return p.resolve()


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def assert_sandbox_runtime_root(path: Path) -> None:
    candidate = _abs(path)
    if not str(candidate):
        raise RehearsalGuardError("sandbox runtime root is empty")
    if candidate == REPO_ROOT:
        raise RehearsalGuardError("sandbox runtime root must not be repo root")
    for protected in (REPO_ROOT / "state", REPO_ROOT / "data", REPO_ROOT / "logs"):
        if candidate == protected or _is_inside(candidate, protected):
            raise RehearsalGuardError(f"sandbox runtime root must not be inside {protected}")


def create_rehearsal_context(
    *,
    scenario: str,
    runtime_root: Path | None = None,
    profile: str = "live",
    backend: str = "fixture",
) -> RehearsalContext:
    if profile != "live":
        raise RehearsalGuardError("ops rehearsal currently supports profile=live only")
    if backend != "fixture":
        raise RehearsalGuardError("ops rehearsal currently supports backend=fixture only")
    root = _abs(runtime_root or (DEFAULT_REHEARSAL_ROOT / _now_id()))
    assert_sandbox_runtime_root(root)
    for rel in ("state", "data", "logs", "reports", "data/rehearsal", "data/kis_cache"):
        (root / rel).mkdir(parents=True, exist_ok=True)

    os.environ["CLAUDETRADE_RUNTIME_DIR"] = str(root)
    os.environ["TRADING_BOT_MODE"] = "live"
    os.environ["OPS_REHEARSAL"] = "true"
    os.environ["OPS_REHEARSAL_CONTEXT"] = "ops_rehearsal"
    os.environ["ORDER_BACKEND"] = backend
    os.environ["BROKER_BACKEND"] = backend
    os.environ["CLAUDE_BACKEND"] = backend
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["SHADOW_AUDIT_ENABLED"] = "false"
    os.environ["ENABLE_AGENT_CALL_EVENT_STORE"] = os.environ.get("ENABLE_AGENT_CALL_EVENT_STORE", "false")
    os.environ["AGENT_CALL_EVENT_DB_PATH"] = str(root / "data" / "audit" / "agent_call_events.db")
    os.environ["ML_DECISIONS_DB_PATH"] = str(root / "data" / "ml" / "decisions.db")

    runtime_paths = sys.modules.get("runtime_paths")
    if runtime_paths is not None and hasattr(runtime_paths, "_RUNTIME_ROOT"):
        setattr(runtime_paths, "_RUNTIME_ROOT", root)

    logger = sys.modules.get("logger")
    if logger is not None and hasattr(logger, "reset_logger_runtime_dir_for_tests"):
        logger.reset_logger_runtime_dir_for_tests(root / "logs", bot_mode="live")

    return RehearsalContext(
        profile=profile,
        backend=backend,
        scenario=str(scenario or "all"),
        sandbox_root=root,
        started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def _iter_snapshot_files() -> Iterator[Path]:
    direct = [
        REPO_ROOT / "config" / "v2_start_config.json",
        REPO_ROOT / "claude_memory" / "brain.json",
        REPO_ROOT / "data" / "exchange_cache.json",
        REPO_ROOT / "state" / "param_tuner_sessions.json",
        REPO_ROOT / "data" / "audit" / "agent_call_events.db",
    ]
    for pattern in (".env*", "state/live_*", "data/*.db"):
        yield from REPO_ROOT.glob(pattern)
    for folder in (
        REPO_ROOT / "data" / "price",
        REPO_ROOT / "data" / "audit",
        REPO_ROOT / "data" / "ml",
        REPO_ROOT / "logs" / "raw_calls",
    ):
        if folder.exists():
            yield from (p for p in folder.rglob("*") if p.is_file())
    for path in direct:
        if path.exists():
            yield path


def _snapshot_ignored(path: Path) -> bool:
    name = path.name
    if name.endswith((".db-wal", ".db-shm")):
        return True
    rel = path.resolve().relative_to(REPO_ROOT).as_posix()
    return rel in {
        "state/live_guardian_heartbeat.json",
        "state/live_runtime_handoff_snapshot.json",
    }


def _file_fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        h = hashlib.sha256()
        with path.open("rb") as fp:
            for chunk in iter(lambda: fp.read(1024 * 1024), b""):
                h.update(chunk)
        return {
            "exists": True,
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": h.hexdigest(),
        }
    except FileNotFoundError:
        return {"exists": False}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def snapshot_repo_live_state() -> dict[str, Any]:
    files: dict[str, Any] = {}
    for path in sorted(set(_iter_snapshot_files()), key=lambda p: p.as_posix()):
        if _snapshot_ignored(path):
            continue
        rel = path.resolve().relative_to(REPO_ROOT).as_posix()
        files[rel] = _file_fingerprint(path)
    return {"repo_root": str(REPO_ROOT), "files": files}


def assert_repo_live_state_unchanged(before: dict[str, Any]) -> dict[str, Any]:
    after = snapshot_repo_live_state()
    before_files = dict((before or {}).get("files") or {})
    after_files = dict(after.get("files") or {})
    changed: dict[str, dict[str, Any]] = {}
    for key in sorted(set(before_files) | set(after_files)):
        if before_files.get(key) != after_files.get(key):
            changed[key] = {"before": before_files.get(key), "after": after_files.get(key)}
    if changed:
        raise RehearsalGuardError(f"repo live state changed during rehearsal: {sorted(changed)[:10]}")
    return after


def _set_module_attr(module_name: str, attr: str, value: Any, overrides: dict[str, Path | str]) -> None:
    module = sys.modules.get(module_name)
    if module is None:
        return
    if hasattr(module, attr):
        setattr(module, attr, value)
        overrides[f"{module_name}.{attr}"] = str(value)


def apply_direct_path_overrides(context: RehearsalContext) -> dict[str, Path | str]:
    root = context.sandbox_root
    overrides: dict[str, Path | str] = {}

    runtime_paths = sys.modules.get("runtime_paths")
    if runtime_paths is not None and hasattr(runtime_paths, "_RUNTIME_ROOT"):
        setattr(runtime_paths, "_RUNTIME_ROOT", root)
        overrides["runtime_paths._RUNTIME_ROOT"] = root

    logger = sys.modules.get("logger")
    if logger is not None and hasattr(logger, "reset_logger_runtime_dir_for_tests"):
        logger.reset_logger_runtime_dir_for_tests(root / "logs", bot_mode="live")
        overrides["logger.LOG_DIR"] = root / "logs"

    _set_module_attr("trading_bot", "_DECISIONS_DB_PATH", root / "data" / "ml" / "decisions.db", overrides)
    _set_module_attr("trading_bot", "DECISIONS_FILE", root / "state" / "live_decisions.jsonl", overrides)
    _set_module_attr("trading_bot", "JUDGMENT_DIR", root / "logs" / "daily_judgment", overrides)
    _set_module_attr("trading_bot", "_LESSON_CANDIDATES_PATH", root / "state" / "lesson_candidates.json", overrides)
    _set_module_attr("trading_bot", "BOT_PID_FILE", root / "state" / "live_trading_bot.pid", overrides)
    _set_module_attr("ticker_selection_db", "DB_PATH", str(root / "data" / "ticker_selection_log.db"), overrides)
    _set_module_attr("intraday_strategy_db", "DB_PATH", str(root / "data" / "intraday_strategy_log.db"), overrides)
    _set_module_attr("strategy.param_tuner", "_DB_PATH", root / "data" / "ml" / "decisions.db", overrides)
    _set_module_attr("strategy.param_tuner", "_SESSION_STATE_PATH", root / "state" / "param_tuner_sessions.json", overrides)
    _set_module_attr("minority_report.raw_call_logger", "_RAW_CALLS_DIR", None, overrides)
    _set_module_attr("minority_report.raw_call_logger", "_AGENT_EVENT_STORE", None, overrides)

    kis_api = sys.modules.get("kis_api")
    if kis_api is not None:
        _set_module_attr("kis_api", "_EXCHANGE_CACHE_FILE", root / "data" / "kis_cache" / "exchange_cache.json", overrides)
        _set_module_attr("kis_api", "_KR_SCREEN_CACHE_PATH", root / "data" / "kis_cache" / "kr_screen_cache.json", overrides)
        _set_module_attr("kis_api", "_US_SCREEN_CACHE_PATH", root / "data" / "kis_cache" / "us_screen_cache.json", overrides)
        if hasattr(kis_api, "_US_EXCHANGE_CACHE"):
            getattr(kis_api, "_US_EXCHANGE_CACHE").clear()
            getattr(kis_api, "_US_EXCHANGE_CACHE").update({"NVDA": "NASD", "AAPL": "NASD"})
            overrides["kis_api._US_EXCHANGE_CACHE"] = "fixture_reset"

    for path in (
        root / "data" / "ml",
        root / "data" / "audit",
        root / "data" / "kis_cache",
        root / "logs" / "daily_judgment",
        root / "state",
    ):
        path.mkdir(parents=True, exist_ok=True)
    return overrides


def _sqlite_target_path(database: Any) -> Path | None:
    if isinstance(database, (str, os.PathLike)):
        raw = str(database)
        if raw in {":memory:", ""}:
            return None
        if raw.startswith("file:"):
            trimmed = raw[5:].split("?", 1)[0]
            if not trimmed:
                return None
            return _abs(trimmed)
        return _abs(raw)
    return None


def _is_write_mode(mode: str) -> bool:
    m = str(mode or "r")
    return any(ch in m for ch in ("w", "a", "x", "+"))


def _guard_path(context: RehearsalContext, path: Any, action: str) -> None:
    if isinstance(path, int):
        return
    try:
        target = _abs(path)
    except Exception:
        return
    if _is_inside(target, context.sandbox_root):
        return
    raise RehearsalGuardError(f"rehearsal blocked {action} outside sandbox: {target}")


@contextlib.contextmanager
def install_write_guard(context: RehearsalContext) -> Iterator[dict[str, Any]]:
    calls: list[dict[str, str]] = []
    originals: dict[str, Any] = {}
    fd_paths: dict[int, Path] = {}

    def remember(action: str, path: Any) -> None:
        calls.append({"action": action, "path": str(path)})

    originals["builtins.open"] = builtins.open
    originals["Path.open"] = Path.open
    originals["Path.write_text"] = Path.write_text
    originals["Path.write_bytes"] = Path.write_bytes
    originals["Path.mkdir"] = Path.mkdir
    originals["Path.unlink"] = Path.unlink
    originals["os.open"] = os.open
    originals["os.fdopen"] = os.fdopen
    originals["os.makedirs"] = os.makedirs
    originals["os.replace"] = os.replace
    originals["os.rename"] = os.rename
    originals["os.remove"] = os.remove
    originals["os.unlink"] = os.unlink
    originals["shutil.copyfile"] = shutil.copyfile
    originals["shutil.copy2"] = shutil.copy2
    originals["shutil.move"] = shutil.move
    originals["sqlite3.connect"] = sqlite3.connect

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if _is_write_mode(mode):
            _guard_path(context, file, f"open({mode})")
            remember(f"open({mode})", file)
        return originals["builtins.open"](file, mode, *args, **kwargs)

    def guarded_path_open(self: Path, mode: str = "r", *args: Any, **kwargs: Any):
        if _is_write_mode(mode):
            _guard_path(context, self, f"Path.open({mode})")
            remember(f"Path.open({mode})", self)
        return originals["Path.open"](self, mode, *args, **kwargs)

    def guarded_write_text(self: Path, *args: Any, **kwargs: Any):
        _guard_path(context, self, "Path.write_text")
        remember("Path.write_text", self)
        return originals["Path.write_text"](self, *args, **kwargs)

    def guarded_write_bytes(self: Path, *args: Any, **kwargs: Any):
        _guard_path(context, self, "Path.write_bytes")
        remember("Path.write_bytes", self)
        return originals["Path.write_bytes"](self, *args, **kwargs)

    def guarded_mkdir(self: Path, *args: Any, **kwargs: Any):
        if not _is_inside(_abs(self), context.sandbox_root) and kwargs.get("exist_ok") and self.exists():
            return originals["Path.mkdir"](self, *args, **kwargs)
        _guard_path(context, self, "Path.mkdir")
        remember("Path.mkdir", self)
        return originals["Path.mkdir"](self, *args, **kwargs)

    def guarded_unlink(self: Path, *args: Any, **kwargs: Any):
        _guard_path(context, self, "Path.unlink")
        remember("Path.unlink", self)
        return originals["Path.unlink"](self, *args, **kwargs)

    def guarded_os_open(path: Any, flags: int, *args: Any, **kwargs: Any):
        write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
        if int(flags) & write_flags:
            _guard_path(context, path, "os.open")
            remember("os.open", path)
        fd = originals["os.open"](path, flags, *args, **kwargs)
        try:
            fd_paths[int(fd)] = _abs(path)
        except Exception:
            pass
        return fd

    def guarded_fdopen(fd: int, mode: str = "r", *args: Any, **kwargs: Any):
        if _is_write_mode(mode) and int(fd) in fd_paths:
            _guard_path(context, fd_paths[int(fd)], f"os.fdopen({mode})")
            remember(f"os.fdopen({mode})", fd_paths[int(fd)])
        return originals["os.fdopen"](fd, mode, *args, **kwargs)

    def guarded_makedirs(name: Any, *args: Any, **kwargs: Any):
        try:
            target = _abs(name)
            if not _is_inside(target, context.sandbox_root) and kwargs.get("exist_ok") and target.exists():
                return originals["os.makedirs"](name, *args, **kwargs)
        except Exception:
            pass
        _guard_path(context, name, "os.makedirs")
        remember("os.makedirs", name)
        return originals["os.makedirs"](name, *args, **kwargs)

    def guarded_replace(src: Any, dst: Any, *args: Any, **kwargs: Any):
        _guard_path(context, dst, "os.replace")
        remember("os.replace", dst)
        return originals["os.replace"](src, dst, *args, **kwargs)

    def guarded_rename(src: Any, dst: Any, *args: Any, **kwargs: Any):
        _guard_path(context, dst, "os.rename")
        remember("os.rename", dst)
        return originals["os.rename"](src, dst, *args, **kwargs)

    def guarded_remove(path: Any, *args: Any, **kwargs: Any):
        _guard_path(context, path, "remove")
        remember("remove", path)
        return originals["os.remove"](path, *args, **kwargs)

    def guarded_copyfile(src: Any, dst: Any, *args: Any, **kwargs: Any):
        _guard_path(context, dst, "copyfile")
        remember("copyfile", dst)
        return originals["shutil.copyfile"](src, dst, *args, **kwargs)

    def guarded_copy2(src: Any, dst: Any, *args: Any, **kwargs: Any):
        _guard_path(context, dst, "copy2")
        remember("copy2", dst)
        return originals["shutil.copy2"](src, dst, *args, **kwargs)

    def guarded_move(src: Any, dst: Any, *args: Any, **kwargs: Any):
        _guard_path(context, dst, "move")
        remember("move", dst)
        return originals["shutil.move"](src, dst, *args, **kwargs)

    def guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any):
        target = _sqlite_target_path(database)
        uri = bool(kwargs.get("uri", False))
        raw = str(database)
        read_only_uri = uri and "mode=ro" in raw
        if target is not None and not read_only_uri:
            _guard_path(context, target, "sqlite3.connect")
            remember("sqlite3.connect", target)
        return originals["sqlite3.connect"](database, *args, **kwargs)

    builtins.open = guarded_open
    Path.open = guarded_path_open
    Path.write_text = guarded_write_text
    Path.write_bytes = guarded_write_bytes
    Path.mkdir = guarded_mkdir
    Path.unlink = guarded_unlink
    os.open = guarded_os_open
    os.fdopen = guarded_fdopen
    os.makedirs = guarded_makedirs
    os.replace = guarded_replace
    os.rename = guarded_rename
    os.remove = guarded_remove
    os.unlink = guarded_remove
    shutil.copyfile = guarded_copyfile
    shutil.copy2 = guarded_copy2
    shutil.move = guarded_move
    sqlite3.connect = guarded_sqlite_connect

    pandas_to_csv_original = None
    try:
        import pandas as pd  # type: ignore

        pandas_to_csv_original = pd.DataFrame.to_csv

        def guarded_to_csv(self: Any, path_or_buf: Any = None, *args: Any, **kwargs: Any):
            if path_or_buf is not None and not hasattr(path_or_buf, "write"):
                _guard_path(context, path_or_buf, "DataFrame.to_csv")
                remember("DataFrame.to_csv", path_or_buf)
            return pandas_to_csv_original(self, path_or_buf, *args, **kwargs)

        pd.DataFrame.to_csv = guarded_to_csv
    except Exception:
        pd = None  # type: ignore

    try:
        yield {"calls": calls}
    finally:
        builtins.open = originals["builtins.open"]
        Path.open = originals["Path.open"]
        Path.write_text = originals["Path.write_text"]
        Path.write_bytes = originals["Path.write_bytes"]
        Path.mkdir = originals["Path.mkdir"]
        Path.unlink = originals["Path.unlink"]
        os.open = originals["os.open"]
        os.fdopen = originals["os.fdopen"]
        os.makedirs = originals["os.makedirs"]
        os.replace = originals["os.replace"]
        os.rename = originals["os.rename"]
        os.remove = originals["os.remove"]
        os.unlink = originals["os.unlink"]
        shutil.copyfile = originals["shutil.copyfile"]
        shutil.copy2 = originals["shutil.copy2"]
        shutil.move = originals["shutil.move"]
        sqlite3.connect = originals["sqlite3.connect"]
        if pandas_to_csv_original is not None:
            try:
                import pandas as pd  # type: ignore

                pd.DataFrame.to_csv = pandas_to_csv_original
            except Exception:
                pass


@contextlib.contextmanager
def install_no_network_guard(context: RehearsalContext) -> Iterator[dict[str, Any]]:
    calls: list[dict[str, str]] = []
    originals: list[tuple[Any, str, Any]] = []

    def block(name: str) -> Callable[..., Any]:
        def _blocked(*args: Any, **kwargs: Any) -> Any:
            calls.append({"call": name})
            stack = "".join(traceback.format_stack(limit=8)[:-1]).strip()
            raise RehearsalGuardError(f"rehearsal blocked network/process call: {name}\n{stack}")

        return _blocked

    def patch(obj: Any, attr: str, value: Any) -> None:
        if obj is not None and hasattr(obj, attr):
            originals.append((obj, attr, getattr(obj, attr)))
            setattr(obj, attr, value)

    try:
        import requests  # type: ignore

        patch(requests.sessions.Session, "request", block("requests.sessions.Session.request"))
        patch(requests, "get", block("requests.get"))
        patch(requests, "post", block("requests.post"))
    except Exception:
        pass

    patch(urllib.request, "urlopen", block("urllib.request.urlopen"))
    original_subprocess_run = subprocess.run
    original_subprocess_popen = subprocess.Popen

    def _allowed_platform_ver_probe(args: Any) -> bool:
        cmd = args[0] if args else ""
        if isinstance(cmd, (list, tuple)):
            parts = [str(part).lower() for part in cmd]
            text = " ".join(parts)
        else:
            text = str(cmd).lower()
        normalized = text.strip().strip('"').strip("'")
        return ("cmd" in text and "ver" in text) or normalized == "ver"

    def guarded_subprocess_run(*args: Any, **kwargs: Any) -> Any:
        if _allowed_platform_ver_probe(args):
            current_popen = subprocess.Popen
            subprocess.Popen = original_subprocess_popen
            try:
                return original_subprocess_run(*args, **kwargs)
            finally:
                subprocess.Popen = current_popen
        return block("subprocess.run")(*args, **kwargs)

    class GuardedPopen(original_subprocess_popen):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            block("subprocess.Popen")(*args, **kwargs)

    patch(subprocess, "run", guarded_subprocess_run)
    patch(subprocess, "Popen", GuardedPopen)

    try:
        import websocket  # type: ignore

        patch(websocket.WebSocketApp, "run_forever", block("websocket.WebSocketApp.run_forever"))
    except Exception:
        pass

    anthropic = sys.modules.get("anthropic")
    if anthropic is not None:
        class _BlockedAnthropic:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                self.messages = type(
                    "_BlockedMessages",
                    (),
                    {"create": staticmethod(block("anthropic.messages.create"))},
                )()

        patch(anthropic, "Anthropic", _BlockedAnthropic)

    try:
        yield {"calls": calls}
    finally:
        for obj, attr, value in reversed(originals):
            try:
                setattr(obj, attr, value)
            except Exception:
                pass


def _candidate_paths_for_assertion(bot: Any | None = None) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for module_name, attrs in {
        "trading_bot": ["DECISIONS_FILE", "_DECISIONS_DB_PATH", "JUDGMENT_DIR", "_LESSON_CANDIDATES_PATH", "BOT_PID_FILE"],
        "ticker_selection_db": ["DB_PATH"],
        "intraday_strategy_db": ["DB_PATH"],
        "kis_api": ["_EXCHANGE_CACHE_FILE", "_KR_SCREEN_CACHE_PATH", "_US_SCREEN_CACHE_PATH"],
        "strategy.param_tuner": ["_DB_PATH", "_SESSION_STATE_PATH"],
        "logger": ["LOG_DIR"],
    }.items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr in attrs:
            if hasattr(module, attr):
                paths[f"{module_name}.{attr}"] = getattr(module, attr)
    if bot is not None:
        pathb = getattr(bot, "pathb", None)
        if pathb is not None:
            store = getattr(pathb, "store", None)
            if store is not None and hasattr(store, "path"):
                paths["bot.pathb.store.path"] = getattr(store, "path")
            control = getattr(pathb, "control_store", None)
            if control is not None and hasattr(control, "path"):
                paths["bot.pathb.control_store.path"] = getattr(control, "path")
        v2 = getattr(bot, "v2", None)
        if v2 is not None:
            registry = getattr(v2, "registry", None)
            store = getattr(registry, "store", None)
            if store is not None and hasattr(store, "path"):
                paths["bot.v2.registry.store.path"] = getattr(store, "path")
            order_unknown = getattr(v2, "order_unknown", None)
            if order_unknown is not None and hasattr(order_unknown, "path"):
                paths["bot.v2.order_unknown.path"] = getattr(order_unknown, "path")
    return paths


def assert_runtime_objects_sandboxed(bot: Any, context: RehearsalContext) -> None:
    if getattr(bot, "is_paper", None) is not False:
        raise RehearsalGuardError("TradingBot must be created with is_paper=False for rehearsal")
    mode = str(getattr(bot, "_mode", "") or "").lower()
    if mode and mode != "live":
        raise RehearsalGuardError(f"TradingBot runtime mode must be live, got {mode}")
    for name, value in _candidate_paths_for_assertion(bot).items():
        if value in (None, ""):
            continue
        try:
            target = _abs(value)
        except Exception:
            continue
        if not _is_inside(target, context.sandbox_root):
            raise RehearsalGuardError(f"{name} points outside sandbox: {target}")


def import_module_if_available(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except Exception:
        return None


def read_order_intents(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
