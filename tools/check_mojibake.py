from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEXT_SUFFIXES = {
    ".bat",
    ".cfg",
    ".css",
    ".env",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".sql",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}

EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
}

EXCLUDED_PATH_PATTERNS = (
    re.compile(r"^audit/encoding_mojibake_report_.*\.(csv|md)$"),
)

MOJIBAKE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("replacement character", re.compile("\ufffd")),
    ("C1 control byte decoded as text", re.compile(r"[\u0080-\u009f]")),
    ("Hangul compatibility jamo", re.compile(r"[\u3130-\u318f]")),
    ("CJK/compat ideograph in Korean source", re.compile(r"[\u4e00-\u9fff\uf900-\ufaff]")),
    ("escaped mojibake byte", re.compile(r"\\x[89a-fA-F][0-9a-fA-F]")),
)


@dataclass
class Finding:
    path: str
    line_no: int | None
    reason: str
    text: str


def _run_git(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _is_excluded(path: str) -> bool:
    norm = path.replace("\\", "/")
    parts = norm.split("/")
    if any(part in EXCLUDED_DIRS for part in parts):
        return True
    return any(pattern.search(norm) for pattern in EXCLUDED_PATH_PATTERNS)


def _is_text_path(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def _check_line(path: str, line_no: int | None, text: str) -> Finding | None:
    for reason, pattern in MOJIBAKE_PATTERNS:
        if pattern.search(text):
            return Finding(path=path, line_no=line_no, reason=reason, text=text)
    return None


def _decode_utf8(path: str, data: bytes) -> tuple[str | None, Finding | None]:
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        finding = Finding(
            path=path,
            line_no=None,
            reason="file is not valid UTF-8",
            text=str(exc),
        )
        return None, finding


def _scan_full_file(path: str, data: bytes) -> list[Finding]:
    text, decode_finding = _decode_utf8(path, data)
    if decode_finding:
        return [decode_finding]
    assert text is not None
    findings: list[Finding] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        finding = _check_line(path, idx, line)
        if finding:
            findings.append(finding)
    return findings


def _iter_worktree_files() -> list[str]:
    paths: list[str] = []
    for root, dirs, files in os.walk(ROOT):
        rel_root = Path(root).relative_to(ROOT).as_posix()
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
        for name in files:
            path = (Path(rel_root) / name).as_posix() if rel_root != "." else name
            if _is_text_path(path) and not _is_excluded(path):
                paths.append(path)
    return sorted(paths)


def scan_all() -> list[Finding]:
    findings: list[Finding] = []
    for path in _iter_worktree_files():
        full_path = ROOT / path
        try:
            data = full_path.read_bytes()
        except OSError as exc:
            findings.append(Finding(path, None, "cannot read file", str(exc)))
            continue
        findings.extend(_scan_full_file(path, data))
    return findings


def scan_staged_added_lines() -> list[Finding]:
    proc = _run_git(["diff", "--cached", "--unified=0", "--no-color", "--diff-filter=ACMR"])
    if proc.returncode != 0:
        return [Finding("<git>", None, "cannot read staged diff", proc.stderr.decode("utf-8", "replace"))]

    text, decode_finding = _decode_utf8("<staged diff>", proc.stdout)
    if decode_finding:
        return [decode_finding]
    assert text is not None

    findings: list[Finding] = []
    current_path: str | None = None
    new_line_no: int | None = None

    for line in text.splitlines():
        if line.startswith("+++ "):
            raw = line[4:]
            current_path = None
            if raw.startswith("b/"):
                candidate = raw[2:]
                if _is_text_path(candidate) and not _is_excluded(candidate):
                    current_path = candidate
            continue

        if line.startswith("@@ "):
            match = re.search(r"\+(\d+)(?:,\d+)?", line)
            new_line_no = int(match.group(1)) if match else None
            continue

        if current_path is None:
            continue

        if line.startswith("+") and not line.startswith("+++"):
            content = line[1:]
            finding = _check_line(current_path, new_line_no, content)
            if finding:
                findings.append(finding)
            if new_line_no is not None:
                new_line_no += 1
        elif not line.startswith("-") and new_line_no is not None:
            new_line_no += 1

    return findings


def _print_findings(findings: list[Finding], max_findings: int) -> None:
    print("Mojibake/encoding check failed.", file=sys.stderr)
    print("Fix the suspicious text or run the check on a narrower patch.", file=sys.stderr)
    for finding in findings[:max_findings]:
        location = finding.path
        if finding.line_no is not None:
            location += f":{finding.line_no}"
        snippet = finding.text.strip()
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        print(f"- {location}: {finding.reason}: {snippet}", file=sys.stderr)
    if len(findings) > max_findings:
        print(f"... {len(findings) - max_findings} more finding(s)", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Detect Korean mojibake before commit.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan added lines in the staged diff")
    mode.add_argument("--all", action="store_true", help="scan all text files in the working tree")
    parser.add_argument("--max-findings", type=int, default=30)
    args = parser.parse_args(argv)

    findings = scan_all() if args.all else scan_staged_added_lines()
    if findings:
        _print_findings(findings, args.max_findings)
        return 1
    print("Mojibake/encoding check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
