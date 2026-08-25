#!/usr/bin/env python3
"""프리마켓 유동성 축 사전 검증 — Alpaca 백필 기반 (2026-08-25 사전등록).

배선(라이브 관측)보다 검증이 먼저다(CLAUDE.md 외부 보강 원칙). 이 스크립트는
과거 신호일들의 프리마켓 거래량을 Alpaca SIP 분봉으로 백필해, 축의 변별력을
후보 원장(us_swing_shadow.db signals, MATURED)에서 검정한다.

== 사전등록 (결과 확인 전 고정 — 2026-08-25 오후) ==
- 가설: 신호일 프리마켓(4:00~9:30 ET) 상대 유동성(프리마켓 거래량 / 직전 20세션
  평균 일거래량, ADV20은 신호일 이전 데이터만)이 계약 net(TP12/SL25/D5 시뮬)을
  가른다. 방향은 고정하지 않는다(관심 집중=완주 vs 투매 지속=악화 양쪽 가능) —
  판정은 부호 일관성과 크기로 한다.
- 표본: signals MATURED 행. 주 검정 = day_losers(계약 소스), 보조 = 전체.
- 분할: 같은 세션 내 상대화 — 세션 중앙값 초과=HIGH, 이하=LOW (같은 세션 내 비교,
  FINRA 교훈 ⑤).
- 통제: 모델 probability와의 상관을 보고하고, 확률 3분위 내부에서 분할을 반복
  (공선이면 소멸하는지 — FINRA 교훈 ④).
- 클러스터: 종목 클러스터 t (kr_rule_discrimination_backtest와 같은 규약).
  그룹별 종목 수 k<15면 판정 유보(08-21 리서치 규율).
- no-lookahead: 프리마켓은 진입 창(개장+5~30분) 전에 완결. ADV20은 signal_date
  미만 행만 사용. 결측(CSV 없음/20행 미만)은 제외하고 건수를 보고한다.
- 판정 기준(고정): ①HIGH−LOW net 차이의 부호가 7월/8월 두 구간에서 일치
  ②각 그룹 종목 k>=15 ③차이 방향이 거래대금 밴드(100~500M) 부분집합에서 유지
  ④확률 3분위 통제 후에도 같은 부호 → 전부 만족 시 "생존: shadow 관측 배선
  승인 요청". 하나라도 깨지면 보류(표본 축적) 또는 기각. 문턱을 결과 보고 조정
  하지 않는다.

백필 캐시: data/analysis/premarket_backfill_us.jsonl (멱등 — 있으면 스킵).
키: 저장소 루트 .env.alpaca. read-only(live DB·주문 접근 없음).
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics as st
import sys
from zoneinfo import ZoneInfo

import requests

ROOT = Path(__file__).resolve().parent.parent
SIGNALS_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
CACHE_PATH = ROOT / "data" / "analysis" / "premarket_backfill_us.jsonl"
PRICE_DIR = ROOT / "data" / "price" / "us"
ENV_FILE = ROOT / ".env.alpaca"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
ET = ZoneInfo("America/New_York")
BAND_MIN_M, BAND_MAX_M = 100.0, 500.0
MIN_CLUSTER_K = 15


def _alpaca_headers() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def _load_signals() -> list[dict]:
    con = sqlite3.connect(f"file:{SIGNALS_DB}?mode=ro", uri=True, timeout=10)
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """SELECT signal_date, ticker, candidate_source, probability, net_krw_pct
               FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL"""
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def _load_cache() -> dict[tuple[str, str], dict]:
    cache: dict[tuple[str, str], dict] = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                cache[(row["signal_date"], row["ticker"])] = row
    return cache


def _backfill(signals: list[dict]) -> dict[tuple[str, str], dict]:
    cache = _load_cache()
    by_date: dict[str, list[str]] = {}
    for row in signals:
        key = (row["signal_date"], row["ticker"])
        if key not in cache:
            by_date.setdefault(row["signal_date"], []).append(row["ticker"])
    if not by_date:
        return cache
    headers = _alpaca_headers()
    appended = 0
    with CACHE_PATH.open("a", encoding="utf-8") as out:
        for session, tickers in sorted(by_date.items()):
            start = datetime.fromisoformat(f"{session}T04:00:00").replace(tzinfo=ET)
            end = datetime.fromisoformat(f"{session}T09:30:00").replace(tzinfo=ET)
            params = {
                "symbols": ",".join(sorted(set(tickers))), "timeframe": "1Min",
                "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "feed": "sip", "limit": "10000", "adjustment": "raw",
            }
            bars_by_symbol: dict[str, list] = {}
            token = ""
            while True:
                if token:
                    params["page_token"] = token
                resp = requests.get(BARS_URL, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
                payload = resp.json()
                for sym, bars in (payload.get("bars") or {}).items():
                    bars_by_symbol.setdefault(sym, []).extend(bars)
                token = payload.get("next_page_token") or ""
                if not token:
                    break
            for ticker in sorted(set(tickers)):
                bars = bars_by_symbol.get(ticker) or []
                row = {
                    "signal_date": session, "ticker": ticker,
                    "pm_volume": sum(int(b.get("v") or 0) for b in bars),
                    "pm_bars": len(bars), "feed": "sip",
                    "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                cache[(session, ticker)] = row
                appended += 1
    print(f"[백필] 신규 {appended}건 (캐시 총 {len(cache)}건)")
    return cache


def _price_rows(ticker: str) -> list[tuple[str, float, float]]:
    path = PRICE_DIR / f"us_{ticker}.csv"
    if not path.exists():
        return []
    rows: list[tuple[str, float, float]] = []
    with path.open(encoding="utf-8-sig") as fh:
        for row in csv.reader(fh):
            if len(row) >= 6 and row[0][:2] == "20":
                try:
                    rows.append((row[0], float(row[4]), float(row[5])))
                except ValueError:
                    continue
    return rows


def _adv20_and_dollar(ticker: str, signal_date: str) -> tuple[float | None, float | None]:
    """(신호일 이전 20세션 평균 거래량, 신호일 거래대금 M$) — no-lookahead."""
    rows = _price_rows(ticker)
    prior = [r for r in rows if r[0] < signal_date]
    adv20 = st.mean([r[2] for r in prior[-20:]]) if len(prior) >= 20 else None
    same_day = next((r for r in rows if r[0] == signal_date), None)
    dollar_m = (same_day[1] * same_day[2] / 1e6) if same_day else None
    return adv20, dollar_m


def _cluster_stats(rows: list[dict]) -> tuple[float | None, int, float]:
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r["net"])
    means = [st.mean(v) for v in by.values()]
    k = len(means)
    if k < 3:
        return None, k, (st.mean(means) if means else 0.0)
    sd = st.pstdev(means)
    t = (st.mean(means) / (sd / k ** 0.5)) if sd > 0 else None
    return t, k, st.mean(means)


def _split_report(label: str, rows: list[dict]) -> tuple[float, int, int] | None:
    """세션 내 중앙값 분할 HIGH/LOW — (차이, k_high, k_low) 반환."""
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(r["signal_date"], []).append(r)
    high, low = [], []
    for session_rows in by_session.values():
        if len(session_rows) < 2:
            continue
        med = st.median(r["rel_pm"] for r in session_rows)
        for r in session_rows:
            (high if r["rel_pm"] > med else low).append(r)
    if not high or not low:
        print(f"  [{label}] 분할 불가 (표본 부족)")
        return None
    t_h, k_h, m_h = _cluster_stats(high)
    t_l, k_l, m_l = _cluster_stats(low)
    diff = m_h - m_l
    print(f"  [{label}] HIGH n={len(high)} k={k_h} 평균 {m_h:+.2f}% (t={t_h if t_h is None else round(t_h,2)}) | "
          f"LOW n={len(low)} k={k_l} 평균 {m_l:+.2f}% (t={t_l if t_l is None else round(t_l,2)}) | 차이 {diff:+.2f}%p")
    return diff, k_h, k_l


def main() -> int:
    parser = argparse.ArgumentParser(description="프리마켓 유동성 축 사전 검증")
    parser.add_argument("--skip-backfill", action="store_true")
    args = parser.parse_args()

    signals = _load_signals()
    print(f"원장 MATURED {len(signals)}행 (세션 {len({s['signal_date'] for s in signals})} · 종목 {len({s['ticker'] for s in signals})})")
    cache = _load_cache() if args.skip_backfill else _backfill(signals)

    rows, miss_pm, miss_adv = [], 0, 0
    for sig in signals:
        pm = cache.get((sig["signal_date"], sig["ticker"]))
        if pm is None:
            miss_pm += 1
            continue
        adv20, dollar_m = _adv20_and_dollar(sig["ticker"], sig["signal_date"])
        if not adv20:
            miss_adv += 1
            continue
        rows.append({
            "signal_date": sig["signal_date"], "ticker": sig["ticker"],
            "source": str(sig["candidate_source"] or ""), "prob": sig["probability"],
            "net": float(sig["net_krw_pct"]), "rel_pm": pm["pm_volume"] / adv20,
            "pm_volume": pm["pm_volume"], "dollar_m": dollar_m,
        })
    print(f"검정 표본 {len(rows)}행 (프리마켓 결측 {miss_pm} · ADV20 결측 {miss_adv})")
    if not rows:
        return 1

    losers = [r for r in rows if r["source"] == "day_losers"]
    print("\n== 주 검정: day_losers · 세션 내 중앙값 분할 (사전등록 §분할) ==")
    main_split = _split_report("전체 기간", losers)
    for month in ("2026-07", "2026-08"):
        _split_report(f"{month} 부호 재현", [r for r in losers if r["signal_date"].startswith(month)])

    print("\n== 밴드(100~500M) 부분집합 방향 (사전등록 §판정③) ==")
    band = [r for r in losers if r["dollar_m"] is not None and BAND_MIN_M <= r["dollar_m"] <= BAND_MAX_M]
    _split_report("밴드 내", band)

    print("\n== 확률 통제 (사전등록 §통제) ==")
    with_prob = [r for r in losers if r["prob"] is not None]
    if len(with_prob) >= 10:
        ranks_p = {id(r): i for i, r in enumerate(sorted(with_prob, key=lambda x: x["prob"]))}
        ranks_v = {id(r): i for i, r in enumerate(sorted(with_prob, key=lambda x: x["rel_pm"]))}
        n = len(with_prob)
        d2 = sum((ranks_p[id(r)] - ranks_v[id(r)]) ** 2 for r in with_prob)
        rho = 1 - 6 * d2 / (n * (n * n - 1))
        print(f"  spearman(rel_pm, probability) = {rho:+.3f} (n={n})")
        terciles = sorted(with_prob, key=lambda x: x["prob"])
        third = max(1, n // 3)
        for idx, name in ((0, "확률 하위"), (1, "확률 중위"), (2, "확률 상위")):
            _split_report(name, terciles[idx * third: (idx + 1) * third if idx < 2 else n])
    print("\n== 보조: 전체 소스 ==")
    _split_report("전체(170행 기반)", rows)

    print("\n판정은 사전등록 기준(①월별 부호 일치 ②k>=15 ③밴드 방향 유지 ④확률 통제 생존)으로 —")
    print("스크립트는 집계만 하고 결론은 운영자 보고로 낸다. 기준 미충족이면 보류/기각.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
