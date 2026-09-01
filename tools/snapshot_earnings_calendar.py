#!/usr/bin/env python3
"""어닝 캘린더 point-in-time 스냅샷 — F1(악재 전파)·S13(어닝 배제)의 데이터 기반.

earnings_calendar.json은 롤링 창(약 2주)이라 덮어써지면 과거 시점의 "그날 알던
어닝 일정"을 복원할 수 없다. F1_BAD_NEWS_ECHO(Codex 제안, 09-01)와 S13의
사후 판정에는 point-in-time 스냅샷이 필수 — **박제 안 한 날은 영원히 잃는
데이터**라 오늘부터 매일 얼린다.

관측 전용. data/shadow/earnings_calendar_snapshots/YYYYMMDD.json, 일 멱등.
사용: python tools/snapshot_earnings_calendar.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "earnings_calendar.json"
OUT_DIR = ROOT / "data" / "shadow" / "earnings_calendar_snapshots"


def main() -> int:
    if not SRC.exists():
        print("[earnings_snap] 원본 없음 — 스킵")
        return 0
    today = datetime.now().strftime("%Y%m%d")
    out = OUT_DIR / f"{today}.json"
    if out.exists():
        print(f"[earnings_snap] {today} 이미 박제됨")
        return 0
    try:
        data = json.loads(SRC.read_text(encoding="utf-8"))
    except ValueError as exc:
        print(f"[earnings_snap] 원본 파싱 실패: {exc}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    n = len(data.get("by_symbol") or {})
    print(f"[earnings_snap] {today} 박제 완료 — {n}종목, 창 {data.get('from')}~{data.get('to')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
