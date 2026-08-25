#!/usr/bin/env python3
"""문헌 축 2종 사전 검증 — 섹터 단독성·비정상 거래량 (2026-08-26 새벽 사전등록).

출처: 밤샘 문헌 리서치(Hameed&Mian 2015·Da-Liu-Schaumburg — 반전은 업종 내
상대 하락분에서 발생 / Avramov-Chordia-Goyal 2006 — 고거래량 하락=비정보성
충격일 확률↑). 내부 XLK 관측(08-16)과 방향 정합. 둘 다 지금 CSV·sector_map으로
계산 가능해 우선 검증 대상 1·2위.

== 사전등록 (결과 확인 전 고정) ==
- 표본: us_swing_shadow signals MATURED(net 있음). 주 검정 day_losers.
- 축1 섹터 단독성: rel_drop = 신호일 종목 수익률 − 같은 섹터 유니버스 평균
  수익률(우리 CSV 1,600여 종목, 신호일 기준). 문헌 예측: **rel_drop이 더 음수
  (섹터 대비 단독 급락)일수록 반등이 강하다.**
- 축2 비정상 거래량: vol_ratio = 신호일 거래량 / 직전 20세션 평균 거래량
  (no-lookahead). 문헌 예측: **높을수록(비정보성 충격) 반등이 강하다.**
  기저 회전율(Medhat-Schmeling)과 혼동 금지 — 이건 당일 충격 비율.
- 방법: 프리마켓 검정과 동일 규약 — 세션 내 중앙값 분할, 종목 클러스터 t,
  월별 부호, 밴드(100~500M) 부분집합, 모델확률 상관 보고.
- 판정 기준(고정): ①문헌 예측 방향과 부호 일치 + 합동 클러스터 t>=2
  ②월별 부호 일치 ③밴드 내 방향 유지 → 전부 만족 시 shadow 관측 배선 승인
  요청. 미달이면 보류(표본 축적). 문턱 사후 조정 금지.

== 판정 (2026-08-26 새벽 실측) ==
- 축1 섹터 단독성: **보류** — 전체 차이 −0.56%p(문헌 예측과 반대 방향), 월별
  부호 불일치(07월 −12.9 vs 08월 +2.2), 밴드 내 n=4. 섹터 맵 커버리지가 원장의
  절반(결측 90/126)이라 검정력 자체가 부족 — 재검 선행 조건은 sector_map 확장.
- 축2 비정상 거래량: **보류/기각 방향** — 전체 −0.10%p 무변별, 월별 부호 불일치.
  문헌(ACG 2006)의 경고("계약비용보다 이익이 작다")와 오히려 정합.
문헌 일반론이 우리 코호트에서 확인되지 않는 세 번째 사례(FINRA·프리마켓에 이어).
"""
from __future__ import annotations

import csv
import json
import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE_DIR = ROOT / "data" / "price" / "us"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
BAND = (100.0, 500.0)


def _load_csv(ticker: str, cache: dict) -> list[tuple[str, float, float]] | None:
    if ticker not in cache:
        path = PRICE_DIR / f"us_{ticker}.csv"
        rows: list[tuple[str, float, float]] = []
        if path.exists():
            with path.open(encoding="utf-8-sig") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 6 and row[0][:2] == "20":
                        try:
                            rows.append((row[0], float(row[4]), float(row[5])))
                        except ValueError:
                            continue
        cache[ticker] = rows or None
    return cache[ticker]


def _sector_day_returns(dates: set[str], sector_of: dict) -> dict[tuple[str, str], float]:
    """신호일별 섹터 평균 수익률 — 우리 CSV 유니버스 기준."""
    acc: dict[tuple[str, str], list[float]] = {}
    for path in PRICE_DIR.glob("us_*.csv"):
        ticker = path.stem.replace("us_", "", 1)
        sector = sector_of.get(ticker)
        if not sector:
            continue
        prev_close = None
        try:
            with path.open(encoding="utf-8-sig") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 6 and row[0][:2] == "20":
                        try:
                            close = float(row[4])
                        except ValueError:
                            continue
                        if row[0] in dates and prev_close and prev_close > 0:
                            acc.setdefault((row[0], sector), []).append(100 * (close / prev_close - 1))
                        prev_close = close
        except OSError:
            continue
    return {k: st.mean(v) for k, v in acc.items() if len(v) >= 5}


