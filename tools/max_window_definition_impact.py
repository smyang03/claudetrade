"""MAX(복권형) 창 정의 2종의 실측 영향 (2026-08-23, Codex 리뷰 P2-12).

배경: 같은 이름 `max_daily_ret_21d`인데 구현이 둘이다.
  · 실주문 브리지 `runtime/us_swing_order_bridge._max_daily_return_21d`
      → 종가 21개 슬라이스 = 일간수익 **20개**
  · Path A 공통 계산기 `bot/pool_quality_features` (lookback+1)
      → 종가 22개 = 일간수익 **21개**

08-20 리서치(밴드+MAX>=8)가 어느 계산을 썼는지 원본 스크립트가 저장소에 없어 확정할 수
없다. 그래서 "22개가 맞다"고 바로 바꾸지 않고 **차이가 실제로 무엇을 바꾸는지** 먼저 잰다.
창을 넓히면 MAX 하한의 통과·탈락이 이동하고, 그건 검증 없이 라이브 선별을 옮기는 것이다.

이 스크립트를 저장소에 남기는 이유: 08-20 리서치가 스크립트를 안 남겨 이 질문 자체가
생겼다(리서치 규율 — 판정 근거 스크립트는 커밋한다).

사용:
    python tools/max_window_definition_impact.py            # 두 모집단 모두
    python tools/max_window_definition_impact.py --floor 8  # 하한 변경
"""

from __future__ import annotations

import argparse
import csv
import glob
import os
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRICE_DIR = ROOT / "data" / "price" / "us"
SHADOW_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"

_CACHE: dict[str, list[tuple[str, float]]] = {}


def _closes(ticker: str) -> list[tuple[str, float]]:
    """일자 오름차순 (date, close). BOM 필수 — CSV는 전부 utf-8-sig다."""
    key = ticker.upper()
    if key in _CACHE:
        return _CACHE[key]
    path = PRICE_DIR / f"us_{key}.csv"
    rows: list[tuple[str, float]] = []
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                date = str(row.get("date") or "")[:10]
                try:
                    close = float(row.get("close") or 0.0)
                except (TypeError, ValueError):
                    continue
                if date and close > 0:
                    rows.append((date, close))
        rows.sort()
    _CACHE[key] = rows
    return rows


def _max_ret(window: list[float]) -> float | None:
    if len(window) < 6:
        return None
    return max((window[i] / window[i - 1] - 1) * 100.0 for i in range(1, len(window)))


def _scan_universe(floor: float) -> None:
    """모집단 A — 보유 가격 CSV 전 종목·전 세션 (배경 분포)."""
    files = sorted(glob.glob(str(PRICE_DIR / "us_*.csv")))
    total = same = flip_up = flip_down = 0
    gap_sum = 0.0
    for path in files:
        ticker = os.path.basename(path)[3:-4]
        rows = _closes(ticker)
        closes = [c for _, c in rows]
        for i in range(25, len(closes)):
            a = _max_ret(closes[i - 21:i])   # 현행
            b = _max_ret(closes[i - 22:i])   # Path A
            if a is None or b is None:
                continue
            total += 1
            if abs(a - b) < 1e-9:
                same += 1
                continue
            gap_sum += b - a
            if (a >= floor) != (b >= floor):
                if b >= floor:
                    flip_up += 1
                else:
                    flip_down += 1
    if not total:
        print("[A] 가격 CSV 없음")
        return
    diff = total - same
    print(f"[A] 전 종목 배경 분포 — 종목 {len(files)} · 표본 {total:,}")
    print(f"    MAX 동일 {same:,} ({100 * same / total:.1f}%) | 다름 {diff:,} ({100 * diff / total:.1f}%)")
    if diff:
        print(f"    다를 때 평균 차이 {gap_sum / diff:+.3f}%p (Path A 정의가 항상 >= 현행 — 창이 넓어 상위집합)")
    print(f"    하한 {floor}% 판정 뒤집힘 {flip_up + flip_down:,} ({100 * (flip_up + flip_down) / total:.2f}%)"
          f" | Path A에서만 통과 {flip_up:,} · 현행에서만 통과 {flip_down:,}")


def _scan_candidates(floor: float) -> None:
    """모집단 B — 실제 급락 후보(shadow signals). 판정에 쓰이는 모집단이다."""
    if not SHADOW_DB.exists():
        print("[B] shadow DB 없음")
        return
    con = sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True, timeout=10)
    try:
        pairs = con.execute(
            "SELECT DISTINCT signal_date, ticker, rank FROM signals ORDER BY signal_date, rank"
        ).fetchall()
    finally:
        con.close()
    total = same = missing = 0
    flips: list[tuple] = []
    for signal_date, ticker, rank in pairs:
        rows = _closes(str(ticker))
        window = [c for d, c in rows if d < str(signal_date)]
        a, b = _max_ret(window[-21:]), _max_ret(window[-22:])
        if a is None or b is None:
            missing += 1
            continue
        total += 1
        if abs(a - b) < 1e-9:
            same += 1
        elif (a >= floor) != (b >= floor):
            flips.append((str(signal_date), str(ticker), int(rank or 0), round(a, 2), round(b, 2)))
    if not total:
        print("[B] 후보 표본 없음")
        return
    print(f"[B] 급락 후보(signals) — 표본 {total} (가격 결손 {missing} 제외)")
    print(f"    MAX 동일 {same} ({100 * same / total:.1f}%) | 다름 {total - same}")
    print(f"    하한 {floor}% 판정 뒤집힘 {len(flips)}건 ({100 * len(flips) / total:.1f}%)")
    for row in flips:
        print(f"      {row[0]} {row[1]} rank{row[2]}: 현행 {row[3]} / PathA {row[4]}")
    rank1 = [f for f in flips if f[2] == 1]
    print(f"    그중 rank1(실제 진입 후보): {len(rank1)}건 {rank1 or ''}")


def main() -> int:
    parser = argparse.ArgumentParser(description="MAX 창 정의 2종 영향 실측")
    parser.add_argument("--floor", type=float, default=8.0, help="MAX 하한(%%) — 라이브 기본 8")
    parser.add_argument("--skip-universe", action="store_true", help="배경 분포 스캔 생략(느림)")
    args = parser.parse_args()

    print(f"=== MAX 창 정의 영향 (하한 {args.floor}%) ===")
    print("현행=종가21/수익20 · PathA=종가22/수익21")
    _scan_candidates(args.floor)
    if not args.skip_universe:
        _scan_universe(args.floor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
