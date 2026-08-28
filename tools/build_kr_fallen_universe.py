#!/usr/bin/env python3
"""KR fallen 스캔 유니버스 생성 — 규칙 기반 재현 가능 (2026-08-28 운영자 승인).

배경: `data/analysis/kr_fallen_universe.json`(641종목)은 생성 코드가 저장소에 없는
수동 산출물이었고, 인구조사(kr_universe_census)에서 **자격 있는 종목 221개가 스캔
대상에서 빠져 있음**이 확인됐다(밖 프로필 충족 433건·forward +6.17% vs 안 +4.41%).
이 도구는 유니버스를 **규칙으로** 만든다 — 왜 이 종목이 들어갔는지 항상 재현된다.

선정 규칙(= KR fallen 규칙의 자격 요건과 같은 축):
  ① 우리 가격 CSV 보유(data/price/kr) — 스캔·정산에 필요한 이력 확보분
  ② 최근 20세션 평균 거래대금 >= 10억원 (규칙의 유동성 하한과 동일)
  ③ 최근 종가 >= 7,110원 (규칙의 가격 하한과 동일)
  ④ 기존 유니버스 종목은 유지(이력 연속성 — 일시 미달로 빠지면 원장이 끊긴다)

⚠️ 규칙 자체(할인·갭 문턱)는 건드리지 않는다. 이 확대는 "같은 규칙을 자격 있는
종목에 적용"이며 문턱 완화가 아니다(08-24 재론 금지와 무충돌).

사용: python tools/build_kr_fallen_universe.py [--apply]
      (--apply 없으면 dry-run: 추가/제외 종목 수만 출력)
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE = ROOT / "data" / "analysis" / "kr_fallen_universe.json"
PRICE_DIR = ROOT / "data" / "price" / "kr"
MIN_AMT20 = 1e9
MIN_CLOSE = 7110
MIN_ROWS = 25


def _tail_rows(path: Path, n: int = 25) -> list[tuple[str, float, float]]:
    rows: list[tuple[str, float, float]] = []
    with path.open(encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    rows.append((r[0], float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return rows[-n:] if len(rows) >= n else rows


def main() -> int:
    parser = argparse.ArgumentParser(description="KR fallen 유니버스 생성")
    parser.add_argument("--apply", action="store_true", help="파일 갱신 (없으면 dry-run)")
    args = parser.parse_args()

    existing = set(json.loads(UNIVERSE.read_text(encoding="utf-8-sig"))) if UNIVERSE.exists() else set()
    qualified: set[str] = set()
    skipped_liq = skipped_price = skipped_short = 0
    for path in sorted(PRICE_DIR.glob("kr_*.csv")):
        ticker = path.stem.replace("kr_", "", 1)
        rows = _tail_rows(path)
        if len(rows) < MIN_ROWS - 5:
            skipped_short += 1
            continue
        amt20 = st.mean(c * v for _, c, v in rows[-20:])
        last_close = rows[-1][1]
        if amt20 < MIN_AMT20:
            skipped_liq += 1
            continue
        if last_close < MIN_CLOSE:
            skipped_price += 1
            continue
        qualified.add(ticker)

    # ④ 기존 종목은 유지 (이력 연속성)
    final = sorted(qualified | existing)
    added = sorted(qualified - existing)
    kept_only = sorted(existing - qualified)
    print(f"CSV 유니버스 {len(list(PRICE_DIR.glob('kr_*.csv')))}종목 스캔")
    print(f"  자격 충족(거래대금 10억+·7,110원+): {len(qualified)}종목")
    print(f"  제외: 유동성 미달 {skipped_liq} · 가격 미달 {skipped_price} · 이력 부족 {skipped_short}")
    print(f"기존 {len(existing)} → 신규 {len(final)} (추가 {len(added)}, 기존 유지분 중 현재 미달 {len(kept_only)})")
    if added:
        print(f"  추가 예시: {added[:12]}")
    if not args.apply:
        print("\n(dry-run — 반영하려면 --apply)")
        return 0
    backup = UNIVERSE.with_suffix(f".json.bak_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}")
    if UNIVERSE.exists():
        backup.write_text(UNIVERSE.read_text(encoding="utf-8-sig"), encoding="utf-8")
        print(f"백업: {backup.name}")
    UNIVERSE.write_text(json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"반영 완료 → {UNIVERSE.name} ({len(final)}종목)")
    print("다음: python tools/kr_fallen_shadow_scan.py --update-cache (장 마감 후, 신규분 가격 수집)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
