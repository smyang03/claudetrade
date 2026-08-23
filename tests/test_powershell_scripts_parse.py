"""운영 PowerShell 스크립트 계약 (2026-08-24).

사고: `tools/restart_live_stack_safely.ps1`에 한국어가 들어간 **문자열 리터럴**을 추가했더니
Windows PowerShell 5.1 파서가 블록을 못 닫고 죽었다. 이 파일은 BOM이 없어서 5.1이 내용을
CP949로 읽는데, 문자열 안의 비ASCII가 깨지면 **닫는 따옴표까지 소실**돼 그 뒤 전체가
꼬인다(스크립트 자신의 주석이 Get-Content에 대해 같은 함정을 경고하고 있었다).

주석의 한국어는 안전하다 — 깨져도 주석이라 파싱에 영향이 없다. 실제로 이 파일들은
설명을 전부 주석에 두고 문자열은 ASCII로만 쓰는 규약을 지켜 왔다(사고 전 실측:
restart 스크립트의 문자열 내 비ASCII **0건**).

여기서 고정하는 계약:
  1. 운영 스크립트는 PowerShell 파서를 통과한다.
  2. 문자열 리터럴 안에는 비ASCII를 넣지 않는다(BOM 없는 파일 한정).

1번은 pwsh/powershell이 있을 때만 돌고, 2번은 어디서나 돈다.
"""

from __future__ import annotations

import codecs
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = [
    ROOT / "tools" / "restart_live_stack_safely.ps1",
    ROOT / "tools" / "start_live_stack_headless.ps1",
]

# "..." 안의 내용을 잡는다. PowerShell은 ``를 이스케이프로 쓰므로 단순 매칭으로 충분하다.
_DQ_STRING = re.compile(r'"([^"\r\n]*)"')


def _has_bom(path: Path) -> bool:
    # codecs.BOM_UTF8을 쓴다 — 바이트를 직접 쓰면 tools/check_mojibake.py가
    # "escaped mojibake byte"로 오탐한다(2026-08-24).
    return path.read_bytes().startswith(codecs.BOM_UTF8)


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_no_non_ascii_inside_string_literals(script: Path) -> None:
    """BOM 없는 스크립트의 문자열 리터럴은 ASCII만 — CP949 오독으로 따옴표가 죽는다."""
    if not script.exists():
        pytest.skip(f"{script.name} 없음")
    if _has_bom(script):
        pytest.skip(f"{script.name}은 BOM이 있어 이 제약이 필요 없다")

    offenders: list[tuple[int, str]] = []
    for lineno, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith("#"):
            continue  # 주석의 한국어는 안전하다
        for body in _DQ_STRING.findall(line):
            if any(ord(ch) > 127 for ch in body):
                offenders.append((lineno, line.strip()[:100]))
                break
    assert not offenders, (
        f"{script.name}: 문자열 리터럴 안에 비ASCII가 있다 — "
        f"PowerShell 5.1이 CP949로 읽어 따옴표가 소실될 수 있다. "
        f"설명은 주석으로 옮길 것. 위치: {offenders}"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_script_parses(script: Path) -> None:
    """실제 PowerShell 파서로 문법을 확인한다 — 운영 중 스크립트가 죽으면 스택이 안 뜬다."""
    if not script.exists():
        pytest.skip(f"{script.name} 없음")
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        pytest.skip("PowerShell 없음 (비 Windows 환경)")

    command = (
        "$errs = $null; "
        f"$null = [System.Management.Automation.Language.Parser]::ParseFile('{script}', "
        "[ref]$null, [ref]$errs); "
        "if ($errs) { $errs | ForEach-Object { $_.Message }; exit 1 } else { exit 0 }"
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"{script.name} 파스 실패:\n{result.stdout}\n{result.stderr}"
