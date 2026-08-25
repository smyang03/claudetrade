#!/usr/bin/env python3
"""밴드 내 순서(모델 rank)의 가치 평가 — 선정 방식 점검 (2026-08-25 밤, 운영자 지시).

현행 선정 파이프라인: day_losers 풀 → 모델 rank → **밴드(100~500M) 재선택
(밴드 내 순서는 모델 rank가 결정)** → MAX>=8 하한 → 순차 진입.
질문: "밴드 내에서 모델순으로 고르는 것"이 다른 순서보다 나은가?

배경 실측(오늘): 모델확률은 TP12 완주율을 2배 가르고(15.8→31.6%), 확률 3분위
net도 단조(−2.85/+2.41/+3.81). 반면 A7은 "판정 5건 모두 모델 허들이면 차단"
— 허들(절대 문턱)과 랭킹(상대 순서)은 다른 축이다. 허들은 이미 폐지됐고
여기서는 랭킹만 본다. (ATR 랭킹 기각·KR 랭킹 재론 금지와 별개 — 이건 현행
방식 자체의 검증이다.)

방법: 원장 MATURED 표본에서 세션별 "밴드 통과 후보"를 재구성(신호일 거래대금
100~500M, 우리 CSV 기준)하고, 순서 규칙별로 **1순위 픽의 계약 net**을 비교:
  모델순(현행, probability desc) / MAX순(직전 20수익 최대값 desc) /
  거래대금 하위순(작은 것 우선) / 역모델순(probability asc) / 세션 평균(전량).
클러스터 t + 월별 부호. 판정 정의: 현행(모델순)이 역순·무작위 대비 우위가
없으면 "순서는 임의 — 단순화 후보", 역순이 낫면 경고. 관측 전용.
"""
from __future__ import annotations

import csv
import sqlite3
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE_DIR = ROOT / "data" / "price" / "us"
BAND_MIN_M, BAND_MAX_M = 100.0, 500.0


def _price_rows(ticker: str, cache: dict) -> list[tuple[str, float, float]]:
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
        cache[ticker] = rows
    return cache[ticker]


def _features(ticker: str, signal_date: str, cache: dict) -> tuple[float | None, float | None]:
    """(신호일 거래대금 M$, 직전 21종가/20수익 MAX%) — no-lookahead."""
    rows = _price_rows(ticker, cache)
    dollar_m = max_ret = None
    for i, (day, close, vol) in enumerate(rows):
        if day == signal_date:
            dollar_m = close * vol / 1e6
            closes = [r[1] for r in rows[max(0, i - 20): i + 1]]
            if len(closes) >= 21:
                rets = [100 * (closes[j] / closes[j - 1] - 1) for j in range(1, len(closes))]
                max_ret = max(rets)
            break
    return dollar_m, max_ret


def _cluster(rows: list[tuple[str, float]]) -> tuple[float, float | None, int]:
    by: dict[str, list[float]] = {}
    for t, v in rows:
        by.setdefault(t, []).append(v)
    means = [st.mean(v) for v in by.values()]
    k = len(means)
    if k < 3:
        return (st.mean(means) if means else 0.0), None, k
    sd = st.pstdev(means)
    return st.mean(means), ((st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None), k


def main() -> int:
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        signals = [dict(r) for r in con.execute(
            """SELECT signal_date, ticker, probability, net_krw_pct FROM signals
               WHERE status='MATURED' AND net_krw_pct IS NOT NULL AND candidate_source='day_losers'"""
        ).fetchall()]
    finally:
        con.close()
    cache: dict = {}
    by_session: dict[str, list[dict]] = {}
    for sig in signals:
        ticker = str(sig["ticker"]).upper()
        day = str(sig["signal_date"])
        dollar_m, max_ret = _features(ticker, day, cache)
        if dollar_m is None or not (BAND_MIN_M <= dollar_m <= BAND_MAX_M):
            continue
        by_session.setdefault(day, []).append({
            "ticker": ticker, "prob": float(sig["probability"] or 0.0),
            "net": float(sig["net_krw_pct"]), "dollar_m": dollar_m,
            "max_ret": max_ret if max_ret is not None else -999.0,
        })
    sessions = {d: rows for d, rows in by_session.items() if rows}
    multi = {d: rows for d, rows in sessions.items() if len(rows) >= 2}
    print(f"밴드 통과 후보 재구성: 세션 {len(sessions)} (2개 이상 {len(multi)}) · 후보 {sum(len(v) for v in sessions.values())}건")

    orders = {
        "모델순(현행)": lambda rows: max(rows, key=lambda x: x["prob"]),
        "역모델순": lambda rows: min(rows, key=lambda x: x["prob"]),
        "MAX순": lambda rows: max(rows, key=lambda x: x["max_ret"]),
        "거래대금 하위순": lambda rows: min(rows, key=lambda x: x["dollar_m"]),
        "거래대금 상위순": lambda rows: max(rows, key=lambda x: x["dollar_m"]),
    }
    print("\n== 세션 1순위 픽 비교 (밴드 통과 후보가 있는 전 세션) ==")
    for name, pick in orders.items():
        picks = [(pick(rows)["ticker"], pick(rows)["net"]) for rows in sessions.values()]
        mean_c, t_stat, k = _cluster(picks)
        wins = sum(1 for _, v in picks if v > 0)
        t_txt = f"{t_stat:.2f}" if t_stat is not None else "-"
        print(f"  {name:12s} n={len(picks)} k={k} 평균 {mean_c:+6.2f}% 승률 {100*wins/len(picks):3.0f}% t={t_txt}")
    everything = [(r["ticker"], r["net"]) for rows in sessions.values() for r in rows]
    mean_c, t_stat, k = _cluster(everything)
    print(f"  {'전량(무선별)':12s} n={len(everything)} k={k} 평균 {mean_c:+6.2f}% t={t_stat if t_stat is None else round(t_stat,2)}")

    print("\n== 순서가 실제로 갈리는 세션만 (후보 2개 이상) — 변별의 실체 ==")
    for name, pick in orders.items():
        picks = [(pick(rows)["ticker"], pick(rows)["net"]) for rows in multi.values()]
        if not picks:
            continue
        mean_c, t_stat, k = _cluster(picks)
        t_txt = f"{t_stat:.2f}" if t_stat is not None else "-"
        print(f"  {name:12s} n={len(picks)} k={k} 평균 {mean_c:+6.2f}% t={t_txt}")

    print("\n== 월별 (모델순-역모델순 차이 부호) ==")
    for month in sorted({d[:7] for d in multi}):
        rows_m = {d: v for d, v in multi.items() if d.startswith(month)}
        if not rows_m:
            continue
        cur = st.mean([max(v, key=lambda x: x["prob"])["net"] for v in rows_m.values()])
        rev = st.mean([min(v, key=lambda x: x["prob"])["net"] for v in rows_m.values()])
        print(f"  {month}: 모델순 {cur:+6.2f}% vs 역순 {rev:+6.2f}% 차이 {cur-rev:+6.2f}%p (세션 {len(rows_m)})")
    print("\n(관측 전용 — 판정: 모델순이 역순 대비 우위 없으면 '순서 임의', 역순 우위면 경고. 적용 변경은 운영자 승인.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
