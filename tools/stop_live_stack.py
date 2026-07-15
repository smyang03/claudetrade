"""라이브 스택 전체 정지 — headless watchdog / wt 탭 어느 쪽이 띄웠든 동일하게 잡는다.

기존 start_live_stack.bat은 CommandLine에 프로젝트 경로가 있는지로 "우리 프로세스"를 판정했다.
그런데 두 launcher 모두 작업디렉터리를 프로젝트로 잡고 상대경로로 실행하므로
(`python trading_bot.py --live`, `python.exe trading_bot.py --live`) CommandLine에 경로가 없다.
그래서 bat이 기존 스택을 "남의 프로세스"로 오판해 죽이지 못하고 새 스택을 또 띄웠고,
라이브 봇이 2개 동시 실행되는 사고가 났다(2026-07-13 텔레그램 getUpdates 409 충돌로 실측).

여기서는 psutil로 **실제 cwd**를 읽어 프로젝트 소속을 판정한다. cwd가 프로젝트 밖이면
CommandLine에 절대경로가 있는 경우만 인정한다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Iterable

import psutil

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 역할 → 스크립트 needle. run_counterfactual_pipeline/integrity_check는 pid 파일이 없어
# 이름 기반이 유일한 식별 수단이다.
ROLE_NEEDLES: dict[str, str] = {
    "live_bot": "trading_bot.py",
    "dashboard": "dashboard_server.py",
    "guardian": "live_guardian.py",
    "broker_truth_scheduler": "broker_truth_scheduler.py",
    "preopen_scheduler": "preopen_scheduler.py",
    "core_shadow_tracker": "core_shadow_tracker.py",
    "counterfactual_pipeline": "run_counterfactual_pipeline.py",
    "integrity_check": "integrity_check.py",
}

# 프로세스가 죽은 뒤에만 지운다. 살아있는 프로세스의 lock을 지우면 중복 기동을 부른다.
STALE_STATE_FILES = (
    "state/live_trading_bot.pid",
    "state/dashboard_server.pid",
    "state/headless_live_stack_pids.json",
    "state/preopen_scheduler.lock.json",
    "state/broker_truth_scheduler.lock.json",
    "state/core_shadow_tracker_heartbeat.json",
    "state/live_guardian_heartbeat.json",
)


def match_role(cmdline: str, cwd: str, root: Path) -> str | None:
    """이 프로세스가 우리 스택의 어떤 역할인지. 아니면 None."""
    if not cmdline:
        return None
    root_str = str(root).lower()
    in_project = (cwd or "").lower().startswith(root_str) or root_str in cmdline.lower()
    if not in_project:
        return None
    for role, needle in ROLE_NEEDLES.items():
        if needle in cmdline:
            return role
    return None


def find_stack_processes(root: Path = PROJECT_ROOT) -> list[tuple[psutil.Process, str, str]]:
    found: list[tuple[psutil.Process, str, str]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
        try:
            name = (proc.info["name"] or "").lower()
            if "python" not in name:
                continue
            cmdline = " ".join(proc.info["cmdline"] or [])
            role = match_role(cmdline, proc.info["cwd"] or "", root)
            if role:
                found.append((proc, role, cmdline))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return found


def clear_stale_state(root: Path, dry_run: bool) -> list[str]:
    removed: list[str] = []
    for rel in STALE_STATE_FILES:
        path = root / rel
        if not path.exists():
            continue
        if dry_run:
            removed.append(rel)
            continue
        try:
            path.unlink()
            removed.append(rel)
        except OSError as exc:  # pragma: no cover - 파일 잠금 등
            print(f"[WARN] {rel} 삭제 실패: {exc}")
    return removed


def stop_stack(root: Path, dry_run: bool, timeout: float) -> dict:
    targets = find_stack_processes(root)
    roles: dict[str, list[int]] = {}
    for proc, role, _ in targets:
        roles.setdefault(role, []).append(proc.pid)

    print(f"[INFO] project={root}")
    if not targets:
        print("[OK] 실행 중인 스택 프로세스 없음")
    for proc, role, cmdline in targets:
        print(f"  {'[DRY-RUN] would stop' if dry_run else '[STOP]'} {role:<24} pid={proc.pid} :: {cmdline[:70]}")

    duplicates = {role: pids for role, pids in roles.items() if len(pids) > 1}
    if duplicates:
        print(f"[WARN] 중복 역할 감지: {duplicates}")

    killed: list[int] = []
    if not dry_run and targets:
        procs = [proc for proc, _, _ in targets]
        for proc in procs:
            try:
                proc.terminate()
            except psutil.NoSuchProcess:
                continue
        gone, alive = psutil.wait_procs(procs, timeout=timeout)
        for proc in alive:
            try:
                proc.kill()
            except psutil.NoSuchProcess:
                continue
        psutil.wait_procs(alive, timeout=timeout)
        killed = [proc.pid for proc in procs]

        still = find_stack_processes(root)
        if still:
            print(f"[ERROR] 종료 실패 pid={[p.pid for p, _, _ in still]}")
            return {
                "ok": False,
                "stopped": killed,
                "duplicates": duplicates,
                "cleared": [],
                "still_alive": [p.pid for p, _, _ in still],
            }
        print(f"[OK] 종료 완료: {len(gone)} terminated / {len(alive)} killed")

    cleared = clear_stale_state(root, dry_run)
    for rel in cleared:
        print(f"  {'[DRY-RUN] would clear' if dry_run else '[CLEAR]'} {rel}")

    return {"ok": True, "stopped": killed, "duplicates": duplicates, "cleared": cleared, "still_alive": []}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="라이브 스택 전체 정지 (headless/wt 무관)")
    parser.add_argument("--dry-run", action="store_true", help="종료하지 않고 대상만 출력")
    parser.add_argument("--timeout", type=float, default=10.0, help="terminate 후 대기 초")
    parser.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = stop_stack(PROJECT_ROOT, args.dry_run, args.timeout)
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
