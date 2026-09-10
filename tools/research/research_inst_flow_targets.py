# -*- coding: utf-8 -*-
"""기관 수급 축 판정에 필요한 (종목 → 최소 날짜) 목록 산정 (2026-09-10).

창 A (인샘플 잔여): virtual_books `xkr_fallen3` 백필 중 KRX로 이미 받은 92세션을 뺀 213세션.
창 B (OOS): `data/analysis/kr_fallen_price_cache_2025.json`(634종목, 2024-11~2026-01)에서
            2024-12-01~2025-05-31 구간의 급락일(전일 ≤−3% & 거래대금 ≥20억). 인샘플(2025-06~) 이전 창이다.
창 B2 (OOS 확장): 스크래치패드 `kr_oos_px/`(자사주 250종목 yfinance, 2022~2025)의 2023-01~2025-05 급락일.

같은 종목이 여러 창에 걸치면 **가장 이른 날짜 하나**로 합친다 — 네이버는 종목당 한 번만 훑으면 되기 때문이다.
사용: python tools/research/research_inst_flow_targets.py <out_targets_json> <krx_flow_jsonl> [<scratchpad_dir>]
"""
import csv
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = f"file:{ROOT / 'data' / 'shadow' / 'virtual_books.db'}?mode=ro"
CACHE_B = ROOT / "data" / "analysis" / "kr_fallen_price_cache_2025.json"


def window_a(krx_flow: Path) -> dict[str, str]:
    have = set()
    if krx_flow.exists():
        for line in krx_flow.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if not r.get("errors"):
                have.add(r["date"])
    with closing(sqlite3.connect(DB, uri=True, timeout=5)) as con:
        rows = con.execute("select session_date, ticker from trades "
                           "where strategy_id='xkr_fallen3' and backfill=1").fetchall()
    out: dict[str, str] = {}
    for d, tk in rows:
        if d in have:
            continue
        if tk not in out or d < out[tk]:
            out[tk] = d
    return out


def _fallen_days(bars: list[tuple], lo: str, hi: str) -> list[str]:
    """전일 ≤−3% & 거래대금 ≥20억 & 분할 의심(≤−30%) 제외."""
    days = []
    for i in range(1, len(bars)):
        d, o, h, l, c, v = bars[i]
        pc = bars[i - 1][4]
        if not (lo <= d <= hi) or pc <= 0 or c <= 0:
            continue
        chg = (c / pc - 1) * 100
        if chg > -3 or chg <= -30 or c * v < 2e9:
            continue
        days.append(d)
    return days


def window_b(lo: str, hi: str) -> dict[str, str]:
    if not CACHE_B.exists():
        return {}
    raw = json.loads(CACHE_B.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for tk, arr in raw.items():
        bars = [(x["d"], x.get("o") or 0, x.get("h") or 0, x.get("l") or 0, x.get("c") or 0, x.get("v") or 0)
                for x in arr if x.get("c")]
        bars.sort()
        ds = _fallen_days(bars, lo, hi)
        if ds:
            out[tk] = min(ds)
    return out


def window_b2(sp: Path, lo: str, hi: str) -> dict[str, str]:
    d = sp / "kr_oos_px"
    if not d.exists():
        return {}
    out: dict[str, str] = {}
    for p in d.glob("*.csv"):
        bars = []
        for r in csv.DictReader(p.open(encoding="utf-8")):
            try:
                bars.append((r["Date"][:10], float(r["Open"]), float(r["High"]), float(r["Low"]),
                             float(r["Close"]), float(r["Volume"] or 0)))
            except (ValueError, KeyError):
                continue
        bars.sort()
        ds = _fallen_days(bars, lo, hi)
        if ds:
            out[p.stem] = min(ds)
    return out


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    out_path = Path(sys.argv[1])
    krx_flow = Path(sys.argv[2])
    sp = Path(sys.argv[3]) if len(sys.argv) > 3 else None

    a = window_a(krx_flow)
    b = window_b("2024-12-01", "2025-05-31")
    b2 = window_b2(sp, "2023-01-01", "2025-05-31") if sp else {}
    merged: dict[str, str] = {}
    for src in (a, b, b2):
        for tk, d in src.items():
            if tk not in merged or d < merged[tk]:
                merged[tk] = d
    print(f"창 A(인샘플 잔여) 종목 {len(a)}")
    print(f"창 B(634종목 2024-12~2025-05) 종목 {len(b)}")
    print(f"창 B2(자사주 250 2023-01~2025-05) 종목 {len(b2)}")
    print(f"합계 고유 종목 {len(merged)}")
    from collections import Counter
    yr = Counter(d[:4] for d in merged.values())
    print("종목별 필요 최소연도 분포:", dict(sorted(yr.items())))
    est = sum(max(1, int((2026 - int(d[:4])) * 250 * 0.75 / 20) + 1) for d in merged.values())
    print(f"예상 페이지 약 {est:,}회 · 0.45초/회면 {est * 0.45 / 3600:.1f}시간")
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"→ {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
