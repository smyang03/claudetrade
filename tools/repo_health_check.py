from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str


def _run_command(cmd: list[str], *, cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _short_output(proc: subprocess.CompletedProcess[str]) -> str:
    text = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
    return text[-1200:] if text else f"exit={proc.returncode}"


def check_py_compile(root: Path) -> CheckResult:
    target = root / "trading_bot.py"
    if not target.exists():
        return CheckResult("trading_bot.py_compile", "FAIL", f"missing file: {target}")
    try:
        proc = _run_command([sys.executable, "-m", "py_compile", str(target)], cwd=root)
    except Exception as exc:
        return CheckResult("trading_bot.py_compile", "FAIL", str(exc))
    if proc.returncode == 0:
        return CheckResult("trading_bot.py_compile", "PASS", "python py_compile passed")
    return CheckResult("trading_bot.py_compile", "FAIL", _short_output(proc))


def check_brain_json(root: Path) -> tuple[CheckResult, Any | None]:
    target = root / "state" / "brain.json"
    if not target.exists():
        return CheckResult("brain.json_parse", "FAIL", f"missing file: {target}"), None
    try:
        with target.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        return CheckResult("brain.json_parse", "FAIL", str(exc)), None
    return CheckResult("brain.json_parse", "PASS", "state/brain.json parsed as UTF-8 JSON"), data


def _iter_execution_lessons(obj: Any, path: str = "$"):
    if isinstance(obj, dict):
        lessons = obj.get("execution_lessons")
        if isinstance(lessons, list):
            for idx, item in enumerate(lessons):
                yield f"{path}.execution_lessons[{idx}]", item
        for key, value in obj.items():
            yield from _iter_execution_lessons(value, f"{path}.{key}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from _iter_execution_lessons(item, f"{path}[{idx}]")


def check_execution_lessons(data: Any | None) -> CheckResult:
    if data is None:
        return CheckResult("brain.execution_lessons_mojibake", "FAIL", "brain.json was not parsed")
    markers = ("\ufffd", "泥", "?섏씡", "?ㅽ뙣", "?좏슚", "?⑦꽩")
    hits: list[str] = []
    lesson_count = 0
    for path, lesson in _iter_execution_lessons(data):
        if not isinstance(lesson, str):
            continue
        lesson_count += 1
        if any(marker in lesson for marker in markers):
            hits.append(f"{path}: {lesson}")
    if hits:
        return CheckResult(
            "brain.execution_lessons_mojibake",
            "FAIL",
            "; ".join(hits[:8]) + ("; ..." if len(hits) > 8 else ""),
        )
    return CheckResult(
        "brain.execution_lessons_mojibake",
        "PASS",
        f"checked {lesson_count} execution_lessons entries",
    )


def _load_trading_bot_ast(root: Path) -> tuple[ast.Module | None, str | None]:
    target = root / "trading_bot.py"
    try:
        source = target.read_text(encoding="utf-8")
        return ast.parse(source, filename=str(target)), None
    except Exception as exc:
        return None, str(exc)


def _target_names(target: ast.AST) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names.update(_target_names(elt))
        return names
    return set()


def _self_attr_name(target: ast.AST) -> str | None:
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Name)
        and target.value.id == "self"
    ):
        return target.attr
    return None


def check_trading_bot_structure(tree: ast.Module | None) -> CheckResult:
    if tree is None:
        return CheckResult("trading_bot.structure", "FAIL", "trading_bot.py AST unavailable")

    top_level_assigned: set[str] = set()
    all_assigned: set[str] = set()
    self_attrs: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                top_level_assigned.update(_target_names(target))

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                all_assigned.update(_target_names(target))
                attr = _self_attr_name(target)
                if attr:
                    self_attrs.add(attr)
        elif isinstance(node, ast.AnnAssign):
            all_assigned.update(_target_names(node.target))
            attr = _self_attr_name(node.target)
            if attr:
                self_attrs.add(attr)

    for_targets = [
        target
        for node in ast.walk(tree)
        if isinstance(node, ast.For)
        for target in _target_names(node.target)
    ]

    missing: list[str] = []
    for name in ("_STARTUP_GUARD_SEC", "_ENTRY_SCAN_REGULAR_INTERVAL_MIN"):
        if name not in top_level_assigned:
            missing.append(f"top-level assignment {name}")
    for attr in ("_hist_fill_last_ts", "_ticker_exclude_log"):
        if attr not in self_attrs:
            missing.append(f"self assignment {attr}")
    for name in ("_since_open", "_holding", "protected", "n_replace", "_SKIP_ACTIONS", "_type_map"):
        if name not in all_assigned:
            missing.append(f"assignment {name}")
    if for_targets.count("_mkt") < 2:
        missing.append("startup/housekeeping for _mkt loops")

    if missing:
        return CheckResult("trading_bot.structure", "FAIL", "missing: " + ", ".join(missing))
    return CheckResult("trading_bot.structure", "PASS", "critical assignments and loop headers are present")


