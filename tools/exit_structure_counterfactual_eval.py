#!/usr/bin/env python3
"""경로 반응형 출구(본전락·트레일) counterfactual — 8월 실매수 코호트 검증 (2026-08-25 밤).

운영자 요청: 고정 TP 변경(무구분)도 종목별 예측(변수 없음)도 아닌 다른 방법을
찾아 8월 매수 건들로 검증하라. 후보 계열 = **경로에 반응하는 출구**:
  - BE락: 봉우리가 +act% 도달하면 손절선을 본전으로 올림 (승자 무손상,
    FRVO형 반납을 0 근처에서 차단이 목표)
  - 트레일: 봉우리 +act% 도달 후 봉우리-w%를 이탈하면 청산 (반납 일부 회수,
    대가로 완주 일부가 조기 청산될 수 있음)
값의 출처: 봉우리는 봇이 이미 실시간 수집 중(sleeve_mfe_path / observed_peak),
라이브 구현도 같은 인프라 — 운영 가능성이 있는 계열이다.

시뮬 규약(보수적, 일봉): TP12·SL25 유지, 동적 손절선은 **전일까지의 봉우리**로만
갱신(당일 고가→당일 이탈의 순서 모호성 배제), 갭은 시가 체결, 같은 날 충돌 시
손절선 우선(계약 SL-first와 동일), 비용 0.50.
검증 표본: ① 8월 실매수(실진입가·실진입일, 진행 중 건은 현재까지)
          ② 후보 원장 171건(t+1 시가 진입, D5) — 일반화 확인.
과거 기각과의 관계: 트레일 완화 반증(07-28)·capture 기각(07-31)은 재구성 이전
다른 모집단/계약이었다. 이 평가는 sleeve 계약 코호트 한정의 관측이며 적용 제안이
아니다 — 판정 정의는 스크립트 말미 출력 참조.
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

PRICE_DIR = ROOT / "data" / "price" / "us"
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
SL = 0.25
TP = 0.12
COST = 0.50

# 8월 실매수 코호트 (실진입가 = 브로커 평단/정본, 보유창 = 실제 계약 D5/D7)
LIVE_TRADES = [
    # (ticker, entry_session, entry_price, hold_sessions, status)
    ("FRMI", "2026-08-03", 5.52, 5, "TP정산 +12.32"),
    ("CVI",  "2026-08-04", 32.00, 5, "시간정산 +0.59"),
    ("MXL",  "2026-08-11", 70.16, 5, "TP정산 +12.46"),
    ("FA",   "2026-08-12", 21.025, 5, "시간정산 -1.33"),
    ("WIX",  "2026-08-17", 72.97, 5, "TP정산 +10.29"),
    ("FRVO", "2026-08-18", 18.02, 7, "보유중 -13%대"),
    ("AXTI", "2026-08-19", 82.09, 7, "보유중 -15%대"),
    ("MXL",  "2026-08-20", 66.89, 7, "보유중"),
    ("AVAV", "2026-08-21", 160.57, 7, "보유중"),
    ("SEI",  "2026-08-21", 55.12, 7, "보유중"),
]

VARIANTS = [
    ("현행 TP12/SL25", {}),
    ("BE락 발동4%", {"be_act": 0.04}),
    ("BE락 발동5%", {"be_act": 0.05}),
    ("BE락 발동6%", {"be_act": 0.06}),
    ("트레일 발동5% 폭2%", {"tr_act": 0.05, "tr_w": 0.02}),
    ("트레일 발동5% 폭3%", {"tr_act": 0.05, "tr_w": 0.03}),
    ("트레일 발동7% 폭3%", {"tr_act": 0.07, "tr_w": 0.03}),
    ("BE락5%+트레일7%/3%", {"be_act": 0.05, "tr_act": 0.07, "tr_w": 0.03}),
    ("TP감쇠 12→8@D3→6@D4", {"tp_decay": {2: 0.08, 3: 0.06}}),
    ("TP감쇠+BE락4%", {"tp_decay": {2: 0.08, 3: 0.06}, "be_act": 0.04}),
]


def _load_bars(ticker: str, cache: dict) -> pd.DataFrame | None:
    if ticker not in cache:
        path = PRICE_DIR / f"us_{ticker}.csv"
        if not path.exists():
            cache[ticker] = None
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            frame["date"] = frame["date"].astype(str)
            cache[ticker] = frame.reset_index(drop=True)
    return cache[ticker]


def _sim(bars: pd.DataFrame, entry_session: str, entry_price: float, hold: int,
         *, include_entry_day: bool, be_act: float = 0.0, tr_act: float = 0.0,
         tr_w: float = 0.0, tp_decay: dict | None = None) -> tuple[float, str] | None:
    idx = bars.index[bars["date"] == entry_session]
    if not len(idx):
        return None
    start = int(idx[0]) + (0 if include_entry_day else 1)
    path = bars.iloc[start:start + hold]
    if not len(path):
        return None
    entry = entry_price if include_entry_day else float(path.iloc[0]["open"])
    if entry <= 0:
        return None
    tp_px, sl_px = entry * (1 + TP), entry * (1 - SL)
    peak = entry  # 동적 손절선은 전일까지의 봉우리로만 갱신 (보수 규약)
    dyn_stop = sl_px
    exit_px, kind = float(path.iloc[-1]["close"]), "time"
    partial = len(path) < hold  # 진행중 건 — 창이 다 안 참
    for day_i, (_, bar) in enumerate(path.iterrows()):
        if tp_decay:
            # 운영자 아이디어(08-25): 12%로 시작해 보유일이 지나면 눈높이를 낮춘다
            tp_px = entry * (1 + tp_decay.get(day_i, TP if day_i < min(tp_decay) else min(tp_decay.values())))
            for edge in sorted(tp_decay):
                if day_i >= edge:
                    tp_px = entry * (1 + tp_decay[edge])
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if day_i > 0 and o <= dyn_stop:
            exit_px, kind = o, "stop_gap"; partial = False; break
        if day_i > 0 and o >= tp_px:
            exit_px, kind = o, "tp_gap"; partial = False; break
        if l <= dyn_stop:
            exit_px, kind = dyn_stop, ("sl" if dyn_stop <= sl_px + 1e-9 else "stop"); partial = False; break
        if h >= tp_px:
            exit_px, kind = tp_px, "tp"; partial = False; break
        peak = max(peak, h)
        gain = peak / entry - 1
        if be_act and gain >= be_act:
            dyn_stop = max(dyn_stop, entry)
        if tr_act and gain >= tr_act:
            dyn_stop = max(dyn_stop, peak * (1 - tr_w))
    if partial:
        kind = "진행중"
    return 100 * (exit_px / entry - 1) - COST, kind


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
    cache: dict = {}
    print("== ① 8월 실매수 코호트 — 실진입가·실진입일 (진행중 건은 현재까지, 보수 규약) ==")
    header = f"{'종목':6s} {'진입':10s} " + " ".join(f"{name[:10]:>11s}" for name, _ in VARIANTS)
    print(header)
    totals = {name: 0.0 for name, _ in VARIANTS}
    counted = 0
    for ticker, session, price, hold, status in LIVE_TRADES:
        bars = _load_bars(ticker, cache)
        if bars is None:
            print(f"{ticker:6s} CSV 없음")
            continue
        cells = []
        ok = True
        for name, kw in VARIANTS:
            sim = _sim(bars, session, price, hold, include_entry_day=True, **kw)
            if sim is None:
                ok = False
                break
            net, kind = sim
            totals[name] += net
            cells.append(f"{net:+7.2f}{'*' if kind == '진행중' else ' '}{kind[:3]:3s}")
        if not ok:
            print(f"{ticker:6s} 데이터 부족")
            continue
        counted += 1
        print(f"{ticker:6s} {session:10s} " + " ".join(f"{c:>11s}" for c in cells) + f"  | {status}")
    print(f"{'합계':6s} {'('+str(counted)+'건)':10s} " + " ".join(f"{totals[name]:+11.2f}" for name, _ in VARIANTS))
    print("  (*=진행중 — 현재 종가 기준 미확정. 정산 5건만의 비교도 병기하라)")

    print("\n== ② 후보 원장 171건 일반화 (t+1 시가 진입, D5) ==")
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        signals = [dict(r) for r in con.execute(
            "SELECT signal_date, ticker FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL"
        ).fetchall()]
    finally:
        con.close()
    for name, kw in VARIANTS:
        rows: list[tuple[str, float]] = []
        for sig in signals:
            bars = _load_bars(str(sig["ticker"]).upper(), cache)
            if bars is None:
                continue
            sim = _sim(bars, str(sig["signal_date"]), 0.0, 5, include_entry_day=False, **kw)
            if sim is None:
                continue
            rows.append((str(sig["ticker"]).upper(), sim[0]))
        mean_c, t_stat, k = _cluster(rows)
        wins = sum(1 for _, v in rows if v > 0)
        t_txt = f"{t_stat:.2f}" if t_stat is not None else "-"
        print(f"  {name:18s} n={len(rows)} k={k} 평균 {mean_c:+6.2f}% 승률 {100*wins/max(1,len(rows)):3.0f}% t={t_txt} 합계 {sum(v for _, v in rows):+8.1f}%")

    print("\n== 판정 정의(제안 — 적용 아님) ==")
    print("  값의 출처: 봉우리·현재가는 봇이 이미 실시간 수집(sleeve_mfe_path·WS) — 라이브 구현 가능.")
    print("  판정: ①원장 171건에서 현행 대비 평균 개선 + 클러스터 t>=2 ②실체결 코호트 정산분에서 방향 일치")
    print("        ③월별 부호 재현 — 전부 만족 시 사전등록 개정안으로 운영자 승인 요청(계약 지문 변경 수반).")
    print("  하나라도 미달이면 관측 지속. 과거 기각(07-28 트레일)은 다른 모집단 — sleeve 코호트로만 판정한다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
