#!/usr/bin/env python3
"""KR BE락 counterfactual — 이식 판정용 (2026-08-26 새벽, 운영자 질문).

US에서 승인된 BE락(봉우리 +4% 도달 시 손절선=본전)을 KR fallen에도 적용할지.
KR 원장(kr_fallen_shadow.jsonl SETTLED, 관측 전용 포함)에 같은 보수 규약
(일봉·전일 봉우리 기준·동일봉 손절선 우선·TP12/SL25/D7·비용 0.45)으로 검정.

== 판정 결과 (2026-08-26 실측, n=79/k=67) ==
  현행     평균 +4.08% (t 3.43)
  BE락 4%  평균 +2.80% (t 2.68) — 살림 15건 +90.1%p vs **놓침 18건 −165.0%p**
  BE락 5%  평균 +3.34% (t 3.16) — 놓침이 여전히 우세
→ **KR 미적용 확정.** US와 정반대 — KR 갭 과잉반응형은 본전 부근을 스치고
재상승하는 경로가 많아 본전락이 회복을 끊는다(US 장중투매형과 구조가 다르다는
기존 실측 계열의 재확인). "KR-US 이식 금지" 원칙이 실측으로 다시 옳았다.
재론 조건: KR 분봉 검증에서 이 일봉 결과가 뒤집히는 증거가 나올 때만.
"""
from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
PRICE_DIR = ROOT / "data" / "price" / "kr"
TP, SL, COST, HOLD = 0.12, 0.25, 0.45, 7


def _load(ticker: str, cache: dict) -> pd.DataFrame | None:
    if ticker not in cache:
        path = PRICE_DIR / f"kr_{ticker}.csv"
        if not path.exists():
            cache[ticker] = None
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            frame["date"] = frame["date"].astype(str)
            cache[ticker] = frame.reset_index(drop=True)
    return cache[ticker]


def _sim(bars: pd.DataFrame, day: str, be_act: float) -> tuple[float, str] | None:
    idx = bars.index[bars["date"] == day]
    if not len(idx):
        return None
    path = bars.iloc[int(idx[0]) + 1:int(idx[0]) + 1 + HOLD]
    if len(path) < HOLD:
        return None
    entry = float(path.iloc[0]["open"])
    if entry <= 0:
        return None
    tp_px, sl_px = entry * (1 + TP), entry * (1 - SL)
    stop, peak = sl_px, entry
    exit_px, kind = float(path.iloc[-1]["close"]), "time"
    for i, (_, bar) in enumerate(path.iterrows()):
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if i > 0 and o <= stop:
            exit_px, kind = o, "stop_gap"; break
        if l <= stop:
            exit_px, kind = stop, ("sl" if stop <= sl_px + 1e-9 else "be"); break
        if h >= tp_px:
            exit_px, kind = tp_px, "tp"; break
        peak = max(peak, h)
        if be_act and (peak / entry - 1) * 100 >= be_act:
            stop = max(stop, entry)
    return 100 * (exit_px / entry - 1) - COST, kind


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


def main() -> int:
    rows = [json.loads(line) for line in LEDGER.open(encoding="utf-8")]
    settled = [r for r in rows if r.get("status") == "SETTLED" and r.get("net_pct") is not None]
    print(f"KR 원장 정산 {len(settled)}건 (관측 전용 포함)")
    cache: dict = {}
    for label, act in (("현행", 0.0), ("BE락4%", 4.0), ("BE락5%", 5.0)):
        pairs: list[tuple[str, float]] = []
        saved = missed = 0
        sv = ms = 0.0
        for r in settled:
            ticker = str(r["ticker"])
            bars = _load(ticker, cache)
            if bars is None:
                continue
            sim = _sim(bars, str(r["session_date"]), act)
            if sim is None:
                continue
            pairs.append((ticker, sim[0]))
            if act:
                base = _sim(bars, str(r["session_date"]), 0.0)
                diff = sim[0] - base[0]
                if diff > 0.01:
                    saved += 1; sv += diff
                elif diff < -0.01:
                    missed += 1; ms += diff
        mean_c, t_stat, k = _cluster(pairs)
        extra = f" | 살림 {saved}건 +{sv:.1f}%p / 놓침 {missed}건 {ms:.1f}%p" if act else ""
        print(f"  {label:8s} n={len(pairs)} k={k} 평균 {mean_c:+6.2f}% t={t_stat if t_stat is None else round(t_stat, 2)}{extra}")
    print("\n판정: KR 미적용 확정(놓침>살림, US와 정반대) — 재론은 KR 분봉 반증 나올 때만.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
