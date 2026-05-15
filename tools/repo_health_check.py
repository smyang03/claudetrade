from __future__ import annotations

import argparse
import ast
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
    markers = ("\ufffd", "\uf9e3", "?\uc10f\uc521", "?\u317d\ub663", "?\uc88f\uc29a", "?\u2466\uaf69")
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
    bad_markers = ("\uf9cf\u2464\ucc18", "?\ub42f\ud1cf", "\u5a9b??", "\u613f\uf9cd", "?\uacd7\ub0fd")
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
        "ml/db_writer.py",
        "ml/forward_updater.py",
        "ml/db_health.py",
        "ml/test_full.py",
        "strategy/param_tuner.py",
        "tools/recover_decisions_db.py",
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


def check_ml_db_health(root: Path) -> CheckResult:
    target = root / "data" / "ml" / "decisions.db"
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from ml import db_health

        result = db_health.check_db_health(target, read_only=True)
    except Exception as exc:
        return CheckResult("ml.decisions_db_health", "FAIL", str(exc))

    if not result.get("exists"):
        return CheckResult("ml.decisions_db_health", "FAIL", f"missing file: {target}")

    fixture_rows = result.get("contamination", {}).get("fixture_rows")
    seq = result.get("sqlite_sequence", {}).get("decisions")
    detail = (
        f"rows={result.get('total_rows')} live={result.get('live_rows')} "
        f"latest={result.get('latest_session_date')} fixture_rows={fixture_rows} "
        f"seq={seq} recent_live={result.get('last_3_trading_days_live_rows')}"
    )
    if result.get("ok"):
        return CheckResult("ml.decisions_db_health", "PASS", detail)
    errors = ", ".join(str(e) for e in result.get("errors", []))
    return CheckResult("ml.decisions_db_health", "FAIL", f"{detail}; errors={errors}")


def check_selection_db_health(root: Path) -> CheckResult:
    target = root / "data" / "ticker_selection_log.db"
    if not target.exists():
        return CheckResult("selection.db_health", "WARN", f"missing file: {target}")
    conn = None
    try:
        conn = sqlite3.connect(str(target), timeout=5)
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ticker_selection_log'"
        ).fetchone()
        if table is None:
            return CheckResult("selection.db_health", "FAIL", "ticker_selection_log table missing")
        total, latest = conn.execute(
            "SELECT COUNT(*), MAX(date) FROM ticker_selection_log"
        ).fetchone()
        traded = conn.execute(
            "SELECT COUNT(*) FROM ticker_selection_log WHERE COALESCE(traded, 0) != 0"
        ).fetchone()[0]
    except Exception as exc:
        return CheckResult("selection.db_health", "FAIL", str(exc))
    finally:
        if conn is not None:
            conn.close()
    return CheckResult(
        "selection.db_health",
        "PASS",
        f"rows={total} traded={traded} latest={latest}",
    )


def check_price_csv_health(root: Path, market: str, trigger: str = "manual") -> list[CheckResult]:
    try:
        from runtime.price_csv_health import price_csv_health_summary

        summary = price_csv_health_summary(root, market)
    except Exception as exc:
        return [CheckResult(f"data.price_csv_freshness.{market.lower()}", "FAIL", f"health check failed: {exc}")]

    counts = summary.get("counts", {})
    malformed = int(counts.get("malformed_csv", 0))
    missing = int(counts.get("missing_csv", 0))
    stale = int(counts.get("stale_csv", 0))
    total = int(summary.get("total", 0))
    fresh_ratio = float(summary.get("fresh_ratio", 0.0))
    trigger_key = str(trigger or "").upper()
    is_postclose = trigger_key == f"{market.upper()}_POSTCLOSE"

    freshness_status = "PASS"
    if total == 0 or stale or fresh_ratio < 0.95:
        freshness_status = "FAIL" if is_postclose and fresh_ratio < 0.95 else "WARN"
    freshness_detail = (
        f"total={total} fresh={summary.get('fresh_count', 0)} "
        f"fresh_ratio={fresh_ratio:.1%} stale={stale} "
        f"expected_last={summary.get('expected_last_date', '')} "
        f"last_range={summary.get('oldest_last_date', '')}..{summary.get('newest_last_date', '')}"
    )
    integrity_status = "PASS"
    if total == 0 or malformed or missing:
        integrity_status = "FAIL" if is_postclose and (malformed or missing) else "WARN"
    integrity_detail = f"total={total} malformed={malformed} missing={missing}"
    return [
        CheckResult(f"data.price_csv_freshness.{market.lower()}", freshness_status, freshness_detail),
        CheckResult(f"data.price_csv_integrity.{market.lower()}", integrity_status, integrity_detail),
    ]


def run_checks(root: Path, include_git: bool, trigger: str = "manual") -> list[CheckResult]:
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
    results.append(check_ml_db_health(root))
    results.append(check_selection_db_health(root))
    results.extend(check_price_csv_health(root, "KR", trigger))
    results.extend(check_price_csv_health(root, "US", trigger))
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
    results = run_checks(root, include_git=not args.skip_git_diff, trigger=args.trigger)
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
