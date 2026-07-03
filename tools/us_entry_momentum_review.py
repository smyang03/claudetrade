#!/usr/bin/env python3
"""US 진입 모멘텀 / 확인게이트 소급 리뷰 (read-only, 2026-07-03).

배경(전 DB 교차검증): US 체결 코호트 fwd_1d -0.33% < 후보풀 -0.06% = 체결 역선택.
독립 원장 2개 일치(v2_event_store·v2_canonical_performance: gross -0.79/net -0.89).
구조적 갭: KR엔 확인게이트(FAST_TRIGGER, 낙하방지)가 있고 US엔 없음 —
KR 체결은 풀을 이기고(+0.24 vs -1.27) US는 짐(-0.33 vs -0.06).

질문: US 진입 시점 모멘텀(진입가 vs N분 전)이 실현 net을 가르나? = 확인게이트가
도움될 근거가 있나. 소급 결과(5분/-0.2%): 하락중 진입 n=8 net -1.03 vs 안정 n=27 -0.57
— 최악은 걸러지나 안정 진입도 비용에 짐. **부분 레버, enforce 아닌 측정.**

판정 문턱(고정): 하락중-진입 코호트 net med가 안정 코호트보다 지속 >0.5%p 나쁘고
n>=25면 US 확인게이트 shadow 논의. 격차 <=0 또는 n<15면 각하.
분봉은 기존 수집분(data/price/minute/us)만. DB mode=ro. 매매 무변경.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "v2_event_store.db"
MIN_DIR = ROOT / "data" / "price" / "minute" / "us"


def _bars(ticker: str) -> list[tuple[str, float]]:
    p = MIN_DIR / f"us_{ticker}.csv"
    if not p.exists():
        return []
    out = []
    with open(p, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((str(r["ts"])[:16], float(r["close"])))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def _price_at(bars: list, ts_key: str) -> float | None:
    prior = None
    for ts, c in bars:
        if ts <= ts_key:
            prior = c
        elif prior is not None:
            break
    return prior


def main() -> int:
    ap = argparse.ArgumentParser(description="US 진입 모멘텀/확인게이트 소급 리뷰 (read-only)")
    ap.add_argument("--since", default="2026-06-18")
    ap.add_argument("--window-min", type=int, default=5, help="진입 전 참조 분")
    ap.add_argument("--falling-thr", type=float, default=-0.2, help="하락중 판정 임계(%)")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=15)
    con.execute("PRAGMA busy_timeout=10000")
    rows = con.execute(
        "SELECT ticker, plan_json FROM v2_path_runs WHERE status='CLOSED' AND market='US' "
        "AND session_date>=?",
        (args.since,),
    ).fetchall()
    con.close()

    buckets: dict[str, list[float]] = defaultdict(list)
    samples = []
    nodata = 0
    for tk, pj in rows:
        d = json.loads(pj or "{}")
        ent = d.get("actual_entry_price")
        efa = str(d.get("entry_filled_at") or "")
        net = d.get("pnl_pct_net_est")
        net = net if net is not None else d.get("pnl_pct")
        if not ent or not efa or net is None:
            continue
        bars = _bars(str(tk))
        if not bars:
            nodata += 1
            continue
        try:
            edt = datetime.fromisoformat(efa)
        except ValueError:
            continue
        prior = _price_at(bars, (edt - timedelta(minutes=args.window_min)).strftime("%Y-%m-%dT%H:%M"))
        if prior is None or prior <= 0:
            nodata += 1
            continue
        slope = (float(ent) / prior - 1) * 100
        bucket = "하락중 진입(knife)" if slope < args.falling_thr else (
            "반등중 진입" if slope > 0.2 else "횡보 진입")
        buckets[bucket].append(net)
        samples.append((str(tk), round(slope, 2), round(net, 2), bucket))

    print(f"=== US 진입 모멘텀별 실현 net (since {args.since}, {args.window_min}분 전 기준) ===")
    print(f"표본 {sum(len(v) for v in buckets.values())}건 / 분봉결측 {nodata}")

    def _s(v):
        if not v:
            return "n=0"
        return (f"n={len(v):3d} net med={st.median(v):+.2f} mean={st.mean(v):+.2f} "
                f"음수%={100*sum(1 for x in v if x<0)/len(v):.0f} 합={sum(v):+.1f}")

    for b in ("하락중 진입(knife)", "횡보 진입", "반등중 진입"):
        if buckets.get(b):
            print(f"  {b:20} {_s(buckets[b])}")

    knife = buckets.get("하락중 진입(knife)", [])
    stable = buckets.get("횡보 진입", []) + buckets.get("반등중 진입", [])
    if knife and stable:
        gap = st.median(knife) - st.median(stable)
        print(f"\n  격차(knife − 안정) net med: {gap:+.2f}%p  (n_knife={len(knife)})")
        print("  판정 문턱: 격차 < -0.5%p AND n_knife>=25 → US 확인게이트 shadow 논의 / "
              "격차>=0 또는 n<15 → 각하")
    if args.show:
        print("\n개별 (ticker, slope%, net%, bucket):")
        for s in sorted(samples, key=lambda x: x[1]):
            print(f"  {s[0]:6} slope={s[1]:+6.2f} net={s[2]:+6.2f} {s[3]}")
    print("\n주: 관측 전용. 소급(in-sample 낚시 위험) — forward 세션 누적으로만 판정. "
          "안정 진입도 net 적자면 게이트는 부분 레버(출혈 감소)지 흑자 치료제 아님.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
