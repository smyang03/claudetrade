#!/usr/bin/env python3
"""전 시장 그물 밖 인구조사 — 계약 프로필 충족 종목 vs 우리 풀 (2026-08-27).

운영자 질문("선정에서 놓치는 후보는 없나")의 실측 도구. Alpaca 전 종목
리스트(~5,300 보통주)에서 계약 프로필(일 수익률<=-5%·종가>=$5·거래대금
100~500M$)을 충족하는 종목을 세션별로 세고, 우리 풀(스크리너 상위 10)과
대조한 뒤, 놓친 종목의 계약 forward(TP12/SL25/D5 일봉 시뮬)를 계산한다.

첫 실측(08-19~26, 6세션): 프로필 충족 4~35개/세션 중 풀 편입 0~1개 —
수집 갭 ~97% 실재. 단 유일 정산 세션(08-19)의 놓친 34건 평균 -2.81%
(승률 29%) — "그물 밖은 넓지만 질이 낮다" 방향(그물 관측 -2.14%와 일치).
판정은 놓친 표본 정산이 쌓이면(주 1회 재실행) 확정한다.

주의: MAX>=8 하한은 미적용(21일 이력 필요 시 추가), ETF/펀드 제외 휴리스틱,
end는 항상 now-20분(고정 문자열은 SIP 403). 관측 전용.
사용: python tools/full_market_net_census.py [--start 2026-08-01]
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.alpaca"
CACHE = ROOT / "data" / "analysis" / "full_market_scan_202608.json"
BAND = (100.0, 500.0)
DROP_LE = -5.0
MIN_CLOSE = 5.0


def _headers() -> dict[str, str]:
    env: dict[str, str] = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def collect(start: str) -> dict:
    headers = _headers()
    end = (datetime.now(tz=timezone.utc) - timedelta(minutes=20)).isoformat(timespec="seconds").replace("+00:00", "Z")
    resp = requests.get("https://paper-api.alpaca.markets/v2/assets", headers=headers,
                        params={"status": "active", "asset_class": "us_equity"}, timeout=60)
    resp.raise_for_status()
    symbols = []
    for asset in resp.json():
        sym = asset.get("symbol") or ""
        name = (asset.get("name") or "").upper()
        if not asset.get("tradable"):
            continue
        if asset.get("exchange") not in ("NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"):
            continue
        if not sym.isalpha() or len(sym) > 5:
            continue
        if any(w in name for w in ("ETF", "FUND", "TRUST", "ETN", "INDEX", "SHARES")):
            continue
        symbols.append(sym)
    print(f"대상 {len(symbols)}종목 수집 중...")
    series: dict[str, list] = {}
    for i in range(0, len(symbols), 200):
        batch = symbols[i:i + 200]
        params = {"symbols": ",".join(batch), "timeframe": "1Day", "start": f"{start}T00:00:00Z",
                  "end": end, "feed": "sip", "limit": "10000", "adjustment": "raw"}
        token = ""
        while True:
            if token:
                params["page_token"] = token
            try:
                rr = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=headers, params=params, timeout=60)
            except requests.RequestException:
                time.sleep(2)
                continue
            if rr.status_code == 429:
                time.sleep(3)
                continue
            if rr.status_code != 200:
                break
            payload = rr.json()
            for sym, bars in (payload.get("bars") or {}).items():
                series.setdefault(sym, []).extend(bars)
            token = payload.get("next_page_token") or ""
            if not token:
                break
    CACHE.write_text(json.dumps(series))
    print(f"수집 {len(series)}종목 → {CACHE.name}")
    return series


def analyze(series: dict, pool_by_session: dict[str, set]) -> None:
    data = {}
    for sym, bars in series.items():
        seq = sorted({b["t"][:10]: (b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars}.items())
        data[sym] = seq

    def contract_net(sym: str, sig_idx: int):
        path = data[sym][sig_idx + 1:sig_idx + 6]
        if len(path) < 5:
            return None
        entry = path[0][1][0]
        if entry <= 0:
            return None
        tp, sl = entry * 1.12, entry * 0.75
        exit_px = path[-1][1][3]
        for i, (_, (o, h, l, c, v)) in enumerate(path):
            if i > 0 and o <= sl:
                exit_px = o; break
            if i > 0 and o >= tp:
                exit_px = o; break
            if l <= sl:
                exit_px = sl; break
            if h >= tp:
                exit_px = tp; break
        return 100 * (exit_px / entry - 1) - 0.5

    missed_all, inpool_all = [], []
    print(f"{'세션':12s} {'충족':>4s} {'풀':>3s} {'놓침':>4s} | 놓친 정산 상위")
    for sess in sorted(pool_by_session):
        hits = []
        for sym, seq in data.items():
            idx = {d: i for i, (d, _) in enumerate(seq)}
            if sess not in idx or idx[sess] < 1:
                continue
            i = idx[sess]
            (o, h, l, c, v) = seq[i][1]
            pc = seq[i - 1][1][3]
            if pc <= 0 or c < MIN_CLOSE:
                continue
            if 100 * (c / pc - 1) <= DROP_LE and BAND[0] <= c * v / 1e6 <= BAND[1]:
                hits.append((sym, i))
        pool = pool_by_session[sess]
        missed = [(s, i) for s, i in hits if s not in pool]
        inpool = [(s, i) for s, i in hits if s in pool]
        fw = sorted([(s, round(n, 1)) for s, i in missed if (n := contract_net(s, i)) is not None],
                    key=lambda x: -x[1])
        missed_all += [v for _, v in fw]
        inpool_all += [n for s, i in inpool if (n := contract_net(s, i)) is not None]
        print(f"{sess:12s} {len(hits):4d} {len(inpool):3d} {len(missed):4d} | {fw[:6]}")
    if missed_all:
        print(f"\n놓친 정산 {len(missed_all)}건 평균 {st.mean(missed_all):+.2f}% 승률 {100*sum(1 for v in missed_all if v>0)/len(missed_all):.0f}%")
    if inpool_all:
        print(f"풀 내 정산 {len(inpool_all)}건 평균 {st.mean(inpool_all):+.2f}%")


# 세션별 우리 풀 (밴드 재선택 로그에서 발췌 — 갱신 시 로그에서 추가)
POOL = {
    "2026-08-19": {"MXL", "HTZ", "SATS", "VSAT", "WULF", "CIFR", "GRRR", "SPHR", "AXTI"},
    "2026-08-20": {"VOYG", "MXL", "CRML", "NVTS", "SEZL", "RGC", "LICN", "FLD"},
    "2026-08-21": {"AVAV", "SEI", "OPEN", "LEU", "SATS", "BETA", "ANNX", "CRDO"},
    "2026-08-24": {"IOVA", "OSIS", "SRE", "HUT", "CBRS", "RIOT", "MRVL", "SA", "BHC", "BKV"},
    "2026-08-25": {"RGTI", "QBTS", "FRVO", "AAOI", "ASTS", "NN", "FSLY", "TEM", "IONQ", "PONY"},
    "2026-08-26": {"RBRK", "ALB", "ASO", "BBWI", "BETA", "MMED", "WSC", "DY", "JMKE"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description="전 시장 그물 밖 인구조사")
    parser.add_argument("--start", default="2026-08-01")
    parser.add_argument("--use-cache", action="store_true")
    args = parser.parse_args()
    series = json.loads(CACHE.read_text()) if (args.use_cache and CACHE.exists()) else collect(args.start)
    analyze(series, POOL)
    return 0


if __name__ == "__main__":
    sys.exit(main())
