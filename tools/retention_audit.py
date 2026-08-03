"""디스크 보존 감사·정리 도구 (2026-08-03 배포 색출 C3).

기본은 dry-run 보고만 한다. 실제 정리는 명시적 플래그 + 운영자 실행일 때만:
  python tools/retention_audit.py                     # 보고만 (안전)
  python tools/retention_audit.py --apply-bak --older-days 7
      -> data/audit/*.bak_* 중 N일 지난 사본 삭제 (라이브 DB는 절대 건드리지 않음)
  python tools/retention_audit.py --archive-logs --older-days 45
      -> logs/ 하위 N일 지난 파일을 logs/archive/YYYYMM/ 으로 이동 (삭제 아님)

라이브 DB(candidate_audit.db·decisions.db 등)의 행 삭제는 이 도구가 하지 않는다 —
원장 축약은 별도 설계·운영자 승인 사안이다.
"""
from __future__ import annotations

import argparse
import shutil
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _fmt_mb(n: float) -> str:
    return f"{n / 1e6:,.0f}MB"


def find_bak_files(older_days: float) -> list[Path]:
    cutoff = time.time() - older_days * 86400
    out = []
    for p in (ROOT / "data" / "audit").glob("*.bak_*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_size, reverse=True)


def find_old_logs(older_days: float) -> list[Path]:
    cutoff = time.time() - older_days * 86400
    out = []
    for p in (ROOT / "logs").rglob("*"):
        if not p.is_file():
            continue
        if "archive" in p.parts:
            continue
        if p.stat().st_mtime < cutoff:
            out.append(p)
    return sorted(out, key=lambda p: p.stat().st_size, reverse=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--older-days", type=float, default=7.0)
    ap.add_argument("--apply-bak", action="store_true", help="오래된 .bak 사본 삭제 (운영자 실행 전용)")
    ap.add_argument("--archive-logs", action="store_true", help="오래된 로그를 logs/archive/로 이동")
    args = ap.parse_args()

    baks = find_bak_files(args.older_days)
    logs = find_old_logs(max(args.older_days, 30.0))
    print(f"== 보존 감사 (기준 {args.older_days:g}일) ==")
    print(f".bak 사본 {len(baks)}개 / {_fmt_mb(sum(p.stat().st_size for p in baks))}:")
    for p in baks:
        print(f"  {p.name}  {_fmt_mb(p.stat().st_size)}  ({datetime.fromtimestamp(p.stat().st_mtime):%m-%d})")
    print(f"오래된 로그 {len(logs)}개 / {_fmt_mb(sum(p.stat().st_size for p in logs))} (기준 {max(args.older_days, 30.0):g}일)")

    if args.apply_bak:
        freed = 0
        for p in baks:
            freed += p.stat().st_size
            p.unlink()
            print(f"[삭제] {p.name}")
        print(f"확보 {_fmt_mb(freed)}")
    if args.archive_logs:
        moved = 0
        for p in logs:
            dest = ROOT / "logs" / "archive" / datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y%m") / p.relative_to(ROOT / "logs")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(dest))
            moved += 1
        print(f"[이동] 로그 {moved}개 -> logs/archive/")
    if not args.apply_bak and not args.archive_logs:
        print("(dry-run — 아무것도 변경하지 않음)")
    return 0


if __name__ == "__main__":
    main()
