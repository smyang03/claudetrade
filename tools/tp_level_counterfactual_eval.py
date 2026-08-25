#!/usr/bin/env python3
"""TP 수준(5/7/12/15) 전량 counterfactual 평가 — 운영자 질문(2026-08-25 밤).

질문: "종목별로 TP12가 맞을까? FRVO는 TP5였으면 먹고 끝났다."
평가 대상은 실제 후보 원장(us_swing_shadow.db MATURED, 우리 풀 그대로)이며,
같은 표본에 TP만 바꿔 출구 시뮬을 재실행한다 — 선별 프록시 문제(오늘 OOS
교훈)와 달리 표본을 고정한 출구 비교라 모집단 어긋남이 없다.

시뮬 규약(_contract_labels와 동일): t+1 시가 진입, 동일봉 TP·SL 동시면 SL 우선,
갭은 시가 체결, SL25 고정, 보유 D5(원장 계약), 비용 0.50. TP만 5/7/12/15 변주.
보조: TP12 도달군/봉우리 반납군 분포 + 모델확률 3분위별 TP12 도달률(= "맞출 수
있는 변수가 있나"의 1차 확인).

관측 전용 — 계약 불변. 판정은 30건 시점 + 사전등록 절차로만.
"""
from __future__ import annotations

import sqlite3
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE_DIR = ROOT / "data" / "price" / "us"
TP_LEVELS = (0.05, 0.07, 0.12, 0.15)
SL = 0.25
HOLD = 5
COST = 0.50


def _bars(ticker: str, cache: dict) -> pd.DataFrame | None:
    if ticker not in cache:
        path = PRICE_DIR / f"us_{ticker}.csv"
        if not path.exists():
            cache[ticker] = None
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            frame["date"] = frame["date"].astype(str)
            cache[ticker] = frame.reset_index(drop=True)
    return cache[ticker]


def _sim(bars: pd.DataFrame, signal_date: str, tp: float) -> tuple[float, str, float] | None:
    idx = bars.index[bars["date"] == signal_date]
    if not len(idx):
        return None
    path = bars.iloc[int(idx[0]) + 1:int(idx[0]) + 1 + HOLD]
    if len(path) < HOLD:
        return None
    entry = float(path.iloc[0]["open"])
    if entry <= 0:
        return None
    tp_px, sl_px = entry * (1 + tp), entry * (1 - SL)
    exit_px, kind = float(path.iloc[-1]["close"]), "time"
    for day_i, (_, bar) in enumerate(path.iterrows()):
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if day_i > 0 and o <= sl_px:
            exit_px, kind = o, "sl_gap"; break
        if day_i > 0 and o >= tp_px:
            exit_px, kind = o, "tp_gap"; break
        if l <= sl_px:
            exit_px, kind = sl_px, "sl"; break
        if h >= tp_px:
            exit_px, kind = tp_px, "tp"; break
    peak = 100 * (float(path["high"].max()) / entry - 1)
    return 100 * (exit_px / entry - 1) - COST, kind, peak


def _cluster_mean_t(rows: list[tuple[str, float]]) -> tuple[float, float | None, int]:
    by: dict[str, list[float]] = {}
    for ticker, net in rows:
        by.setdefault(ticker, []).append(net)
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
            """SELECT signal_date, ticker, candidate_source, probability
               FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL"""
        ).fetchall()]
    finally:
        con.close()
    cache: dict = {}
    results: dict[float, list[tuple[str, float]]] = {tp: [] for tp in TP_LEVELS}
    peaks: list[dict] = []
    for sig in signals:
        ticker, day = str(sig["ticker"]).upper(), str(sig["signal_date"])
        bars = _bars(ticker, cache)
        if bars is None:
            continue
        row_peak = None
        for tp in TP_LEVELS:
            sim = _sim(bars, day, tp)
            if sim is None:
                break
            net, kind, peak = sim
            results[tp].append((ticker, net))
            row_peak = peak
        if row_peak is not None:
            peaks.append({"ticker": ticker, "prob": sig["probability"],
                          "source": str(sig["candidate_source"] or ""), "peak": row_peak})
    n = len(results[TP_LEVELS[0]])
    print(f"표본 {n}건 / {len({t for t, _ in results[TP_LEVELS[0]]})}종목 (원장 MATURED, 시뮬 재실행)")
    print("\n== TP 수준별 성과 (같은 표본, SL25/D5/비용0.5 고정) ==")
    for tp in TP_LEVELS:
        mean_c, t_stat, k = _cluster_mean_t(results[tp])
        nets = [x for _, x in results[tp]]
        wins = sum(1 for x in nets if x > 0)
        t_txt = f"{t_stat:.2f}" if t_stat is not None else "-"
        tag = " ← 현행" if abs(tp - 0.12) < 1e-9 else ""
        print(f"  TP{int(tp*100):2d}: 평균(클러스터) {mean_c:+6.2f}% | 승률 {100*wins/len(nets):3.0f}% | t={t_txt} | 합계 {sum(nets):+8.1f}%{tag}")

    print("\n== 봉우리 분포 (D5 창 내 최고가, 진입가 대비) ==")
    buckets = [(0, "0% 미만(무봉우리)"), (5, "0~5%"), (7, "5~7%"), (12, "7~12%"), (999, "12%+(완주권)")]
    prev = -999.0
    for edge, label in buckets:
        cnt = sum(1 for p in peaks if prev <= p["peak"] < edge)
        print(f"  {label:16s} {cnt:4d}건 ({100*cnt/len(peaks):.0f}%)")
        prev = edge

    print("\n== '맞출 수 있나' 1차 확인: 모델확률 3분위별 TP12 도달률 ==")
    with_prob = sorted([p for p in peaks if p["prob"] is not None], key=lambda x: x["prob"])
    third = max(1, len(with_prob) // 3)
    for i, name in ((0, "확률 하위"), (1, "확률 중위"), (2, "확률 상위")):
        grp = with_prob[i * third:(i + 1) * third if i < 2 else len(with_prob)]
        reach = sum(1 for p in grp if p["peak"] >= 12)
        mid = sum(1 for p in grp if 4 <= p["peak"] < 12)
        print(f"  {name}: n={len(grp)} | 12%+ 도달 {100*reach/len(grp):4.1f}% | 4~12% 반납권 {100*mid/len(grp):4.1f}%")
    print("\n(관측 전용 — 계약 불변. 판정은 30건 시점·사전등록 절차로만.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