def _cluster(pairs: list[tuple[str, float]]) -> tuple[float, float | None, int]:
    by: dict[str, list[float]] = {}
    for t, v in pairs:
        by.setdefault(t, []).append(v)
    means = [st.mean(v) for v in by.values()]
    k = len(means)
    if k < 3:
        return (st.mean(means) if means else 0.0), None, k
    sd = st.pstdev(means)
    return st.mean(means), ((st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None), k


def _split(label: str, rows: list[dict], key: str, high_is: str) -> None:
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["day"], []).append(r)
    high, low = [], []
    for sess in by_session.values():
        if len(sess) < 2:
            continue
        med = st.median(x[key] for x in sess)
        for x in sess:
            (high if x[key] > med else low).append(x)
    if not high or not low:
        print(f"  [{label}] 분할 불가")
        return
    mh, th, kh = _cluster([(x["ticker"], x["net"]) for x in high])
    ml, tl, kl = _cluster([(x["ticker"], x["net"]) for x in low])
    print(f"  [{label}] {high_is}높음 n={len(high)} k={kh} 평균 {mh:+.2f}%(t={th if th is None else round(th,2)}) | "
          f"낮음 n={len(low)} k={kl} 평균 {ml:+.2f}%(t={tl if tl is None else round(tl,2)}) | 차이 {mh-ml:+.2f}%p")


def main() -> int:
    sector_raw = json.loads(SECTOR_MAP.read_text(encoding="utf-8-sig"))
    # 구조: {"US": {ticker: {"sector": ...}}, "KR": {...}, "_meta": ...}
    sector_of = {
        str(t).upper(): str((info or {}).get("sector") or "")
        for t, info in (sector_raw.get("US") or {}).items()
        if (info or {}).get("sector")
    }
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    signals = [dict(r) for r in con.execute(
        "SELECT signal_date, ticker, candidate_source, probability, net_krw_pct FROM signals "
        "WHERE status='MATURED' AND net_krw_pct IS NOT NULL")]
    con.close()
    dates = {str(s["signal_date"]) for s in signals}
    sector_ret = _sector_day_returns(dates, sector_of)
    cache: dict = {}
    rows = []
    miss_sector = 0
    for s in signals:
        t = str(s["ticker"]).upper()
        day = str(s["signal_date"])
        bars = _load_csv(t, cache)
        if not bars:
            continue
        idx = next((i for i, (d, _, _) in enumerate(bars) if d == day), None)
        if idx is None or idx < 21:
            continue
        chg = 100 * (bars[idx][1] / bars[idx - 1][1] - 1) if bars[idx - 1][1] > 0 else None
        adv20 = st.mean(v for _, _, v in bars[idx - 20:idx])
        vol_ratio = bars[idx][2] / adv20 if adv20 > 0 else None
        dollar_m = bars[idx][1] * bars[idx][2] / 1e6
        sector = sector_of.get(t)
        s_ret = sector_ret.get((day, sector)) if sector else None
        if s_ret is None:
            miss_sector += 1
        if chg is None or vol_ratio is None:
            continue
        rows.append({
            "ticker": t, "day": day, "net": float(s["net_krw_pct"]),
            "source": str(s["candidate_source"] or ""), "prob": s["probability"],
            "vol_ratio": vol_ratio, "in_band": BAND[0] <= dollar_m <= BAND[1],
            "rel_drop": (chg - s_ret) if s_ret is not None else None,
        })
    losers = [r for r in rows if r["source"] == "day_losers"]
    print(f"표본 {len(rows)}행 (day_losers {len(losers)}) · 섹터 결측 {miss_sector}")

    print("\n== 축1 섹터 단독성 (rel_drop 낮을수록=단독 급락. 문헌 예측: 단독일수록 반등↑) ==")
    with_sector = [r for r in losers if r["rel_drop"] is not None]
    # rel_drop이 낮은 쪽이 '단독성 높음' — 부호 해석 주의를 위해 -rel_drop으로 분할
    for r in with_sector:
        r["solo"] = -r["rel_drop"]
    _split("전체", with_sector, "solo", "단독성")
    for month in ("2026-07", "2026-08"):
        _split(f"{month}", [r for r in with_sector if r["day"].startswith(month)], "solo", "단독성")
    _split("밴드 내", [r for r in with_sector if r["in_band"]], "solo", "단독성")

    print("\n== 축2 비정상 거래량 (문헌 예측: 높을수록 반등↑) ==")
    _split("전체", losers, "vol_ratio", "거래량비")
    for month in ("2026-07", "2026-08"):
        _split(f"{month}", [r for r in losers if r["day"].startswith(month)], "vol_ratio", "거래량비")
    _split("밴드 내", [r for r in losers if r["in_band"]], "vol_ratio", "거래량비")

    with_prob = [r for r in losers if r["prob"] is not None]
    if len(with_prob) >= 10:
        for key, name in (("vol_ratio", "거래량비"), ):
            ranks_p = {id(r): i for i, r in enumerate(sorted(with_prob, key=lambda x: x["prob"]))}
            ranks_v = {id(r): i for i, r in enumerate(sorted(with_prob, key=lambda x: x[key]))}
            n = len(with_prob)
            d2 = sum((ranks_p[id(r)] - ranks_v[id(r)]) ** 2 for r in with_prob)
            print(f"\n  spearman({name}, 모델확률) = {1 - 6 * d2 / (n * (n * n - 1)):+.3f}")
    print("\n(판정: 사전등록 기준 ①방향+t>=2 ②월별 ③밴드 내 — 미달이면 보류. 적용은 운영자 승인.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
