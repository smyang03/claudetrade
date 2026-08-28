#!/usr/bin/env python3
"""무봉우리형(최대 손실원) 예고 변수 탐색 (2026-08-28 사전등록).

배경: 출구 규칙 분해(08-27)에서 최대 손실원이 확인됐다 — 진입 후 한 번도
+4%를 못 가보는 "무봉우리형"이 표본의 36%인데 전체 손실 기여 -376%p를 차지하고,
**어떤 출구 규칙(TP·트레일·BE락)도 이 구간은 손대지 못한다**(봉우리가 없으므로).
AXTI(-19.97%, MFE 0.0)가 전형. 따라서 손실 축소의 남은 길은 (a) 진입 시점
예고 후 배제 (b) 초기 무반등 시 조기 손절 둘뿐이고, 이 스크립트는 (a)를 검정한다.

== 사전등록 (결과 확인 전 고정) ==
- 라벨: 진입 후 D5 창의 MFE < 4%(무봉우리) 여부. 표본: signals MATURED.
- 후보 변수(진입 시점 가용만): 신호일 등락·종가위치·갭·장중흐름·거래량비·
  MAX20·거래대금·20일고점대비 할인깊이·진입갭·모델확률.
- 1차: 표준화 효과크기 |d|>=0.3만 정밀검정 통과.
- 2차(정밀): 세션 내 중앙값 분할, 종목 클러스터 t, 월별 부호, 밴드 부분집합.
- 판정 기준(고정): ①방향 일관 + 클러스터 t>=2 ②월별 부호 일치 ③밴드 내 유지
  → 전부 만족 시 배제 필터 승인 요청. 미달이면 보류.

== 판정 (2026-08-28 실측, 표본 199건/무봉우리 72건 36%) ==
1차 통과: 20일고점대비 할인깊이(d=+0.42)·모델확률(d=-0.35). 나머지 8종 무변별
(신호일 등락·종가위치·갭·장중·거래량비·MAX20·거래대금·진입갭 — 08-20 전면
스윕의 기각 결과와 정합).
- **할인깊이: 기각.** 무봉우리 예고에서는 차이가 있으나 net으로는 무변별
  (day_losers -0.12%p, 밴드 내 -0.15%p, 월별 혼재). 깊은 할인이 무봉우리를
  덜 만드는 것도 아니었다(38% vs 36%).
- **모델확률: 부분 생존 — 무봉우리율을 일관되게 가른다.**
  day_losers 상위 29% vs 하위 44%(-15%p), 08월 32% vs 48%, 07월 0% vs 12%.
  net 차이도 +0.82%p(08월 +1.07%p)로 방향 일치. **단 밴드 내에서는 net이 역전
  (-1.00%p)** — 밴드가 이미 같은 정보를 먹었을 가능성(08-25 "밴드 내 순서로서의
  모델은 우위 측정 안 됨"과 정합). 클러스터 t는 전 구간 2 미만.
→ **사전등록 3기준 미달로 배제 필터 승인 요청하지 않는다.** 대신 "모델확률이
   무봉우리율을 가른다"를 관측 가설로 등록하고 30건 시점에 재검(밴드 내 역전이
   표본 부족인지 실체인지가 관건). 무봉우리 공략의 남은 경로는 (b) 초기 무반등
   조기 손절 — 별도 사전등록으로 검정할 것.
"""
from __future__ import annotations

import csv
import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE_DIR = ROOT / "data" / "price" / "us"
NO_BUMP_PCT = 4.0
BAND = (100.0, 500.0)


def _bars(ticker: str, cache: dict) -> list[tuple]:
    if ticker not in cache:
        path = PRICE_DIR / f"us_{ticker}.csv"
        rows = []
        if path.exists():
            with path.open(encoding="utf-8-sig") as fh:
                for r in csv.reader(fh):
                    if len(r) >= 6 and r[0][:2] == "20":
                        try:
                            rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                        except ValueError:
                            continue
        cache[ticker] = rows
    return cache[ticker]


def _cluster(pairs: list[tuple[str, float]]) -> tuple[float, float | None, int]:
    by: dict[str, list[float]] = {}
    for t, v in pairs:
        by.setdefault(t, []).append(v)
    means = [st.mean(x) for x in by.values()]
    k = len(means)
    if k < 3:
        return (st.mean(means) if means else 0.0), None, k
    sd = st.pstdev(means)
    return st.mean(means), ((st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None), k


def _split(label: str, rows: list[dict], key: str, desc: str) -> None:
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["day"], []).append(r)
    hi, lo = [], []
    for group in by_session.values():
        if len(group) < 2:
            continue
        med = st.median(x[key] for x in group)
        for x in group:
            (hi if x[key] > med else lo).append(x)
    if not hi or not lo:
        print(f"  [{label}] 분할 불가")
        return
    mh, th, kh = _cluster([(x["t"], x["net"]) for x in hi])
    ml, tl, kl = _cluster([(x["t"], x["net"]) for x in lo])
    nb_h = 100 * sum(1 for x in hi if x["mfe"] < NO_BUMP_PCT) / len(hi)
    nb_l = 100 * sum(1 for x in lo if x["mfe"] < NO_BUMP_PCT) / len(lo)
    print(f"  [{label}] {desc}높음 n={len(hi)} k={kh} net {mh:+.2f}%(t={th if th is None else round(th,2)}) 무봉우리 {nb_h:.0f}% | "
          f"낮음 n={len(lo)} k={kl} net {ml:+.2f}%(t={tl if tl is None else round(tl,2)}) 무봉우리 {nb_l:.0f}% | 차이 {mh-ml:+.2f}%p")


