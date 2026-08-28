#!/usr/bin/env python3
"""KR 유니버스 인구조사 — 스캐너 그물 밖에 놓친 후보가 있는가 (2026-08-28).

운영자 지시("놓치는 후보 없는지 US·KR 다 분석"). US 인구조사(full_market_net_census)
의 KR 판. KR fallen 스캐너는 캐시 기준 641종목만 훑는데, 우리 가격 CSV 유니버스는
1,301종목이고 KRX 상장은 ~2,900종목이다. 그물 밖에 규칙 프로필 충족 종목이
얼마나 있고, 그것들이 실제로 돈이 됐는지 계약 forward로 검정한다.

프로필(R4 근사, no-lookahead): 갭 <= -4% & 20일 고점 대비 <= -20% & 종가 >= 7,110원
& 직전 20세션 평균 거래대금 >= 10억. 라벨: TP12/SL25/D7, 진입 t+1 시가, 비용 0.45%.

== 판정 (2026-08-28 첫 실측, 2026-06-01~) ==
- 프로필 충족: 스캐너 안 596건 / **밖 433건(42%)** — 그물 밖 공급이 실재한다.
- 계약 forward: 안 585건 +4.41%(클러스터 t 6.69, k=195) vs
  **밖 428건 +6.17%(t 12.87, k=162)** — 밖이 **더 좋다**(+1.76%p).
- 즉 KR은 US와 정반대다(US 그물 밖 -2.81%). **스캐너 유니버스 확대가
  KR 레인의 유효 개선 후보**로 승격.
- ⚠️ 한계: 이 표본은 우리 CSV 1,301종목 안에서만 센 것(KRX 전체 아님).
  스캐너 유니버스 선정 기준(왜 641인가)과 확대 비용(스캔 시간·API 부하)은
  코드 확인 후 별도 판단. 적용은 운영자 승인 + 사냥철 전 리허설 후.
"""
from __future__ import annotations

import csv
import json
import statistics as st
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
PRICE_DIR = ROOT / "data" / "price" / "kr"
TP, SL, COST, HOLD = 0.12, 0.25, 0.45, 7
MIN_CLOSE = 7110
MIN_AMT20 = 1e9
GAP_LE, FROM_HIGH_LE = -4.0, -20.0
START = "2026-06-01"


def _rows(path: Path) -> list[tuple]:
    out = []
    with path.open(encoding="utf-8-sig") as fh:
        for r in csv.reader(fh):
            if len(r) >= 6 and r[0][:2] == "20":
                try:
                    out.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                except ValueError:
                    continue
    return out


def _sim(bars: list[tuple], i: int) -> float | None:
    path = bars[i + 1:i + 1 + HOLD]
    if len(path) < HOLD:
        return None
    entry = path[0][1]
    if entry <= 0:
        return None
    tp, sl = entry * (1 + TP), entry * (1 - SL)
    exit_px = path[-1][4]
    for d in range(len(path)):
        o, h, l, c = path[d][1], path[d][2], path[d][3], path[d][4]
        if d > 0 and o <= sl:
            exit_px = o; break
        if d > 0 and o >= tp:
            exit_px = o; break
        if l <= sl:
            exit_px = sl; break
        if h >= tp:
            exit_px = tp; break
    return 100 * (exit_px / entry - 1) - COST


def _cluster(pairs: list[tuple[str, float]]) -> tuple[float, float | None, int]:
    by: dict[str, list[float]] = {}
    for t, v in pairs:
        by.setdefault(t, []).append(v)
    means = [st.mean(x) for x in by.values()]
    k = len(means)
    sd = st.pstdev(means) if k > 1 else 0.0
    return st.mean(means), ((st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None), k


def main() -> int:
    raw = json.loads(CACHE.read_text(encoding="utf-8-sig"))
    inner = raw.get("tickers") or raw.get("data") or raw
    scanner = {k for k in inner if not str(k).startswith("_")} if isinstance(inner, dict) else set()
    hits: dict[str, list[tuple[str, str]]] = {"안": [], "밖": []}
    nets: dict[str, list[tuple[str, float]]] = {"안": [], "밖": []}
    for path in PRICE_DIR.glob("kr_*.csv"):
        ticker = path.stem.replace("kr_", "", 1)
        bars = _rows(path)
        if len(bars) < 25:
            continue
        side = "안" if ticker in scanner else "밖"
        for i in range(21, len(bars)):
            day, o, h, l, c, v = bars[i]
            if day < START:
                continue
            prev_close = bars[i - 1][4]
            if prev_close <= 0 or c < MIN_CLOSE:
                continue
            gap = 100 * (o / prev_close - 1)
            from_high = 100 * (c / max(x[4] for x in bars[i - 19:i + 1]) - 1)
            amt20 = st.mean(x[4] * x[5] for x in bars[i - 20:i])
            if gap <= GAP_LE and from_high <= FROM_HIGH_LE and amt20 >= MIN_AMT20:
                hits[side].append((day, ticker))
                net = _sim(bars, i)
                if net is not None:
                    nets[side].append((ticker, net))
    print(f"스캐너 유니버스 {len(scanner)}종목 | CSV 유니버스 {len(list(PRICE_DIR.glob('kr_*.csv')))}종목")
    print(f"프로필 충족({START}~): 안 {len(hits['안'])}건 / 밖 {len(hits['밖'])}건")
    for side in ("안", "밖"):
        counter = Counter(d[:7] for d, _ in hits[side])
        print(f"  {side}: " + " ".join(f"{m} {counter[m]}" for m in sorted(counter)))
    for side in ("안", "밖"):
        if not nets[side]:
            continue
        mean, t_stat, k = _cluster(nets[side])
        wins = sum(1 for _, v in nets[side] if v > 0)
        print(f"[forward] 스캐너 {side}: 정산 {len(nets[side])}건 k={k} 평균 {mean:+.2f}% "
              f"승률 {100*wins/len(nets[side]):.0f}% t={t_stat if t_stat is None else round(t_stat,2)}")
    print("\n(판정: 밖이 안보다 좋으면 유니버스 확대가 개선 후보 — 적용은 운영자 승인·리허설 후.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
