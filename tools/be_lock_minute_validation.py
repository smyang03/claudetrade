#!/usr/bin/env python3
"""BE락(본전 잠금) 분봉 재검 — Alpaca SIP 1분봉, 인트라데이 순서 실측 (2026-08-25 밤).

일봉 평가(exit_structure_counterfactual_eval)의 한계 = 같은 날 "봉우리→이탈"
순서를 모른다는 것. 운영자가 정확히 짚은 질문도 같은 것이다:
  "+4% 찍고 잠깐 본전 밑으로 갔다 다시 올라오는 건(놓친 완주)이 얼마나 되나?"
분봉으로 그 빈도와 비용을 직접 센다.

규약: 정규장(9:30~16:00 ET) 분봉만(계약이 정규장 기준). 진입 = 신호 다음
세션 첫 분봉 시가(원장 규약과 동일). 분봉 내 충돌은 손절선 우선(보수).
BE락: 진입 후 분봉 고가 누적 peak가 +act% 도달하는 순간부터 손절선=진입가.
표본: 후보 원장 MATURED(우리 풀 그대로). 관측 전용 — 적용은 사전등록+승인 후.

출력: 현행 vs BE락(4%/5%) 성과 + 분류표(살린 반납/놓친 완주/무영향) — 운영자가
"욕심인지 포기인지"를 숫자로 판단할 수 있게.
"""
from __future__ import annotations

import sqlite3
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRICE_DIR = ROOT / "data" / "price" / "us"
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
ENV_FILE = ROOT / ".env.alpaca"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ET = ZoneInfo("America/New_York")
SL = 0.25
TP = 0.12
COST = 0.50
HOLD = 5
BE_ACTS = (0.04, 0.05)


def _alpaca_headers() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def _sessions_after(ticker: str, signal_date: str, cache: dict) -> list[str]:
    if ticker not in cache:
        path = PRICE_DIR / f"us_{ticker}.csv"
        if not path.exists():
            cache[ticker] = None
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
            cache[ticker] = [str(d) for d in frame["date"].astype(str)]
    dates = cache[ticker]
    if not dates or signal_date not in dates:
        return []
    idx = dates.index(signal_date)
    return dates[idx + 1: idx + 1 + HOLD]


def _fetch_minutes(headers: dict, ticker: str, sessions: list[str]) -> list[dict]:
    start = datetime.fromisoformat(f"{sessions[0]}T09:30:00").replace(tzinfo=ET)
    end = datetime.fromisoformat(f"{sessions[-1]}T16:00:00").replace(tzinfo=ET)
    params = {
        "symbols": ticker, "timeframe": "1Min",
        "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "feed": "sip", "limit": "10000", "adjustment": "raw",
    }
    bars: list[dict] = []
    token = ""
    while True:
        if token:
            params["page_token"] = token
        resp = requests.get(BARS_URL, headers=headers, params=params, timeout=30)
        if resp.status_code == 429:
            import time as _t
            _t.sleep(3)
            continue
        resp.raise_for_status()
        payload = resp.json()
        bars.extend((payload.get("bars") or {}).get(ticker) or [])
        token = payload.get("next_page_token") or ""
        if not token:
            break
    session_set = set(sessions)
    out = []
    for bar in bars:
        ts = datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)
        if ts.date().isoformat() in session_set and (9, 30) <= (ts.hour, ts.minute) < (16, 0):
            out.append(bar)
    return out


def _sim_minutes(bars: list[dict], be_act: float) -> tuple[float, str] | None:
    if not bars:
        return None
    entry = float(bars[0]["o"])
    if entry <= 0:
        return None
    tp_px, sl_px = entry * (1 + TP), entry * (1 - SL)
    stop = sl_px
    peak = entry
    for bar in bars:
        o, h, l = float(bar["o"]), float(bar["h"]), float(bar["l"])
        if o <= stop:
            return 100 * (o / entry - 1) - COST, ("sl" if stop <= sl_px + 1e-9 else "be_stop")
        if l <= stop:
            return 100 * (stop / entry - 1) - COST, ("sl" if stop <= sl_px + 1e-9 else "be_stop")
        if h >= tp_px:
            return 100 * (tp_px / entry - 1) - COST, "tp"
        peak = max(peak, h)
        if be_act and peak >= entry * (1 + be_act):
            stop = max(stop, entry)
    return 100 * (float(bars[-1]["c"]) / entry - 1) - COST, "time"


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
            "SELECT signal_date, ticker FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL"
        ).fetchall()]
    finally:
        con.close()
    headers = _alpaca_headers()
    daily_cache: dict = {}
    base_rows: list[tuple[str, float]] = []
    be_rows: dict[float, list[tuple[str, float]]] = {a: [] for a in BE_ACTS}
    saved: dict[float, list[float]] = {a: [] for a in BE_ACTS}      # 살린 반납(현행<BE)
    missed: dict[float, list[float]] = {a: [] for a in BE_ACTS}     # 놓친 완주(현행>BE)
    fetched = skipped = 0
    month_diffs: dict[str, dict[float, list[float]]] = {}
    for sig in signals:
        ticker = str(sig["ticker"]).upper()
        sessions = _sessions_after(ticker, str(sig["signal_date"]), daily_cache)
        if len(sessions) < HOLD:
            skipped += 1
            continue
        try:
            minutes = _fetch_minutes(headers, ticker, sessions)
        except requests.RequestException:
            skipped += 1
            continue
        base = _sim_minutes(minutes, 0.0)
        if base is None:
            skipped += 1
            continue
        fetched += 1
        month = str(sig["signal_date"])[:7]
        base_rows.append((ticker, base[0]))
        month_diffs.setdefault(month, {a: [] for a in BE_ACTS})
        for act in BE_ACTS:
            be = _sim_minutes(minutes, act)
            be_rows[act].append((ticker, be[0]))
            diff = be[0] - base[0]
            month_diffs[month][act].append(diff)
            if diff > 0.01:
                saved[act].append(diff)
            elif diff < -0.01:
                missed[act].append(diff)
    print(f"분봉 재검 표본 {fetched}건 (제외 {skipped}: 데이터 부족)")
    mean_b, t_b, k_b = _cluster(base_rows)
    print(f"\n  현행 TP12/SL25  평균(클러스터) {mean_b:+6.2f}% (k={k_b}, t={t_b if t_b is None else round(t_b,2)}) 합계 {sum(v for _, v in base_rows):+8.1f}%")
    for act in BE_ACTS:
        mean_a, t_a, k_a = _cluster(be_rows[act])
        n_sav, n_mis = len(saved[act]), len(missed[act])
        print(f"  BE락 {int(act*100)}% (분봉)  평균(클러스터) {mean_a:+6.2f}% (t={t_a if t_a is None else round(t_a,2)}) 합계 {sum(v for _, v in be_rows[act]):+8.1f}%")
        print(f"      살린 반납 {n_sav}건 (+{sum(saved[act]):.1f}%p) | 놓친 완주/회복 {n_mis}건 ({sum(missed[act]):.1f}%p) | 무영향 {fetched-n_sav-n_mis}건")
    print("\n  [월별 재현] BE락-현행 차이 평균:")
    for month in sorted(month_diffs):
        parts = " | ".join(
            f"BE{int(a*100)} {st.mean(month_diffs[month][a]):+5.2f}%p(n={len(month_diffs[month][a])})"
            for a in BE_ACTS
        )
        print(f"    {month}: {parts}")
    print("\n(관측 전용. 판정: 분봉 기준 BE락 평균>현행 + 클러스터 t>=2 + 월별 재현 — 사전등록 후 운영자 승인.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