def main() -> int:
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    signals = [dict(r) for r in con.execute(
        "SELECT signal_date,ticker,probability,candidate_source,net_krw_pct FROM signals "
        "WHERE status='MATURED' AND net_krw_pct IS NOT NULL")]
    con.close()
    cache: dict = {}
    rows = []
    for s in signals:
        ticker = str(s["ticker"]).upper()
        day = str(s["signal_date"])
        bars = _bars(ticker, cache)
        i = next((j for j, r in enumerate(bars) if r[0] == day), None)
        if i is None or i < 21 or i + 5 >= len(bars):
            continue
        o, h, l, c, v = bars[i][1], bars[i][2], bars[i][3], bars[i][4], bars[i][5]
        path = bars[i + 1:i + 6]
        entry = path[0][1]
        if entry <= 0:
            continue
        adv20 = st.mean(x[5] for x in bars[i - 20:i])
        rng = h - l
        rows.append({
            "t": ticker, "day": day, "net": float(s["net_krw_pct"]),
            "mfe": 100 * (max(x[2] for x in path) / entry - 1),
            "chg": 100 * (c / bars[i - 1][4] - 1),
            "close_pos": (c - l) / rng if rng > 0 else 0.5,
            "gap": 100 * (o / bars[i - 1][4] - 1),
            "intraday": 100 * (c / o - 1) if o > 0 else 0.0,
            "volr": v / adv20 if adv20 > 0 else 0.0,
            "max20": max(100 * (bars[j][4] / bars[j - 1][4] - 1) for j in range(i - 19, i + 1)),
            "dm": c * v / 1e6,
            "deep": -100 * (c / max(x[4] for x in bars[i - 19:i + 1]) - 1),
            "entry_gap": 100 * (entry / c - 1),
            "prob": s["probability"] or 0.0,
            "src": str(s["candidate_source"] or ""),
            "band": BAND[0] <= c * v / 1e6 <= BAND[1],
        })
    n = len(rows)
    nb = sum(1 for r in rows if r["mfe"] < NO_BUMP_PCT)
    print(f"표본 {n}건 | 무봉우리(MFE<{NO_BUMP_PCT}%) {nb}건 ({100*nb/n:.0f}%)")
    print(f"\n[1차 효과크기] |d|>=0.3만 정밀검정 진출")
    for key, label in (("chg", "신호일 등락"), ("close_pos", "종가위치"), ("gap", "진입일 갭"),
                       ("intraday", "신호일 장중"), ("volr", "거래량비"), ("max20", "MAX20"),
                       ("dm", "거래대금M"), ("deep", "할인깊이"), ("entry_gap", "진입갭"), ("prob", "모델확률")):
        a = [r[key] for r in rows if r["mfe"] < NO_BUMP_PCT]
        b = [r[key] for r in rows if r["mfe"] >= NO_BUMP_PCT]
        if not a or not b:
            continue
        sd = st.pstdev(a + b) or 1.0
        d = (st.mean(a) - st.mean(b)) / sd
        mark = "★" if abs(d) >= 0.3 else ("·" if abs(d) >= 0.15 else " ")
        print(f"  {mark} {label:12s} 무봉우리 {st.mean(a):8.2f} vs 봉우리 {st.mean(b):8.2f}  d={d:+.2f}")
    losers = [r for r in rows if r["src"] == "day_losers"]
    print("\n[정밀] 할인깊이")
    _split("day_losers", losers, "deep", "할인깊이")
    _split("밴드 내", [r for r in losers if r["band"]], "deep", "할인깊이")
    print("\n[정밀] 모델확률")
    _split("day_losers", losers, "prob", "모델확률")
    for month in ("2026-07", "2026-08"):
        _split(month, [r for r in losers if r["day"].startswith(month)], "prob", "모델확률")
    _split("밴드 내", [r for r in losers if r["band"]], "prob", "모델확률")
    print("\n(판정: 사전등록 3기준 미달 → 배제 필터 미승인. 모델확률-무봉우리율은 관측 가설로 30건 재검.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
