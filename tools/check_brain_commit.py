"""Pre-commit guard: block accidental brain.json commits.

Usage:
  python tools/check_brain_commit.py          # called by pre-commit hook
  ALLOW_BRAIN_COMMIT=1 git commit ...         # bypass with env flag
  python tools/check_brain_commit.py --staged # explicit check
"""
from __future__ import annotations

import os
import subprocess
import sys


GUARDED_PATH = "state/brain.json"
BYPASS_ENV = "ALLOW_BRAIN_COMMIT"


def _staged_files() -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception as exc:
        print(f"[check_brain_commit] git diff failed: {exc}", file=sys.stderr)
        return []


def main() -> int:
    if os.environ.get(BYPASS_ENV, "").strip().lower() in {"1", "true", "yes"}:
        return 0

    staged = _staged_files()
    if GUARDED_PATH not in staged:
        return 0

    print(
        f"\n[BLOCKED] {GUARDED_PATH} is staged.\n"
        f"\n"
        f"  brain.json은 정책 메모리입니다. screener/code 커밋과 분리해야 합니다.\n"
        f"  승인된 brain 업데이트라면 아래 명령으로 커밋하세요:\n"
        f"\n"
        f"    ALLOW_BRAIN_COMMIT=1 git commit ...\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