def _literal_assignment(tree: ast.Module, name: str) -> Any | None:
    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                return ast.literal_eval(node.value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return ast.literal_eval(node.value)
    return None


def check_strategy_aliases(tree: ast.Module | None) -> CheckResult:
    if tree is None:
        return CheckResult("strategy_aliases", "FAIL", "trading_bot.py AST unavailable")
    try:
        strategy_map = _literal_assignment(tree, "_STRATEGY_NAME_MAP")
    except Exception as exc:
        return CheckResult("strategy_aliases", "FAIL", f"cannot read _STRATEGY_NAME_MAP: {exc}")
    if not isinstance(strategy_map, dict):
        return CheckResult("strategy_aliases", "FAIL", "_STRATEGY_NAME_MAP is missing or not a dict literal")

    required = {
        "모멘텀": "momentum",
        "평균회귀": "mean_reversion",
        "갭풀백": "gap_pullback",
        "갭 풀백": "gap_pullback",
        "갭눌림": "gap_pullback",
        "연속진입": "continuation",
        "관망": "",
    }
    missing = [key for key, value in required.items() if strategy_map.get(key) != value]
    bad_markers = ("紐⑤찘", "?됯퇏", "媛??", "愿留", "?곗냽")
    bad_keys = [str(key) for key in strategy_map if any(marker in str(key) for marker in bad_markers)]
    if missing or bad_keys:
        detail_parts: list[str] = []
        if missing:
            detail_parts.append("missing/incorrect aliases: " + ", ".join(missing))
        if bad_keys:
            detail_parts.append("mojibake aliases: " + ", ".join(bad_keys[:8]))
        return CheckResult("strategy_aliases", "FAIL", "; ".join(detail_parts))
    return CheckResult("strategy_aliases", "PASS", "Korean strategy aliases are mapped")


def check_git_diff(root: Path, include_git: bool) -> CheckResult:
    if not include_git:
        return CheckResult("git.diff_check", "WARN", "skipped by option")
    cmd = [
        "git",
        "diff",
        "--check",
        "--",
        "trading_bot.py",
        "state/brain.json",
        "tools/repo_health_check.py",
        "docs/plans/recovery_and_followup_todo_20260501.md",
    ]
    try:
        proc = _run_command(cmd, cwd=root, timeout=60)
    except FileNotFoundError:
        return CheckResult("git.diff_check", "WARN", "git command not found")
    except Exception as exc:
        return CheckResult("git.diff_check", "WARN", str(exc))
    if proc.returncode == 0:
        return CheckResult("git.diff_check", "PASS", "git diff --check passed for recovery files")
    return CheckResult("git.diff_check", "FAIL", _short_output(proc))


def run_checks(root: Path, include_git: bool) -> list[CheckResult]:
    results: list[CheckResult] = []
    results.append(check_py_compile(root))
    brain_result, brain_data = check_brain_json(root)
    results.append(brain_result)
    results.append(check_execution_lessons(brain_data))
    tree, ast_error = _load_trading_bot_ast(root)
    if ast_error:
        results.append(CheckResult("trading_bot.ast", "FAIL", ast_error))
    results.append(check_trading_bot_structure(tree))
    results.append(check_strategy_aliases(tree))
    results.append(check_git_diff(root, include_git))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repository health check for trading bot recovery safeguards.")
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    parser.add_argument("--trigger", default="manual", help="manual, KR_PREOPEN, KR_POSTCLOSE, US_PREOPEN, US_POSTCLOSE")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    parser.add_argument("--skip-git-diff", action="store_true", help="skip git diff --check")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    results = run_checks(root, include_git=not args.skip_git_diff)
    ok = all(result.status != "FAIL" for result in results)
    payload = {
        "ok": ok,
        "trigger": args.trigger,
        "root": str(root),
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "checks": [asdict(result) for result in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"repo_health_check trigger={args.trigger} ok={ok}")
        for result in results:
            print(f"[{result.status}] {result.name}: {result.detail}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
