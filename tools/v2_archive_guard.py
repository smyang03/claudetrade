from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re


EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "archive",
    "data",
    "state",
    "venv",
    ".venv",
}

ARCHIVE_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+(archive(?:\.[A-Za-z_][A-Za-z0-9_]*)*)\s+import|import\s+(archive(?:\.[A-Za-z_][A-Za-z0-9_]*)*))"
)


@dataclass(frozen=True)
class ArchiveImportFinding:
    path: str
    line: int
    target: str


def scan_archive_imports(root: str | Path) -> list[ArchiveImportFinding]:
    root_path = Path(root)
    findings: list[ArchiveImportFinding] = []
    for path in _iter_python_files(root_path):
        try:
            body = path.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(body, filename=str(path))
        except SyntaxError as exc:
            findings.extend(_scan_import_lines(path))
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_blocked(alias.name):
                        findings.append(ArchiveImportFinding(str(path), node.lineno, alias.name))
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if _is_blocked(module):
                    findings.append(ArchiveImportFinding(str(path), node.lineno, module))
    return findings


def _iter_python_files(root: Path):
    for path in root.rglob("*.py"):
        try:
            rel = path.relative_to(root)
        except ValueError:
            rel = path
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue
        yield path


def _is_blocked(target: str) -> bool:
    target = str(target or "")
    return target == "archive" or target.startswith("archive.")


def _scan_import_lines(path: Path) -> list[ArchiveImportFinding]:
    findings: list[ArchiveImportFinding] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return findings
    for line_no, line in enumerate(lines, start=1):
        match = ARCHIVE_IMPORT_RE.match(line)
        if match:
            target = next((group for group in match.groups() if group), "archive")
            findings.append(ArchiveImportFinding(str(path), line_no, target))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if live code imports archive modules.")
    parser.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()

    findings = scan_archive_imports(args.root)
    if findings:
        for finding in findings:
            print(f"{finding.path}:{finding.line}: blocked archive import {finding.target}")
        return 1
    print("archive import guard: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
